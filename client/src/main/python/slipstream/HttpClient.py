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

from __future__ import print_function

import time
import httplib
import requests
import socket
from random import random
from threading import Lock
from urlparse import urlparse
from requests.exceptions import RequestException
from requests.cookies import RequestsCookieJar

try:
    from requests.packages.urllib3.exceptions import HTTPError
except:
    from urllib3.exceptions import HTTPError

import slipstream.exceptions.Exceptions as Exceptions
import slipstream.util as util
from slipstream.api.api import Api

etree = util.importETree()

DEFAULT_SS_COOKIE_NAME = 'com.sixsq.slipstream.cookie'


def get_cookie(cookie_jar, domain, path=None, name=DEFAULT_SS_COOKIE_NAME):
    """Returns requested cookie from the provided cookie_jar."""
    jar = RequestsCookieJar()
    jar.update(cookie_jar)
    cookie = None
    if path is None:
        cookies = jar.get_dict(domain=domain)
        cookie = cookies.get(name)
    elif path == '/':
        cookies = jar.get_dict(domain=domain, path=path)
        cookie = cookies.get(name)
    else:
        url_path = path.split('/')
        for n in range(len(url_path), 0, -1):
            path = '/'.join(url_path[0:n]) or '/'
            cookies = jar.get_dict(domain=domain, path=path)
            if name in cookies:
                cookie = cookies.get(name)
                break
    if cookie is None:
        return cookie
    else:
        return '%s=%s' % (name, cookie)


# Client Error
NOT_FOUND_ERROR = 404
CONFLICT_ERROR = 409
PRECONDITION_FAILED_ERROR = 412
EXPECTATION_FAILED_ERROR = 417
TOO_MANY_REQUESTS_ERROR = 429
# Server Error
SERVICE_UNAVAILABLE_ERROR = 503


def http_debug():
    import logging
    from httplib import HTTPConnection

    HTTPConnection.debuglevel = 3

    logging.basicConfig()
    logging.getLogger().setLevel(logging.DEBUG)

    requests_log = logging.getLogger("requests.packages.urllib3")
    requests_log.setLevel(logging.DEBUG)
    requests_log.propagate = True


def disable_urllib3_warnings():
    try:
        requests.packages.urllib3.disable_warnings(
            requests.packages.urllib3.exceptions.InsecureRequestWarning)
    except:
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except:
            pass


class HttpClient(object):

    def __init__(self, configHolder=None):
        self.host_cert_verify = False
        self.verboseLevel = util.VERBOSE_LEVEL_NORMAL
        self.cookie_filename = util.DEFAULT_COOKIE_FILE

        self.lock = Lock()
        self.too_many_requests_count = 0

        if configHolder:
            configHolder.assign(self)

        if self.verboseLevel >= 3:
            http_debug()
        else:
            disable_urllib3_warnings()

        self.session = None

    def get(self, url, accept='application/xml', retry=True):
        resp = self._call(url, 'GET', accept=accept, retry=retry)
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
              retry=True):

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
                return self.session.request(method, url,
                                            data=body,
                                            headers=headers,
                                            verify=self.host_cert_verify)
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

            except (httplib.HTTPException, socket.error, HTTPError, RequestException,
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

    def delete_local_cookie(self, url):
        _url = urlparse(url)
        if self.session is None:
            self.init_session(url)
        self.session.clear(_url.netloc, _url.path, DEFAULT_SS_COOKIE_NAME)

    def init_session(self, url):
        if self.session is None:
            url_parts = urlparse(url)
            endpoint = '%s://%s' % url_parts[:2]
            login_creds = {}
            if hasattr(self, 'username') and hasattr(self, 'password'):
                login_creds = {'username': self.username, 'password': self.password}
            elif hasattr(self, 'api_key') and hasattr(self, 'api_secret'):
                login_creds = {'key': self.api_key, 'secret': self.api_secret}
            api = Api(endpoint=endpoint, cookie_file=self.cookie_filename, reauthenticate=True,
                      login_creds=login_creds)
            self.session = api.session

    def _log_normal(self, message):
        util.printDetail(message, self.verboseLevel,
                         util.VERBOSE_LEVEL_NORMAL)

    def _log_debug(self, message):
        util.printDetail(message, self.verboseLevel,
                         util.VERBOSE_LEVEL_DETAILED)

    def _log_response(self, resp, max_characters=1000):
        msg = 'Received response: %s\nWith content: %s' % (resp, resp.text)
        if len(msg) > max_characters:
            msg = '%s\n                         %s' % (
                msg[:max_characters], '::::: Content truncated :::::')
        self._log_debug(msg)

    def get_session(self):
        return self.session
