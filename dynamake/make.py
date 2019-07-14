"""
Utilities for dynamic make.
"""

# pylint: disable=too-many-lines

from .application import *  # pylint: disable=redefined-builtin,wildcard-import,unused-wildcard-import
from .config import Config
from argparse import ArgumentParser
from argparse import Namespace
from datetime import datetime
from inspect import iscoroutinefunction
from textwrap import dedent
from threading import current_thread
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
        Resources.total = dict(jobs=int(Prog.get_parameter('jobs')))
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
        total = int(Prog.get_parameter(name))
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


class RestartException(Exception):
    """
    Indicates a step needs to be re-run, this time executing all actions.
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
            capture = clean_path(capture)
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


class UpToDate:
    """
    Data for each up-to-date target.
    """

    def __init__(self, producer: str, mtime_ns: int = -1) -> None:
        """
        Record a new up-to-date target.
        """
        #: The step (with parameters) that updated the target.
        self.producer = producer

        #: The modified time of the target (in nanoseconds).
        #:
        #: This is negative until we know the correct time.
        self.mtime_ns = mtime_ns


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

        #: The up-to-date data for each input.
        self.required: Dict[str, UpToDate] = {}

        #: The previous action of the step, if any.
        self.previous = previous

    def require(self, path: str, up_to_date: UpToDate) -> None:
        """
        Add a required input to the action.
        """
        self.required[path] = up_to_date

    def run_action(self, kind: str, command: List[str]) -> None:
        """
        Set the executed command of the action.
        """
        assert kind in ['shell', 'spawn']
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

        datum: Dict[str, Any] = \
            dict(required={name: dict(producer=up_to_date.producer,
                                      mtime=str(_datetime_from_nanoseconds(up_to_date.mtime_ns)))
                           for name, up_to_date in self.required.items()})

        if self.kind == 'phony':
            assert self.start is None
            assert self.end is None
        else:
            assert self.start is not None
            assert self.end is not None
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

        action.required = {name: UpToDate(up_to_date['producer'],
                                          _nanoseconds_from_datetime_str(up_to_date['mtime']))
                           for name, up_to_date in datum['required'].items()}

        for kind in ['shell', 'spawn']:
            if kind in datum:
                action.kind = kind

        if action.kind != 'phony':
            action.command = datum[action.kind]
            action.start = _datetime_from_str(datum['start'])
            action.end = _datetime_from_str(datum['end'])

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

    #: The origin and time of targets that were built or otherwise proved to be up-to-date so far.
    up_to_date: Dict[str, UpToDate]

    #: The files that failed to build and must not be used by other steps.
    poisoned: Set[str]

    #: A running counter of the executed actions.
    actions_count: int

    #: A running counter of the skipped actions.
    skipped_count: int

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Invocation.active = {}
        Invocation.current = None  # type: ignore
        Invocation.top = Invocation(None)
        Invocation.top._become_current()  # pylint: disable=protected-access
        Invocation.up_to_date = {}
        Invocation.phony = set()
        Invocation.poisoned = set()
        Invocation.actions_count = 0
        Invocation.skipped_count = 0

    def __init__(self,  # pylint: disable=too-many-statements
                 step: Optional[Step],  # pylint: disable=redefined-outer-name
                 **kwargs: Any) -> None:
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
            self.stack: str = '#0'

            #: Context for formatting action wrappers (run prefix and suffix).
            self.context: Dict[str, Any] = {}
        else:
            self.parent.sub_count += 1
            if self.parent.stack == '#0':
                self.stack = '#%s' % self.parent.sub_count
            else:
                self.stack = '%s.%s' % (self.parent.stack, self.parent.sub_count)
            self.context = self.parent.context.copy()

        if Prog._is_test:  # pylint: disable=protected-access
            self._log = self.stack + ' - ' + self.name
        else:
            self._log = self.name

        self._verify_no_loop()

        #: A condition variable to wait on for this invocation.
        self.condition: Optional[asyncio.Condition] = None

        #: The required input targets (phony or files) the invocations depends on.
        self.required: List[str] = []

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

        #: The built outputs, if any.
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
        self.exception: Optional[StepException] = None

        #: The old persistent actions (from the disk) for ensuring rebuild when actions change.
        self.old_persistent_actions: List[PersistentAction] = []

        #: The old list of outputs (from the disk) for ensuring complete dynamic outputs.
        self.old_persistent_outputs: List[str] = []

        #: The new persistent actions (from the code) for ensuring rebuild when actions change.
        self.new_persistent_actions: List[PersistentAction] = []

        #: Whether we already decided to run actions.
        self.must_run_action = False

        #: Whether we actually skipped all actions so far.
        self.did_skip_actions = False

        #: Whether we actually run any actions.
        self.did_run_actions = False

        #: Whether we should remove stale outputs before running the next action.
        self.should_remove_stale_outputs = Make.remove_stale_outputs

        #: The path to the persistent configuration file, if it is used.
        self.config_path: Optional[str] = None

        #: The full configuration for the step.
        self.full_config_values: Dict[str, Any] = {}

        #: The persistent configuration for the step.
        self.file_config_values: Dict[str, Any] = {}

        self._compute_config_values()

    def _compute_config_values(self) -> None:
        if self.step is None:
            return
        assert 'step' not in self.kwargs
        full_context = self.kwargs.copy()
        full_context.update(self.context)
        full_context['step'] = self.step.name()
        self.file_config_values = Config.values_for_context(full_context)
        self.full_config_values = {}
        for name, value in self.file_config_values.items():
            if name[-1] == '?':
                name = name[:-1]
            self.full_config_values[name] = value

    def _restart(self) -> None:
        if self.parent is None:
            self.context = {}
        else:
            self.context = self.parent.context.copy()

        self.required = []
        self.newest_input_path = None
        self.newest_input_mtime_ns = 0

        assert not self.async_actions

        self.abandoned_output = None
        self.oldest_output_path = None

        assert self.exception is None

        if self.new_persistent_actions:
            self.new_persistent_actions = [PersistentAction()]

        self.must_run_action = True
        self.did_skip_actions = False
        assert self.should_remove_stale_outputs == Make.remove_stale_outputs

        self.config_path = None
        self._compute_config_values()

    def _verify_no_loop(self) -> None:
        call_chain = [self.name]
        parent = self.parent
        while parent is not None:
            call_chain.append(parent.name)
            if self.name == parent.name:
                raise RuntimeError('step invokes itself: ' + ' -> '.join(reversed(call_chain)))
            parent = parent.parent

    def read_old_persistent_actions(self) -> None:
        """
        Read the old persistent data from the disk file.

        These describe the last successful build of the outputs.
        """
        path = os.path.join(Make.PERSISTENT_DIRECTORY, self.name + '.actions.yaml')
        if not os.path.exists(path):
            Prog.logger.log(Make.WHY,
                            '%s - Must run actions because missing the persistent actions: %s',
                            self._log, path)
            self.must_run_action = True
            return

        try:
            with open(path, 'r') as file:
                data = yaml.full_load(file.read())
            self.old_persistent_actions = PersistentAction.from_data(data['actions'])
            self.old_persistent_outputs = data['outputs']
            Prog.logger.debug('%s - Read the persistent actions: %s', self._log, path)

        except BaseException:  # pylint: disable=broad-except
            Prog.logger.warn('%s - Must run actions '
                             'because read the invalid persistent actions: %s',
                             self._log, path)
            self.must_run_action = True

    def remove_old_persistent_data(self) -> None:
        """
        Remove the persistent data from the disk in case the build failed.
        """
        path = os.path.join(Make.PERSISTENT_DIRECTORY, self.name + '.actions.yaml')
        if os.path.exists(path):
            Prog.logger.debug('%s - Remove the persistent actions: %s', self._log, path)
            os.remove(path)

        path = os.path.join(Make.PERSISTENT_DIRECTORY, self.name + '.config.yaml')
        if os.path.exists(path):
            Prog.logger.debug('%s - Remove the persistent configuration: %s', self._log, path)
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
        Prog.logger.debug('%s - Write the persistent actions: %s', self._log, path)

        for action in self.new_persistent_actions:
            for name, partial_up_to_date in action.required.items():
                full_up_to_date = Invocation.up_to_date[name]
                assert full_up_to_date.producer == partial_up_to_date.producer
                partial_up_to_date.mtime_ns = full_up_to_date.mtime_ns

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
        self._become_current()

        path = clean_path(path)

        Prog.logger.debug('%s - Build the required: %s', self._log, path)

        self.required.append(path)

        if path in Invocation.poisoned:
            self.abort('%s - The required: %s has failed to build' % (self._log, path))
            return

        up_to_date = Invocation.up_to_date.get(path)
        if up_to_date is not None:
            Prog.logger.debug('%s - The required: %s was built', self._log, path)
            if self.new_persistent_actions:
                self.new_persistent_actions[-1].require(path, UpToDate(up_to_date.producer))
            return

        step, kwargs = self.producer_of(path)  # pylint: disable=redefined-outer-name
        if kwargs is None:
            return

        if step is None:
            stat = Stat.try_stat(path)
            if stat is None:
                if dp.is_optional(path):
                    Prog.logger.debug('%s - The optional required: %s '
                                      "does not exist and can't be built", self._log, path)
                else:
                    self.log_and_abort("%s - Don't know how to make the required: %s"
                                       % (self._log, path))
                return
            Prog.logger.debug('%s - The required: %s is a source file', self._log, path)
            up_to_date = UpToDate('', stat.st_mtime_ns)
            Invocation.up_to_date[path] = up_to_date
            if self.new_persistent_actions:
                self.new_persistent_actions[-1].require(path, up_to_date)
            return

        invocation = Invocation(step, **kwargs)
        if self.new_persistent_actions:
            self.new_persistent_actions[-1].require(path, UpToDate(invocation.name))
        Prog.logger.debug('%s - The required: %s '
                          'will be produced by the spawned: %s',
                          self._log, path, invocation._log)  # pylint: disable=protected-access
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

    async def run(self) -> Optional[BaseException]:  # pylint: disable=too-many-branches,too-many-statements
        """
        Actually run the invocation.
        """
        active = Invocation.active.get(self.name)
        if active is not None:
            return await self.done(self.wait_for(active))

        self._become_current()
        Prog.logger.log(Prog.TRACE, '%s - Call', self._log)

        if Make.rebuild_changed_actions:
            self.new_persistent_actions.append(PersistentAction())
            self.read_old_persistent_actions()

        assert self.name not in Invocation.active
        Invocation.active[self.name] = self
        self.collect_initial_outputs()

        try:
            assert self.step is not None
            try:
                await self.done(self.step.func.wrapped(**self.kwargs))
            except RestartException:
                self._restart()
                await self.done(self.step.func.wrapped(**self.kwargs))
            await self.done(self.sync())
            await self.done(self.collect_final_outputs())

        except StepException as exception:  # pylint: disable=broad-except
            self.exception = exception

        finally:
            self._become_current()

        if self.exception is None:
            assert not self.async_actions
            if self.new_persistent_actions:
                if len(self.new_persistent_actions) > 1 \
                        and self.new_persistent_actions[-1].is_empty():
                    self.new_persistent_actions.pop()

                if not self.did_skip_actions:
                    self.write_new_persistent_actions()
                elif len(self.new_persistent_actions) < len(self.old_persistent_actions):
                    Prog.logger.warn('%s - Skipped some action(s) '
                                     'even though it has changed to remove some final action(s)',
                                     self._log)

            self._verify_used_config()
            if self.did_run_actions:
                Prog.logger.log(Prog.TRACE, '%s - Done', self._log)
            elif self.did_skip_actions:
                Prog.logger.log(Prog.TRACE, '%s - Skipped', self._log)
            else:
                Prog.logger.log(Prog.TRACE, '%s - Complete', self._log)

        else:
            while self.async_actions:
                try:
                    await self.done(self.async_actions.pop())
                except StepException:
                    pass
            self.poison_all_outputs()
            self.remove_old_persistent_data()
            Prog.logger.log(Prog.TRACE, '%s - Fail', self._log)

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
        self._become_current()

        Prog.logger.debug('%s - Paused by waiting for: %s',
                          self._log, active._log)  # pylint: disable=protected-access

        if active.condition is None:
            active.condition = asyncio.Condition()

        await self.done(active.condition.acquire())
        await self.done(active.condition.wait())
        active.condition.release()

        Prog.logger.debug('%s - Resumed by completion of: %s',
                          self._log, active._log)  # pylint: disable=protected-access

        return active.exception

    def collect_initial_outputs(self) -> None:  # pylint: disable=too-many-branches
        """
        Check which of the outputs already exist and what their modification times are, to be able
        to decide whether actions need to be run to create or update them.
        """
        assert self.step is not None
        missing_outputs = []
        for pattern in sorted(self.step.output):
            formatted_pattern = dp.fmt_capture(self.kwargs, pattern)
            if dp.is_phony(formatted_pattern):
                self.phony_outputs.append(formatted_pattern)
                Invocation.phony.add(formatted_pattern)
                continue

            try:
                paths = dp.glob_paths(formatted_pattern)
                if not paths:
                    Prog.logger.debug('%s - Nonexistent optional output(s): %s',
                                      self._log, pattern)
                else:
                    for path in paths:
                        self.initial_outputs.append(path)
                        if path == pattern:
                            Prog.logger.debug('%s - Existing output: %s', self._log, path)
                        else:
                            Prog.logger.debug('%s - Existing output: %s -> %s',
                                              self._log, pattern, path)
            except dp.NonOptionalException:
                Prog.logger.debug('%s - Nonexistent required output(s): %s',
                                  self._log, pattern)
                self.missing_output = formatted_pattern
                missing_outputs.append(dp.capture2re(formatted_pattern))

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
                    Prog.logger.debug('%s - Changed to abandon the output: %s', self._log, path)
                    self.abandoned_output = path
                else:
                    Prog.logger.debug('%s - Missing the old built output: %s', self._log, path)
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
            Prog.logger.debug('%s - Oldest output: %s time: %s',
                              self._log, self.oldest_output_path,
                              _datetime_from_nanoseconds(self.oldest_output_mtime_ns))

    async def collect_final_outputs(self) -> None:  # pylint: disable=too-many-branches
        """
        Ensure that all the (required) outputs were actually created and are newer than all input
        files specified so far.

        If successful, this marks all the outputs as up-to-date so that steps that depend on them
        will immediately proceed.
        """
        self._become_current()

        missing_outputs = False
        assert self.step is not None

        did_sleep = False
        waited = 0.0
        next_wait = 0.1

        for pattern in sorted(self.step.output):  # pylint: disable=too-many-nested-blocks
            formatted_pattern = dp.fmt_capture(self.kwargs, pattern)
            if dp.is_phony(pattern):
                Invocation.up_to_date[formatted_pattern] = \
                    UpToDate(self.name, self.newest_input_mtime_ns + 1)
                continue

            did_wait = False
            while True:
                try:
                    paths = dp.glob_paths(formatted_pattern)
                    if not paths:
                        # TODO: Should we wait here for NFS timeout? We don't, so we might record
                        # partial outputs on the persistent action.
                        Prog.logger.debug('%s - Did not make the optional output(s): %s',
                                          self._log, pattern)
                    else:
                        for path in paths:
                            self.built_outputs.append(path)
                            if did_wait:
                                Prog.logger.warn('%s - Waited: %s seconds for the output: %s',
                                                 self._log, round(waited, 2), path)

                            if Make.touch_success_outputs and not dp.is_exists(path):
                                if not did_sleep:
                                    await self.done(asyncio.sleep(0.01))
                                    did_sleep = True
                                Prog.logger.debug('%s - Touch the output: %s', self._log, path)
                                Stat.touch(path)

                            mtime_ns = Stat.stat(path).st_mtime_ns
                            Invocation.up_to_date[path] = UpToDate(self.name, mtime_ns)

                            if Prog.logger.isEnabledFor(logging.DEBUG):
                                if path == formatted_pattern:
                                    Prog.logger.debug('%s - Has the output: %s time: %s',
                                                      self._log, path,
                                                      _datetime_from_nanoseconds(mtime_ns))
                                else:
                                    Prog.logger.debug('%s - Has the output: %s -> %s time: %s',
                                                      self._log, pattern, path,
                                                      _datetime_from_nanoseconds(mtime_ns))

                    break

                except dp.NonOptionalException:
                    self._become_current()
                    if Make.wait_nfs_outputs and waited < Make.nfs_outputs_timeout:
                        await self.done(asyncio.sleep(next_wait))
                        did_sleep = True
                        waited += next_wait
                        next_wait *= 2
                        did_wait = True
                        continue

                    Prog.logger.error('%s - Missing the output(s): %s', self._log, pattern)
                    missing_outputs = True
                    break

        if missing_outputs:
            self.abort('%s - Missing some output(s)' % self._log)

    def remove_stale_outputs(self) -> None:
        """
        Delete stale outputs before running a action.

        This is only done before running the first action of a step.
        """
        for path in sorted(self.initial_outputs):
            if self.should_remove_stale_outputs and not is_precious(path):
                Prog.logger.debug('%s - Remove the stale output: %s', self._log, path)
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
                Prog.logger.debug('%s - Remove the empty directory: %s', self._log, path)
            except OSError:
                return

    def poison_all_outputs(self) -> None:
        """
        Mark all outputs as poisoned for a failed step.

        Typically also removes them.
        """
        assert self.step is not None

        for pattern in sorted(self.step.output):
            formatted_pattern = dp.fmt_capture(self.kwargs, dp.optional(pattern))
            if dp.is_phony(formatted_pattern):
                Invocation.poisoned.add(formatted_pattern)
                continue
            for path in dp.glob_paths(dp.optional(formatted_pattern)):
                Invocation.poisoned.add(path)
                if Make.remove_failed_outputs and not is_precious(path):
                    Prog.logger.debug('%s - Remove the failed output: %s', self._log, path)
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
            Prog.logger.log(Make.WHY, '%s - Must run actions to satisfy the phony output: %s',
                            self._log, self.phony_outputs[0])
            return True

        if self.missing_output is not None:
            Prog.logger.log(Make.WHY,
                            '%s - Must run actions to create the missing output(s): %s',
                            self._log, self.missing_output)
            return True

        if self.abandoned_output is not None:
            Prog.logger.log(Make.WHY,
                            '%s - Must run actions since it has changed to abandon the output: %s',
                            self._log, self.abandoned_output)
            return True

        if self.new_persistent_actions:
            # Compare with last successful build action.
            index = len(self.new_persistent_actions) - 1
            if index >= len(self.old_persistent_actions):
                Prog.logger.log(Make.WHY,
                                '%s - Must run actions since it has changed to add action(s)',
                                self._log)
                return True
            new_action = self.new_persistent_actions[index]
            old_action = self.old_persistent_actions[index]
            if self.different_actions(old_action, new_action):
                return True

        # All output files exist:

        if self.newest_input_path is None:
            # No input files (pure computation).
            Prog.logger.debug('%s - Can skip actions '
                              'because all the outputs exist and there are no newer inputs',
                              self._log)
            return False

        # There are input files:

        if self.oldest_output_path is not None \
                and self.oldest_output_mtime_ns <= self.newest_input_mtime_ns:
            # Some output file is not newer than some input file.
            Prog.logger.log(Make.WHY,
                            '%s - Must run actions '
                            'because the output: %s '
                            'is not newer than the input: %s',
                            self._log, self.oldest_output_path,
                            self.newest_input_path)
            return True

        # All output files are newer than all input files.
        Prog.logger.debug('%s - Can skip actions '
                          'because all the outputs exist and are newer than all the inputs',
                          self._log)
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
                            '%s - Must run actions '
                            'because it has changed the %s command: %s into the %s command: %s',
                            self._log,
                            old_action.kind, ' '.join(old_action.command or 'none'),
                            new_action.kind, ' '.join(new_action.command or 'none'))
            return True
        return False

    def different_required(self, old_required: Dict[str, UpToDate],
                           new_required: Dict[str, UpToDate]) -> bool:
        """
        Check whether the required inputs of the new action are different from the required inputs
        of the last build action.
        """
        for new_path in sorted(new_required.keys()):
            if new_path not in old_required:
                Prog.logger.log(Make.WHY,
                                '%s - Must run actions because it has changed to require: %s',
                                self._log, new_path)
                return True

        for old_path in sorted(old_required.keys()):
            if old_path not in new_required:
                Prog.logger.log(Make.WHY,
                                '%s - Must run actions because it has changed to not require: %s',
                                self._log, old_path)
                return True

        for path in sorted(new_required.keys()):
            old_up_to_date = old_required[path]
            new_up_to_date = new_required[path]
            if old_up_to_date.producer != new_up_to_date.producer:
                Prog.logger.log(Make.WHY,
                                '%s - Must run actions '
                                'because the producer of the required: %s '
                                'has changed from: %s into: %s',
                                self._log, path,
                                (old_up_to_date.producer or 'source file'),
                                (new_up_to_date.producer or 'source file'))
                return True
            if old_up_to_date.mtime_ns != new_up_to_date.mtime_ns:
                Prog.logger.log(Make.WHY,
                                '%s - Must run actions '
                                'because the modification time of the required: %s '
                                'has changed from: %s into: %s',
                                self._log, path,
                                _datetime_from_nanoseconds(old_up_to_date.mtime_ns),
                                _datetime_from_nanoseconds(new_up_to_date.mtime_ns))
                return True

        return False

    async def run_action(self,  # pylint: disable=too-many-branches,too-many-statements
                         kind: str, runner: Callable, *command: Strings, **resources: int) -> None:
        """
        Spawn a action to actually create some files.
        """
        self._become_current()

        await self.done(self.sync())

        run_parts = []
        persistent_parts = []
        log_parts = []
        is_silent = None
        for part in dp.each_string(*command):
            if is_silent is None:
                if part.startswith('@'):
                    is_silent = True
                    if part == '@':
                        continue
                    part = part[1:]
                else:
                    is_silent = False

            run_parts.append(part)
            if not dp.is_phony(part):
                persistent_parts.append(part)

            if kind != 'shell':
                part = dp.copy_annotations(part, shlex.quote(part))
            log_parts.append(dp.color(part))

        log_command = ' '.join(log_parts)

        if self.exception is not None:
            Prog.logger.debug("%s - Can't run: %s", self._log, log_command)
            raise self.exception

        if self.new_persistent_actions:
            self.new_persistent_actions[-1].run_action(kind, persistent_parts)

        if not self.should_run_action():
            if Make.log_skipped_actions and not is_silent:
                Prog.logger.info('%s - Skip: %s', self._log, log_command)
            else:
                Prog.logger.debug('%s - Skip: %s', self._log, log_command)
            self.did_skip_actions = True
            if self.new_persistent_actions:
                self.new_persistent_actions.append(  #
                    PersistentAction(self.new_persistent_actions[-1]))
            Invocation.skipped_count += 1
            return

        if self.did_skip_actions:
            self.must_run_action = True
            Prog.logger.debug('Must restart step to run skipped action(s)')
            raise RestartException('To run skipped action(s)')

        self.must_run_action = True
        self.did_run_actions = True

        Invocation.actions_count += 1

        resources = Resources.effective(resources)
        if resources:
            await self.done(self._use_resources(resources))

        try:
            self.remove_stale_outputs()

            self.oldest_output_path = None

            if is_silent:
                Prog.logger.debug('%s - Run: %s', self._log, log_command)
            else:
                Prog.logger.info('%s - Run: %s', self._log, log_command)

            sub_process = await self.done(runner(*run_parts))
            exit_status = await self.done(sub_process.wait())

            if self.new_persistent_actions:
                persistent_action = self.new_persistent_actions[-1]
                persistent_action.done_action()
                self.new_persistent_actions.append(PersistentAction(persistent_action))

            if exit_status != 0:
                self.log_and_abort('%s - Failure: %s' % (self._log, log_command))
                return

            Prog.logger.log(Prog.TRACE, '%s - Success: %s', self._log, log_command)
        finally:
            self._become_current()
            if resources:
                if Prog.logger.isEnabledFor(logging.DEBUG):
                    Prog.logger.debug('%s - Free resources: %s',
                                      self._log, _dict_to_str(resources))
                Resources.free(resources)
                if Prog.logger.isEnabledFor(logging.DEBUG):
                    Prog.logger.debug('%s - Available resources: %s',
                                      self._log, _dict_to_str(Resources.available))
                await self.done(Resources.condition.acquire())
                Resources.condition.notify_all()
                Resources.condition.release()

    async def _use_resources(self, amounts: Dict[str, int]) -> None:
        self._become_current()

        while True:
            if Resources.have(amounts):
                if Prog.logger.isEnabledFor(logging.DEBUG):
                    Prog.logger.debug('%s - Grab resources: %s',
                                      self._log, _dict_to_str(amounts))
                Resources.grab(amounts)
                if Prog.logger.isEnabledFor(logging.DEBUG):
                    Prog.logger.debug('%s - Available resources: %s',
                                      self._log, _dict_to_str(Resources.available))
                return

            if Prog.logger.isEnabledFor(logging.DEBUG):
                if Prog.logger.isEnabledFor(logging.DEBUG):
                    Prog.logger.debug('%s - Available resources: %s',
                                      self._log, _dict_to_str(Resources.available))
                    Prog.logger.debug('%s - Paused by waiting for resources: %s',
                                      self._log, _dict_to_str(amounts))

            await self.done(Resources.condition.acquire())
            await self.done(Resources.condition.wait())

            Resources.condition.release()

    async def sync(self) -> Optional[BaseException]:  # pylint: disable=too-many-branches
        """
        Wait until all the async actions queued so far are complete.

        This is implicitly called before running a action.
        """
        self._become_current()

        if self.async_actions:
            Prog.logger.debug('%s - Sync', self._log)
            results: List[Optional[StepException]] = \
                await self.done(asyncio.gather(*self.async_actions))
            if self.exception is None:
                for exception in results:
                    if exception is not None:
                        self.exception = exception
                        break
            self.async_actions = []

        Prog.logger.debug('%s - Synced', self._log)

        failed_inputs = False
        for path in sorted(self.required):
            if path in Invocation.poisoned \
                    or (not dp.is_optional(path) and path not in Invocation.up_to_date):
                if self.exception is None:
                    level = logging.ERROR
                else:
                    level = logging.DEBUG
                Prog.logger.log(level, '%s - The required: %s has failed to build',
                                self._log, path)
                Invocation.poisoned.add(path)
                failed_inputs = True
                continue

            if path not in Invocation.up_to_date:
                assert dp.is_optional(path)
                continue

            Prog.logger.debug('%s - Has the required: %s', self._log, path)

            if dp.is_exists(path):
                continue

            if path in Invocation.phony:
                mtime_ns = Invocation.up_to_date[path].mtime_ns
            else:
                mtime_ns = Stat.stat(path).st_mtime_ns

            if self.newest_input_path is None or self.newest_input_mtime_ns < mtime_ns:
                self.newest_input_path = path
                self.newest_input_mtime_ns = mtime_ns

        if failed_inputs:
            self.abort('%s - Failed to build the required target(s)' % self._log)
            return self.exception

        if self.exception is None \
                and Prog.logger.isEnabledFor(logging.DEBUG) \
                and self.oldest_output_path is not None:
            if self.newest_input_path is None:
                Prog.logger.debug('%s - No inputs', self._log)
            else:
                Prog.logger.debug('%s - Newest input: %s time: %s',
                                  self._log, self.newest_input_path,
                                  _datetime_from_nanoseconds(self.newest_input_mtime_ns))

        return self.exception

    def config_param(self, name: str, default: Any = None,
                     *, keep_in_file: bool = False, is_required: bool = False) -> Any:
        """
        Access the value of a parameter from the step-specific configuration.

        If ``keep_in_file`` is ``False``, then the parameter will be removed from the generated
        persistent configuration file (if any). This ensures that when changing parameters that only
        affect the internals of the build step, the resulting actions will not be triggered unless
        they actually changed as well.
        """
        value = self.full_config_values.get(name, default)
        if is_required and value is None:
            self.log_and_abort('%s - No value for the step configuration parameter: %s'
                               % (self._log, name))

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
            return self.config_path

        new_config_text = yaml.dump(self.file_config_values)

        self.config_path = os.path.join(Make.PERSISTENT_DIRECTORY, self.name + '.config.yaml')

        if not os.path.exists(self.config_path):
            Prog.logger.log(Make.WHY,
                            '%s - Must run actions '
                            'because creating the missing persistent configuration: %s',
                            self._log, self.config_path)
        else:
            with open(self.config_path, 'r') as file:
                old_config_text = file.read()
            if new_config_text == old_config_text:
                Prog.logger.debug('%s - Use the same persistent configuration: %s',
                                  self._log, self.config_path)
                return self.config_path

            Prog.logger.log(Make.WHY,
                            '%s - Must run actions '
                            'because changed the persistent configuration: %s',
                            self._log, self.config_path)
            Prog.logger.debug('%s - From the old persistent configuration:\n%s',
                              self._log, old_config_text)
            Prog.logger.debug('%s - To the new persistent configuration:\n%s',
                              self._log, new_config_text)

        self.must_run_action = True
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, 'w') as file:
            file.write(new_config_text)
        return self.config_path

    def _verify_used_config(self) -> None:
        if self.config_path is not None:
            return
        unused_parameters = \
            sorted([name for name in self.file_config_values if not name.endswith('?')])
        if unused_parameters:
            self.log_and_abort('%s - Unused step configuration parameter(s): %s'
                               % (self._log, ', '.join(unused_parameters)))

    async def done(self, awaitable: Awaitable) -> Any:
        """
        Await some non-DynaMake function.
        """
        result = await awaitable
        self._become_current()
        return result

    def _become_current(self) -> None:
        Invocation.current = self
        current_thread().name = self.stack


_QUANTIZED_OF_NANOSECONDS: Dict[int, float] = {}
_NANOSECONDS_OF_QUANTIZED: Dict[str, int] = {}


def _datetime_from_str(string: str) -> datetime:
    return datetime.strptime(string, '%Y-%m-%d %H:%M:%S.%f')


def _datetime_from_nanoseconds(nanoseconds: int) -> str:
    if not Prog._is_test:  # pylint: disable=protected-access
        seconds = datetime.fromtimestamp(nanoseconds // 1_000_000_000).strftime('%Y-%m-%d %H:%M:%S')
        fraction = '%09d' % (nanoseconds % 1_000_000_000)
        return '%s.%s' % (seconds, fraction)

    global _QUANTIZED_OF_NANOSECONDS
    quantized = _QUANTIZED_OF_NANOSECONDS.get(nanoseconds, None)
    if quantized is not None:
        return str(quantized)

    higher_nanoseconds = None
    higher_quantized = None
    lower_nanoseconds = None
    lower_quantized = None

    for old_nanoseconds, old_quantized in _QUANTIZED_OF_NANOSECONDS.items():
        if old_nanoseconds < nanoseconds:
            if lower_nanoseconds is None or lower_nanoseconds < old_nanoseconds:
                lower_nanoseconds = old_nanoseconds
                lower_quantized = old_quantized
        if old_nanoseconds > nanoseconds:
            if higher_nanoseconds is None or higher_nanoseconds < old_nanoseconds:
                higher_nanoseconds = nanoseconds
                higher_quantized = old_quantized

    if lower_quantized is None:
        if higher_quantized is None:
            quantized = 1
        else:
            quantized = higher_quantized - 1
    else:
        if higher_quantized is None:
            quantized = lower_quantized + 1
        else:
            quantized = (lower_quantized + higher_quantized) / 2

    _QUANTIZED_OF_NANOSECONDS[nanoseconds] = quantized
    _NANOSECONDS_OF_QUANTIZED[str(quantized)] = nanoseconds
    return str(quantized)


def _nanoseconds_from_datetime_str(string: str) -> int:
    if Prog._is_test:  # pylint: disable=protected-access
        return _NANOSECONDS_OF_QUANTIZED[string]
    seconds_string, nanoseconds_string = string.split('.')

    seconds_datetime = _datetime_from_str(seconds_string + '.0')
    seconds = int(seconds_datetime.timestamp())

    nanoseconds_string = (nanoseconds_string + 9 * '0')[:9]
    nanoseconds = int(nanoseconds_string)

    return seconds * 1_000_000_000 + nanoseconds


def _reset_test_dates() -> None:
    global _QUANTIZED_OF_NANOSECONDS
    global _NANOSECONDS_OF_QUANTIZED
    _QUANTIZED_OF_NANOSECONDS = {}
    _NANOSECONDS_OF_QUANTIZED = {}


def step(output: Strings) -> Callable[[Callable], Callable]:
    """
    Decorate a build step functions.

    If ``top`` is ``True``, this is a top-level step that can be directly invoked from the main
    function.
    """
    def _wrap(wrapped: Callable) -> Callable:
        return Step.collect(wrapped, output).func.wrapper
    return _wrap


def require(*paths: Strings) -> None:
    """
    Require an input file for the step.

    This queues an async build of the input file using the appropriate step,
    and immediately returns.
    """
    for path in dp.each_string(*paths):
        Invocation.current.require(path)


def erequire(*templates: Strings) -> None:
    """
    Similar to :py:func:`dynamake.make.require`, but first :py:func:`dynamake.make.e`-xpands each
    parameter.

    That is, ``erequire(...)`` is the same as ``require(e(...))``.
    """
    require(e(*templates))


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

    The caller is responsible for all quotations. If the first character of the command is ``@``
    then it is "silent", that is, it is logged in the DEBUG level and not the INFO level.

    This first waits until all input files requested so far are ready.
    """
    current = Invocation.current
    await current.done(current.run_action('shell', _run_shell, *command, **resources))


def _run_shell(*command: str) -> Any:
    return asyncio.create_subprocess_shell(' '.join(command))


async def eshell(*templates: Strings) -> None:
    """
    Similar to :py:func:`dynamake.make.shell`, but first :py:func:`dynamake.make.e`-xpands each
    parameter.

    That is, ``eshell(...)`` is the same as ``shell(e(...))``.
    """
    await shell(e(*templates))


async def spawn(*command: Strings, **resources: int) -> None:
    """
    Execute an external program with arguments.

    If the first character of the command is ``@`` then it is "silent", that is, it is logged in the
    DEBUG level and not the INFO level.

    This first waits until all input files requested so far are ready.
    """
    current = Invocation.current
    await current.done(current.run_action('spawn', asyncio.create_subprocess_exec,
                                          *command, **resources))


async def espawn(*templates: Strings) -> None:
    """
    Similar to :py:func:`dynamake.make.spawn`, but first :py:func:`dynamake.make.e`-xpands each
    parameter.

    That is, ``espawn(...)`` is the same as ``spawn(e(...))``.
    """
    await spawn(e(*templates))


async def submit(*command: Strings, **resources: int) -> None:
    """
    Execute an external command using a submit prefix.

    This allows the action configuration file to specify a ``run_prefix`` and/or ``run_suffix``
    injected before and/or after the spawned command. The result is treated as a shell command. This
    allows setting up environment variables, submitting the command to execute on a compute cluster,
    etc.
    """
    current = Invocation.current
    current.context['action_id'] = Invocation.actions_count

    prefix = current.config_param('run_prefix', [])
    suffix = current.config_param('run_suffix', [])
    if not prefix and not suffix:
        await spawn(*command, **resources)
        return

    parts = list(dp.each_string(*command))
    if prefix and parts and parts[0].startswith('@'):
        if parts[0] == '@':
            parts = parts[1:]
        else:
            parts[0] = parts[0][1:]

        if not prefix[0].startswith('@'):
            prefix[0] = '@' + prefix[0]

    prefix = [dp.phony(part.format(**current.context)) for part in dp.each_string(prefix)]
    wrapped = [dp.copy_annotations(part, shlex.quote(part)) for part in parts]
    suffix = [dp.phony(part.format(**current.context)) for part in dp.each_string(suffix)]

    await shell(*prefix, *wrapped, *suffix, **resources)


async def esubmit(*templates: Strings) -> None:
    """
    Similar to :py:func:`dynamake.make.submit`, but first :py:func:`dynamake.make.e`-xpands each
    parameter.

    That is, ``esubmit(...)`` is the same as ``submit(e(...))``.
    """
    await submit(e(*templates))


def context() -> Dict[str, Any]:
    """
    Access the context of the current build step.

    Modifications to this context will be visible in the context of steps invoked to create required
    input files.

    .. note::

        If a required file was already built (or started to be built) for another step, then it will
        use the original requiring step context.
    """
    return Invocation.current.context


def config_param(name: str, default: Any = None,
                 *, keep_in_file: bool = False, is_required: bool = False) -> Any:
    """
    Access the value of a parameter from the step-specific configuration.
    """
    return Invocation.current.config_param(name, default, keep_in_file=keep_in_file,
                                           is_required=is_required)


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
    Resources.available['jobs'] = Resources.total['jobs'] = int(Prog.get_parameter('jobs'))
    Make.failure_aborts_build = Prog.get_parameter('failure_aborts_build')
    Make.remove_stale_outputs = Prog.get_parameter('remove_stale_outputs')
    Make.wait_nfs_outputs = Prog.get_parameter('wait_nfs_outputs')
    Make.nfs_outputs_timeout = Prog.get_parameter('nfs_outputs_timeout')
    Make.touch_success_outputs = Prog.get_parameter('touch_success_outputs')
    Make.remove_failed_outputs = Prog.get_parameter('remove_failed_outputs')
    Make.remove_empty_directories = Prog.get_parameter('remove_empty_directories')
    Make.log_skipped_actions = Prog.get_parameter('log_skipped_actions')
    Make.rebuild_changed_actions = Prog.get_parameter('rebuild_changed_actions')


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
    group = Prog.add_global_parameters(parser)
    group.add_argument('--list_steps', '-ls', default=False, action='store_true',
                       help='List the build steps and their targets, and exit.')
    group.add_argument('--step_config', '-sc', metavar='FILE', action='append',
                       help='Load a step parameters configuration YAML file')

    Prog.add_sorted_parameters(parser, extra_help=_extra_parameter_help)
    args = parser.parse_args()
    Prog.parse_args(args)

    if os.path.exists(Make.DEFAULT_STEP_CONFIG):
        Config.load(Make.DEFAULT_STEP_CONFIG)
    for path in (args.step_config or []):
        Config.load(path)

    if adapter is not None:
        adapter(args)

    _collect_parameters()

    if args.list_steps:
        _list_steps()

    _build_targets([path for path in args.TARGET if path is not None]
                   or dp.flatten(default_targets))


def _build_targets(targets: List[str]) -> None:
    Prog.logger.log(Prog.TRACE, '%s - Targets: %s',
                    Invocation.top._log, ' '.join(targets))  # pylint: disable=protected-access
    if Prog.logger.isEnabledFor(logging.DEBUG):
        for value in Resources.available.values():
            if value > 0:
                Prog.logger.debug('%s - Available resources: %s',
                                  Invocation.top._log,  # pylint: disable=protected-access
                                  _dict_to_str(Resources.available))
                break
    # TODO: Switch to `asyncio.run(sync())` in Python 3.7.
    try:
        for target in targets:
            require(target)
        result: Optional[BaseException] = run(Invocation.top.sync())
    except StepException as exception:  # pylint: disable=broad-except
        result = exception

    if result is None:
        if Invocation.actions_count > 0:
            Prog.logger.log(Prog.TRACE, '%s - Done',
                            Invocation.top._log)  # pylint: disable=protected-access
        elif Invocation.skipped_count > 0:
            Prog.logger.log(Prog.TRACE, '%s - Skipped',
                            Invocation.top._log)  # pylint: disable=protected-access
        else:
            Prog.logger.log(Prog.TRACE, '%s - Complete',
                            Invocation.top._log)  # pylint: disable=protected-access
        if not Prog._is_test:  # pylint: disable=protected-access
            sys.exit(0)
    else:
        Prog.logger.error('%s - Fail', Invocation.top._log)  # pylint: disable=protected-access
        if not Prog._is_test:  # pylint: disable=protected-access
            sys.exit(1)
        raise result


def _list_steps() -> None:
    is_first = True
    for name, step_data in sorted(Step.by_name.items()):
        if not is_first:
            print()
        is_first = False
        doc = step_data.func.wrapped.__doc__
        if doc:
            print('# ' + dedent(doc).strip().replace('\n', '\n# '))
        print('%s:' % name)
        for output in sorted(step_data.output):
            properties = []
            if dp.is_exists(output):
                properties.append('exists')
            if dp.is_optional(output):
                properties.append('optional')
            if dp.is_phony(output):
                properties.append('phony')
            if dp.is_precious(output):
                properties.append('precious')
            if properties:
                print('- %s: %s' % (output, ', '.join(properties)))
            else:
                print('- %s' % output)
    if not Prog._is_test:  # pylint: disable=protected-access
        sys.exit(0)


# pylint: disable=function-redefined
# pylint: disable=missing-docstring,pointless-statement,multiple-statements,unused-argument

@overload
def e(string: str) -> str: ...  # pylint: disable=invalid-name


@overload
def e(not_string: NotString) -> List[str]: ...  # pylint: disable=invalid-name


@overload
def e(first: Strings, second: Strings,  # pylint: disable=invalid-name
      *strings: Strings) -> List[str]: ...


# pylint: enable=missing-docstring,pointless-statement,multiple-statements,unused-argument

def e(*strings: Any) -> Any:  # type: ignore # pylint: disable=invalid-name
    """
    Similar to :py:func:`dynamake.patterns.fmt` but automatically uses the named arguments
    of the current step.

    That is, ``dm.e(...)`` is the same as ``dm.fmt(dm.step_kwargs(), ...)``.
    """
    return fmt(step_kwargs(), *strings)

# pylint: enable=function-redefined


def eglob_capture(*patterns: Strings) -> Captured:
    """
    Similar to :py:func:`dynamake.patterns.glob_capture` but automatically uses the named arguments
    of the current step.

    That is, ``dm.eglob_capture(...)`` is the same as
    ``dm.glob_capture(*fmt_capture(dm.step_kwargs(), ...))``.
    """
    return glob_capture(fmt_capture(step_kwargs(), *patterns))


def eglob_paths(*patterns: Strings) -> List[str]:
    """
    Similar to :py:func:`dynamake.patterns.glob_paths` but automatically uses the named arguments of
    the current step.

    That is, ``dm.eglob_paths(...)`` is the same as ``dm.glob_paths*fmt_capture(dm.step_kwargs(),
    ...))``.
    """
    return glob_paths(fmt_capture(step_kwargs(), *patterns))


def eglob_fmt(pattern: str, *patterns: Strings) -> List[str]:
    """
    Similar to :py:func:`dynamake.patterns.glob_fmt` but automatically uses the named arguments of
    the current step.

    That is, ``dm.eglob_fmt(...)`` is the same as ``dm.glob_fmt(fmt_capture(dm.step_kwargs(),
    ...))``.
    """
    return glob_fmt(fmt_capture(step_kwargs(), pattern), fmt_capture(step_kwargs(), *patterns))


def eglob_extract(*patterns: Strings) -> List[Dict[str, Any]]:
    """
    Similar to :py:func:`dynamake.patterns.glob_extract` but automatically uses the named arguments
    of the current step.

    That is, ``dm.eglob_extract(...)`` is the same as
    ``dm.glob_extract(fmt_capture(dm.step_kwargs(), ...))``.
    """
    return glob_extract(fmt_capture(step_kwargs(), *patterns))


def step_kwargs() -> Dict[str, Any]:
    """
    Return the named arguments of the current step.

    These are the captured names extracted from the output file(s) that the current
    step was invoked to build.
    """
    return Invocation.current.kwargs


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
