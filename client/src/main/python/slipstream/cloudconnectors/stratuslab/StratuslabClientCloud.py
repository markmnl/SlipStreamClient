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

import base64
import commands
import os
import socket
import sys
import time

from stratuslab.ConfigHolder import ConfigHolder as StratuslabConfigHolder
from stratuslab.marketplace.ManifestDownloader import ManifestDownloader
from stratuslab.Creator import Creator
from stratuslab.Creator import CreatorBaseListener
from stratuslab.vm_manager.Runner import Runner
from stratuslab.volume_manager.volume_manager_factory import VolumeManagerFactory
from stratuslab.Exceptions import OneException

import slipstream.exceptions.Exceptions as Exceptions
import slipstream.util as util

from slipstream.cloudconnectors.stratuslab.stratuslabPatch import patch_stratuslab
from slipstream.cloudconnectors.BaseCloudConnector import BaseCloudConnector
from slipstream.utils.ssh import generate_ssh_keypair
from slipstream.util import override


def getConnector(configHolder):
    return getConnectorClass()(configHolder)


def getConnectorClass():
    return StratuslabClientCloud


class StratuslabClientCloud(BaseCloudConnector):
    RUNINSTANCE_RETRY_TIMEOUT = 3

    cloudName = 'stratuslab'

    def __init__(self, slipstreamConfigHolder):
        self.creator = None

        super(StratuslabClientCloud, self).__init__(slipstreamConfigHolder)

        self.slConfigHolder = StratuslabConfigHolder(slipstreamConfigHolder.options,
                                                     slipstreamConfigHolder.config)
        self._set_listener(CreatorBaseListener(verbose=(self.verboseLevel > 1)))

        self._set_capabilities(contextualization=True,
                               direct_ip_assignment=True,
                               orchestrator_can_kill_itself_or_its_vapp=True)
        patch_stratuslab()

    def _start_image_for_build(self, user_info, node_instance):

        self._prepare_machine_for_build_image()

        self.slConfigHolder.set('marketplaceEndpoint', user_info.get_cloud('marketplace.endpoint'))

        manifest_downloader = ManifestDownloader(self.slConfigHolder)

        image_id = node_instance.get_image_id()
        node_instance.set_image_attributes({'imageVersion': manifest_downloader.getImageVersion(imageId=image_id)})

        self._update_stratuslab_config_holder_for_build_image(user_info, node_instance)

        self.creator = Creator(image_id, self.slConfigHolder)
        self.creator.setListener(self._get_listener())

        createImageTemplateDict = self.creator._getCreateImageTemplateDict()  # pylint: disable=protected-access

        def our_create_template_dict():
            createImageTemplateDict.update({})
            return createImageTemplateDict

        self.creator._getCreateImageTemplateDict = our_create_template_dict  # pylint: disable=protected-access

        self.creator.createStep1()

        vm = self.creator.runner
        return vm

    @override
    def _build_image(self, user_info, node_instance):

        self.creator.createStep2()

        return self._poll_storage_for_new_image(self.slConfigHolder)

    def _poll_storage_for_new_image(self, slConfigHolder):
        new_image_id = ''

        msg_endpoint = os.environ.get('SLIPSTREAM_PDISK_ENDPOINT', None)

        if msg_endpoint:
            diid = os.environ.get('SLIPSTREAM_DIID', None)
            if diid:
                tag = "SlipStream-%s" % diid
                filters = {'tag': [tag, ]}

                slConfigHolder.set('pdiskEndpoint', msg_endpoint)

                pdisk = VolumeManagerFactory.create(slConfigHolder)

                print >> sys.stdout, "Searching on %s for disk with tag %s." % (msg_endpoint, tag)
                sys.stdout.flush()

                # hardcoded polling for 30' at 1' intervals
                for i in range(30):
                    print >> sys.stdout, "Search iteration %d" % i
                    sys.stdout.flush()
                    volumes = pdisk.describeVolumes(filters)
                    if len(volumes) > 0:
                        try:
                            new_image_id = volumes[0]['identifier']
                        except Exception as ex:
                            print "Exception occurred looking for volume: %s" % ex
                        break
                    time.sleep(60)

        print "Returning new image ID value: %s" % new_image_id
        return new_image_id

    @staticmethod
    def _get_create_image_messaging_message(image_resource_uri):
        return base64.b64encode('{"uri":"%s", "imageid":""}' % image_resource_uri)

    @override
    def _initialization(self, user_info):
        self.slConfigHolder.options.update(Runner.defaultRunOptions())
        self._set_user_info_on_stratuslab_config_holder(user_info)

    @override
    def _start_image(self, user_info, node_instance, vm_name):
        if self.is_build_image():
            return self._start_image_for_build(user_info, node_instance)
        else:
            return self._start_image_for_deployment(node_instance, vm_name)

    def _start_image_for_deployment(self, node_instance, vm_name):
        configHolder = self.slConfigHolder.deepcopy()

        self._set_instance_params_on_config_holder(configHolder, node_instance)

        image_id = node_instance.get_image_id()

        self._set_extra_context_data_on_config_holder(configHolder, node_instance)
        self._set_vm_name_on_config_holder(configHolder, vm_name)

        runner = self._run_instance(image_id, configHolder)
        return runner

    @override
    def _vm_get_ip(self, runner):
        return runner.instancesDetail[0]['ip']

    @override
    def _vm_get_id(self, runner):
        return runner.instancesDetail[0]['id']

    def _set_instance_params_on_config_holder(self, configHolder, node_instance):
        self._set_instance_size_on_config_holder(configHolder, node_instance)
        self._set_extra_disks_on_config_holder(configHolder, node_instance)
        self._set_network_type_on_config_holder(configHolder, node_instance)

    def _set_instance_size_on_config_holder(self, configHolder, node_instance):
        self._set_instance_type_on_configholder(configHolder, node_instance)
        self._set_cpu_ram_on_config_holder(configHolder, node_instance)

    def _set_instance_type_on_configholder(self, configHolder, node_instance):
        configHolder.instanceType = node_instance.get_instance_type()

    def _set_cpu_ram_on_config_holder(self, configHolder, node_instance):
        configHolder.vmCpu = node_instance.get_cpu() or None
        vm_ram_gb = node_instance.get_ram() or None
        if vm_ram_gb:
            try:
                # StratusLab needs value in MB
                configHolder.vmRam = str(int(vm_ram_gb.strip()) * 1024)
            except:
                pass

    def _set_extra_disks_on_config_holder(self, configHolder, node_instance):
        # 'extra_disk_volatile' is given in GB - 'extraDiskSize' needs to be in MB
        configHolder.extraDiskSize = int(node_instance.get_volatile_extra_disk_size() or 0) * 1024
        configHolder.persistentDiskUUID = node_instance.get_cloud_parameter('extra_disk_persistent', '')
        configHolder.readonlyDiskId = node_instance.get_cloud_parameter('extra_disk_readonly', '')

    def _set_extra_context_data_on_config_holder(self, configHolder, node_instance):
        nodename = node_instance.get_name()
        configHolder.extraContextData = '#'.join(
            ['%s=%s' % (k, v) for (k, v) in os.environ.items() if k.startswith('SLIPSTREAM_')])
        configHolder.extraContextData += '#SLIPSTREAM_NODENAME=%s' % nodename
        configHolder.extraContextData += '#SCRIPT_EXEC=%s' % self._build_slipstream_bootstrap_command(node_instance)

    def _set_vm_name_on_config_holder(self, configHolder, vm_name):
        configHolder.vmName = vm_name

    def _run_instance(self, image_id, configHolder, max_attempts=3):
        if max_attempts <= 0:
            max_attempts = 1
        attempt = 1
        while True:
            try:
                runner = self._do_run_instance(image_id, configHolder)
            except socket.error, ex:
                if attempt >= max_attempts:
                    raise Exceptions.ExecutionException(
                        "Failed to launch instance after %i attempts: %s" %
                        (attempt, str(ex)))
                time.sleep(self.RUNINSTANCE_RETRY_TIMEOUT)
                attempt += 1
            else:
                return runner

    def _do_run_instance(self, image_id, configHolder):
        runner = self._get_stratuslab_runner(image_id, configHolder)
        try:
            runner.runInstance()
        except OneException as ex:
            # Retry once on a machine allocation error. OpenNebula has a problem
            # in authorization module which on a heavy load may through this error.
            if str(ex).strip().startswith('[VirtualMachineAllocate]'):
                time.sleep(2)
                runner.runInstance()
            else:
                raise
        return runner

    def _get_stratuslab_runner(self, image_id, configHolder):
        return Runner(image_id, configHolder)

    def _prepare_machine_for_build_image(self):
        generate_ssh_keypair(self.sshPrivKeyFile)
        self._installPackagesLocal(['curl'])

    @staticmethod
    def _installPackagesLocal(packages):
        cmd = 'apt-get -y install %s' % ' '.join(packages)
        rc, output = commands.getstatusoutput(cmd)
        if rc != 0:
            raise Exceptions.ExecutionException('Could not install required packages: %s\n%s' % (cmd, output))
            # FIXME: ConfigHolder needs more info for a proper bootstrap. Substitute later.
        #            machine = SystemFactory.getSystem('ubuntu', self.slConfigHolder)
        #            machine.installPackages(packages)

    def _build_slipstream_bootstrap_command(self, node_instance, user=None):
        return "sleep 15; " + super(StratuslabClientCloud, self)._build_slipstream_bootstrap_command(node_instance,
                                                                                                     user)

    @override
    def _stop_deployment(self):
        errors = []
        for nodename, runner in self.get_vms().items():
            try:
                runner.killInstances()
            except Exception:
                # Retry killing instances.
                try:
                    time.sleep(2)
                    runner.killInstances()
                except Exception as ex:
                    errors.append('Error killing node %s\n%s' % (nodename, str(ex)))
        if errors:
            raise Exceptions.CloudError('Failed stopping following instances. Details: %s' % '\n   -> '.join(errors))

    @override
    def _stop_vms_by_ids(self, ids):
        configHolder = self.slConfigHolder.copy()
        runner = Runner(None, configHolder)
        runner.killInstances(map(int, ids))

    def _update_stratuslab_config_holder_for_build_image(self, user_info, node_instance):

        self.slConfigHolder.set('verboseLevel', self.verboseLevel)

        self.slConfigHolder.set('comment', '')

        title = "SlipStream-%s" % os.environ.get('SLIPSTREAM_DIID', 'undefined diid')
        self.slConfigHolder.set('title', title)

        self._set_user_info_on_stratuslab_config_holder(user_info, build_image=True)
        self._set_image_info_on_stratuslab_config_holder(node_instance)

        self._set_instance_size_on_config_holder(self.slConfigHolder, node_instance)

    def _set_image_info_on_stratuslab_config_holder(self, node_instance):
        self._set_build_targets_on_stratuslab_config_holder(node_instance)
        self._set_new_image_group_version_on_stratuslab_config_holder(node_instance)

    def _set_build_targets_on_stratuslab_config_holder(self, node_instance):

        self.slConfigHolder.set('prerecipe', node_instance.get_prerecipe())
        self.slConfigHolder.set('recipe', node_instance.get_recipe())

        packages = ','.join(node_instance.get_packages())
        self.slConfigHolder.set('packages', packages)

    def _set_new_image_group_version_on_stratuslab_config_holder(self, node_instance):
        def _increment_minor_version_number(version):
            try:
                x, y = version.split('.')
                return '.'.join([x, str(int(y) + 1)])
            except:
                return version

        new_version = _increment_minor_version_number(node_instance.get_image_attribute('imageVersion'))
        self.slConfigHolder.set('newImageGroupVersion', new_version)
        self.slConfigHolder.set('newImageGroupVersionWithManifestId', True)

    def _set_user_info_on_stratuslab_config_holder(self, user_info, build_image=False):
        try:
            if build_image:
                self.slConfigHolder.set('author',
                                        '%s %s' % (user_info.get_first_name(),
                                                   user_info.get_last_name()))
                self.slConfigHolder.set('authorEmail',
                                        user_info.get_email())
                self.slConfigHolder.set('saveDisk', True)

            sshPubKeysFile = self.__populate_ssh_pub_keys_file(user_info)
            self.slConfigHolder.set('userPublicKeyFile', sshPubKeysFile)

            self.slConfigHolder.set('endpoint', user_info.get_cloud_endpoint())
            self.slConfigHolder.set('username', user_info.get_cloud_username())
            self.slConfigHolder.set('password', user_info.get_cloud_password())

            self.slConfigHolder.set('marketplaceEndpoint',
                                    user_info.get_cloud('marketplace.endpoint'))
        except KeyError, ex:
            raise Exceptions.ExecutionException('Error bootstrapping from User Parameters. %s' % str(ex))

        #        onErrorRunForever = userInfo.get_global('On Error Run Forever', 'off')
        #        if onErrorRunForever == 'on':
        #            shutdownVm = False
        #        else:
        #            shutdownVm = True
        # To be able to create a new image we need to shutdown the instance.
        shutdownVm = True
        self.slConfigHolder.set('shutdownVm', shutdownVm)

    def _set_network_type_on_config_holder(self, configHolder, node_instance):
        # SS's 'Private' maps to 'local' in SL. The default is 'public' in SL.
        # We don't use SL's 'private' IPs.
        if 'Private' == node_instance.get_network_type():
            configHolder.set('isLocalIp', True)

    def __populate_ssh_pub_keys_file(self, user_info):
        sshPubKeyFileTemp = self.sshPubKeyFile + '.temp'

        try:
            sshPubKeyLocal = util.fileGetContent(self.sshPubKeyFile)
        except:
            sshPubKeyLocal = ''

        userSshPubKey = user_info.get_public_keys()

        sshPubKeys = ''
        for sshKey in [sshPubKeyLocal, userSshPubKey]:
            if sshKey:
                sshPubKeys += '%s\n' % sshKey.strip()

        util.filePutContent(sshPubKeyFileTemp, sshPubKeys)

        return sshPubKeyFileTemp
