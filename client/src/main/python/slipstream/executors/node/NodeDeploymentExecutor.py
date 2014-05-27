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
import codecs
import tempfile

from slipstream.ConfigHolder import ConfigHolder
from slipstream.executors.MachineExecutor import MachineExecutor
import slipstream.util as util
from slipstream.exceptions.Exceptions import ExecutionException
from slipstream.util import appendSshPubkeyToAuthorizedKeys


def getExecutor(wrapper, configHolder):
    return NodeDeploymentExecutor(wrapper, configHolder)


class NodeDeploymentExecutor(MachineExecutor):
    def __init__(self, wrapper, configHolder=ConfigHolder()):
        self.verboseLevel = 0
        super(NodeDeploymentExecutor, self).__init__(wrapper, configHolder)
        self.targets = {}

    def onInitializing(self):
        util.printAction('Initializing')

        self._addSshPubkeyIfNeeded()

        util.printStep('Getting deployment targets')

        self.targets = self.wrapper.getTargets()

        util.printDetail('Deployment targets:')
        for target, script in self.targets.items():
            util.printAndFlush('-' * 25)
            util.printDetail('Target: %s' % target)
            util.printDetail('Script:\n%s\n' % script[0])

    def onRunning(self):
        util.printAction('Running')
        self._executeTarget('execute')

    def onSendingFinalReport(self):
        util.printAction('Sending report')
        try:
            self._executeTarget('report')
        except ExecutionException as ex:
            util.printDetail("Failed executing 'report' with: \n%s" % str(ex),
                             verboseLevel=self.verboseLevel,
                             verboseThreshold=util.VERBOSE_LEVEL_NORMAL)
            raise
        finally:
            super(NodeDeploymentExecutor, self).onSendingFinalReport()

    def _executeTarget(self, target):
        util.printStep("Executing target '%s'" % target)
        if target in self.targets:
            self._run_target_script(self.targets[target][0])
        else:
            util.printAndFlush('Nothing to do\n')

    def _run_target_script(self, target_script):
        if not target_script:
            util.printAndFlush('Script is empty\n')
            return

        tmpfilesuffix = ''
        if util.isWindows():
            tmpfilesuffix = '.ps1'
        fn = tempfile.mktemp(suffix=tmpfilesuffix)
        if isinstance(target_script, unicode):
            with codecs.open(fn, 'w', 'utf8') as fh:
                fh.write(target_script)
        else:
            with open(fn, 'w') as fh:
                fh.write(target_script)
        os.chmod(fn, 0755)
        currentDir = os.getcwd()
        os.chdir(tempfile.gettempdir() + os.sep)

        timeout = int(self.wrapper.getUserInfo().get_general('Timeout'))

        try:
            self._executeRaiseOnError(fn, timeout=timeout)
        finally:
            os.chdir(currentDir)

    def _addSshPubkeyIfNeeded(self):
        if util.needToAddSshPubkey():
            self._addSshPubkey()

    def _addSshPubkey(self):
        util.printStep('Adding the public key')
        appendSshPubkeyToAuthorizedKeys(self._getUserSshPubkey())

    def _getUserSshPubkey(self):
        return self.wrapper.getUserSshPubkey()
