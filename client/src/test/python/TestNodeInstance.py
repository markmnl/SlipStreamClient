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

import unittest

from slipstream.NodeInstance import NodeInstance
from slipstream.NodeDecorator import NodeDecorator


class TestNodeInstance(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_get_networks(self):

        ni = NodeInstance()
        assert [] == ni.get_networks()

        ni = NodeInstance({'test.networks': ''})
        assert [] == ni.get_networks()

        ni = NodeInstance({NodeDecorator.CLOUDSERVICE_KEY: 'cloud-x',
                           'cloud-x.networks': 'foo'})
        assert ['foo'] == ni.get_networks()

        ni = NodeInstance({NodeDecorator.CLOUDSERVICE_KEY: 'cloud-x',
                           'cloud-x.networks': 'foo, bar'})
        assert ['foo', 'bar'] == ni.get_networks()


if __name__ == '__main__':
    unittest.main()
