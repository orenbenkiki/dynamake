"""
Dynamic make main code.
"""

# pylint: disable=too-many-lines

from .patterns import *  # pylint: disable=redefined-builtin,wildcard-import,unused-wildcard-import
from argparse import ArgumentParser
from argparse import Namespace
from datetime import datetime
from importlib import import_module
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

import argparse
import asyncio
import dynamake.patterns as dp
import logging
import os
import re
import shlex
import sys
import yaml

#: The log level for tracing calls.
TRACE = (2 * logging.DEBUG + logging.INFO) // 3

#: The log level for logging the reasons for action execution.
WHY = (logging.DEBUG + 2 * logging.INFO) // 3

logging.addLevelName(TRACE, 'TRACE')
logging.addLevelName(WHY, 'WHY')

#: A configured logger for the build process.
logger: logging.Logger

#: The default module to load for steps and parameter definitions.
DEFAULT_MODULE = 'DynaMake'

#: The default parameter configuration YAML file to load.
DEFAULT_CONFIG = 'DynaMake.yaml'

_is_test: bool = False


def _dict_to_str(values: Dict[str, Any]) -> str:
    return ','.join(['%s=%s' % (quote_plus(name), quote_plus(str(value)))
                     for name, value in sorted(values.items())])


class Parameter:  # pylint: disable=too-many-instance-attributes
    """
    Describe a configurable build parameter.
    """

    #: The current known parameters.
    by_name: Dict[str, 'Parameter']

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Parameter.by_name = {}

    def __init__(self, *, name: str, default: Any, parser: Callable[[str], Any], description: str,
                 short: Optional[str] = None, order: Optional[int] = None,
                 metavar: Optional[str] = None) -> None:
        """
        Create and register a parameter description.
        """

        #: The unique name of the parameter.
        self.name = name

        #: The unique short name of the parameter.
        self.short = short

        #: The value to use if the parameter is not explicitly configured.
        self.default = default

        #: How to parse the parameter value from a string (command line argument).
        self.parser = parser

        #: A description of the parameter for help messages.
        self.description = description

        #: Optional name of the command line parameter value (``metavar`` in ``argparse``).
        self.metavar = metavar

        #: Optional order of parameter (in help message)
        self.order = order

        #: The effective value of the parameter.
        self.value = default

        if name in Parameter.by_name:
            raise RuntimeError('Multiple definitions for the parameter: %s' % name)
        Parameter.by_name[name] = self

    @staticmethod
    def add_to_parser(parser: ArgumentParser) -> None:
        """
        Add a command line flag for each parameter to the parser to allow
        overriding parameter values directly from the command line.
        """
        parser.add_argument('--config', '-c', metavar='FILE', action='append',
                            help='Load a parameters configuration YAML file')
        parameters = [(parameter.order, parameter.name, parameter)
                      for parameter in Parameter.by_name.values()]
        for _, _, parameter in sorted(parameters):
            text = parameter.description.replace('%', '%%') + ' (default: %s)' % parameter.default
            if parameter.short:
                parser.add_argument('--' + parameter.name, '-' + parameter.short,
                                    help=text, metavar=parameter.metavar)
            else:
                parser.add_argument('--' + parameter.name, help=text, metavar=parameter.metavar)

    @staticmethod
    def parse_args(args: Namespace) -> None:
        """
        Update the values based on loaded configuration files and/or explicit
        command line flags.
        """
        if os.path.exists(DEFAULT_CONFIG):
            Parameter.load_config(DEFAULT_CONFIG)
        for path in (args.config or []):
            Parameter.load_config(path)

        for name, parameter in Parameter.by_name.items():
            value = vars(args).get(name)
            if value is not None:
                try:
                    parameter.value = parameter.parser(value)
                except BaseException:
                    raise RuntimeError(  # pylint: disable=raise-missing-from
                        'Invalid value: %s for the parameter: %s' % (vars(args)[name], name))

        name = sys.argv[0].split('/')[-1]

    @staticmethod
    def load_config(path: str) -> None:
        """
        Load a configuration file.
        """
        with open(path, 'r') as file:
            data = yaml.safe_load(file.read())

        if data is None:
            data = {}

        if not isinstance(data, dict):
            raise RuntimeError('The configuration file: %s '
                               'does not contain a top-level mapping' % path)

        for name, value in data.items():
            parameter = Parameter.by_name.get(name)
            if parameter is None:
                raise RuntimeError('Unknown parameter: %s '
                                   'specified in the configuration file: %s'
                                   % (name, path))

            if isinstance(value, str):
                try:
                    value = parameter.parser(value)
                except BaseException:
                    raise RuntimeError(  # pylint: disable=raise-missing-from
                        'Invalid value: %s '
                        'for the parameter: %s '
                        'specified in the configuration file: %s'
                        % (value, name, path))

            parameter.value = value


#: The number of jobs to run in parallel.
jobs: Parameter

#: The level of messages to log.
log_level: Parameter

#: Whether to log (level INFO) skipped actions (by default, ``False``).
log_skipped_actions: Parameter

#: Whether to rebuild outputs if the actions have changed (by default, ``True``).
rebuild_changed_actions: Parameter

#: The directory to keep persistent state in.
persistent_directory: Parameter

#: Whether to stop the script if any action fails (by default, ``True``).
#
#: If this is ``False``, then the build will continue to execute unrelated
#: actions. In all cases, actions that have already started will be allowed to
#: end normally.
failure_aborts_build: Parameter

#: Whether to remove old output files before executing an action (by default,
#: ``True``).
remove_stale_outputs: Parameter

# Whether to touch output files on a successful action to ensure they are newer
# than the input file(s) (by default, ``False``).
#
#: In these modern times, this is mostly unneeded as we use the nanosecond
#: modification time, which pretty much guarantees that output files will be
#: newer than input files. In the "bad old days", files created within a second
#: of each other had the same modification time, which would confuse the build
#: tools.
#
#: This might still be needed if an output is a directory (not a file) and
#: `remove_stale_outputs` is ``False``, since otherwise the ``mtime`` of an
#: existing directory will not necessarily be updated to reflect the fact the
#: action was executed. In general it is not advised to depend on the ``mtime``
#: of directories; it is better to specify a glob matching the expected files
#: inside them, or use an explicit timestamp file.
touch_success_outputs: Parameter

#: Whether to remove output files on a failing action (by default, ``True``).
remove_failed_outputs: Parameter

#: Whether to (try to) remove empty directories when deleting the last file in
#: them (by default, ``False``).
remove_empty_directories: Parameter


def _define_parameters() -> None:
    # pylint: disable=invalid-name

    global jobs
    jobs = Parameter(  #
        name='jobs',
        short='j',
        metavar='INT',
        default=-1,
        parser=dp.str2int(),
        description="""
            The number of jobs to run in parallel. Use 0 for unlimited
            parallelism, 1 for serial jobs execution, and a negative number for
            a fraction of the logical processors in the system (-1 for one per
            logical processor, -2 for one per two logical processors, etc.).
        """)

    global log_level
    log_level = Parameter(  #
        name='log_level',
        short='ll',
        metavar='STR',
        default='WARN',
        parser=str,
        description='The log level to use')

    global log_skipped_actions
    log_skipped_actions = Parameter(  #
        name='log_skipped_actions',
        short='lsa',
        metavar='BOOL',
        default=False,
        parser=dp.str2bool,
        description='Whether to log (level INFO) skipped actions')

    global rebuild_changed_actions
    rebuild_changed_actions = Parameter(  #
        name='rebuild_changed_actions',
        short='rca',
        metavar='BOOL',
        default=True,
        parser=dp.str2bool,
        description='Whether to rebuild outputs if the actions have changed')

    global persistent_directory
    persistent_directory = Parameter(  #
        name='persistent_directory',
        short='pp',
        metavar='STR',
        default='.dynamake',
        parser=str,
        description="""
            The directory to keep persistent data in, if
            rebuild_changed_actions is  True.
        """)

    global failure_aborts_build
    failure_aborts_build = Parameter(  #
        name='failure_aborts_build',
        short='fab',
        metavar='BOOL',
        default=True,
        parser=dp.str2bool,
        description='Whether to stop the script if any action fails')

    global remove_stale_outputs
    remove_stale_outputs = Parameter(  #
        name='remove_stale_outputs',
        short='dso',
        metavar='BOOL',
        default=True,
        parser=dp.str2bool,
        description='Whether to remove old output files before executing an action')

    global touch_success_outputs
    touch_success_outputs = Parameter(  #
        name='touch_success_outputs',
        short='tso',
        metavar='BOOL',
        default=False,
        parser=dp.str2bool,
        description="""
            Whether to touch output files on a successful action to ensure they
            are newer than the input file(s)
        """)

    global remove_failed_outputs
    remove_failed_outputs = Parameter(  #
        name='remove_failed_outputs',
        short='dfo',
        metavar='BOOL',
        default=True,
        parser=dp.str2bool,
        description='Whether to remove output files on a failing action')

    global remove_empty_directories
    remove_empty_directories = Parameter(  #
        name='remove_empty_directories',
        short='ded',
        metavar='BOOL',
        default=False,
        parser=dp.str2bool,
        description='Whether to remove empty directories when deleting the last file in them')
    # pylint: enable=invalid-name


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
        Resources.total = dict(jobs=os.cpu_count() or 1)
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

    Each resource should have been declared as a :py:class:`Parameter`.  The
    value given here is the default amount of the resource used by each action
    that does not specify an explicit value.
    """
    for name, amount in default_amounts.items():
        total = Resources.total.get(name)
        if total is None:
            parameter = Parameter.by_name.get(name)
            if parameter is None:
                raise RuntimeError('Unknown resource parameter: %s' % name)
            total = int(parameter.value)
            Resources.total[name] = total
            Resources.available[name] = total

        if amount > total:
            raise RuntimeError('The default amount: %s '
                               'of the resource: %s '
                               'is greater than the total amount: %s'
                               % (amount, name, total))

        Resources.default[name] = amount


class StepException(Exception):
    """
    Indicates a step has aborted and its output must not be used by other steps.
    """


class RestartException(Exception):
    """
    Indicates a step needs to be re-run, this time executing all actions.
    """


class Step:
    """
    A build step.
    """

    #: The current known steps.
    by_name: Dict[str, 'Step']

    #: The step for building any output capture pattern.
    by_regexp: List[Tuple[Pattern, 'Step']]

    _is_finalized: bool

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Step.by_name = {}
        Step.by_regexp = []
        Step._is_finalized = False

    def __init__(self, function: Callable, output: Strings, priority: int) -> None:
        """
        Register a build step function.
        """
        #: The wrapped function that implements the step.
        self.function = function

        while hasattr(function, '__func__'):
            function = getattr(function, '__func__')

        if Step._is_finalized:
            raise RuntimeError('Late registration of the step: %s.%s'
                               % (function.__module__, function.__qualname__))

        if not iscoroutinefunction(function):
            raise RuntimeError('The step function: %s.%s is not a coroutine'
                               % (function.__module__, function.__qualname__))

        #: The name of the step.
        self.name = function.__name__

        #: The outputs generated by the step.
        self.output: List[str] = []

        #: The priority allowing overriding steps.
        self.priority = priority

        for capture in dp.each_string(output):
            capture = clean_path(capture)
            self.output.append(capture)
            Step.by_regexp.append((capture2re(capture), self))

        if not self.output:
            raise RuntimeError('The step function: %s.%s specifies no output'
                               % (self.function.__module__, self.function.__qualname__))

        if self.name in Step.by_name:
            conflicting = Step.by_name[self.name].function
            raise RuntimeError('Conflicting definitions for the step: %s '
                               'in both: %s.%s '
                               'and: %s.%s'
                               % (self.name,
                                  conflicting.__module__, conflicting.__qualname__,
                                  function.__module__, function.__qualname__))
        Step.by_name[self.name] = self


class UpToDate:
    """
    Data for each up-to-date target.
    """

    def __init__(self, producer: str, mtime_ns: int = 0) -> None:
        """
        Record a new up-to-date target.
        """
        #: The step (and parameters) that updated the target.
        self.producer = producer

        #: The modified time of the target (in nanoseconds).
        #:
        #: This is negative until we know the correct time.
        self.mtime_ns = mtime_ns

    def into_data(self) -> Dict[str, Any]:
        """
        Serialize for dumping to YAML.
        """
        data = dict(producer=self.producer)
        if self.mtime_ns > 0:
            data['mtime'] = str(_datetime_from_nanoseconds(self.mtime_ns))
        return data

    @staticmethod
    def from_data(data: Dict[str, str]) -> 'UpToDate':
        """
        Load from YAML data.
        """
        producer = data['producer']
        mtime_str = data.get('mtime')
        if mtime_str is None:
            mtime_ns = 0
        else:
            mtime_ns = _nanoseconds_from_datetime_str(mtime_str)
        return UpToDate(producer, mtime_ns)


class PersistentAction:
    """
    An action taken during step execution.

    We can persist this to ensure the action taken in a future invocation is
    identical, to trigger rebuild if the list of actions changes.
    """

    def __init__(self, previous: Optional['PersistentAction'] = None) -> None:
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

    def run_action(self, command: List[str]) -> None:
        """
        Set the executed command of the action.
        """
        self.command = [word for word in command if not dp.is_phony(word)]
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
        return self.command is None and not self.required

    def into_data(self) -> List[Dict[str, Any]]:
        """
        Serialize for dumping to YAML.
        """
        if self.previous:
            data = self.previous.into_data()
        else:
            data = []

        datum: Dict[str, Any] = dict(required={name: up_to_date.into_data()
                                               for name, up_to_date in self.required.items()})

        if self.command is None:
            assert self.start is None
            assert self.end is None
        else:
            assert self.start is not None
            assert self.end is not None
            datum['command'] = self.command
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

        action.required = {name: UpToDate.from_data(up_to_date)
                           for name, up_to_date in datum['required'].items()}

        if 'command' in datum:
            action.command = datum['command']
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

        #: The full name (including parameters) of the invocation.
        self.name = 'make'
        if self.step is not None:
            self.name = self.step.name
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
        else:
            self.parent.sub_count += 1
            if self.parent.stack == '#0':
                self.stack = '#%s' % self.parent.sub_count
            else:
                self.stack = '%s.%s' % (self.parent.stack, self.parent.sub_count)

        if _is_test:  # pylint: disable=protected-access
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
        self.should_remove_stale_outputs = remove_stale_outputs.value

    def _restart(self) -> None:
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
        assert self.should_remove_stale_outputs == remove_stale_outputs.value

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
        path = os.path.join(persistent_directory.value, self.name + '.actions.yaml')
        if not os.path.exists(path):
            logger.log(WHY,
                       '%s - Must run actions because missing the persistent actions: %s',
                       self._log, path)
            self.must_run_action = True
            return

        try:
            with open(path, 'r') as file:
                data = yaml.full_load(file.read())
            self.old_persistent_actions = PersistentAction.from_data(data['actions'])
            self.old_persistent_outputs = data['outputs']
            logger.debug('%s - Read the persistent actions: %s', self._log, path)

        except BaseException:  # pylint: disable=broad-except
            logger.warning('%s - Must run actions '
                           'because read the invalid persistent actions: %s',
                           self._log, path)
            self.must_run_action = True

    def remove_old_persistent_data(self) -> None:
        """
        Remove the persistent data from the disk in case the build failed.
        """
        path = os.path.join(persistent_directory.value, self.name + '.actions.yaml')
        if os.path.exists(path):
            logger.debug('%s - Remove the persistent actions: %s', self._log, path)
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
        path = os.path.join(persistent_directory.value, self.name + '.actions.yaml')
        logger.debug('%s - Write the persistent actions: %s', self._log, path)

        os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(path, 'w') as file:
            data = dict(actions=self.new_persistent_actions[-1].into_data(),
                        outputs=self.built_outputs)
            file.write(yaml.dump(data))

    def log_and_abort(self, reason: str) -> None:
        """
        Abort the invocation for some reason.
        """
        logger.error(reason)
        return self.abort(reason)

    def abort(self, reason: str) -> None:
        """
        Abort the invocation for some reason.
        """
        self.exception = StepException(reason)
        if failure_aborts_build.value:
            raise self.exception

    def require(self, path: str) -> None:
        """
        Require a file to be up-to-date before executing any actions or completing the current
        invocation.
        """
        self._become_current()

        path = clean_path(path)

        logger.debug('%s - Build the required: %s', self._log, path)

        self.required.append(path)

        if path in Invocation.poisoned:
            self.abort('%s - The required: %s has failed to build' % (self._log, path))
            return

        up_to_date = Invocation.up_to_date.get(path)
        if up_to_date is not None:
            logger.debug('%s - The required: %s was built', self._log, path)
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
                    logger.debug('%s - The optional required: %s '
                                 "does not exist and can't be built", self._log, path)
                else:
                    self.log_and_abort("%s - Don't know how to make the required: %s"
                                       % (self._log, path))
                return
            logger.debug('%s - The required: %s is a source file', self._log, path)
            up_to_date = UpToDate('', stat.st_mtime_ns)
            Invocation.up_to_date[path] = up_to_date
            if self.new_persistent_actions:
                self.new_persistent_actions[-1].require(path, up_to_date)
            return

        invocation = Invocation(step, **kwargs)
        if self.new_persistent_actions:
            self.new_persistent_actions[-1].require(path, UpToDate(invocation.name))
        logger.debug('%s - The required: %s '
                     'will be produced by the spawned: %s',
                     self._log, path, invocation._log)  # pylint: disable=protected-access
        self.async_actions.append(asyncio.Task(invocation.run()))  # type: ignore

    def producer_of(self,  # pylint: disable=too-many-locals
                    path: str) -> Tuple[Optional[Step], Optional[Dict[str, Any]]]:
        """
        Find the unique step, if any, that produces the file.

        Also returns the keyword arguments needed to invoke the step function (deduced from the
        path).
        """
        kwargs: Dict[str, Any] = {}
        producer: Optional[Step] = None

        producers: List[Tuple[int, str, re.Match, Step]] = []

        for (regexp, step) in Step.by_regexp:  # pylint: disable=redefined-outer-name
            match = re.fullmatch(regexp, path)
            if not match:
                continue

            producers.append((-step.priority, step.name, match, step))

        producers = sorted(producers)

        if logger.isEnabledFor(logging.DEBUG) and len(producers) > 1:
            for _, _, _, candidate in producers:
                logger.debug('%s - candidate producer: %s priority: %s',
                             self._log, candidate.name, candidate.priority)

        if len(producers) > 1:
            first_priority, first_name, _, _ = producers[0]
            second_priority, second_name, _, _ = producers[1]

            if second_priority == first_priority:
                self.log_and_abort('the output: %s '
                                   'may be created by both the step: %s '
                                   'and the step: %s '
                                   'at the same priority: %s'
                                   % (path, first_name, second_name, first_priority))
                return None, None

        if len(producers) > 0:
            _, _, match, producer = producers[0]
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
        logger.log(TRACE, '%s - Call', self._log)

        if rebuild_changed_actions.value:
            self.new_persistent_actions.append(PersistentAction())
            self.read_old_persistent_actions()

        assert self.name not in Invocation.active
        Invocation.active[self.name] = self
        self.collect_initial_outputs()

        try:
            assert self.step is not None
            try:
                await self.done(self.step.function(**self.kwargs))
            except RestartException:
                self._restart()
                await self.done(self.step.function(**self.kwargs))
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
                    logger.warning('%s - Skipped some action(s) '
                                   'even though it has changed to remove some final action(s)',
                                   self._log)

            if self.did_run_actions:
                logger.log(TRACE, '%s - Done', self._log)
            elif self.did_skip_actions:
                logger.log(TRACE, '%s - Skipped', self._log)
            else:
                logger.log(TRACE, '%s - Complete', self._log)

        else:
            while self.async_actions:
                try:
                    await self.done(self.async_actions.pop())
                except StepException:
                    pass
            self.poison_all_outputs()
            self.remove_old_persistent_data()
            logger.log(TRACE, '%s - Fail', self._log)

        del Invocation.active[self.name]
        if self.condition is not None:
            await self.done(self.condition.acquire())
            self.condition.notify_all()
            self.condition.release()

        if self.exception is not None and failure_aborts_build.value:
            raise self.exception

        return self.exception

    async def wait_for(self, active: 'Invocation') -> Optional[BaseException]:
        """
        Wait until the invocation is done.

        This is used by other invocations that use this invocation's output(s) as their input(s).
        """
        self._become_current()

        logger.debug('%s - Paused by waiting for: %s',
                     self._log, active._log)  # pylint: disable=protected-access

        if active.condition is None:
            active.condition = asyncio.Condition()

        await self.done(active.condition.acquire())
        await self.done(active.condition.wait())
        active.condition.release()

        logger.debug('%s - Resumed by completion of: %s',
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
                    logger.debug('%s - Nonexistent optional output(s): %s',
                                 self._log, pattern)
                else:
                    for path in paths:
                        self.initial_outputs.append(path)
                        if path == pattern:
                            logger.debug('%s - Existing output: %s', self._log, path)
                        else:
                            logger.debug('%s - Existing output: %s -> %s',
                                         self._log, pattern, path)
            except dp.NonOptionalException:
                logger.debug('%s - Nonexistent required output(s): %s',
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
                    logger.debug('%s - Changed to abandon the output: %s', self._log, path)
                    self.abandoned_output = path
                else:
                    logger.debug('%s - Missing the old built output: %s', self._log, path)
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

        if logger.isEnabledFor(logging.DEBUG) and self.oldest_output_path is not None:
            logger.debug('%s - Oldest output: %s time: %s',
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

        for pattern in sorted(self.step.output):  # pylint: disable=too-many-nested-blocks
            formatted_pattern = dp.fmt_capture(self.kwargs, pattern)
            if dp.is_phony(pattern):
                Invocation.up_to_date[formatted_pattern] = \
                    UpToDate(self.name, self.newest_input_mtime_ns + 1)
                continue

            try:
                paths = dp.glob_paths(formatted_pattern)
                if not paths:
                    logger.debug('%s - Did not make the optional output(s): %s',
                                 self._log, pattern)
                else:
                    for path in paths:
                        self.built_outputs.append(path)

                        if touch_success_outputs.value:
                            if not did_sleep:
                                await self.done(asyncio.sleep(1.0))
                                did_sleep = True
                            logger.debug('%s - Touch the output: %s', self._log, path)
                            Stat.touch(path)

                        mtime_ns = Stat.stat(path).st_mtime_ns
                        Invocation.up_to_date[path] = UpToDate(self.name, mtime_ns)

                        if logger.isEnabledFor(logging.DEBUG):
                            if path == formatted_pattern:
                                logger.debug('%s - Has the output: %s time: %s',
                                             self._log, path,
                                             _datetime_from_nanoseconds(mtime_ns))
                            else:
                                logger.debug('%s - Has the output: %s -> %s time: %s',
                                             self._log, pattern, path,
                                             _datetime_from_nanoseconds(mtime_ns))

            except dp.NonOptionalException:
                self._become_current()
                logger.error('%s - Missing the output(s): %s', self._log, pattern)
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
                logger.debug('%s - Remove the stale output: %s', self._log, path)
                self.remove_output(path)
            else:
                Stat.forget(path)

        self.should_remove_stale_outputs = False

    def remove_output(self, path: str) -> None:
        """
        Remove an output file, and possibly the directories that became empty as a result.
        """
        try:
            Stat.remove(path)
            while remove_empty_directories.value:
                path = os.path.dirname(path)
                Stat.rmdir(path)
                logger.debug('%s - Remove the empty directory: %s', self._log, path)
        except OSError:
            pass

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
                if remove_failed_outputs.value and not is_precious(path):
                    logger.debug('%s - Remove the failed output: %s', self._log, path)
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
            logger.log(WHY, '%s - Must run actions to satisfy the phony output: %s',
                            self._log, self.phony_outputs[0])
            return True

        if self.missing_output is not None:
            logger.log(WHY,
                       '%s - Must run actions to create the missing output(s): %s',
                       self._log, self.missing_output)
            return True

        if self.abandoned_output is not None:
            logger.log(WHY,
                       '%s - Must run actions since it has changed to abandon the output: %s',
                       self._log, self.abandoned_output)
            return True

        if self.new_persistent_actions:
            # Compare with last successful build action.
            index = len(self.new_persistent_actions) - 1
            if index >= len(self.old_persistent_actions):
                logger.log(WHY,
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
            logger.debug('%s - Can skip actions '
                         'because all the outputs exist and there are no newer inputs',
                         self._log)
            return False

        # There are input files:

        if self.oldest_output_path is not None \
                and self.oldest_output_mtime_ns <= self.newest_input_mtime_ns:
            # Some output file is not newer than some input file.
            logger.log(WHY,
                       '%s - Must run actions '
                       'because the output: %s '
                       'is not newer than the input: %s',
                       self._log, self.oldest_output_path,
                       self.newest_input_path)
            return True

        # All output files are newer than all input files.
        logger.debug('%s - Can skip actions '
                     'because all the outputs exist and are newer than all the inputs',
                     self._log)
        return False

    def different_actions(self, old_action: PersistentAction, new_action: PersistentAction) -> bool:
        """
        Check whether the new action is different from the last build action.
        """
        if self.different_required(old_action.required, new_action.required):
            return True

        if old_action.command != new_action.command:
            if old_action.command is None:
                old_action_kind = 'a phony command'
            else:
                old_action_kind = 'the command: %s' % ' '.join(old_action.command)

            if new_action.command is None:
                new_action_kind = 'a phony command'
            else:
                new_action_kind = 'the command: %s' % ' '.join(new_action.command)

            logger.log(WHY,
                       '%s - Must run actions '
                       'because it has changed %s into %s',
                       self._log, old_action_kind, new_action_kind)
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
                logger.log(WHY,
                           '%s - Must run actions because it has changed to require: %s',
                           self._log, new_path)
                return True

        for old_path in sorted(old_required.keys()):
            if old_path not in new_required:
                logger.log(WHY,
                           '%s - Must run actions because it has changed to not require: %s',
                           self._log, old_path)
                return True

        for path in sorted(new_required.keys()):
            old_up_to_date = old_required[path]
            new_up_to_date = new_required[path]
            if old_up_to_date.producer != new_up_to_date.producer:
                logger.log(WHY,
                           '%s - Must run actions '
                           'because the producer of the required: %s '
                           'has changed from: %s into: %s',
                           self._log, path,
                           (old_up_to_date.producer or 'source file'),
                           (new_up_to_date.producer or 'source file'))
                return True
            if not is_exists(path) and old_up_to_date.mtime_ns != new_up_to_date.mtime_ns:
                logger.log(WHY,
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
            logger.debug("%s - Can't run: %s", self._log, log_command)
            raise self.exception

        if self.new_persistent_actions:
            self.new_persistent_actions[-1].run_action(persistent_parts)

        if not self.should_run_action():
            if log_skipped_actions.value and not is_silent:
                logger.info('%s - Skip: %s', self._log, log_command)
            else:
                logger.debug('%s - Skip: %s', self._log, log_command)
            self.did_skip_actions = True
            if self.new_persistent_actions:
                self.new_persistent_actions.append(  #
                    PersistentAction(self.new_persistent_actions[-1]))
            Invocation.skipped_count += 1
            return

        if self.did_skip_actions:
            self.must_run_action = True
            logger.debug('Must restart step to run skipped action(s)')
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
                logger.debug('%s - Run: %s', self._log, log_command)
            else:
                logger.info('%s - Run: %s', self._log, log_command)

            sub_process = await self.done(runner(*run_parts))
            exit_status = await self.done(sub_process.wait())

            if self.new_persistent_actions:
                persistent_action = self.new_persistent_actions[-1]
                persistent_action.done_action()
                self.new_persistent_actions.append(PersistentAction(persistent_action))

            if exit_status != 0:
                self.log_and_abort('%s - Failure: %s' % (self._log, log_command))
                return

            logger.log(TRACE, '%s - Success: %s', self._log, log_command)
        finally:
            self._become_current()
            if resources:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug('%s - Free resources: %s',
                                 self._log, _dict_to_str(resources))
                Resources.free(resources)
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug('%s - Available resources: %s',
                                 self._log, _dict_to_str(Resources.available))
                await self.done(Resources.condition.acquire())
                Resources.condition.notify_all()
                Resources.condition.release()

    async def _use_resources(self, amounts: Dict[str, int]) -> None:
        self._become_current()

        while True:
            if Resources.have(amounts):
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug('%s - Grab resources: %s',
                                 self._log, _dict_to_str(amounts))
                Resources.grab(amounts)
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug('%s - Available resources: %s',
                                 self._log, _dict_to_str(Resources.available))
                return

            if logger.isEnabledFor(logging.DEBUG):
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug('%s - Available resources: %s',
                                 self._log, _dict_to_str(Resources.available))
                    logger.debug('%s - Paused by waiting for resources: %s',
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
            logger.debug('%s - Sync', self._log)
            results: List[Optional[StepException]] = \
                await self.done(asyncio.gather(*self.async_actions))
            if self.exception is None:
                for exception in results:
                    if exception is not None:
                        self.exception = exception
                        break
            self.async_actions = []

        logger.debug('%s - Synced', self._log)

        failed_inputs = False
        for path in sorted(self.required):
            if path in Invocation.poisoned \
                    or (not dp.is_optional(path) and path not in Invocation.up_to_date):
                if self.exception is None:
                    level = logging.ERROR
                else:
                    level = logging.DEBUG
                logger.log(level, '%s - The required: %s has failed to build',
                           self._log, path)
                Invocation.poisoned.add(path)
                failed_inputs = True
                continue

            if path not in Invocation.up_to_date:
                assert dp.is_optional(path)
                continue

            logger.debug('%s - Has the required: %s', self._log, path)

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

        if self.exception is not None:
            return self.exception

        for action in self.new_persistent_actions:
            for name, partial_up_to_date in action.required.items():
                full_up_to_date = Invocation.up_to_date.get(name)
                if full_up_to_date is None:
                    partial_up_to_date.mtime_ns = 0
                else:
                    assert full_up_to_date.producer == partial_up_to_date.producer
                    partial_up_to_date.mtime_ns = full_up_to_date.mtime_ns

        if logger.isEnabledFor(logging.DEBUG) and self.oldest_output_path is not None:
            if self.newest_input_path is None:
                logger.debug('%s - No inputs', self._log)
            else:
                logger.debug('%s - Newest input: %s time: %s',
                             self._log, self.newest_input_path,
                             _datetime_from_nanoseconds(self.newest_input_mtime_ns))

        return None

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
    if not _is_test:  # pylint: disable=protected-access
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
    if _is_test:  # pylint: disable=protected-access
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


def step(output: Strings, priority: int = 0) -> Callable[[Callable], Callable]:
    """
    Decorate a build step functions.

    If ``top`` is ``True``, this is a top-level step that can be directly invoked from the main
    function.
    """
    def _wrap(wrapped: Callable) -> Callable:
        Step(wrapped, output, priority)
        return wrapped
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


async def eshell(*templates: Strings, **resources: int) -> None:
    """
    Similar to :py:func:`dynamake.make.shell`, but first :py:func:`dynamake.make.e`-xpands each
    parameter.

    That is, ``eshell(...)`` is the same as ``shell(e(...))``.
    """
    await shell(e(*templates), **resources)


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


async def espawn(*templates: Strings, **resources: int) -> None:
    """
    Similar to :py:func:`dynamake.make.spawn`, but first :py:func:`dynamake.make.e`-xpands each
    parameter.

    That is, ``espawn(...)`` is the same as ``spawn(e(...))``.
    """
    await spawn(e(*templates), **resources)


def log_prefix() -> str:
    """
    A prefix for log messages.
    """
    return Invocation.current._log  # pylint: disable=protected-access


def make(parser: ArgumentParser, *,
         default_targets: Strings = 'all',
         logger_name: str = 'dynamake',
         adapter: Optional[Callable[[Namespace], None]] = None) -> None:
    """
    A generic ``main`` function for ``DynaMake``.

    If no explicit targets are given, will build the ``default_targets``
    (default: ``all``).

    Uses the ``logger_name`` (default: ``dynamake``) to create the global
    logger.

    The optional ``adapter`` may perform additional adaptation of the execution
    environment based on the parsed command-line arguments before the actual
    function(s) are invoked.
    """
    default_targets = dp.flatten(default_targets)

    _load_modules()

    parser.add_argument('TARGET', nargs='*',
                        help='The file or target to make (default: %s)' % ' '.join(default_targets))

    parser.add_argument('--module', '-m', metavar='MODULE', action='append',
                        help='A Python module to load (containing function definitions)')

    Parameter.add_to_parser(parser)

    parser.add_argument('--list_steps', '-ls', default=False, action='store_true',
                        help='List all the build steps and their targets, and exit.')

    args = parser.parse_args()
    Parameter.parse_args(args)

    _setup_logging(logger_name)

    if adapter is not None:
        adapter(args)

    _compute_jobs()

    if args.list_steps:
        _list_steps()
    else:
        _build_targets([path for path in args.TARGET if path is not None]
                       or dp.flatten(default_targets))


def _load_modules() -> None:
    # TODO: This needs to be done before we set up the command line options
    # parser, because the options depend on the loaded modules. Catch-22. This
    # therefore employs a brutish option detection which may not be 100% correct.
    did_import = False
    for option, value in zip(sys.argv, sys.argv[1:]):
        if option in ['-m', '--module']:
            did_import = True
            import_module(value)
    if not did_import and os.path.exists(DEFAULT_MODULE + '.py'):
        import_module(DEFAULT_MODULE)


def _setup_logging(logger_name: str) -> None:
    global logger  # pylint: disable=invalid-name
    logger = logging.getLogger(logger_name)
    logging.getLogger('asyncio').setLevel('WARN')

    if not _is_test:
        handler = logging.StreamHandler(sys.stderr)
        log_format = '%(asctime)s - dynamake - %(levelname)s - %(message)s'
        handler.setFormatter(LoggingFormatter(log_format))
        logger.addHandler(handler)

    logger.setLevel(log_level.value)


def _compute_jobs() -> None:
    if jobs.value < 0:
        jobs.value = (os.cpu_count() or 1) // -jobs.value
        if jobs.value < 1:
            jobs.value = 1
    Resources.available['jobs'] = Resources.total['jobs'] = jobs.value


def _list_steps() -> None:
    is_first = True
    steps = [(step.priority, step.name, step) for step in Step.by_name.values()]
    for _, _, step in sorted(steps):  # pylint: disable=redefined-outer-name
        if not is_first:
            print()
        is_first = False

        doc = step.function.__doc__
        if doc:
            print('# ' + dedent(doc).strip().replace('\n', '\n# '))
        print('%s:' % step.name)
        print('  priority: %s' % step.priority)
        print('  outputs:')
        for output in sorted(step.output):
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
                print('  - %s: %s' % (output, ', '.join(properties)))
            else:
                print('  - %s' % output)


def _build_targets(targets: List[str]) -> None:
    logger.log(TRACE, '%s - Targets: %s',
               Invocation.top._log, ' '.join(targets))  # pylint: disable=protected-access
    if logger.isEnabledFor(logging.DEBUG):
        for value in Resources.available.values():
            if value > 0:
                logger.debug('%s - Available resources: %s',
                             Invocation.top._log,  # pylint: disable=protected-access
                             _dict_to_str(Resources.available))
                break
    try:
        for target in targets:
            require(target)
        result: Optional[BaseException] = \
            asyncio.get_event_loop().run_until_complete(Invocation.top.sync())
    except StepException as exception:  # pylint: disable=broad-except
        result = exception

    if result is not None:
        logger.error('%s - Fail', Invocation.top._log)  # pylint: disable=protected-access
        if _is_test:  # pylint: disable=protected-access
            raise result
        sys.exit(1)

    if Invocation.actions_count > 0:
        logger.log(TRACE, '%s - Done',
                   Invocation.top._log)  # pylint: disable=protected-access
    elif Invocation.skipped_count > 0:
        logger.log(TRACE, '%s - Skipped',
                   Invocation.top._log)  # pylint: disable=protected-access
    else:
        logger.log(TRACE, '%s - Complete',
                   Invocation.top._log)  # pylint: disable=protected-access


def reset_make(is_test: bool = False, reset_test_times: bool = False) -> None:
    """
    Reset all the current state, for tests.
    """
    Parameter.reset()
    _define_parameters()

    Resources.reset()
    Step.reset()
    Invocation.reset()
    Stat.reset()

    if is_test:
        global _is_test, logger  # pylint: disable=invalid-name
        _is_test = True
        logger = logging.getLogger('dynamake')
        logger.setLevel('DEBUG')
        logging.getLogger('asyncio').setLevel('WARN')

    if reset_test_times:
        _reset_test_dates()


reset_make()


def main() -> None:
    """
    Universal main function for invoking DynaMake steps.
    """
    make(argparse.ArgumentParser(description='Build some target(s) using DynaMake.'),
         logger_name=sys.argv[0])


if __name__ == '__main__':
    main()

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
