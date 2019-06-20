"""
Utilities for dynamic make.
"""

# pylint: disable=too-many-lines

from .application import *  # pylint: disable=redefined-builtin,wildcard-import,unused-wildcard-import
from .config import Config
from .patterns import *  # pylint: disable=redefined-builtin,wildcard-import,unused-wildcard-import
from .stat import Stat
from argparse import ArgumentParser
from argparse import Namespace
from datetime import datetime
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

import asyncio
import dynamake.patterns as dp
import logging
import os
import re
import shlex
import sys
import yaml


def _dict_to_str(values: Dict[str, Any]) -> str:
    return ','.join(['%s=%s' % (quote_plus(name), quote_plus(str(value)))
                     for name, value in sorted(values.items())])


class Resources:
    """
    Restrict parallelism using some resources.
    """

    #: The total amount of each resource.
    total: Dict[str, int]

    #: The unused amount of each resource.
    available: Dict[str, int]

    #: The default amount used by each action.
    default: Dict[str, int]

    #: A condition for synchronizing between the asynchronous actions.
    condition: asyncio.Condition

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        assert Prog.current is not None
        Resources.total = dict(jobs=int(Prog.current.get_parameter('jobs')))
        Resources.available = Resources.total.copy()
        Resources.default = dict(jobs=1)
        Resources.condition = asyncio.Condition()

    @staticmethod
    def effective(requested: Dict[str, int]) -> Dict[str, int]:
        """
        Return the effective resource amounts given the explicitly requested amounts.
        """
        amounts: Dict[str, int] = {}

        for name, amount in sorted(requested.items()):
            total = Resources.total.get(name)
            if total is None:
                raise RuntimeError('Requested the unknown resource: %s' % name)
            if amount == 0 or Resources.total[name] == 0:
                continue
            if amount > total:
                raise RuntimeError('The requested resource: %s amount: %s '
                                   'is greater than the total amount: %s'
                                   % (name, amount, total))
            amounts[name] = amount

        for name, amount in Resources.total.items():
            if name in requested or amount <= 0:
                continue
            amount = Resources.default[name]
            if amount <= 0:
                continue
            amounts[name] = amount

        return amounts

    @staticmethod
    def have(amounts: Dict[str, int]) -> bool:
        """
        Return whether there are available resource to cover the requested amounts.
        """
        for name, amount in amounts.items():
            if amount > Resources.available[name]:
                return False
        return True

    @staticmethod
    def grab(amounts: Dict[str, int]) -> None:
        """
        Take ownership of some resource amounts.
        """
        for name, amount in amounts.items():
            Resources.available[name] -= amount

    @staticmethod
    def free(amounts: Dict[str, int]) -> None:
        """
        Release ownership of some resource amounts.
        """
        for name, amount in amounts.items():
            Resources.available[name] += amount

    @staticmethod
    async def use(**amounts: int) -> Dict[str, int]:
        """
        Wait for and grab some resource amounts.

        Returns the actual used resource amounts. If a resource is not explicitly given an amount,
        the default used amount from the :py:func:`dynamake.make.resource_parameters` declaration is
        used.

        The caller is responsible for invoking :py:func:`dynamake.make.Resources.free` to
        release the actual used resources.
        """


def resource_parameters(**default_amounts: int) -> None:
    """
    Declare additional resources for controlling parallel action execution.

    Each resource should have been declared as a :py:class:`dynamake.application.Param`.
    The value given here is the default amount of the resource used by each action that
    does not specify an explicit value.
    """
    for name, amount in default_amounts.items():
        total = int(Prog.current.get_parameter(name))
        if amount > total:
            raise RuntimeError('The default amount: %s '
                               'of the resource: %s '
                               'is greater than the total amount: %s'
                               % (amount, name, total))
        Resources.total[name] = total
        Resources.available[name] = total
        Resources.default[name] = amount
        Func.names_by_parameter[name] = []


class StepException(Exception):
    """
    Indicates a step has aborted and its output must not be used by other steps.
    """


class Make:
    """
    Global build configuration and state.
    """
    #: The directory to keep persistent state in.
    PERSISTENT_DIRECTORY: str

    #: The default steps configuration to load.
    DEFAULT_STEP_CONFIG: str

    #: The log level for logging the reasons for action execution.
    WHY = (Prog.TRACE + logging.INFO) // 2

    #: Whether to rebuild outputs if the actions have changed (by default, ``True``).
    rebuild_changed_actions: bool

    #: Whether to stop the script if any action fails (by default, ``True``).
    #:
    #: If this is ``False``, then the build will continue to execute unrelated actions.
    #: In all cases, actions that have already been submitted will be allowed to end normally.
    failure_aborts_build: bool

    #: Whether to remove old output files before executing an action (by default, ``True``).
    #:
    #: It is possible to override this on a per-action basis.
    remove_stale_outputs: bool

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
    #: In these modern times, this is mostly unneeded as we use the nanosecond modification time,
    #: which pretty much guarantees that output files will be newer than input files. In the "bad
    #: old days", files created within a second of each other had the same modification time.
    #:
    #: This might still be needed if an output is a directory (not a file) and
    #: :py:attr:`dynamake.make.Make.remove_stale_outputs` is ``False``, since otherwise the
    #: ``mtime`` of an existing directory will not necessarily be updated to reflect the fact the
    #: action was executed. In general it is not advised to depend on the ``mtime`` of
    #: directories; it is better to specify a glob matching the expected files inside them, or use
    #: an explicit timestamp file.
    touch_success_outputs: bool

    #: Whether to remove output files on a failing action (by default, ``True``).
    #:
    #: It is possible to override this on a per-action basis.
    remove_failed_outputs: bool

    #: Whether to (try to) remove empty directories when deleting the last file in them (by default,
    #: ``False``).
    remove_empty_directories: bool

    #: Whether to log (level INFO) skipped actions (by default, ``False``).
    log_skipped_actions: bool

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Make.PERSISTENT_DIRECTORY = os.getenv('DYNAMAKE_PERSISTENT_DIR', '.dynamake')
        Make.DEFAULT_STEP_CONFIG = 'DynaMake.yaml'
        Make.failure_aborts_build = True
        Make.remove_stale_outputs = True
        Make.wait_nfs_outputs = False
        Make.nfs_outputs_timeout = 60
        Make.touch_success_outputs = True
        Make.remove_failed_outputs = True
        Make.remove_empty_directories = False
        Make.log_skipped_actions = False
        Make.rebuild_changed_actions = True


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

        if not self.output:
            raise RuntimeError('The step function: %s.%s specifies no output'
                               % (func.wrapped.__module__, func.wrapped.__qualname__))

        Step.by_name[self.name()] = self

    @staticmethod
    def collect(wrapped: Callable, output: Strings) -> 'Step':
        """
        Collect a build step function.
        """
        func = Func.collect(wrapped, is_top=False)
        if not iscoroutinefunction(func.wrapped):
            raise RuntimeError('The step function: %s.%s is not a coroutine'
                               % (func.wrapped.__module__, func.wrapped.__qualname__))
        return Step(func, output)

    def name(self) -> str:
        """
        The name of the function implementing the step.
        """
        return self.func.name


class PersistentAction:
    """
    An action taken during step execution.

    We can persist this to ensure the actions taken in a future invocation is identical,
    to trigger rebuild if the list of actions changes.
    """

    def __init__(self, previous: Optional['PersistentAction'] = None) -> None:
        #: The kind of command ('phony', 'shell' or 'spawn').
        self.kind: str = 'phony'

        #: The executed command.
        self.command: Optional[List[str]] = None

        #: The time the command started execution.
        self.start: Optional[datetime] = None

        #: The time the command ended execution.
        self.end: Optional[datetime] = None

        #: The called step (with parameters) for each required input of the command.
        self.required: Dict[str, str] = {}

        #: The previous action of the step, if any.
        self.previous = previous

    def require(self, path: str, origin: str) -> None:
        """
        Add a required input to the action.

        The origin is empty for source files. Otherwise, it is the name of the step, with any
        parameters.
        """
        self.required[path] = origin

    def run_action(self, kind: str, command: List[str]) -> None:
        """
        Set the executed command of the action.
        """
        self.command = [word for word in command if not dp.is_phony(word)]
        self.kind = kind
        self.start = datetime.now()

    def done_action(self) -> None:
        """
        Record the end time of the command.
        """
        self.end = datetime.now()

    def is_empty(self) -> bool:
        """
        Whether this action has any additional information over its predecessor.
        """
        return self.kind == 'phony' and not self.required

    def into_data(self) -> List[Dict[str, Any]]:
        """
        Serialize for dumping to YAML.
        """
        if self.previous:
            data = self.previous.into_data()
        else:
            data = []

        datum: Dict[str, Any] = dict(required=self.required)
        if self.kind != 'phony':
            datum[self.kind] = self.command
            datum['start'] = str(self.start)
            datum['end'] = str(self.end)

        data.append(datum)
        return data

    @staticmethod
    def from_data(data: List[Dict[str, Any]]) -> List['PersistentAction']:
        """
        Construct the data from loaded YAML.
        """
        if not data:
            return [PersistentAction()]

        datum = data[-1]
        data = data[:-1]

        if data:
            actions = PersistentAction.from_data(data)
            action = PersistentAction(actions[-1])
            actions.append(action)
        else:
            action = PersistentAction()
            actions = [action]

        action.required = datum['required']

        for kind in ['shell', 'spawn']:
            if kind in datum:
                action.kind = kind

        if action.kind != 'phony':
            action.command = datum[action.kind]
            action.start = datetime.strptime(datum['start'], '%Y-%m-%d %H:%M:%S.%f')
            action.end = datetime.strptime(datum['end'], '%Y-%m-%d %H:%M:%S.%f')

        return actions


class Invocation:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """
    An active invocation of a build step.
    """

    #: The active invocations.
    active: Dict[str, 'Invocation']

    #: The current invocation.
    current: 'Invocation'

    #: The top-level invocation.
    top: 'Invocation'

    #: The paths for phony targets.
    phony: Set[str]

    #: The origin of targets that were built or otherwise proved to be up-to-date so far.
    up_to_date: Dict[str, str]

    #: The files that failed to build and must not be used by other steps.
    poisoned: Set[str]

    #: A running counter of the executed actions.
    actions_count: int

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Invocation.active = {}
        Invocation.current = None  # type: ignore
        Invocation.top = Invocation(None)
        Invocation.current = Invocation.top
        Invocation.up_to_date = {}
        Invocation.phony = set()
        Invocation.poisoned = set()
        Invocation.actions_count = 0

    def __init__(self, step: Optional[Step], **kwargs: Any) -> None:  # pylint: disable=redefined-outer-name
        """
        Track the invocation of an async step.
        """
        #: The parent invocation, if any.
        self.parent: Optional[Invocation] = Invocation.current

        #: The step being invoked.
        self.step = step

        #: The arguments to the invocation.
        self.kwargs = kwargs
        if step is not None:
            self.kwargs = step.func.invocation_kwargs(**kwargs)

        #: The full name (including parameters) of the invocation.
        self.name = 'make'
        if self.step is not None:
            self.name = self.step.name()
        args_string = _dict_to_str(kwargs)
        if args_string:
            self.name += '/'
            self.name += args_string

        assert (self.parent is None) == (step is None)

        #: How many sub-invocations were created so far.
        self.sub_count = 0

        if self.parent is None:
            #: A short unique stack to identify invocations in the log.
            self.stack: str = '.'

            #: Context for formatting action wrappers (run prefix and suffix).
            self.context: Dict[str, Any] = {}
        else:
            self.parent.sub_count += 1
            if self.parent.stack == '.':
                self.stack = '.%s' % self.parent.sub_count
            else:
                self.stack = '%s.%s' % (self.parent.stack, self.parent.sub_count)
            self.context = self.parent.context.copy()

        #: The prefix for log messages.
        self.log_prefix = '[%s] %s:' % (self.stack, self.name)

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
        self.async_actions: List[Coroutine] = []

        #: The output files that existed prior to the invocation.
        self.initial_outputs: List[str] = []

        #: The phony outputs, if any.
        self.phony_outputs: List[str] = []

        #: The (non-phony) built outputs, if any.
        self.built_outputs: List[str] = []

        #: A pattern for some missing output file(s), if any.
        self.missing_output: Optional[str] = None

        #: A path for some missing old built output file, if any.
        self.abandoned_output: Optional[str] = None

        #: The oldest existing output file path, or None if some output files are missing.
        self.oldest_output_path: Optional[str] = None

        #: The modification time of the oldest existing output path.
        self.oldest_output_mtime_ns = 0

        #: The reason to abort this invocation, if any.
        self.exception: Optional[BaseException] = None

        #: The old persistent actions (from the disk) for ensuring rebuild when actions change.
        self.old_persistent_actions: List[PersistentAction] = []

        #: The old list of outputs (from the disk) for ensuring complete dynamic outputs.
        self.old_persistent_outputs: List[str] = []

        #: The new persistent actions (from the code) for ensuring rebuild when actions change.
        self.new_persistent_actions: List[PersistentAction] = []

        #: Whether we already decided to run actions.
        self.must_run_action = False

        #: Whether we haven't run (or skipped) the first action yet.
        self.is_first_action = True

        #: Whether we run all actions seen so far.
        self.did_run_all_actions = True

        #: Whether we should remove stale outputs before running the next action.
        self.should_remove_stale_outputs = Make.remove_stale_outputs

        #: The full configuration for the step, if used.
        self.full_config_values: Optional[Dict[str, Any]] = None

        #: The path to the persistent configuration file, if it is used.
        self.config_path: Optional[str] = None

        #: The persistent configuration for the step, if the file is used.
        self.file_config_values: Optional[Dict[str, Any]] = None

    def read_old_persistent_actions(self) -> None:
        """
        Read the old persistent data from the disk file.

        These describe the last successful build of the outputs.
        """
        path = os.path.join(Make.PERSISTENT_DIRECTORY, self.name + '.actions.yaml')
        if not os.path.exists(path):
            Prog.logger.log(Make.WHY,
                            '%s must run actions because missing the persistent actions: %s',
                            self.log_prefix, path)
            self.must_run_action = True
            return

        try:
            with open(path, 'r') as file:
                data = yaml.full_load(file.read())
            self.old_persistent_actions = PersistentAction.from_data(data['actions'])
            self.old_persistent_outputs = data['outputs']
            Prog.logger.debug('%s read the persistent actions: %s', self.log_prefix, path)

        except BaseException:  # pylint: disable=broad-except
            Prog.logger.warn('%s must run actions because read the invalid persistent actions: %s',
                             self.log_prefix, path)
            self.must_run_action = True

    def remove_old_persistent_data(self) -> None:
        """
        Remove the persistent data from the disk in case the build failed.
        """
        path = os.path.join(Make.PERSISTENT_DIRECTORY, self.name + '.actions.yaml')
        if os.path.exists(path):
            Prog.logger.debug('%s remove the persistent actions: %s', self.log_prefix, path)
            os.remove(path)

        path = os.path.join(Make.PERSISTENT_DIRECTORY, self.name + '.config.yaml')
        if os.path.exists(path):
            Prog.logger.debug('%s remove the persistent configuration: %s', self.log_prefix, path)
            os.remove(path)

        if '/' not in self.name:
            return
        try:
            os.rmdir(os.path.dirname(path))
        except OSError:
            pass

    def write_new_persistent_actions(self) -> None:
        """
        Write the new persistent data into the disk file.

        This is only done on a successful build.
        """
        path = os.path.join(Make.PERSISTENT_DIRECTORY, self.name + '.actions.yaml')
        Prog.logger.debug('%s write the persistent actions: %s', self.log_prefix, path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as file:
            data = dict(actions=self.new_persistent_actions[-1].into_data(),
                        outputs=self.built_outputs)
            file.write(yaml.dump(data))

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

    def require(self, path: str) -> None:
        """
        Require a file to be up-to-date before executing any actions or completing the current
        invocation.
        """
        Invocation.current = self

        Prog.logger.debug('%s build the required: %s', self.log_prefix, path)

        self.all_inputs.append(path)

        if path in Invocation.poisoned:
            self.abort('%s the required: %s has failed to build' % (self.log_prefix, path))
            return

        origin = Invocation.up_to_date.get(path)
        if origin is not None:
            Prog.logger.debug('%s the required: %s was built', self.log_prefix, path)
            if self.new_persistent_actions:
                self.new_persistent_actions[-1].require(path, origin)
            return

        step, kwargs = self.producer_of(path)  # pylint: disable=redefined-outer-name
        if kwargs is None:
            return

        if step is None:
            stat = Stat.try_stat(path)
            if stat is None:
                self.log_and_abort("%s don't know how to make the required: %s"
                                   % (self.log_prefix, path))
                return
            Prog.logger.debug('%s the required: %s is a source file', self.log_prefix, path)
            Invocation.up_to_date[path] = ''
            if self.new_persistent_actions:
                self.new_persistent_actions[-1].require(path, '')
            return

        invocation = Invocation(step, **kwargs)
        if self.new_persistent_actions:
            self.new_persistent_actions[-1].require(path, invocation.name)
        Prog.logger.debug('%s the required: %s '
                          'will be produced by the spawned: %s',
                          self.log_prefix, path, invocation.log_prefix[:-1])
        self.async_actions.append(asyncio.Task(invocation.run()))  # type: ignore

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

    async def run(self) -> Optional[BaseException]:  # pylint: disable=too-many-branches
        """
        Actually run the invocation.
        """
        active = Invocation.active.get(self.name)
        if active is not None:
            return await self.done(self.wait_for(active))

        Invocation.current = self
        Prog.logger.log(Prog.TRACE, '%s call', self.log_prefix)

        if Make.rebuild_changed_actions:
            self.new_persistent_actions.append(PersistentAction())
            self.read_old_persistent_actions()

        assert self.name not in Invocation.active
        Invocation.active[self.name] = self
        self.collect_initial_outputs()

        try:
            assert self.step is not None
            await self.done(self.step.func.wrapped(**self.kwargs))
            await self.done(self.sync())
            await self.done(self.collect_final_outputs())

        except BaseException as exception:  # pylint: disable=broad-except
            self.exception = exception

        finally:
            Invocation.current = self

        if self.exception is None:
            if self.new_persistent_actions:
                if len(self.new_persistent_actions) > 1 \
                        and self.new_persistent_actions[-1].is_empty():
                    self.new_persistent_actions.pop()

                if self.did_run_all_actions:
                    self.write_new_persistent_actions()
                elif len(self.new_persistent_actions) < len(self.old_persistent_actions):
                    Prog.logger.warn('%s skipped some action(s) '
                                     'even though it has changed to remove some final action(s)',
                                     self.log_prefix)

            Prog.logger.log(Prog.TRACE, '%s done', self.log_prefix)

        else:
            self.poison_all_outputs()
            self.remove_old_persistent_data()
            Prog.logger.log(Prog.TRACE, '%s fail', self.log_prefix)

        del Invocation.active[self.name]
        if self.condition is not None:
            await self.done(self.condition.acquire())
            self.condition.notify_all()
            self.condition.release()

        if self.exception is not None and Make.failure_aborts_build:
            raise self.exception

        return self.exception

    async def wait_for(self, active: 'Invocation') -> Optional[BaseException]:
        """
        Wait until the invocation is done.

        This is used by other invocations that use this invocation's output(s) as their input(s).
        """
        Invocation.current = self

        Prog.logger.debug('%s paused by waiting for: %s', self.log_prefix, active.log_prefix[:-1])

        if active.condition is None:
            active.condition = asyncio.Condition()

        await self.done(active.condition.acquire())
        await self.done(active.condition.wait())
        active.condition.release()

        Prog.logger.debug('%s resumed by completion of: %s',
                          self.log_prefix, active.log_prefix[:-1])

        return active.exception

    def collect_initial_outputs(self) -> None:  # pylint: disable=too-many-branches
        """
        Check which of the outputs already exist and what their modification times are, to be able
        to decide whether actions need to be run to create or update them.
        """
        assert self.step is not None
        missing_outputs = []
        for pattern in sorted(self.step.output):
            if dp.is_phony(pattern):
                path = dp.capture2glob(pattern).format(**self.kwargs)
                self.phony_outputs.append(path)
                Invocation.phony.add(path)
                continue
            try:
                for path in sorted(dp.fmt_glob_paths(self.kwargs, pattern)):
                    self.initial_outputs.append(path)
                    if path == pattern:
                        Prog.logger.debug('%s exists output: %s', self.log_prefix, path)
                    else:
                        Prog.logger.debug('%s exists output: %s -> %s',
                                          self.log_prefix, pattern, path)
            except dp.NonOptionalException:
                Prog.logger.debug('%s missing the output(s): %s', self.log_prefix, pattern)
                self.missing_output = pattern
                missing_outputs.append(dp.capture2re(pattern))

        if self.new_persistent_actions:
            for path in self.old_persistent_outputs:
                if path in self.initial_outputs:
                    continue

                was_reported = False
                for regexp in missing_outputs:
                    if re.fullmatch(regexp, path):
                        was_reported = True
                        break

                if was_reported:
                    continue

                if Stat.exists(path):
                    Prog.logger.debug('%s changed to abandon the output: %s', self.log_prefix, path)
                    self.abandoned_output = path
                else:
                    Prog.logger.debug('%s missing the old built output: %s', self.log_prefix, path)
                    self.missing_output = path

                Stat.forget(path)

        if self.must_run_action \
                or self.phony_outputs \
                or self.missing_output is not None \
                or self.abandoned_output is not None:
            return

        for output_path in sorted(self.initial_outputs):
            if dp.is_exists(output_path):
                continue
            output_mtime_ns = Stat.stat(output_path).st_mtime_ns
            if self.oldest_output_path is None or self.oldest_output_mtime_ns > output_mtime_ns:
                self.oldest_output_path = output_path
                self.oldest_output_mtime_ns = output_mtime_ns

        if Prog.logger.isEnabledFor(logging.DEBUG) and self.oldest_output_path is not None:
            Prog.logger.debug('%s oldest output: %s time: %s',
                              self.log_prefix, self.oldest_output_path,
                              _datetime_from_nanoseconds(self.oldest_output_mtime_ns))

    async def collect_final_outputs(self) -> None:  # pylint: disable=too-many-branches
        """
        Ensure that all the (required) outputs were actually created and are newer than all input
        files specified so far.

        If successful, this marks all the outputs as up-to-date so that steps that depend on them
        will immediately proceed.
        """
        Invocation.current = self

        missing_outputs = False
        assert self.step is not None
        for path in self.phony_outputs:
            Invocation.up_to_date[path] = self.name

        did_sleep = False
        waited = 0.0
        next_wait = 0.1

        for pattern in sorted(self.step.output):  # pylint: disable=too-many-nested-blocks
            if dp.is_phony(pattern):
                continue

            did_wait = False
            while True:
                try:
                    for path in sorted(dp.fmt_glob_paths(self.kwargs, pattern)):
                        self.built_outputs.append(path)

                        if did_wait:
                            Prog.logger.warn('%s waited: %s seconds for the output: %s',
                                             self.log_prefix, round(waited, 2), path)

                        if Make.touch_success_outputs and not dp.is_exists(path):
                            if not did_sleep:
                                await self.done(asyncio.sleep(0.01))
                                did_sleep = True
                            Prog.logger.debug('%s touch the output: %s', self.log_prefix, path)
                            Stat.touch(path)

                        Invocation.up_to_date[path] = self.name
                        mtime_ns = Stat.stat(path).st_mtime_ns

                        if Prog.logger.isEnabledFor(logging.DEBUG):
                            if path == pattern:
                                Prog.logger.debug('%s has the output: %s time: %s',
                                                  self.log_prefix, path,
                                                  _datetime_from_nanoseconds(mtime_ns))
                            else:
                                Prog.logger.debug('%s has the output: %s -> %s time: %s',
                                                  self.log_prefix, pattern, path,
                                                  _datetime_from_nanoseconds(mtime_ns))

                    break

                except dp.NonOptionalException:
                    Invocation.current = self
                    if Make.wait_nfs_outputs and waited < Make.nfs_outputs_timeout:
                        await self.done(asyncio.sleep(next_wait))
                        did_sleep = True
                        waited += next_wait
                        next_wait *= 2
                        did_wait = True
                        continue

                    Prog.logger.error('%s missing the output(s): %s', self.log_prefix, pattern)
                    missing_outputs = True
                    break

        if missing_outputs:
            self.abort('%s missing some output(s)' % self.log_prefix)

    def remove_stale_outputs(self) -> None:
        """
        Delete stale outputs before running a action.

        This is only done before running the first action of a step.
        """
        for path in sorted(self.initial_outputs):
            if self.should_remove_stale_outputs and not is_precious(path):
                if self.is_first_action:  # TODO: Warn if otherwise (keeping the stale output)?
                    Prog.logger.debug('%s remove the stale output: %s', self.log_prefix, path)
                    self.remove_output(path)
            Stat.forget(path)

        self.should_remove_stale_outputs = False

    def remove_output(self, path: str) -> None:
        """
        Remove an output file, and possibly the directories that became empty as a result.
        """
        Stat.remove(path)
        while Make.remove_empty_directories:
            path = os.path.dirname(path)
            try:
                Stat.rmdir(path)
                Prog.logger.debug('%s remove the empty directory: %s', self.log_prefix, path)
            except OSError:
                return

    def poison_all_outputs(self) -> None:
        """
        Mark all outputs as poisoned for a failed step.

        Typically also removes them.
        """
        assert self.step is not None

        for path in self.phony_outputs:
            Invocation.poisoned.add(path)

        for pattern in sorted(self.step.output):
            if dp.is_phony(pattern):
                continue
            for path in sorted(dp.fmt_glob_paths(self.kwargs, dp.optional(pattern))):
                Invocation.poisoned.add(path)
                if Make.remove_failed_outputs and not is_precious(path):
                    Prog.logger.debug('%s remove the failed output: %s', self.log_prefix, path)
                    self.remove_output(path)

    def should_run_action(self) -> bool:  # pylint: disable=too-many-return-statements
        """
        Test whether all (required) outputs already exist, and are newer than all input files
        specified so far.
        """
        if self.must_run_action:
            return True

        if self.phony_outputs:
            # Either no output files (pure action) or missing output files.
            Prog.logger.log(Make.WHY, '%s must run actions to satisfy the phony output: %s',
                            self.log_prefix, self.phony_outputs[0])
            return True

        if self.phony_inputs:
            Prog.logger.log(Make.WHY, '%s must run actions '
                            'because has rebuilt the required phony: %s',
                            self.log_prefix, self.phony_inputs[0])
            return True

        if self.missing_output is not None:
            Prog.logger.log(Make.WHY,
                            '%s must run actions to create the missing output(s): %s',
                            self.log_prefix, self.missing_output)
            return True

        if self.abandoned_output is not None:
            Prog.logger.log(Make.WHY,
                            '%s must run actions since it has changed to abandon the output: %s',
                            self.log_prefix, self.abandoned_output)
            return True

        if self.new_persistent_actions:
            # Compare with last successful build action.
            index = len(self.new_persistent_actions) - 1
            if index >= len(self.old_persistent_actions):
                Prog.logger.log(Make.WHY,
                                '%s must run actions since it has changed to add action(s)',
                                self.log_prefix)
                return True
            new_action = self.new_persistent_actions[index]
            old_action = self.old_persistent_actions[index]
            if self.different_actions(old_action, new_action):
                return True

        # All output files exist:

        if self.newest_input_path is None:
            # No input files (pure computation).
            Prog.logger.debug('%s can skip actions '
                              'because all the outputs exist and there are no newer inputs',
                              self.log_prefix)
            return False

        # There are input files:

        if self.oldest_output_mtime_ns <= self.newest_input_mtime_ns:
            # Some output file is not newer than some input file.
            Prog.logger.log(Make.WHY,
                            '%s must run actions '
                            'because the output: %s '
                            'is not newer than the input: %s',
                            self.log_prefix, self.oldest_output_path,
                            self.newest_input_path)
            return True

        # All output files are newer than all input files.
        Prog.logger.debug('%s can skip actions '
                          'because all the outputs exist and are newer than all the inputs',
                          self.log_prefix)
        return False

    def different_actions(self, old_action: PersistentAction, new_action: PersistentAction) -> bool:
        """
        Check whether the new action is different from the last build action.
        """
        if self.different_required(old_action.required, new_action.required):
            return True
        if old_action.kind != new_action.kind \
                or old_action.command != new_action.command:
            Prog.logger.log(Make.WHY,
                            '%s must run actions '
                            'because it has changed the %s command: %s into the %s command: %s',
                            self.log_prefix,
                            old_action.kind, ' '.join(old_action.command or 'none'),
                            new_action.kind, ' '.join(new_action.command or 'none'))
            return True
        return False

    def different_required(self, old_required: Dict[str, str],
                           new_required: Dict[str, str]) -> bool:
        """
        Check whether the required inputs of the new action are different from the required inputs
        of the last build action.
        """
        for new_path in sorted(new_required.keys()):
            if new_path not in old_required:
                Prog.logger.log(Make.WHY,
                                '%s must run actions because it has changed to require: %s',
                                self.log_prefix, new_path)
                return True

        for old_path in sorted(old_required.keys()):
            if old_path not in new_required:
                Prog.logger.log(Make.WHY,
                                '%s must run actions because it has changed to not require: %s',
                                self.log_prefix, old_path)
                return True

        for path in sorted(new_required.keys()):
            old_invocation = old_required[path]
            new_invocation = new_required[path]
            if old_invocation != new_invocation:
                Prog.logger.log(Make.WHY,
                                '%s must run actions '
                                'because the producer of the required: %s '
                                'has changed from: %s into: %s',
                                self.log_prefix, path,
                                (old_invocation or 'source file'),
                                (new_invocation or 'source file'))
                return True

        return False

    async def run_action(self,  # pylint: disable=too-many-branches,too-many-statements
                         kind: str, runner: Callable, *command: Strings, **resources: int) -> None:
        """
        Spawn a action to actually create some files.
        """
        Invocation.current = self

        await self.done(self.sync())

        run_parts = []
        log_parts = []
        for part in dp.each_string(*command):
            run_parts.append(part)
            if kind != 'shell':
                part = dp.copy_annotations(part, shlex.quote(part))
            log_parts.append(dp.color(part))
        log_command = ' '.join(log_parts)

        if self.exception is not None:
            Prog.logger.debug("%s can't run: %s", self.log_prefix, log_command)
            raise self.exception

        if self.new_persistent_actions:
            self.new_persistent_actions[-1].run_action(kind, run_parts)

        if not self.should_run_action():
            if Make.log_skipped_actions:
                Prog.logger.info('%s skip: %s', self.log_prefix, log_command)
            else:
                Prog.logger.debug('%s skip: %s', self.log_prefix, log_command)
            self.did_run_all_actions = False
            self.is_first_action = False
            if self.new_persistent_actions:
                self.new_persistent_actions.append(  #
                    PersistentAction(self.new_persistent_actions[-1]))
            return

        Invocation.actions_count += 1

        resources = Resources.effective(resources)
        if resources:
            await self.done(self._use_resources(resources))

        try:
            self.remove_stale_outputs()

            self.oldest_output_path = None
            self.must_run_action = True
            self.is_first_action = False

            Prog.logger.info('%s run: %s', self.log_prefix, log_command)

            sub_process = await self.done(runner(*run_parts))
            exit_status = await self.done(sub_process.wait())

            if self.new_persistent_actions:
                persistent_action = self.new_persistent_actions[-1]
                persistent_action.done_action()
                self.new_persistent_actions.append(PersistentAction(persistent_action))

            if exit_status != 0:
                self.log_and_abort('%s failure: %s' % (self.log_prefix, log_command))
                return

            Prog.logger.log(Prog.TRACE, '%s success: %s', self.log_prefix, log_command)
        finally:
            Invocation.current = self
            if resources:
                if Prog.logger.isEnabledFor(logging.DEBUG):
                    Prog.logger.debug('%s free resources: %s',
                                      self.log_prefix, _dict_to_str(resources))
                Resources.free(resources)
                if Prog.logger.isEnabledFor(logging.DEBUG):
                    Prog.logger.debug('%s available resources: %s',
                                      self.log_prefix, _dict_to_str(Resources.available))
                await self.done(Resources.condition.acquire())
                Resources.condition.notify_all()
                Resources.condition.release()

    async def _use_resources(self, amounts: Dict[str, int]) -> None:
        Invocation.current = self

        while True:
            if Resources.have(amounts):
                if Prog.logger.isEnabledFor(logging.DEBUG):
                    Prog.logger.debug('%s grab resources: %s',
                                      self.log_prefix, _dict_to_str(amounts))
                Resources.grab(amounts)
                if Prog.logger.isEnabledFor(logging.DEBUG):
                    Prog.logger.debug('%s available resources: %s',
                                      self.log_prefix, _dict_to_str(Resources.available))
                return

            if Prog.logger.isEnabledFor(logging.DEBUG):
                if Prog.logger.isEnabledFor(logging.DEBUG):
                    Prog.logger.debug('%s available resources: %s',
                                      self.log_prefix, _dict_to_str(Resources.available))
                    Prog.logger.debug('%s paused by waiting for resources: %s',
                                      self.log_prefix, _dict_to_str(amounts))

            await self.done(Resources.condition.acquire())
            await self.done(Resources.condition.wait())

            Resources.condition.release()

    async def sync(self) -> Optional[BaseException]:  # pylint: disable=too-many-branches
        """
        Wait until all the async actions queued so far are complete.

        This is implicitly called before running a action.
        """
        Invocation.current = self

        if self.async_actions:
            Prog.logger.debug('%s sync', self.log_prefix)
            results: List[Optional[BaseException]] = \
                await self.done(asyncio.gather(*self.async_actions))
            if self.exception is None:
                for exception in results:
                    if exception is not None:
                        self.exception = exception
                        break
            self.async_actions = []

        Prog.logger.debug('%s synced', self.log_prefix)

        failed_inputs = False
        self.phony_inputs = []
        for path in sorted(self.all_inputs):
            if path in Invocation.poisoned \
                    or (not dp.is_optional(path) and path not in Invocation.up_to_date):
                if self.exception is None:
                    level = logging.ERROR
                else:
                    level = logging.DEBUG
                Prog.logger.log(level, '%s the required: %s has failed to build',
                                self.log_prefix, path)
                Invocation.poisoned.add(path)
                failed_inputs = True
                continue

            if path not in Invocation.up_to_date:
                assert dp.is_optional(path)
                continue

            Prog.logger.debug('%s has the required: %s', self.log_prefix, path)

            if path in Invocation.phony:
                self.phony_inputs.append(path)
                continue

            if dp.is_exists(path):
                continue

            result = Stat.stat(path)
            if self.newest_input_path is None or self.newest_input_mtime_ns < result.st_mtime_ns:
                self.newest_input_path = path
                self.newest_input_mtime_ns = result.st_mtime_ns

        if failed_inputs:
            self.abort('%s failed to build the required target(s)' % self.log_prefix)
            return self.exception

        if self.exception is None \
                and Prog.logger.isEnabledFor(logging.DEBUG) \
                and self.oldest_output_path is not None:
            Prog.logger.debug('%s newest input: %s time: %s',
                              self.log_prefix, self.newest_input_path,
                              _datetime_from_nanoseconds(self.newest_input_mtime_ns))

        return self.exception

    def config_param(self, name: str, default: Any = None, keep_in_file: bool = False) -> Any:
        """
        Access the value of a parameter from the step-specific configuration.

        If ``keep_in_file`` is ``False``, then the parameter will be removed from the generated
        persistent configuration file (if any). This ensures that when changing parameters that only
        affect the internals of the build step, the resulting actions will not be triggered unless
        they actually changed as well.
        """
        self._ensure_config_values()
        assert self.full_config_values is not None
        assert self.file_config_values is not None
        value = self.full_config_values.get(name, default)

        if not keep_in_file:
            for name_in_file in [name, name + '?']:
                if name_in_file in self.file_config_values:
                    del self.file_config_values[name_in_file]

        return value

    def config_file(self) -> str:
        """
        Use the step-specific configuration file in the following step action(s).
        """
        if self.config_path is not None:
            assert self.file_config_values is not None
            return self.config_path

        self._ensure_config_values()
        new_config_text = yaml.dump(self.file_config_values)

        self.config_path = os.path.join(Make.PERSISTENT_DIRECTORY, self.name + '.config.yaml')

        if not os.path.exists(self.config_path):
            Prog.logger.log(Make.WHY,
                            '%s must run actions '
                            'because creating the missing persistent configuration: %s',
                            self.log_prefix, self.config_path)
        else:
            with open(self.config_path, 'r') as file:
                old_config_text = file.read()
            if new_config_text == old_config_text:
                Prog.logger.debug('%s use the same persistent configuration: %s',
                                  self.log_prefix, self.config_path)
                return self.config_path

            Prog.logger.log(Make.WHY,
                            '%s must run actions because changed the persistent configuration: %s',
                            self.log_prefix, self.config_path)
            Prog.logger.debug('%s from the old persistent configuration:\n%s',
                              self.log_prefix, old_config_text)
            Prog.logger.debug('%s to the new persistent configuration:\n%s',
                              self.log_prefix, new_config_text)

        self.must_run_action = True
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, 'w') as file:
            file.write(new_config_text)
        return self.config_path

    def _ensure_config_values(self) -> None:
        if self.full_config_values is not None:
            return

        assert 'step' not in self.kwargs
        assert self.step is not None
        context = self.kwargs.copy()
        context.update(self.context)
        context['step'] = self.step.name()
        self.file_config_values = Config.values_for_context(context)
        self.full_config_values = {}
        for name, value in self.file_config_values.items():
            if name[-1] == '?':
                name = name[:-1]
            self.full_config_values[name] = value

    async def done(self, awaitable: Awaitable) -> Any:
        """
        Await some non-DynaMake function.
        """
        result = await awaitable
        Invocation.current = self
        return result


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


def _reset_test_dates() -> None:
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


async def sync() -> Optional[BaseException]:
    """
    Wait until all the input files specified so far are built.

    This is invoked automatically before running actions.
    """
    current = Invocation.current
    return await current.done(current.sync())


async def shell(*command: Strings, **resources: int) -> None:
    """
    Execute a shell command.

    The caller is responsible for all quotations.

    This first waits until all input files requested so far are ready.
    """
    current = Invocation.current
    await current.done(current.run_action('shell', _run_shell, *command, **resources))


def _run_shell(*command: str) -> Any:
    return asyncio.create_subprocess_shell(' '.join(command))


async def spawn(*command: Strings, **resources: int) -> None:
    """
    Execute an external program with arguments.

    This first waits until all input files requested so far are ready.
    """
    current = Invocation.current
    await current.done(current.run_action('spawn', asyncio.create_subprocess_exec,
                                          *command, **resources))


async def submit(*command: Strings, **resources: int) -> None:
    """
    Execute an external command using a submit prefix.

    This allows the action configuration file to specify a ``run_prefix`` and/or ``run_suffix``
    injected before and/or after the spawned command. The result is treated as a shell command. This
    allows setting up environment variables, submitting the command to execute on a compute cluster,
    etc.
    """
    current = Invocation.current
    prefix = current.config_param('run_prefix', [])
    suffix = current.config_param('run_suffix', [])
    if prefix or suffix:
        current.context['action_id'] = Invocation.actions_count
        prefix = [dp.phony(part.format(**current.context)) for part in dp.each_string(prefix)]
        wrapped = \
            [dp.copy_annotations(part, shlex.quote(part)) for part in dp.each_string(*command)]
        suffix = [dp.phony(part.format(**current.context)) for part in dp.each_string(suffix)]
        wrapper = prefix + wrapped + suffix
        await shell(*wrapper, **resources)
    else:
        await spawn(*command, **resources)


def config_param(name: str, default: Any) -> Any:
    """
    Access the value of a parameter from the step-specific configuration.
    """
    return Invocation.current.config_param(name, default)


def config_file() -> str:
    """
    Use the step-specific configuration file in the following step action(s).
    """
    return Invocation.current.config_file()


def with_config() -> List[str]:
    """
    A convenient shorthand for writing [``--config``, `config_file()`].
    """
    return ['--config', config_file()]


def _define_parameters() -> None:
    Param(name='failure_aborts_build', short='fab', metavar='BOOL', default=True,
          parser=dp.str2bool, group='global options',
          description='Whether to stop the script if any action fails')

    Param(name='remove_stale_outputs', short='dso', metavar='BOOL', default=True,
          parser=dp.str2bool, group='global options',
          description='Whether to remove old output files before executing an action')

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

    Param(name='remove_failed_outputs', short='dfo', metavar='BOOL', default=True,
          parser=dp.str2bool, group='global options',
          description='Whether to remove output files on a failing action')

    Param(name='remove_empty_directories', short='ded', metavar='BOOL', default=False,
          parser=dp.str2bool, group='global options',
          description='Whether to remove empty directories when deleting the last file in them')

    Param(name='log_skipped_actions', short='lsa', metavar='BOOL', default=False,
          parser=dp.str2bool, group='global options',
          description='Whether to log (level INFO) skipped actions')

    Param(name='rebuild_changed_actions', short='rca', metavar='BOOL', default=True,
          parser=dp.str2bool, group='global options',
          description='Whether to rebuild outputs if the actions have changed')

    @config(top=True)
    def _use_parameters(  # pylint: disable=unused-argument,too-many-arguments
        failure_aborts_build: bool = env(),
        remove_stale_outputs: bool = env(),
        wait_nfs_outputs: bool = env(),
        nfs_outputs_timeout: int = env(),
        touch_success_outputs: bool = env(),
        remove_failed_outputs: bool = env(),
        remove_empty_directories: bool = env(),
        log_skipped_actions: bool = env(),
        rebuild_changed_actions: bool = env(),
    ) -> None:
        pass


def _collect_parameters() -> None:
    Resources.available['jobs'] = Resources.total['jobs'] = int(Prog.current.get_parameter('jobs'))
    Make.failure_aborts_build = Prog.current.get_parameter('failure_aborts_build')
    Make.remove_stale_outputs = Prog.current.get_parameter('remove_stale_outputs')
    Make.wait_nfs_outputs = Prog.current.get_parameter('wait_nfs_outputs')
    Make.nfs_outputs_timeout = Prog.current.get_parameter('nfs_outputs_timeout')
    Make.touch_success_outputs = Prog.current.get_parameter('touch_success_outputs')
    Make.remove_failed_outputs = Prog.current.get_parameter('remove_failed_outputs')
    Make.remove_empty_directories = Prog.current.get_parameter('remove_empty_directories')
    Make.log_skipped_actions = Prog.current.get_parameter('log_skipped_actions')
    Make.rebuild_changed_actions = Prog.current.get_parameter('rebuild_changed_actions')


def make(parser: ArgumentParser, *,
         default_targets: Strings = 'all', logger_name: Optional[str] = None,
         adapter: Optional[Callable[[Namespace], None]] = None) -> None:
    """
    A generic ``main`` function for ``DynaMake``.

    The optional ``adapter`` may perform additional adaptation of the execution environment based on
    the parsed command-line arguments before the actual function(s) are invoked.
    """
    Func.collect_indirect_invocations = False
    Prog.load_modules()
    Prog.logger = logging.getLogger(logger_name or sys.argv[0])
    logging.getLogger('asyncio').setLevel('WARN')
    default_targets = dp.flatten(default_targets)
    parser.add_argument('TARGET', nargs='*',
                        help='The file or target to make (default: %s)' % ' '.join(default_targets))
    group = Prog.current.add_global_parameters(parser)
    group.add_argument('--step_config', '-sc', metavar='FILE', action='append',
                       help='Load a step parameters configuration YAML file')

    Prog.current.add_sorted_parameters(parser, extra_help=_extra_parameter_help)
    args = parser.parse_args()
    Prog.parse_args(args)

    if os.path.exists(Make.DEFAULT_STEP_CONFIG):
        Config.load(Make.DEFAULT_STEP_CONFIG)
    for path in (args.step_config or []):
        Config.load(path)

    if adapter is not None:
        adapter(args)

    _collect_parameters()

    targets = [path for path in args.TARGET if path is not None] or dp.flatten(default_targets)

    Prog.logger.log(Prog.TRACE, '[.] make: %s', ' '.join(targets))
    if Prog.logger.isEnabledFor(logging.DEBUG):
        for value in Resources.available.values():
            if value > 0:
                Prog.logger.debug('[.] make: available resources: %s',
                                  _dict_to_str(Resources.available))
                break
    # TODO: Switch to `asyncio.run(sync())` in Python 3.7.
    for target in targets:
        require(target)
    try:
        result: Optional[BaseException] = run(Invocation.top.sync())
    except BaseException as exception:  # pylint: disable=broad-except
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


async def done(awaitable: Awaitable) -> Any:
    """
    Await some non-DynaMake function.
    """
    return await Invocation.current.done(awaitable)


def run(awaitable: Awaitable) -> Any:
    """
    A Python3.6 way to implement the `asyncio.run` function from Python 3.7.
    """
    return asyncio.get_event_loop().run_until_complete(awaitable)


def _extra_parameter_help(parameter_name: str) -> str:
    globs: List[str] = []
    for func_name in Func.names_by_parameter[parameter_name]:
        for pattern in Step.by_name[func_name].output:
            globs.append(dp.capture2glob(pattern))
    if not globs:
        return ''
    return '. Used when making: %s' % ' '.join(sorted(globs))


logging.addLevelName(Make.WHY, 'WHY')


def reset_make() -> None:
    """
    Reset all the current state, for tests.
    """
    reset_application()
    Prog.DEFAULT_MODULE = 'DynaMake'
    Prog.DEFAULT_CONFIG = 'DynaConf.yaml'
    Resources.reset()
    Make.reset()
    Config.reset()
    Invocation.reset()
    Step.reset()
    _define_parameters()


reset_make()
