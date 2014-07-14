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

import re
import time

from urlparse import urlparse

from slipstream.cloudconnectors.BaseCloudConnector import BaseCloudConnector
from slipstream.utils.tasksrunner import TasksRunner
from slipstream.util import override
import slipstream.util as util
import slipstream.exceptions.Exceptions as Exceptions

from libcloud.compute.base import KeyPair
from libcloud.compute.types import Provider
from libcloud.compute.providers import get_driver

import libcloud.security


def getConnector(configHolder):
    return getConnectorClass()(configHolder)


def getConnectorClass():
    return CloudStackClientCloud


class CloudStackClientCloud(BaseCloudConnector):

    cloudName = 'cloudstack'

    def __init__(self, configHolder):
        libcloud.security.VERIFY_SSL_CERT = False

        super(CloudStackClientCloud, self).__init__(configHolder)

        self._set_capabilities(contextualization=True,
                               generate_password=True,
                               direct_ip_assignment=True,
                               orchestrator_can_kill_itself_or_its_vapp=True)

    @override
    def _initialization(self, user_info):
        util.printStep('Initialize the CloudStack connector.')
        self._thread_local.driver = self._get_driver(user_info)
        self.sizes = self._thread_local.driver.list_sizes() # pylint: disable=attribute-defined-outside-init
        self.images = self._thread_local.driver.list_images() # pylint: disable=attribute-defined-outside-init
        self.user_info = user_info # pylint: disable=attribute-defined-outside-init

        if self.is_build_image():
            raise NotImplementedError('The run category "%s" is not yet implemented' % self.run_category)
        elif self.is_deployment():
            try:
                self._import_keypair(user_info)
            except Exceptions.ExecutionException as e:
                util.printError(e)

    @override
    def _finalization(self, user_info):
        try:
            kp_name = user_info.get_keypair_name()
            if kp_name:
                self._delete_keypair(kp_name)
        except: # pylint: disable=bare-except
            pass

    @override
    def _start_image(self, user_info, node_instance, vm_name):
        self._thread_local.driver = self._get_driver(user_info)
        return self._start_image_on_cloudstack(user_info, node_instance, vm_name)

    def _start_image_on_cloudstack(self, user_info, node_instance, vm_name):
        image_id = node_instance.get_image_id()
        instance_name = self.format_instance_name(vm_name)
        instance_type = node_instance.get_instance_type()
        ip_type = node_instance.get_network_type()

        keypair = None
        contextualization_script = None
        if not node_instance.is_windows():
            keypair = user_info.get_keypair_name()
            contextualization_script = self.is_build_image() and '' or self._get_bootstrap_script(node_instance)

        security_groups = node_instance.get_security_groups()
        security_groups = (len(security_groups) > 0) and security_groups or None

        try:
            size = [i for i in self.sizes if i.name == instance_type][0]
        except IndexError:
            raise Exceptions.ParameterNotFoundException(
                "Couldn't find the specified instance type: %s" % instance_type)
        try:
            image = [i for i in self.images if i.id == image_id][0]
        except IndexError:
            raise Exceptions.ParameterNotFoundException(
                "Couldn't find the specified image: %s" % image_id)

        if node_instance.is_windows():
            instance = self._thread_local.driver.create_node(
                name=instance_name,
                size=size,
                image=image,
                ex_security_groups=security_groups)
        else:
            instance = self._thread_local.driver.create_node(
                name=instance_name,
                size=size,
                image=image,
                ex_keyname=keypair,
                ex_userdata=contextualization_script,
                ex_security_groups=security_groups)

        ip = self._get_instance_ip_address(instance, ip_type)
        if not ip:
            raise Exceptions.ExecutionException("Couldn't find a '%s' IP" % ip_type)

        vm = dict(instance=instance,
                  ip=ip,
                  id=instance.id)
        return vm

    def list_instances(self):
        return self._thread_local.driver.list_nodes()

    def _stop_instances(self, instances):
        max_workers = self._get_max_workers(self.configHolder)
        tasksRunnner = TasksRunner(self.__stop_instance,
                                   max_workers=max_workers,
                                   verbose=self.verboseLevel)
        for instance in instances:
            tasksRunnner.put_task(instance)

        tasksRunnner.run_tasks()
        tasksRunnner.wait_tasks_processed()

    def __stop_instance(self, instance):
        driver = self._get_driver(self.user_info)
        driver.destroy_node(instance)

    @override
    def _stop_deployment(self):
        instances = [vm['instance'] for vm in self.get_vms().itervalues()]
        self._stop_instances(instances)

    @override
    def _stop_vms_by_ids(self, ids):
        instances = [i for i in self.list_instances() if i.id in ids]
        self._stop_instances(instances)

    @staticmethod
    def _get_driver(userInfo):
        CloudStack = get_driver(Provider.CLOUDSTACK)

        url = urlparse(userInfo.get_cloud('endpoint'))
        secure = (url.scheme == 'https')

        return CloudStack(userInfo.get_cloud('username'),
                          userInfo.get_cloud('password'),
                          secure=secure,
                          host=url.hostname,
                          port=url.port,
                          path=url.path)

    @override
    def _vm_get_password(self, vm):
        password = vm['instance'].extra.get('password', None)
        print 'VM Password: ', password
        return password

    @override
    def _vm_get_ip(self, vm):
        return vm['ip']

    @override
    def _vm_get_id(self, vm):
        return vm['id']

    def _get_instance_ip_address(self, instance, ipType):
        if ipType.lower() == 'private':
            return (len(instance.private_ips) != 0) and instance.private_ips[0] or (len(instance.public_ips) != 0) and instance.public_ips[0] or ''
        else:
            return (len(instance.public_ips) != 0) and instance.public_ips[0] or (len(instance.private_ips) != 0) and instance.private_ips[0] or ''

    def _import_keypair(self, user_info):
        kp_name = 'ss-key-%i' % int(time.time())
        public_key = user_info.get_public_keys()
        try:
            kp = self._thread_local.driver.import_key_pair_from_string(kp_name, public_key)
        except Exception as e:
            user_info.set_keypair_name(None)
            raise Exceptions.ExecutionException('Cannot import the public key. Reason: %s' % e)
        kp_name = kp.name
        user_info.set_keypair_name(kp_name)
        return kp_name

    def _create_keypair_and_set_on_user_info(self, user_info):
        kp_name = 'ss-build-image-%i' % int(time.time())
        kp = self._thread_local.driver.create_key_pair(kp_name)
        user_info.set_private_key(kp.private_key)
        kp_name = kp.name
        user_info.set_keypair_name(kp_name)
        return kp_name

    def _delete_keypair(self, kp_name):
        kp = KeyPair(name=kp_name, public_key=None, fingerprint=None, driver=self._thread_local.driver)
        return self._thread_local.driver.delete_keypair(kp)

    def format_instance_name(self, name):
        name = self.remove_bad_char_in_instance_name(name)
        return self.truncate_instance_name(name)

    def truncate_instance_name(self, name):
        if len(name) <= 63:
            return name
        else:
            return name[:31] + '-' + name[-31:]

    def remove_bad_char_in_instance_name(self, name):
        try:
            newname = re.sub(r'[^a-zA-Z0-9-]', '', name)
            m = re.search('[a-zA-Z]([a-zA-Z0-9-]*[a-zA-Z0-9]+)?', newname)
            return m.string[m.start():m.end()]
        except:
            raise Exceptions.ExecutionException(
                'Cannot handle the instance name "%s". Instance name can '
                'contain ASCII letters "a" through "z", the digits "0" '
                'through "9", and the hyphen ("-"), must be between 1 and 63 '
                'characters long, and can\'t start or end with "-" '
                'and can\'t start with digit' % name)
