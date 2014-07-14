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
from slipstream.NodeDecorator import (NodeDecorator, RUN_CATEGORY_IMAGE, RUN_CATEGORY_DEPLOYMENT,
                                      KEY_RUN_CATEGORY)
from slipstream.utils.tasksrunner import TasksRunner
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
        self.run_category = getattr(configHolder, KEY_RUN_CATEGORY, None)

        self._set_capabilities(contextualization=True,
                             generate_password=True,
                             direct_ip_assignment=True,
                             orchestrator_can_kill_itself_or_its_vapp=True)

    def initialization(self, user_info):
        util.printStep('Initialize the CloudStack connector.')
        self._thread_local.driver = self._get_driver(user_info)
        self.sizes = self._thread_local.driver.list_sizes()
        self.images = self._thread_local.driver.list_images()
        self.user_info = user_info

        if self.run_category == RUN_CATEGORY_DEPLOYMENT:
            try:
                self._import_keypair(user_info)
            except Exceptions.ExecutionException, e:
                util.printError(e)
        elif self.run_category == RUN_CATEGORY_IMAGE:
            raise NotImplementedError('The run category "%s" is not yet '
                                      'implemented' % self.run_category)

    def finalization(self, user_info):
        try:
            kp_name = self._userInfoGetKeypairName(user_info)
            if kp_name:
                self._delete_keypair(kp_name)
        except:
            pass

    def _startImage(self, user_info, image_info, instance_name,
                    cloudSpecificData=None):
        self._thread_local.driver = self._get_driver(user_info)
        return self._startImageOnCloudStack(user_info, image_info, instance_name,
                                            cloudSpecificData)

    def _startImageOnCloudStack(self, user_info, image_info, instance_name,
                                cloudSpecificData=None):
        imageId = self.getImageId(image_info)
        instance_name = self.formatInstanceName(instance_name)
        instanceType = self._getInstanceType(image_info)
        ipType = self.getCloudParameters(image_info)['network']

        keypair = None
        contextualizationScript = None
        if not self.isWindows():
            keypair = self._userInfoGetKeypairName(user_info)
            contextualizationScript = cloudSpecificData or None

        securityGroups = None
        security_groups = self._getCloudParameter(image_info, 'security.groups')
        if security_groups:
            securityGroups = [x.strip() for x in security_groups.split(',') if x]

        try:
            size = [i for i in self.sizes if i.name == instanceType][0]
        except IndexError:
            raise Exceptions.ParameterNotFoundException(
                "Couldn't find the specified instance type: %s" % instanceType)
        try:
            image = [i for i in self.images if i.id == imageId][0]
        except IndexError:
            raise Exceptions.ParameterNotFoundException(
                "Couldn't find the specified image: %s" % imageId)

        if self.isWindows():
            instance = self._thread_local.driver.create_node(
                name=instance_name,
                size=size,
                image=image,
                ex_security_groups=securityGroups)
        else:
            instance = self._thread_local.driver.create_node(
                name=instance_name,
                size=size,
                image=image,
                ex_keyname=keypair,
                ex_userdata=contextualizationScript,
                ex_security_groups=securityGroups)

        ip = self._get_instance_ip_address(instance, ipType)
        if not ip:
            raise Exceptions.ExecutionException("Couldn't find a '%s' IP" % ipType)

        vm = dict(networkType=ipType,
                  instance=instance,
                  ip=ip,
                  id=instance.id)
        return vm

    def _getCloudSpecificData(self, node_info, node_number, nodename):
        return self._get_bootstrap_script(nodename)

    def list_instances(self):
        return self._thread_local.driver.list_nodes()

    def _stopInstances(self, instances):
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

    def stopDeployment(self):
        instances = [vm['instance'] for vm in self.getVms().itervalues()]
        self._stopInstances(instances)

    def stopVmsByIds(self, ids):
        instances = [i for i in self.list_instances() if i.id in ids]
        self._stopInstances(instances)

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

    def _vm_get_password(self, vm):
        print 'VM Password: ', vm['instance'].extra.get('password', None)
        return vm['instance'].extra.get('password', None)

    def vmGetIp(self, vm):
        return vm['ip']

    def vmGetId(self, vm):
        return vm['id']

    def _get_instance_ip_address(self, instance, ipType):
        if ipType.lower() == 'private':
            return (len(instance.private_ips) != 0) and instance.private_ips[0] or (len(instance.public_ips) != 0) and instance.public_ips[0] or ''
        else:
            return (len(instance.public_ips) != 0) and instance.public_ips[0] or (len(instance.private_ips) != 0) and instance.private_ips[0] or ''

    def _import_keypair(self, user_info):
        kp_name = 'ss-key-%i' % int(time.time())
        public_key = self._getPublicSshKey(user_info)
        try:
            kp = self._thread_local.driver.import_key_pair_from_string(
                kp_name, public_key)
        except Exception as e:
            self._userInfoSetKeypairName(user_info, None)
            raise Exceptions.ExecutionException('Cannot import the public key. '
                                                'Reason: %s' % e)
        kp_name = kp.name
        self._userInfoSetKeypairName(user_info, kp_name)
        return kp_name

    def _create_keypair_and_set_on_user_info(self, user_info):
        kp_name = 'ss-build-image-%i' % int(time.time())
        kp = self._thread_local.driver.create_key_pair(kp_name)
        self._userInfoSetPrivateKey(user_info, kp.private_key)
        kp_name = kp.name
        self._userInfoSetKeypairName(user_info, kp_name)
        return kp_name

    def _delete_keypair(self, kp_name):
        kp = KeyPair(name=kp_name, public_key=None, fingerprint=None, driver=self._thread_local.driver)
        return self._thread_local.driver.delete_keypair(kp)

    def formatInstanceName(self, name):
        name = self.removeBadCharInInstanceName(name)
        return self.truncateInstanceName(name)

    def truncateInstanceName(self, name):
        if len(name) <= 63:
            return name
        else:
            return name[:31] + '-' + name[-31:]

    def removeBadCharInInstanceName(self, name):
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
