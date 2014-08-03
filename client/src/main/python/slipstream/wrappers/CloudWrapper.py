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

from slipstream.wrappers.BaseWrapper import BaseWrapper
from slipstream.cloudconnectors.CloudConnectorFactory import CloudConnectorFactory
from slipstream import util
from slipstream.NodeDecorator import NodeDecorator
from slipstream.exceptions.Exceptions import ExecutionException


class CloudWrapper(BaseWrapper):

    def __init__(self, configHolder):
        super(CloudWrapper, self).__init__(configHolder)

        self._instance_names_to_be_gone = []

        # Explicitly call initCloudConnector() to set the cloud connector.
        self.cloudProxy = None
        self.configHolder = configHolder
        self.imagesStopped = False

    def initCloudConnector(self, configHolder=None):
        self.cloudProxy = CloudConnectorFactory.createConnector(configHolder or self.configHolder)

    def build_image(self):
        self.cloudProxy.set_slipstream_client_as_listener(self.get_slipstream_client())
        user_info = self._get_user_info(self._get_cloud_service_name())

        node_instance = self._get_node_instances_to_start().get(NodeDecorator.MACHINE_NAME)
        if node_instance is None:
            raise ExecutionException('Failed to get node instance for instance named "%s"' %
                                     NodeDecorator.MACHINE_NAME)

        new_id = self.cloudProxy.build_image(user_info, node_instance)

        self._update_slipstream_image(node_instance, new_id)

    def start_node_instances(self):
        user_info = self._get_user_info(self._get_cloud_service_name())
        nodes_instances = self._get_node_instances_to_start()
        self.cloudProxy.start_nodes_and_clients(user_info, nodes_instances)

    def _get_node_instances_to_start(self):
        return self.get_node_instances_in_scale_state(
            self.SCALE_STATE_CREATING, self._get_cloud_service_name())

    def _get_node_instances_to_stop(self):
        return self.get_node_instances_in_scale_state(
            self.SCALE_STATE_REMOVING, self._get_cloud_service_name())

    def stop_node_instances(self):
        ids = []
        node_instances_to_stop = self._get_node_instances_to_stop()
        for node_instance in node_instances_to_stop.values():
            ids.append(node_instance.get_instance_id())
        if len(ids) > 0:
            self.cloudProxy.stop_vms_by_ids(ids)

        instance_names_removed = node_instances_to_stop.keys()
        self.set_scale_state_on_node_instances(instance_names_removed,
                                               self.SCALE_STATE_REMOVED)

        # Cache instance names that are to be set as 'gone' at Ready state.
        self._instance_names_to_be_gone = instance_names_removed

    def set_removed_instances_as_gone(self):
        '''Using cached list of instance names that were set as 'removed'.
        '''
        self.set_scale_state_on_node_instances(self._instance_names_to_be_gone,
                                               self.SCALE_STATE_GONE)
        self._instance_names_to_be_gone = {}

    def stopCreator(self):
        if self.need_to_stop_images(True):
            creator_id = self.cloudProxy.get_creator_vm_id()
            if creator_id:
                if not self._is_vapp():
                    self.cloudProxy.stop_vms_by_ids([creator_id])
                elif not self._is_build_in_single_vapp():
                    self.cloudProxy.stop_vapps_by_ids([creator_id])

    def stopNodes(self):
        if self.need_to_stop_images():
            if not self._is_vapp():
                self.cloudProxy.stop_deployment()
            self.imagesStopped = True

    def stopOrchestrator(self, is_build_image=False):
        if is_build_image:
            self.stopOrchestratorBuild()
        else:
            self.stopOrchestratorDeployment()

    def stopOrchestratorBuild(self):
        if self.need_to_stop_images(True):
            orch_id = self.get_cloud_instance_id()

            if self._is_vapp():
                if self._orchestrator_can_kill_itself_or_its_vapp():
                    if self._is_build_in_single_vapp():
                        self.cloudProxy.stop_deployment()
                    else:
                        self.cloudProxy.stop_vapps_by_ids([orch_id])
                else:
                    self._terminate_run_server_side()
            else:
                if self._orchestrator_can_kill_itself_or_its_vapp():
                    self.cloudProxy.stop_vms_by_ids([orch_id])
                else:
                    self._terminate_run_server_side()

    def stopOrchestratorDeployment(self):
        if self._is_vapp() and self.need_to_stop_images():
            if self._orchestrator_can_kill_itself_or_its_vapp():
                self.cloudProxy.stop_deployment()
            else:
                self._terminate_run_server_side()
        elif self.need_to_stop_images() and not self._orchestrator_can_kill_itself_or_its_vapp():
            self._terminate_run_server_side()
        else:
            orch_id = self.get_cloud_instance_id()
            self.cloudProxy.stop_vms_by_ids([orch_id])

    def _is_build_in_single_vapp(self):
        return self.cloudProxy.has_capability(
            self.cloudProxy.CAPABILITY_BUILD_IN_SINGLE_VAPP)

    def _is_vapp(self):
        return self.cloudProxy.has_capability(self.cloudProxy.CAPABILITY_VAPP)

    def _orchestrator_can_kill_itself_or_its_vapp(self):
        return self.cloudProxy.has_capability(
            self.cloudProxy.CAPABILITY_ORCHESTRATOR_CAN_KILL_ITSELF_OR_ITS_VAPP)

    def need_to_stop_images(self, ignore_on_success_run_forever=False):
        runParameters = self._get_run_parameters()

        onErrorRunForever = runParameters.get('General.On Error Run Forever', 'false')
        onSuccessRunForever = runParameters.get('General.On Success Run Forever', 'false')

        stop = True
        if self.isAbort():
            if onErrorRunForever == 'true':
                stop = False
        elif onSuccessRunForever == 'true' and not ignore_on_success_run_forever:
            stop = False

        return stop

    def _update_slipstream_image(self, node_instance, new_image_id):
        util.printStep("Updating SlipStream image run")

        url = '%s/%s' % (node_instance.get_image_resource_uri(),
                         self._get_cloud_service_name())
        self._put_new_image_id(url, new_image_id)

    def _get_cloud_service_name(self):
        return self.cloudProxy.get_cloud_service_name()
