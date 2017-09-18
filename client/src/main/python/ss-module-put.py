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

from slipstream.command.ModuleCommand import ModuleCommand


class MainProgram(ModuleCommand):
    """A command-line program to show/list module definition(s)."""

    def __init__(self):
        super(MainProgram, self).__init__()
        self._module_str = ''

    def parse(self):
        usage = '''usage: %prog [options] [<module-xml>]

<module-xml>    XML rendering of the module to update (e.g. as produced by
                ss-module-get).
                For example: ./ss-module-put "`cat module.xml`"'''

        self.parser.usage = usage

        self.add_authentication_options()
        self.add_endpoint_option()

        self.parser.add_option('-i', '--ifile', dest='ifile', metavar='FILE',
                               help='Optional input file. '
                                    'Replaces <module-xml> argument')

        self.options, self.args = self.parser.parse_args()

        self._check_args()

    def _check_args(self):
        if self.options.ifile:
            self._module_str = self.read_input_file(self.options.ifile)
        else:
            if len(self.args) < 1:
                self.parser.error('Missing module-xml')
            if len(self.args) > 1:
                self.usageExitTooManyArguments()
            self._module_str = self.args[0]

    def do_work(self):
        self.module_create(self._module_str)


if __name__ == "__main__":
    try:
        MainProgram()
    except KeyboardInterrupt:
        print('\n\nExecution interrupted by the user... goodbye!')
        sys.exit(-1)
