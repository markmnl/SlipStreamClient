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
import time
import traceback
import tarfile
import tempfile

from slipstream.ConfigHolder import ConfigHolder
from slipstream.exceptions.Exceptions import TimeoutException, \
    AbortException, TerminalStateException, ExecutionException
from slipstream import util
from slipstream.Client import Client
from slipstream.util import deprecated


class MachineExecutor(object):
    def __init__(self, wrapper, configHolder=ConfigHolder()):
        self.wrapper = wrapper
        self.timeout = 30 * 60  # 30 minutes
        self.ssLogDir = Client.REPORTSDIR
        self.verboseLevel = 0
        configHolder.assign(self)

        self.reportFilesAndDirsList = [self.ssLogDir]

    def execute(self):
        self._execute()

    def _execute(self, state=None):
        state = (state and state) or self.wrapper.getState()
        if not state:
            raise ExecutionException('Machine executor: No state to execute specified.')
        try:
            getattr(self, 'on' + state)()
        except AbortException as ex:
            util.printError('Abort flag raised: %s' % ex)
        except TerminalStateException:
            return
        except KeyboardInterrupt:
            raise
        except (SystemExit, Exception) as ex:
            util.printError('Error executing node, with detail: %s' % ex)
            traceback.print_exc()
            self.wrapper.fail(str(ex))

        self.wrapper.advance()
        state = self._waitForNextState(state)
        self._execute(state)

    def _fail(self, exception):
        self.wrapper.fail("Exception %s with detail %s" % (exception.__class__,
                                                           str(exception)))

    def _waitForNextState(self, state):
        timeSleep = 5
        timeMax = time.time() + float(self.timeout)
        util.printDetail('Waiting for next state transition, currently in %s' %
                         state, self.verboseLevel, util.VERBOSE_LEVEL_NORMAL)
        while time.time() <= timeMax:
            newState = self.wrapper.getState()
            if state != newState:
                return newState
            else:
                time.sleep(timeSleep)
        raise TimeoutException('Timeout reached waiting for next state, current state: %s' % state)

    def onInactive(self):
        pass

    def onInitializing(self):
        util.printAction('Initializing')

    def onRunning(self):
        util.printAction('Running')

    def onSendingFinalReport(self):
        util.printAction('Sending report')
        reportFileName = '%s_report_%s.tgz' % (
            self._nodename(), util.toTimeInIso8601NoColon(time.time()))
        reportFileName = os.path.join(tempfile.gettempdir(), reportFileName)
        try:
            archive = tarfile.open(reportFileName, 'w:gz')
            for element in self.reportFilesAndDirsList:
                archive.add(element)
        except Exception as e:
            raise RuntimeError("Failed to bundle reports:\n%s" % e)
        archive.close()

        self.wrapper.clientSlipStream.sendReport(reportFileName)

    def _nodename(self):
        return self.wrapper.nodename()

    def onFinalizing(self):
        util.printAction('Finalizing')

    def onTerminal(self):
        if self.wrapper.isAbort():
            util.printError("Failed")
        else:
            util.printAction('Done!')

    def onDetached(self):
        util.printAction('Detached')
        raise TerminalStateException('Detached')

    def _executeRaiseOnError(self, cmd):
        res = util.execute(cmd.split(' '))
        if res:
            raise ExecutionException('Failed executing: %s' % cmd)

    def _killItself(self, is_build_image=False):
        self.wrapper.stopOrchestrator(is_build_image)

    def _killItselfServerSide(self):
        self.wrapper.terminateRunServerSide()

    def _getMyCloudInstanceId(self):
        return self.wrapper.getMachineCloudInstanceId()

    @deprecated
    def isTerminateRunServerSide(self):
        return self.wrapper.isTerminateRunServerSide()
