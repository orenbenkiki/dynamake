"""
Utilities for dynamic make.
"""

# pylint: disable=too-many-lines

from .application import config
from .application import Func
from .application import override  # pylint: disable=unused-import
from .application import Param
from .application import Prog
from .application import reset_application
from .config import Config
from .parameters import env  # pylint: disable=unused-import
from .patterns import capture2re
from .patterns import emphasized  # pylint: disable=unused-import
from .patterns import exists  # pylint: disable=unused-import
from .patterns import is_precious
from .patterns import optional  # pylint: disable=unused-import
from .patterns import phony  # pylint: disable=unused-import
from .patterns import precious  # pylint: disable=unused-import
from .patterns import Strings
from .stat import Stat
from argparse import ArgumentParser
from argparse import Namespace
from datetime import datetime
from hashlib import md5
from inspect import iscoroutinefunction
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Coroutine
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
from typing.re import Pattern  # type: ignore # pylint: disable=import-error
from urllib.parse import quote_plus
from uuid import UUID

import asyncio
import dynamake.patterns as dp
import logging
import os
import re
import sys
import yaml


class StepException(Exception):
    """
    Indicates a step has aborted and its output must not be used by other steps.
    """


class Make:
    """
    Global build configuration and state.
    """
    #: The default configuration file path.
    FILE: str

    #: The log level for logging the reasons for sub-process invocations.
    WHY = (Prog.TRACE + logging.INFO) // 2

    #: Whether to stop the script if any action fails (by default, ``True``).
    #:
    #: If this is ``False``, then the build will continue to execute unrelated actions.
    #: In all cases, actions that have already been submitted will be allowed to end normally.
    failure_aborts_build: bool

    #: Whether to delete old output files before executing an action (by default, ``True``).
    #:
    #: It is possible to override this on a per-action basis.
    delete_stale_outputs: bool

    #: Whether to wait before assuming an output file does not exist (by default, ``False``).
    #:
    #: This may be required if the output file(s) are on an NFS-mounted partition, and the NFS
    #: client is caching `stat` results (the default behavior, since otherwise performance would be
    #: horrible).
    #:
    #: Setting the NFS mount flags to include `lookupcache=positive` will force the client to avoid
    #: caching a "file not found" `stat` result, thereby ensuring that if we detect a missing output
    #: file, it really is missing. This has minimal impact on performance (since, most of the time,
    #: `stat` calls are for existing files).
    #:
    #: If you can't tweak the NFS mount flags, set `wait_nfs_outputs`; this will cause us to wait up
    #: to 60 seconds (the default NFS `stat` cache time) before pronouncing that the output file
    #: really is missing.
    wait_nfs_outputs: bool

    #: The amount of time to wait for slow NFS outputs (by default, 60 seconds, which is the default
    #: timeout of the NFS client cache).
    nfs_outputs_timeout: int

    #: Whether to touch output files on a successful action to ensure they are newer than
    #: the input file(s) (by default, ``False``).
    #:
    #: In these modern times, this is mostly unneeded as aw use the nano-second modification time,
    #: which pretty much guarantees that output files will be newer than input files. In the "bad
    #: old days", files created within a second of each other had the same modification time.
    #:
    #: This might still be needed if an output is a directory and
    #: :py:attr:`dynamake.make.Make.delete_stale_outputs` is ``False``, since otherwise the
    #: ``mtime`` of the directory will not necessarily be updated to reflect the fact the action was
    #: executed. In general it is ill advised to depend on the ``mtime`` of directories; it is
    #: better to specify a glob matching the expected files inside them.
    touch_success_outputs: bool

    #: Whether to delete output files on a failing action (by default, ``True``).
    #:
    #: It is possible to override this on a per-action basis.
    delete_failed_outputs: bool

    #: Whether to (try to) delete empty directories when deleting the last file in them (by default,
    #: ``False``).
    delete_empty_directories: bool

    #: Whether to log (level INFO) skipped actions (by default, ``False``).
    log_skipped_actions: bool

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Make.FILE = os.getenv('DYNAMAKE_CONFIG_FILE', 'Config.yaml')
        Make.failure_aborts_build = True
        Make.delete_stale_outputs = True
        Make.wait_nfs_outputs = False
        Make.nfs_outputs_timeout = 60
        Make.touch_success_outputs = True
        Make.delete_failed_outputs = True
        Make.delete_empty_directories = False
        Make.log_skipped_actions = False


class Step:
    """
    A build step.
    """

    #: The current known steps.
    by_name: Dict[str, 'Step']

    #: The step for building any output capture pattern.
    by_regexp: List[Tuple[Pattern, 'Step']]

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Step.by_name = {}
        Step.by_regexp = []

    def __init__(self, func: Func, output: Strings) -> None:
        """
        Register a build step function.
        """
        #: The configured function that implements the step.
        self.func = func

        #: The outputs generated by the step.
        self.output: List[str] = []

        for capture in dp.each_string(output):
            self.output.append(capture)
            Step.by_regexp.append((capture2re(capture), self))

        Step.by_name[self.name()] = self

    @staticmethod
    def collect(wrapped: Callable, output: Strings) -> 'Step':
        """
        Collect a build step function.
        """
        func = Func.collect(wrapped, is_top=False)
        if not iscoroutinefunction(func.wrapped):
            raise RuntimeError('Step function: %s.%s is not a coroutine'
                               % (func.wrapped.__module__, func.wrapped.__qualname__))
        return Step(func, output)

    def name(self) -> str:
        """
        The name of the function implementing the step.
        """
        return self.func.name


class Invocation:  # pylint: disable=too-many-instance-attributes
    """
    An active invocation of a build step.
    """

    #: The active invocations.
    active: Dict[UUID, 'Invocation']

    #: The current invocation.
    current: 'Invocation'

    #: The paths for phony targets.
    phony: Set[str]

    #: The targets that were built or otherwise proved to be up-to-date so far.
    up_to_date: Set[str]

    #: The files that failed to build and must not be used by other steps.
    poisoned: Set[str]

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Invocation.active = {}
        Invocation.current = None  # type: ignore
        Invocation.current = Invocation(None)
        Invocation.up_to_date = set()
        Invocation.phony = set()
        Invocation.poisoned = set()

    def __init__(self, step: Optional[Step], **kwargs: Any) -> None:  # pylint: disable=redefined-outer-name
        """
        Track the invocation of an async step.
        """
        #: The parent invocation, if any.
        self.parent: Optional[Invocation] = Invocation.current

        #: The step being invoked.
        self.step = step

        assert (self.parent is None) == (step is None)

        #: How many sub-invocations were created so far.
        self.sub_count = 0

        if self.parent is None:
            #: A short unique stack to identify invocations in the log.
            self.stack: str = '.'
        else:
            self.parent.sub_count += 1
            if self.parent.stack == '.':
                self.stack = '.%s' % self.parent.sub_count
            else:
                self.stack = '%s.%s' % (self.parent.stack, self.parent.sub_count)

        #: The arguments to the invocation.
        self.kwargs = kwargs
        if step is not None:
            self.kwargs = step.func.invocation_kwargs(**kwargs)

        #: The arguments in HTTP query format (for logging, file names, etc.).
        self.args_string = _args_string(kwargs)

        digester = md5()
        digester.update(yaml.dump(dict(step=self.name(), kwargs=kwargs)).encode('utf-8'))

        #: A stable (across program executions) unique identifier of the invocation.
        self.uuid = UUID(bytes=digester.digest())

        #: A condition variable to wait on for this invocation.
        self.condition: Optional[asyncio.Condition] = None

        #: The name of the phony inputs, if any.
        self.phony_inputs: List[str] = []

        #: The required input targets (phony or files) the invocations depends on.
        self.all_inputs: List[str] = []

        #: The newest input file, if any.
        self.newest_input_path: Optional[str] = None

        #: The modification time of the newest input file, if any.
        self.newest_input_mtime_ns = 0

        #: The queued async actions for creating the input files.
        self.actions: List[Coroutine] = []

        #: The output files that existed prior to the invocation.
        self.initial_outputs: List[str] = []

        #: The phony outputs, if any.
        self.phony_outputs: List[str] = []

        #: A pattern for some missing output file(s), if any.
        self.missing_output: Optional[str] = None

        #: The oldest existing output file path, or None if some output files are missing.
        self.oldest_output_path: Optional[str] = None

        #: The modification time of the oldest existing output path.
        self.oldest_output_mtime_ns = 0

        #: Whether to delete all existing output files before executing the next sub-process.
        self.should_delete_stale_outputs = Make.delete_stale_outputs

        #: The reason to abort this invocation, if any.
        self.exception: Optional[StepException] = None

    def log_and_abort(self, reason: str) -> None:
        """
        Abort the invocation for some reason.
        """
        Prog.logger.error(reason)
        return self.abort(reason)

    def abort(self, reason: str) -> None:
        """
        Abort the invocation for some reason.
        """
        self.exception = StepException(reason)
        if Make.failure_aborts_build:
            raise self.exception

    def name(self) -> str:
        """
        The name of the function implementing the step.
        """
        if self.step is None:
            return "make"
        return self.step.name()

    def require(self, path: str) -> None:
        """
        Require a file to be up-to-date before executing any sub-processes or completing the current
        invocation.
        """
        assert id(Invocation.current) == id(self)

        Prog.logger.debug('[%s] %s: build the required: %s',
                          self.stack, self.name(), path)

        self.all_inputs.append(path)

        if path in Invocation.poisoned:
            self.abort('[%s] %s: the required: %s has failed to build'
                       % (self.stack, self.name(), path))
            return

        if path in Invocation.up_to_date:
            Prog.logger.debug('[%s] %s: the required: %s was built',
                              self.stack, self.name(), path)
            return

        step, kwargs = self.producer_of(path)  # pylint: disable=redefined-outer-name
        if kwargs is None:
            return

        if step is None:
            stat = Stat.try_stat(path)
            if stat is None:
                self.log_and_abort("[%s] %s: don't know how to make the required: %s"
                                   % (self.stack, self.name(), path))
                return
            Prog.logger.debug('[%s] %s: the required: %s is a source file',
                              self.stack, self.name(), path)
            Invocation.up_to_date.add(path)
            return

        invocation = Invocation(step, **kwargs)
        Prog.logger.debug('[%s] %s: the required: %s '
                          'will be produced by the spawned: [%s] %s',
                          self.stack, self.name(), path, invocation.stack, invocation.name())
        self.actions.append(invocation.run())
        Invocation.current = self

    def producer_of(self, path: str) -> Tuple[Optional[Step], Optional[Dict[str, Any]]]:
        """
        Find the unique step, if any, that produces the file.

        Also returns the keyword arguments needed to invoke the step function (deduced from the
        path).
        """
        kwargs: Dict[str, Any] = {}
        producer: Optional[Step] = None

        for (regexp, step) in Step.by_regexp:  # pylint: disable=redefined-outer-name
            match = re.fullmatch(regexp, path)
            if not match:
                continue

            if producer is not None:
                self.log_and_abort('the output: %s '
                                   'may be created by both the step: %s '
                                   'and the step: %s'
                                   % (path, producer.name(), step.name()))
                return None, None

            producer = step
            for name, value in match.groupdict().items():
                if name[0] != '_':
                    kwargs[name] = str(value or '')

        return producer, kwargs

    async def run(self) -> Optional[StepException]:
        """
        Actually run the invocation.
        """
        active = Invocation.active.get(self.uuid)
        if active is not None:
            return await self.wait_for(active)

        Invocation.current = self

        if self.kwargs:
            Prog.logger.log(Prog.TRACE, '[%s] %s: call with: %s',
                            self.stack, self.name(), self.args_string)
        else:
            Prog.logger.log(Prog.TRACE, '[%s] %s: call', self.stack, self.name())

        assert self.uuid not in Invocation.active
        Invocation.active[self.uuid] = self
        self.collect_initial_outputs()

        try:
            assert self.step is not None
            await self.step.func.wrapped(**self.kwargs)
            Invocation.current = self
            await self.sync()
            Invocation.current = self
            await self.collect_final_outputs()
            Invocation.current = self

        except StepException as exception:
            Invocation.current = self
            self.exception = exception

        if self.exception is None:
            Prog.logger.log(Prog.TRACE, '[%s] %s: done', self.stack, self.name())
        else:
            self.poison_all_outputs()
            Prog.logger.log(Prog.TRACE, '[%s] %s: fail', self.stack, self.name())

        del Invocation.active[self.uuid]
        if self.condition is not None:
            await self.condition.acquire()
            Invocation.current = self
            self.condition.notify_all()
            self.condition.release()

        if self.exception is not None and Make.failure_aborts_build:
            raise self.exception

        return self.exception

    async def wait_for(self, active: 'Invocation') -> Optional[StepException]:
        """
        Wait until the invocation is done.

        This is used by other invocations that use this invocation's output(s) as their input(s).
        """
        Prog.logger.debug('[%s] %s: paused by waiting for [%s] %s',
                          self.stack, self.name(),
                          active.stack, active.name())

        if active.condition is None:
            active.condition = asyncio.Condition()

        await active.condition.acquire()
        Invocation.current = self

        await active.condition.wait()
        Invocation.current = self

        active.condition.release()

        Prog.logger.debug('[%s] %s: resumed by completion of [%s] %s',
                          self.stack, self.name(),
                          active.stack, active.name())

        return active.exception

    def collect_initial_outputs(self) -> None:
        """
        Check which of the outputs already exist and what their modification times are, to be able
        to decide whether sub-processes need to be run to create or update them.
        """
        assert self.step is not None
        for pattern in sorted(self.step.output):
            if dp.is_phony(pattern):
                path = dp.capture2glob(pattern).format(**self.kwargs)
                self.phony_outputs.append(path)
                Invocation.phony.add(path)
                continue
            try:
                for path in sorted(dp.glob_strings(self.kwargs, pattern)):
                    self.initial_outputs.append(path)
                    if path == pattern:
                        Prog.logger.debug('[%s] %s: exists output: %s',
                                          self.stack, self.name(), path)
                    else:
                        Prog.logger.debug('[%s] %s: exists output: %s -> %s',
                                          self.stack, self.name(), pattern, path)
            except dp.NonOptionalException:
                Prog.logger.debug('[%s] %s: missing output(s): %s',
                                  self.stack, self.name(), pattern)
                self.missing_output = pattern

        if self.phony_outputs or self.missing_output is not None:
            return

        for output_path in sorted(self.initial_outputs):
            output_mtime_ns = Stat.stat(output_path).st_mtime_ns
            if self.oldest_output_path is None or self.oldest_output_mtime_ns > output_mtime_ns:
                self.oldest_output_path = output_path
                self.oldest_output_mtime_ns = output_mtime_ns

        if Prog.logger.isEnabledFor(logging.DEBUG) and self.oldest_output_path is not None:
            Prog.logger.debug('[%s] %s: oldest output: %s time: %s',
                              self.stack, self.name(), self.oldest_output_path,
                              _datetime_from_nanoseconds(self.oldest_output_mtime_ns))

    async def collect_final_outputs(self) -> None:  # pylint: disable=too-many-branches
        """
        Ensure that all the (required) outputs were actually created and are newer than all input
        files specified so far.

        If successful, this marks all the outputs as up-to-date so that steps that depend on them
        will immediately proceed.
        """

        missing_outputs = False
        assert self.step is not None
        for path in self.phony_outputs:
            Invocation.up_to_date.add(path)

        did_sleep = False
        waited = 0.0
        next_wait = 0.1

        for pattern in sorted(self.step.output):  # pylint: disable=too-many-nested-blocks
            if dp.is_phony(pattern):
                continue

            did_wait = False
            while True:
                try:
                    for path in sorted(dp.glob_strings(self.kwargs, pattern)):
                        if did_wait:
                            Prog.logger.warn('[%s] %s: waited: %s seconds for the output: %s',
                                             self.stack, self.name(), round(waited, 2), path)
                        if Make.touch_success_outputs:
                            if not did_sleep:
                                await asyncio.sleep(0.01)
                                Invocation.current = self
                                did_sleep = True
                            Prog.logger.debug('[%s] %s: touch output: %s',
                                              self.stack, self.name(), path)
                            Stat.touch(path)
                        Invocation.up_to_date.add(path)
                        mtime_ns = Stat.stat(path).st_mtime_ns
                        if Prog.logger.isEnabledFor(logging.DEBUG):
                            if path == pattern:
                                Prog.logger.debug('[%s] %s: has output: %s time: %s',
                                                  self.stack, self.name(), path,
                                                  _datetime_from_nanoseconds(mtime_ns))
                            else:
                                Prog.logger.debug('[%s] %s: has output: %s -> %s time: %s',
                                                  self.stack, self.name(), pattern, path,
                                                  _datetime_from_nanoseconds(mtime_ns))
                    break

                except dp.NonOptionalException:
                    if Make.wait_nfs_outputs and waited < Make.nfs_outputs_timeout:
                        await asyncio.sleep(next_wait)
                        Invocation.current = self
                        did_sleep = True
                        waited += next_wait
                        next_wait *= 2
                        did_wait = True
                        continue

                    Prog.logger.error('[%s] %s: missing output(s): %s',
                                      self.stack, self.name(), pattern)
                    missing_outputs = True
                    break

        if missing_outputs:
            self.abort('[%s] %s: missing output(s)' % (self.stack, self.name()))

    def delete_stale_outputs(self) -> None:
        """
        Delete stale outputs before running a sub-process.

        This is only done before running the first sub-process of a step.
        """
        for path in sorted(self.initial_outputs):
            if self.should_delete_stale_outputs and not is_precious(path):
                Prog.logger.debug('[%s] %s: remove stale output: %s',
                                  self.stack, self.name(), path)
                self.remove_output(path)
            else:
                Stat.forget(path)

        self.should_delete_stale_outputs = False

    def remove_output(self, path: str) -> None:
        """
        Remove an output file, and possibly the directories that became empty as a result.
        """
        Stat.remove(path)
        while Make.delete_empty_directories:
            path = os.path.dirname(path)
            try:
                Stat.rmdir(path)
                Prog.logger.debug('[%s] %s: remove empty directory: %s',
                                  self.stack, self.name(), path)
            except OSError:
                return

    def poison_all_outputs(self) -> None:
        """
        Mark all outputs as poisoned for a failed step.

        Typically also deletes them.
        """
        assert self.step is not None

        for path in self.phony_outputs:
            Invocation.poisoned.add(path)

        for pattern in sorted(self.step.output):
            if dp.is_phony(pattern):
                continue
            for path in sorted(dp.glob_strings(self.kwargs, dp.optional(pattern))):
                Invocation.poisoned.add(path)
                if Make.delete_failed_outputs and not is_precious(path):
                    Prog.logger.debug('[%s] %s: remove failed output: %s',
                                      self.stack, self.name(), path)
                    self.remove_output(path)

    def should_run_sub_process(self) -> bool:
        """
        Test whether all (required) outputs already exist, and are newer than all input files
        specified so far.
        """
        # Either no output files (pure action) or missing output files.
        if self.phony_outputs:
            Prog.logger.log(Make.WHY, '[%s] %s: must run processes to ensure phony output: %s',
                            self.stack, self.name(), self.phony_outputs[0])
            return True

        if self.phony_inputs:
            Prog.logger.log(Make.WHY, '[%s] %s: must run processes '
                            'because rebuilt the required phony: %s',
                            self.stack, self.name(), self.phony_inputs[0])
            return True

        if self.missing_output is not None:
            Prog.logger.log(Make.WHY,
                            '[%s] %s: must run processes to create missing output(s): %s',
                            self.stack, self.name(), self.missing_output)
            return True

        # All output files exist.

        # No input files (pure computation).
        if self.newest_input_path is None:
            Prog.logger.debug('[%s] %s: can skip processes '
                              'because all outputs exist and there are no inputs',
                              self.stack, self.name())
            return False

        # There are input files.

        #: Some output file is not newer than some input file.
        if self.oldest_output_mtime_ns <= self.newest_input_mtime_ns:
            Prog.logger.log(Make.WHY, '[%s] %s: must run processes '
                            'because the output: %s '
                            'is not newer than the input: %s',
                            self.stack, self.name(), self.oldest_output_path,
                            self.newest_input_path)
            return True

        # All output files are newer than all input files:
        Prog.logger.debug('[%s] %s: skip processes '
                          'because all outputs exist and are newer than all inputs',
                          self.stack, self.name())
        return False

    async def run_sub_process(self, runner: Callable, *command: Strings) -> None:
        """
        Spawn a sub-process to actually create some files.
        """
        assert id(Invocation.current) == id(self)

        await self.sync()
        Invocation.current = self

        run_words = []
        log_words = []
        for word in dp.each_string(*command):
            run_words.append(word)
            log_words.append(''.join(dp.color(word)))
        log_command = ' '.join(log_words)

        if self.exception is not None:
            Prog.logger.debug("[%s] %s: can't run: %s", self.stack, self.name(), log_command)
            raise self.exception

        if not self.should_run_sub_process():
            if Make.log_skipped_actions:
                Prog.logger.info('[%s] %s: skip: %s', self.stack, self.name(), log_command)
            else:
                Prog.logger.debug('[%s] %s: skip: %s', self.stack, self.name(), log_command)
            return

        self.delete_stale_outputs()

        Prog.logger.info('[%s] %s: run: %s', self.stack, self.name(), log_command)
        process = await runner(*run_words)
        Invocation.current = self

        exit_status = await process.wait()
        Invocation.current = self

        if exit_status != 0:
            self.log_and_abort('[%s] %s: failure: %s'
                               % (self.stack, self.name(), ' '.join(run_words)))
            return

        Prog.logger.log(Prog.TRACE, '[%s] %s: success: %s', self.stack, self.name(), log_command)

    async def sync(self) -> None:
        """
        Wait until all the async actions queued so far are complete.

        This is implicitly called before running a sub-process.
        """
        assert id(Invocation.current) == id(self)

        if self.actions:
            Prog.logger.debug('[%s] %s: sync', self.stack, self.name())
            results: List[Optional[StepException]] = await asyncio.gather(*self.actions)
            Invocation.current = self
            if self.exception is None:
                for exception in results:
                    if exception is not None:
                        self.exception = exception
                        break
            self.actions = []

        Prog.logger.debug('[%s] %s: synced', self.stack, self.name())

        failed_inputs = False
        self.phony_inputs = []
        for path in sorted(self.all_inputs):
            if path in Invocation.poisoned or path not in Invocation.up_to_date:
                Prog.logger.debug('[%s] %s: the required: %s has failed to build',
                                  self.stack, self.name(), path)
                Invocation.poisoned.add(path)
                failed_inputs = True
                continue

            Prog.logger.debug('[%s] %s: has the required: %s', self.stack, self.name(), path)

            if path in Invocation.phony:
                self.phony_inputs.append(path)
                continue

            result = Stat.stat(path)
            if self.newest_input_path is None or self.newest_input_mtime_ns < result.st_mtime_ns:
                self.newest_input_path = path
                self.newest_input_mtime_ns = result.st_mtime_ns

        if failed_inputs:
            assert self.exception is not None
            self.abort('[%s] %s: failed to build required target(s)'
                       % (self.stack, self.name()))
            return

        if self.exception is None \
                and Prog.logger.isEnabledFor(logging.DEBUG) \
                and self.oldest_output_path is not None:
            Prog.logger.debug('[%s] %s: newest input: %s time: %s',
                              self.stack, self.name(), self.newest_input_path,
                              _datetime_from_nanoseconds(self.newest_input_mtime_ns))


_OLD_DATES: Dict[int, float] = {}


def _datetime_from_nanoseconds(nanoseconds: int) -> str:
    if not Prog.is_test:
        seconds = datetime.fromtimestamp(nanoseconds // 1000000000).strftime('%Y-%m-%d %H:%M:%S')
        fractions = '%09d' % (nanoseconds % 1000000000)
        return '%s.%s' % (seconds, fractions)

    global _OLD_DATES
    stamp = _OLD_DATES.get(nanoseconds, None)
    if stamp is not None:
        return str(stamp)

    higher_time = None
    higher_stamp = None
    lower_time = None
    lower_stamp = None
    for time, stamp in _OLD_DATES.items():
        if time < nanoseconds:
            if lower_time is None or lower_time < time:
                lower_time = time
                lower_stamp = stamp
        if time > nanoseconds:
            if higher_time is None or higher_time < time:
                higher_time = time
                higher_stamp = stamp

    if lower_stamp is None:
        if higher_stamp is None:
            stamp = 1
        else:
            stamp = higher_stamp - 1
    else:
        if higher_stamp is None:
            stamp = lower_stamp + 1
        else:
            stamp = (lower_stamp + higher_stamp) / 2

    _OLD_DATES[nanoseconds] = stamp
    return str(stamp)


def _args_string(kwargs: Dict[str, Any]) -> str:
    return ','.join(['%s=%s' % (quote_plus(name), quote_plus(str(value)))
                     for name, value in sorted(kwargs.items())])


def reset_test_dates() -> None:
    """
    Reset the cached dates used for deterministic test logs.
    """
    global _OLD_DATES
    _OLD_DATES = {}


def step(output: Strings) -> Callable[[Callable], Callable]:
    """
    Decorate a build step functions.

    If ``top`` is ``True``, this is a top-level step that can be directly invoked from the main
    function.
    """
    def _wrap(wrapped: Callable) -> Callable:
        return Step.collect(wrapped, output).func.wrapper
    return _wrap


def require(path: str) -> None:
    """
    Require an input file for the step.

    This queues an async build of the input file using the appropriate step,
    and immediately returns.
    """
    Invocation.current.require(path)


async def sync() -> None:
    """
    Wait until all the input files specified so far are built.

    This is invoked automatically before running sub-processes.
    """
    current = Invocation.current
    await current.sync()
    Invocation.current = current


async def shell(*command: Strings) -> None:
    """
    Execute a shell command.

    The caller is responsible for all quotations.

    This first waits until all input files requested so far are ready.
    """
    current = Invocation.current
    await current.run_sub_process(asyncio.create_subprocess_shell, *command)
    Invocation.current = current


async def spawn(*command: Strings) -> None:
    """
    Execute an external program with arguments.

    This first waits until all input files requested so far are ready.
    """
    current = Invocation.current
    await current.run_sub_process(asyncio.create_subprocess_exec, *command)
    Invocation.current = current


def _define_parameters() -> None:
    Param(name='failure_aborts_build', short='fab', metavar='BOOL', default=True,
          parser=dp.str2bool, group='global options',
          description='Whether to stop the script if any action fails')

    Param(name='delete_stale_outputs', short='dso', metavar='BOOL', default=True,
          parser=dp.str2bool, group='global options',
          description='Whether to delete old output files before executing an action')

    Param(name='wait_nfs_outputs', short='wno', metavar='BOOL', default=False,
          parser=dp.str2bool, group='global options',
          description='Whether to wait before assuming an output file does not exist')

    Param(name='nfs_outputs_timeout', short='not', metavar='SECONDS', default=60,
          parser=dp.str2int(min=1), group='global options',
          description='The amount of time to wait for slow NFS outputs')

    Param(name='touch_success_outputs', short='tso', metavar='BOOL', default=False,
          parser=dp.str2bool, group='global options',
          description='Whether to touch output files on a successful action '
          'to ensure they are newer than the input file(s)')

    Param(name='delete_failed_outputs', short='dfo', metavar='BOOL', default=True,
          parser=dp.str2bool, group='global options',
          description='Whether to delete output files on a failing action')

    Param(name='delete_empty_directories', short='ded', metavar='BOOL', default=False,
          parser=dp.str2bool, group='global options',
          description='Whether to delete empty directories when deleting the last file in them')

    Param(name='log_skipped_actions', short='lsa', metavar='BOOL', default=False,
          parser=dp.str2bool, group='global options',
          description='Whether to log (level INFO) skipped actions')

    @config(top=True)
    def _use_parameters(  # pylint: disable=unused-argument
        *,
        failure_aborts_build: bool = env(),
        delete_stale_outputs: bool = env(),
        wait_nfs_outputs: bool = env(),
        nfs_outputs_timeout: int = env(),
        touch_success_outputs: bool = env(),
        delete_failed_outputs: bool = env(),
        delete_empty_directories: bool = env(),
        log_skipped_actions: bool = env(),
    ) -> None:
        pass


def _collect_parameters() -> None:
    Make.failure_aborts_build = Prog.current.get('failure_aborts_build', make)
    Make.delete_stale_outputs = Prog.current.get('delete_stale_outputs', make)
    Make.wait_nfs_outputs = Prog.current.get('wait_nfs_outputs', make)
    Make.nfs_outputs_timeout = Prog.current.get('nfs_outputs_timeout', make)
    Make.touch_success_outputs = Prog.current.get('touch_success_outputs', make)
    Make.delete_failed_outputs = Prog.current.get('delete_failed_outputs', make)
    Make.delete_empty_directories = Prog.current.get('delete_empty_directories', make)
    Make.log_skipped_actions = Prog.current.get('log_skipped_actions', make)


def make(parser: ArgumentParser, *,
         default_targets: Strings = 'all', logger_name: Optional[str] = None,
         adapter: Optional[Callable[[Namespace], None]] = None) -> None:
    """
    A generic ``main`` function for ``DynaMake``.

    The optional ``adapter`` may perform additional adaptation of the execution environment based on
    the parsed command-line arguments before the actual function(s) are invoked.
    """
    Prog.load_modules()
    Prog.logger = logging.getLogger(logger_name or sys.argv[0])
    logging.getLogger('asyncio').setLevel('WARN')
    default_targets = list(dp.each_string(default_targets))
    parser.add_argument('TARGET', nargs='*',
                        help='The file or target to make (default: %s)' % ' '.join(default_targets))
    Prog.current.add_global_parameters(parser)
    Prog.current.add_sorted_parameters(parser, extra_help=_extra_parameter_help)
    args = parser.parse_args()
    Prog.parse_args(args)
    if adapter is not None:
        adapter(args)
    _collect_parameters()
    targets = [path for path in args.TARGET if path is not None] \
        or list(dp.each_string(default_targets))

    Prog.logger.log(Prog.TRACE, '[.] make: call with: %s', ' '.join(targets))
    # TODO: Switch to `asyncio.run(sync())` in Python 3.7.
    for target in targets:
        require(target)
    try:
        result: Optional[StepException] = run(sync())
    except StepException as exception:
        result = exception

    if result is None:
        Prog.logger.log(Prog.TRACE, '[.] make: done')
        if not Prog.is_test:
            sys.exit(0)
    else:
        Prog.logger.log(Prog.TRACE, '[.] make: fail')
        if not Prog.is_test:
            sys.exit(1)
        raise result


def run(invocation: Awaitable) -> Any:
    """
    A Python3.6 way to implement the `asyncio.run` function from Python 3.7.
    """
    return asyncio.get_event_loop().run_until_complete(invocation)


def _extra_parameter_help(parameter_name: str) -> str:
    globs: List[str] = []
    for func_name in Func.names_by_parameter[parameter_name]:
        for pattern in Step.by_name[func_name].output:
            globs.append(dp.capture2glob(pattern))
    return '. Used when making: %s' % ' '.join(sorted(globs))


logging.addLevelName(Make.WHY, 'WHY')


def reset_make() -> None:
    """
    Reset all the current state, for tests.
    """
    reset_application()
    Make.reset()
    Config.reset()
    Invocation.reset()
    Step.reset()
    _define_parameters()


reset_make()
