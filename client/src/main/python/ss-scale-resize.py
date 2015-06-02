#!/usr/bin/env python
"""
 SlipStream Client
 =====
 Copyright (C) 2014 SixSq Sarl (sixsq.com)
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

import sys
from slipstream.NodeDecorator import NodeDecorator
from slipstream.SlipStreamHttpClient import DomExtractor

from slipstream.command.CommandBase import CommandBase
from slipstream.HttpClient import HttpClient
import slipstream.util as util
from slipstream.wrappers.BaseWrapper import BaseWrapper

etree = util.importETree()


class MainProgram(CommandBase):
    """A command-line program to request vertical scaling of node instances
    in a deployment.
    """

    def __init__(self, argv=None):
        self.run_url = None
        self.run_dom = None
        self.node_name = None
        self.instances_to_scale = []
        self.rtp_scale_values = {}

        self.options = None
        self.args = None

        self.username = None
        self.password = None
        self.cookie = None
        self.endpoint = None
        super(MainProgram, self).__init__(argv)

    def parse(self):
        usage = """usage: %prog [options] [[--cpu <num> --ram <MB>] | --instance-type <type>] <run> <node-name> <ids> [<ids> ...]
<run>        Run ID. Run should be mutable and in Ready state.
<node-name>  Node name to scale the instances of.
<ids>        Ids of the node instances to scale."""

        self.parser.usage = usage
        self.add_authentication_options()
        self.addEndpointOption()
        self.add_scale_options()

        self.options, self.args = self.parser.parse_args()
        self._check_args()

    def add_scale_options(self):
        self.parser.add_option('--cpu', dest='cpu', default=None,
                               help='New number of CPUs.', metavar='CPU')
        self.parser.add_option('--ram', dest='ram', default=None,
                               help='New number of RAM (GB).', metavar='RAM')
        self.parser.add_option('--instance-type', dest='instance_type', default=None,
                               help='New instance type.', metavar='INSTANCETYPE')

    def _check_args(self):
        if len(self.args) < 3:
            self.usageExitTooFewArguments()
        run_id = self.args[0]
        self.run_url = self.options.endpoint + util.RUN_RESOURCE_PATH + '/' + run_id
        self.node_name = self.args[1]

        self._validate_and_set_scale_options()

        try:
            self.instances_to_scale = map(int, self.args[2:])
        except ValueError:
            self.usageExit("Invalid ids, they must be integers")

    def _validate_and_set_scale_options(self):
        if not any([self.options.cpu, self.options.ram, self.options.instance_type]):
            self.usageExit("CPU/RAM or instance type should be defined. "
                           "Make sure cloud supports either of those.")
        if any([self.options.cpu, self.options.ram]) and self.options.instance_type:
            self.usageExit("Either CPU/RAM or instance type must be provided.")

        if self.options.cpu:
            self.rtp_scale_values['cpu'] = self.options.cpu
        if self.options.ram:
            self.rtp_scale_values['ram'] = self.options.ram
            return

        self.rtp_scale_values['instance.type'] = self.options.instance_type

    def doWork(self):

        client = HttpClient(self.options.username, self.options.password)
        client.verboseLevel = self.verboseLevel

        self._retrieve_and_set_run(client)

        self._check_allowed_to_scale(client)

        self._set_new_size(client)

        self._set_scale_state(client, BaseWrapper.SCALE_STATE_RESIZING)
        self._set_run_to_provisioning(client)

    def _set_new_size(self, client):
        self.log("Requesting to resize node instances: %s" % self.instances_to_scale)

        cloudservice_name = self._get_cloudservice_name()

        node_url = self.run_url + "/" + self.node_name
        for _id in self.instances_to_scale:
            url = node_url + NodeDecorator.NODE_MULTIPLICITY_SEPARATOR + \
                str(_id) + NodeDecorator.NODE_PROPERTY_SEPARATOR + cloudservice_name + '.'
            for scale_key, value in self.rtp_scale_values.items():
                client.put(url + scale_key, value)

    def _get_cloudservice_name(self):
        run_params = DomExtractor.extract_run_parameters_from_run(self.run_dom)
        return run_params[self.node_name + NodeDecorator.NODE_PROPERTY_SEPARATOR +
                          NodeDecorator.CLOUDSERVICE_KEY]

    def _set_scale_state(self, client, state):
        node_url = self.run_url + "/" + self.node_name
        for _id in self.instances_to_scale:
            url = node_url + NodeDecorator.NODE_MULTIPLICITY_SEPARATOR + \
                str(_id) + NodeDecorator.NODE_PROPERTY_SEPARATOR + 'scale.state'
            client.put(url, state)

    def _set_run_to_provisioning(self, client):
        client.put(self.run_url + '/ss:state', 'Provisioning')

    def _check_allowed_to_scale(self, client):
        err_msg = "ERROR: Run should be mutable and in Ready state."

        ss_state = self._get_ss_state(client)
        if 'Ready' != ss_state:
            self.usageExit(err_msg + " Run is in %s state." % ss_state)

        if not self._is_run_mutable():
            self.usageExit(err_msg + " Run is not mutable.")

    def _get_ss_state(self, client):
        ss_state_url = self.run_url + "/" + NodeDecorator.globalNamespacePrefix + 'state'
        _, ss_state = client.get(ss_state_url)
        return ss_state

    def _retrieve_and_set_run(self, client):
        _, run_xml = client.get(self.run_url, 'application/xml')
        self.run_dom = etree.fromstring(run_xml)

    def _is_run_mutable(self):
        mutable = DomExtractor.extract_mutable_from_run(self.run_dom)
        return util.str2bool(mutable)


if __name__ == "__main__":
    try:
        MainProgram()
    except KeyboardInterrupt:
        print('\n\nExecution interrupted by the user... goodbye!')
        sys.exit(-1)
