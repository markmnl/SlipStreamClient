"""
 SlipStream Client
 =====
 Copyright (C) 2013 SixSq Sarl (sixsq.com)
 =====
 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

      http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
"""

import os
import re
import time
import httplib
import requests
import socket
import stat
from random import random
from threading import Lock
from urlparse import urlparse
from cookielib import CookieJar
from requests import Request
import sseclient
from requests.cookies import MockResponse, MockRequest, RequestsCookieJar
from six.moves.http_cookiejar import MozillaCookieJar

import slipstream.exceptions.Exceptions as Exceptions
import slipstream.util as util

etree = util.importETree()


class SessionStore(requests.Session):
    """Session with extended MozillaCookieJar file-based store.
    """

    def __init__(self, cookie_file=None):
        super(SessionStore, self).__init__()
        if cookie_file is None:
            cookie_file = util.DEFAULT_COOKIE_FILE
        cookie_dir = os.path.dirname(cookie_file)
        self.cookies = MozillaCookieJar(cookie_file)
        if not os.path.isdir(cookie_dir):
            os.mkdir(cookie_dir, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        if os.path.isfile(cookie_file):
            self.cookies.load(ignore_discard=True)
            self.cookies.clear_expired_cookies()

    def request(self, *args, **kwargs):
        response = super(SessionStore, self).request(*args, **kwargs)
        if not self.verify and response.cookies:
            self._unsecure_cookie(args[1], response)
        self.cookies.save(ignore_discard=True)
        return response

    def _unsecure_cookie(self, url_str, response):
        url = urlparse(url_str)
        if url.scheme == 'http':
            for cookie in response.cookies:
                cookie.secure = False
                self.cookies.set_cookie_if_ok(cookie,
                                              MockRequest(response.request))

    def clear(self, domain):
        try:
            self.cookies.clear(domain)
            self.cookies.save()
        except KeyError:
            pass

    def set_cookies(self, cookies=[]):
        for c in cookies:
            self.cookies.set_cookie(c)


LOGIN_URI = 'auth/login'
DEFAULT_SS_COOKIE_NAME = 'com.sixsq.slipstream.cookie'

# Client Error
NOT_FOUND_ERROR = 404
CONFLICT_ERROR = 409
PRECONDITION_FAILED_ERROR = 412
EXPECTATION_FAILED_ERROR = 417
TOO_MANY_REQUESTS_ERROR = 429
# Server Error
SERVICE_UNAVAILABLE_ERROR = 503

HEADER_SSE = 'text/event-stream'


def http_debug():
    import logging
    from httplib import HTTPConnection

    HTTPConnection.debuglevel = 3

    logging.basicConfig()
    logging.getLogger().setLevel(logging.DEBUG)

    requests_log = logging.getLogger("requests.packages.urllib3")
    requests_log.setLevel(logging.DEBUG)
    requests_log.propagate = True


class HttpClient(object):
    def __init__(self, username=None, password=None, cookie=None,
                 configHolder=None):
        self.cookie = cookie
        self.username = username
        self.password = password
        self.verboseLevel = util.VERBOSE_LEVEL_NORMAL
        self.cookie_filename = util.DEFAULT_COOKIE_FILE

        self.lock = Lock()
        self.too_many_requests_count = 0

        if configHolder:
            configHolder.assign(self)

        if self.verboseLevel >= 3:
            http_debug()
        else:
            requests.packages.urllib3.disable_warnings()

        self.session = None

    def get(self, url, accept='application/xml', retry=True, stream=False):
        """
        NB! If stream is True, the second argument returned is the streamed
        response wrapped into sseclient.SSEClient. To get the events, one
        should iterate over `stream.events()` consuming `data` field of each
        event. When the required data is obtained, `close()` the stream. E.g.:
        for i, e in enumerate(stream.events()):
            print e.data
            if i >= 4:
                stream.close()
                break
        """
        if stream:
            accept = '%s, %s' % (accept, HEADER_SSE)
        resp = self._call(url, 'GET', accept=accept, retry=retry, stream=stream)
        if stream:
            return resp, sseclient.SSEClient(resp)
        return resp, resp.text

    def put(self, url, body=None, contentType='application/xml',
            accept='application/xml', retry=True):
        resp = self._call(url, 'PUT', body, contentType, accept, retry=retry)
        return resp, resp.text

    def post(self, url, body=None, contentType='application/xml', retry=True):
        resp = self._call(url, 'POST', body, contentType, retry=retry)
        return resp, resp.text

    def delete(self, url, body=None, retry=True):
        resp = self._call(url, 'DELETE', body, retry=retry)
        return resp, resp.text

    def _call(self, url, method,
              body=None,
              contentType='application/xml',
              accept='application/xml',
              retry=True,
              stream=False):

        def _handle3xx(resp):
            raise Exception("Redirect should have been handled by HTTP library."
                            "%s: %s" % (resp.status_code, resp.reason))

        def _handle4xx(resp):
            if resp.status_code == CONFLICT_ERROR:
                raise Exceptions.AbortException(_extract_detail(resp.text))
            if resp.status_code == PRECONDITION_FAILED_ERROR:
                raise Exceptions.NotYetSetException(_extract_detail(resp.text))
            if resp.status_code == EXPECTATION_FAILED_ERROR:
                raise Exceptions.TerminalStateException(_extract_detail(
                    resp.text))
            if resp.status_code == TOO_MANY_REQUESTS_ERROR:
                raise Exceptions.TooManyRequestsError("Too Many Requests")

            if resp.status_code == NOT_FOUND_ERROR:
                clientEx = Exceptions.NotFoundError(resp.reason)
            else:
                detail = _extract_detail(resp.text)
                detail = detail and detail or (
                    "%s (%d)" % (resp.reason, resp.status_code))
                msg = "Failed calling method %s on url %s, with reason: %s" % (
                    method, url, detail)
                clientEx = Exceptions.ClientError(msg)

            clientEx.code = resp.status_code
            raise clientEx

        def _handle5xx(resp):
            if resp.status_code == SERVICE_UNAVAILABLE_ERROR:
                raise Exceptions.ServiceUnavailableError(
                    "SlipStream is in maintenance.")
            else:
                raise Exceptions.ServerError(
                    "Failed calling method %s on url %s, with reason: %d: %s"
                    % (method, url, resp.status_code, resp.reason))

        def _extract_detail(content):
            if not content:
                return content
            try:
                return etree.fromstring(content).text
            except Exception:
                return content

        def _build_headers():
            headers = {}
            if contentType:
                headers['Content-Type'] = contentType
            if accept:
                headers['Accept'] = accept

            return headers

        def _request(headers):
            try:
                auth = ()
                cookie_used = False
                if self.session_cookie_exists(self.session, url):
                    cookie_used = True
                else:
                    self._log_debug("No session cookie found. Using Basic "
                                    "authentication.")
                    auth = (self.username, self.password)
                resp = self.session.request(method, url,
                                            auth=auth,
                                            data=body,
                                            headers=headers,
                                            stream=stream)
                if resp.status_code == 401 and cookie_used and (
                            self.username and self.password):
                    resp = self.session.request(method, url,
                                                auth=(
                                                    self.username,
                                                    self.password),
                                                data=body,
                                                headers=headers,
                                                stream=stream)
                return resp
            except requests.exceptions.InvalidSchema as ex:
                raise Exceptions.ClientError("Malformed URL: %s" % ex)
            except httplib.BadStatusLine:
                raise Exceptions.NetworkError(
                    "Error: BadStatusLine contacting: %s" % url)

        def _handle_response(resp):
            self._log_response(resp)

            if 100 <= resp.status_code < 300:
                return resp

            if 300 <= resp.status_code < 400:
                return _handle3xx(resp)

            if 400 <= resp.status_code < 500:
                return _handle4xx(resp)

            if 500 <= resp.status_code < 600:
                return _handle5xx(resp)

            raise Exceptions.NetworkError('Unknown HTTP return code: %s' %
                                          resp.status_code)

        self.init_session(url)

        self._log_normal(
            'Contacting the server with %s, at: %s' % (method, url))

        retry_until = 60 * 60 * 24 * 7  # 7 days in seconds
        max_wait_time = 60 * 15  # 15 minutes in seconds
        retry_count = 0

        first_request_time = time.time()

        while True:
            try:
                headers = _build_headers()
                resp = _request(headers)
                resp = _handle_response(resp)
                with self.lock:
                    if self.too_many_requests_count > 0:
                        self.too_many_requests_count -= 1
                return resp

            except (Exceptions.TooManyRequestsError,
                    Exceptions.ServiceUnavailableError) as ex:
                sleep = min(
                    abs(float(self.too_many_requests_count) / 10.0 * 290 + 10),
                    300)

            except (httplib.HTTPException, socket.error,
                    Exceptions.NetworkError, Exceptions.ServerError) as ex:
                timed_out = (time.time() - first_request_time) >= retry_until
                if retry is False or timed_out:
                    self._log_normal('HTTP call error: %s' % ex)
                    raise
                sleep = min(float(retry_count) * 10.0, float(max_wait_time))
                retry_count += 1

            sleep += (random() * sleep * 0.2) - (sleep * 0.1)
            with self.lock:
                if self.too_many_requests_count < 11:
                    self.too_many_requests_count += 1
            self._log_normal('Error: %s. Retrying in %s seconds.' % (ex, sleep))
            time.sleep(sleep)
            self._log_normal('Retrying...')

    @staticmethod
    def _cookie_from_str(url, cookie):
        from StringIO import StringIO
        data = "Set-Cookie: %s" % cookie
        headers = httplib.HTTPMessage(StringIO(data))
        resp = MockResponse(headers)
        req = MockRequest(Request(method='GET', url=url))
        jar = CookieJar()
        return jar.make_cookies(resp, req)

    def _set_oldstyle_cookie_on_session(self, url):
        if self.cookie and not (self.password and self.username):
            self.session.set_cookies(self._cookie_from_str(url, self.cookie))
            self.cookie = None

    def init_session(self, url):
        if self.session is None:
            self.session = SessionStore(cookie_file=self.cookie_filename)
            # TODO: remove when old cookie from ConfigHolder is gone.
            self._set_oldstyle_cookie_on_session(url)

    @staticmethod
    def session_cookie_exists(session, url, cookie_name=DEFAULT_SS_COOKIE_NAME):
        _url = urlparse(url)
        jar = RequestsCookieJar()
        jar.update(session.cookies)
        url_path = _url.path.split('/')
        for n in range(len(url_path), 0, -1):
            path = '/'.join(url_path[0:n]) or '/'
            cookies = jar.get_dict(domain=_url.netloc, path=path)
            if cookie_name in cookies:
                return True
        return False

    @staticmethod
    def is_streamed_response(resp):
        streamed = re.search(HEADER_SSE, resp.request.headers.get('Accept', ''))
        return streamed and resp.ok

    def _log_normal(self, message):
        util.printDetail(message, self.verboseLevel,
                         util.VERBOSE_LEVEL_NORMAL)

    def _log_debug(self, message):
        util.printDetail(message, self.verboseLevel,
                         util.VERBOSE_LEVEL_DETAILED)

    def _log_response(self, resp, max_characters=1000):
        if self.is_streamed_response(resp):
            msg = "Received streamed response."
        else:
            msg = 'Received response: %s\nWith content: %s' % (resp, resp.text)
        if len(msg) > max_characters:
            msg = '%s\n                         %s' % (
                msg[:max_characters], '::::: Content truncated :::::')
        self._log_debug(msg)
