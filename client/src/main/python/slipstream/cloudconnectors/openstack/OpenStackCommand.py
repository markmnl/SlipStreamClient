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

from slipstream.command.CloudClientCommand import CloudClientCommand
from slipstream.cloudconnectors.openstack.OpenStackClientCloud import OpenStackClientCloud


class OpenStackCommand(CloudClientCommand):

    REGION_KEY = 'region'
    PROJECT_KEY = 'project'
    ENDPOINT_KEY = 'endpoint'
    SERVICE_TYPE_KEY = 'service-type'
    SERVICE_NAME_KEY = 'service-name'

    def __init__(self, timeout=None):
        super(OpenStackCommand, self).__init__(timeout)

    def get_connector_class(self):
        return OpenStackClientCloud

    def set_cloud_specific_options(self, parser):
        parser.add_option('--' + self.ENDPOINT_KEY, dest=self.ENDPOINT_KEY, help='Identity service (Keystone)',
                          default='', metavar='ENDPOINT')

        parser.add_option('--' + self.REGION_KEY, dest=self.REGION_KEY, help='Region (default: regionOne)',
                          default='regionOne', metavar='REGION')

        parser.add_option('--' + self.PROJECT_KEY, dest=self.PROJECT_KEY, help='Project (Tenant)',
                          default='', metavar='PROJECT')

        parser.add_option('--' + self.SERVICE_TYPE_KEY, dest=self.SERVICE_TYPE_KEY,
                          help='Type-name of the service which provides the instances functionality (default: compute)',
                          default='compute', metavar='TYPE')

        parser.add_option('--' + self.SERVICE_NAME_KEY, dest=self.SERVICE_NAME_KEY,
                          help='Name of the service which provides the instances functionality (default: nova)',
                          default='nova', metavar='NAME')

    def get_cloud_specific_user_cloud_params(self):
        return {'tenant.name': self.get_option(self.PROJECT_KEY),
                'service.region': self.get_option(self.REGION_KEY),
                self.ENDPOINT_KEY: self.get_option(self.ENDPOINT_KEY),
                'service.type': self.get_option(self.SERVICE_TYPE_KEY),
                'service.name': self.get_option(self.SERVICE_NAME_KEY)}

    def get_cloud_specific_mandatory_options(self):
        return [self.REGION_KEY,
                self.PROJECT_KEY,
                self.ENDPOINT_KEY,
                self.SERVICE_TYPE_KEY,
                self.SERVICE_NAME_KEY]


