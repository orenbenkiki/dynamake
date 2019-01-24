"""
Utilities for dynamic make.
"""

# pylint: disable=too-many-lines

import argparse
import logging
import os
import shutil
import subprocess
import sys
import threading
from abc import abstractmethod
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from importlib import import_module
from inspect import Parameter
from inspect import signature
from textwrap import dedent
from threading import Condition
from time import sleep
from types import SimpleNamespace
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterator
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
from typing import TypeVar
from typing import Union

import yaml

import dynamake.patterns as dp

from .config import Config
from .config import Rule
from .patterns import Captured
from .patterns import Strings
from .patterns import exists  # pylint: disable=unused-import
from .patterns import optional  # pylint: disable=unused-import
from .patterns import precious  # pylint: disable=unused-import

#: The type of a wrapped function.
Wrapped = TypeVar('Wrapped', bound=Callable)


class Make:  # pylint: disable=too-many-instance-attributes
    """
    Global build state.
    """

    #: The default configuration file path.
    FILE: str

    #: The executor for parallel steps.
    executor: ThreadPoolExecutor

    #: A condition variable for synchronizing access to the available resources.
    condition: Condition

    #: The amount of available resources for restricting parallel actions.
    available_resources: Dict[str, float]

    #: The amount of resources currently being used by parallel actions.
    used_resources: Dict[str, float]

    #: The number of parallel actions currently being executed.
    parallel_actions: int

    #: Known (wrapped) step functions.
    step_by_name: Dict[str, Callable]

    #: The logger for tracking the build flow.
    logger: logging.Logger

    #: Whether to delete old output files before executing an action (by default, ``True``).
    #:
    #: It is possible to override this on a per-action basis.
    delete_stale_outputs: bool

    #: Whether to touch output files on a successful action to ensure they are newer than
    #: the input file(s) (by default, ``False``).
    #:
    #: This might be needed if an output is a directory and
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

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Make.FILE = os.getenv('DYNAMAKE_CONFIG_FILE', 'Config.yaml')
        Make.executor = ThreadPoolExecutor(thread_name_prefix='MakeThread')
        Make.condition = Condition()
        Make.available_resources = {
            'steps': Make.executor._max_workers  # type: ignore # pylint: disable=protected-access
        }
        Make.used_resources = {'steps': 0}
        Make.parallel_actions = 0
        Make.step_by_name = {}
        Make.logger = logging.getLogger('dynamake')
        Make.delete_stale_outputs = True
        Make.touch_success_outputs = False
        Make.delete_failed_outputs = True
        Make.delete_empty_directories = False


class Step:  # pylint: disable=too-many-instance-attributes
    """
    A single step function execution state.
    """

    _thread_local: threading.local

    @staticmethod
    def current() -> 'Step':
        """
        Acccess the current step.
        """
        return getattr(Step._thread_local, 'current')

    @staticmethod
    def set_current(step: 'Step') -> None:
        """
        Set the current step for the current thread.
        """
        setattr(Step._thread_local, 'current', step)

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Step._thread_local = threading.local()
        Step.set_current(Planner(None, None, (), {}))

    @staticmethod
    def call_in_parallel(parent: 'Step', step: Callable, *args: Any, **kwargs: Any) -> Any:
        """
        Invoke a step inside a parallel thread.
        """
        Step.set_current(parent)
        return step(*args, **kwargs)

    @staticmethod
    def call_current(make: type, function: Callable,
                     args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Any:
        """
        Invoke the specified build step function.
        """
        parent = Step.current()
        step = make(parent, function, args, kwargs)
        Step.set_current(step)
        try:
            return step.call()
        finally:
            Step.set_current(parent)

    def __init__(self, parent: Optional['Step'],  # pylint: disable=too-many-branches
                 function: Optional[Callable],
                 args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> None:
        """
        Create a new tested build state.
        """

        #: The step function implementation.
        self.function = function or (lambda: None)

        #: The positional invocation arguments.
        self.args = args

        #: The named invocation arguments.
        self.kwargs = kwargs

        #: The name of the current step function.
        self.name = '' if function is None else function.__name__

        #: The caller invocation.
        self.parent: Step

        #: The ``/``-separated call stack.
        self.stack: str

        #: The current known parameters (expandable wildcards).
        self.wildcards = kwargs.copy()

        #: The used configuration values for the step, if configuration is used.
        self.used_params: Set[str] = set()

        #: The path name of the generated configuration file, if used.
        self.config_path: Optional[str] = None

        #: The parent step of this one.
        if parent is None:
            self.parent = self
            self.stack = '/'
            self.wildcards['step'] = '/'
            self.wildcards['stack'] = '/'
            assert isinstance(self, Planner)
        else:
            self.parent = parent
            if parent.stack == '/':
                self.stack = parent.stack + self.name
            else:
                self.stack = parent.stack + '/' + self.name

        self.wildcards['step'] = self.name
        self.wildcards['stack'] = self.stack

        #: The configuration values for the step.
        self.config_values = Config.values_for_context(self.wildcards)

        if isinstance(parent, Agent):
            raise RuntimeError('The nested step: %s.%s '
                               'is invoked from an action step: %s.%s'
                               % (self.function.__module__, self.function.__qualname__,
                                  parent.function.__module__, parent.function.__qualname__))

        if function is None:
            return

        specified_names = set(kwargs.keys())
        positional_argument_names = getattr(function, '_dynamake_positional_argument_names')
        for name, arg in zip(positional_argument_names, args):
            specified_names.add(name)
            self.wildcards[name] = arg

        environment_argument_names = getattr(function, '_dynamake_environment_argument_names')
        for name in environment_argument_names:
            if name not in specified_names:
                step = self
                while True:
                    if name in step.wildcards:
                        specified_names.add(name)
                        self.wildcards[name] = self.kwargs[name] = step.wildcards[name]
                        break
                    if step.stack == '/':
                        break
                    step = step.parent
            if name not in specified_names:
                raise RuntimeError('Missing value for the parameter: %s of the step: %s'
                                   % (name, self.stack))

    def config_param(self, name: str, default: Any) -> Any:
        """
        Access the value of a parameter for this step.
        """
        self.used_params.add(name)
        if name in self.config_values:
            return self.config_values[name]
        return self.config_values.get(name + '?', default)

    def config_file(self) -> str:
        """
        Return the path name of the generated configuration file for this step.
        """
        if self.config_path is None:
            self.config_path = Config.path_for_context(self.wildcards)
            config_text = yaml.dump(self.config_values)

            disk_text: Optional[str] = None
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r') as file:
                    disk_text = file.read()

            if disk_text == config_text:
                Make.logger.debug('%s: use existing config: %s',
                                  self.stack, self.config_path)
            else:
                if not os.path.exists(Config.DIRECTORY):
                    os.mkdir(Config.DIRECTORY)
                Make.logger.debug('%s: write new config: %s',
                                  self.stack, self.config_path)
                with open(self.config_path, 'w') as file:
                    file.write(config_text)

        return self.config_path

    def call(self) -> Any:
        """
        Invoke the step function.
        """
        result = self._call()
        if self.config_path is None:
            for parameter_name in self.config_values:
                if not parameter_name.endswith('?') and parameter_name not in self.used_params:
                    raise RuntimeError('Unused configuration parameter: %s '
                                       'for the step: %s'
                                       % (parameter_name, self.stack))
        return result

    @abstractmethod
    def _call(self) -> Any:
        """
        Actually invoke the step function.
        """


class Planner(Step):
    """
    Implement a planning step.
    """

    def _call(self) -> Any:
        return self.function(*self.args, **self.kwargs)


class Agent(Step):
    """
    Implement an action step.
    """

    def _call(self) -> Any:
        result = self.function(*self.args, **self.kwargs)
        if isinstance(result, Action) and result.needs_to_execute:
            with _request_resources(result.resources):
                result.call()
        return result


def available_resources(**kwargs: float) -> None:
    """
    Declare resources for restricting parallel action execution.

    This should be done before invoking the top-level step function.

    Later invocations override values specified by earlier invocations.
    """
    for name, amount in kwargs.items():
        Make.available_resources[name] = amount
        Make.used_resources[name] = 0


@contextmanager
def _request_resources(resources: Dict[str, float]) -> Iterator[None]:
    stack = Step.current().stack
    for name, amount in resources.items():
        if name not in Make.available_resources:
            raise RuntimeError('Unknown resource: %s '
                               'requested by the step: %s'
                               % (name, stack))
        if amount < 0:
            raise RuntimeError('Negative amount: %s '
                               'requested for the resource: %s '
                               'by the step: %s'
                               % (amount, name, stack))

    with Make.condition:
        while Make.parallel_actions > 0 and not _has_resources(stack, resources):
            Make.condition.wait()
        assert Make.parallel_actions >= 0
        Make.parallel_actions += 1
        _use_resources(stack, resources)

    try:
        yield
    finally:
        with Make.condition:
            _free_resources(stack, resources)
            Make.parallel_actions -= 1
            assert Make.parallel_actions >= 0
            Make.condition.notify_all()


def _has_resources(stack: str, resources: Dict[str, float]) -> bool:
    for name, amount in resources.items():
        remaining = Make.available_resources[name] - Make.used_resources[name]
        if amount > remaining:
            Make.logger.debug('%s: waiting for resource: %s amount: %s remaining: %s',
                              stack, name, amount, remaining)
            return False
    return True


def _use_resources(stack: str, resources: Dict[str, float]) -> None:
    for name, amount in resources.items():
        if amount > Make.available_resources[name]:
            Make.logger.warn('%s: requested resource: %s amount: %s is more than available: %s',
                             stack, name, amount, Make.available_resources[name])
            amount = Make.available_resources[name]

        Make.used_resources[name] += amount
        Make.logger.debug('%s: use resource: %s amount: %s remaining: %s',
                          stack, name, amount,
                          Make.available_resources[name] - Make.used_resources[name])
        assert 0 <= Make.used_resources[name] <= Make.available_resources[name]


def _free_resources(stack: str, resources: Dict[str, float]) -> None:
    for name, amount in resources.items():
        if amount <= Make.available_resources[name]:
            Make.used_resources[name] -= amount
        else:
            Make.used_resources[name] = 0
        Make.logger.debug('%s: free resource: %s amount: %s remaining: %s',
                          stack, name, amount,
                          Make.available_resources[name] - Make.used_resources[name])
        assert 0 <= Make.used_resources[name] <= Make.available_resources[name]


class Action(SimpleNamespace):  # pylint: disable=too-many-instance-attributes
    """
    An atomic executable action invoking external programs.
    """

    def __init__(self, *, input: Strings,  # pylint: disable=redefined-builtin,too-many-locals
                 output: Strings, run: Strings,
                 runner: Strings = None,
                 ignore_exit_status: bool = False,
                 delete_stale_outputs: Optional[bool] = None,
                 touch_success_outputs: Optional[bool] = None,
                 delete_failed_outputs: Optional[bool] = None,
                 delete_empty_directories: Optional[bool] = None,
                 resources: Optional[Dict[str, Any]] = None,
                 **kwargs: Any) -> None:
        """
        Create (but not execute yet) an action.

        Parameters
        ----------
        input
            The path names of the input file(s) that will be read by the external command.
            These are automatically :py:func:`dynamake.make.glob`-ed before the action is
            executed.
        output
            The path names of the output file(s) that will be read by the external command.
            These are automatically :py:func:`dynamake.make.glob`-ed twice; first before the
            action is executed, to check whether the existing outputs are
            :py:func:`dynamake.make.Action.is_up_to_date`, and then after the action was executed,
            to detect the actual possibly dynamic list of actual outputs.
        run
            If the 1st element is a string, this is the shell command to execute. Otherwise, this is
            a list of such commands. All strings pass through :py:func:`dynamake.make.expand`, where
            the available wildcards also include the (expanded) ``input`` list and the unexpanded
            ``output`` list.
        runner
            If not specified, taken from :py:func:`dynamake.make.config_param` with a default value
            of ``[]``.

            If empty, ``run`` commands are just the name of a program followed by the command line
            arguments. There is no issue of quoting.

            If this ``shell``, then ``run`` commands are arbitrary shell commands, including
            pipelines, redirections, wildcard expansions, etc. Quoting unsafe values is the
            responsibility of the caller.

            Otherwise, the ``runner`` is a prefix added before each ``run`` command. This is a
            convenient way to submit commands to clusters (e.g., ``runner=['qsub', ...]``).
        ignore_exit_status
            If ``True``, the exit status of the command(s) is ignored. Otherwise, if it is not zero,
            the action is considered a failure.
        delete_stale_outputs
            Optional override for :py:attr:`dynamake.make.Make.delete_stale_outputs` for this
            action.
        touch_success_outputs
            Optional override for :py:attr:`dynamake.make.Make.touch_success_outputs` for this
            action.
        delete_failed_outputs
            Optional override for :py:attr:`dynamake.make.Make.delete_failed_outputs` for this
            action.
        delete_empty_directories
            Optional override for :py:attr:`dynamake.make.Make.delete_empty_directories` for this
            action.
        resources
            Optional resources to restrict parallel action execution. Specified
            resources must be pre-declared via :py:func:`dynamake.resource`.

            A default value ``1`` is given to the resource ``steps``. The default number of
            available ``steps`` is the maximal number of workers provided by the
            :py:attr:`dynamake.make.executor`. This can be overriden using
            :py:func:dynamake.make.available_resources` to restrict the maximal number of concurrent
            steps.
        kwargs
            Any additional named parameters are injected into the action object
            (``SimpleNamespace``). This makes it easy to return additional values from an action
            step, to be used by the caller plan step.
        """
        super().__init__(**kwargs)

        #: Whether to delete out-of-date outputs.
        self.delete_stale_outputs = \
            Make.delete_stale_outputs if delete_stale_outputs is None else delete_stale_outputs

        #: Whether to touch outputs if the execution succeeds.
        self.touch_success_outputs = \
            Make.touch_success_outputs if touch_success_outputs is None else touch_success_outputs

        #: Whether to delete outputs if the execution fails.
        self.delete_failed_outputs = \
            Make.delete_failed_outputs if delete_failed_outputs is None else delete_failed_outputs

        #: Whether to delete outputs if the execution fails.
        self.delete_empty_directories = \
            Make.delete_empty_directories if delete_empty_directories is None \
            else delete_empty_directories

        #: The resources needed by the action, to restrict parallel action execution.
        self.resources = {'steps': 1.0}
        self.resources.update(resources or {})
        self.resources.update(config_param('resources', {}))

        #: How to run each command.
        self.runner = dp.flatten(runner or config_param('runner', []))

        #: Whether to ignore the command(s) exit status.
        self.ignore_exit_status = ignore_exit_status

        if not run:
            run = []
        elif isinstance(run, str):
            run = [[run]]
        elif run and isinstance(run[0], str):
            run = [run]  # type: ignore

        #: The expanded command(s) to execute.
        self.run = [expand(command) for command in run]

        step = Step.current()

        #: The call stack creating this action.
        self.stack = step.stack

        #: The expanded names of all the input files.
        self.input = dp.flatten(input)
        Make.logger.debug('%s: input(s): %s', self.stack, ' '.join(self.input) or 'None')

        #: Before the action is executed, the unexpanded patterns of all the outputs.
        #: After the action is executed, the actual matched paths from all these patterns.
        self.output = dp.flatten(output)
        Make.logger.debug('%s: output(s): %s', self.stack, ' '.join(self.output) or 'None')

        #: The paths of the existing input files matching the input ``glob`` patterns.
        self.input_paths: List[str] = []

        self._collect_input_paths()

        #: The path of the existing output files matching the output ``glob`` patterns.
        self.output_paths: List[str] = []

        #: Whether the action needs to be executed.
        self.needs_to_execute = self._needs_to_execute(step.config_path)

    def _collect_input_paths(self) -> None:
        missing_input_patterns: List[str] = []
        for input_pattern in self.input:
            try:
                self.input_paths += self._log_glob('input', input_pattern)
            except dp.NonOptionalException:
                missing_input_patterns.append(input_pattern)
        self._raise_if_missing('input', missing_input_patterns)

    def _needs_to_execute(self,  # pylint: disable=too-many-return-statements,too-many-branches
                          config_path: Optional[str]) -> bool:
        if not self.output:
            Make.logger.debug('%s: needs to execute because has no outputs', self.stack)
            return True

        minimal_output_mtime: Optional[int] = None
        for output_pattern in self.output:
            try:
                output_paths = self._log_glob('output', output_pattern)
                self.output_paths += output_paths
            except dp.NonOptionalException as exception:
                if exception.glob == output_pattern:
                    Make.logger.debug('%s: needs to execute because missing output(s): %s',
                                      self.stack, output_pattern)
                else:
                    Make.logger.debug('%s: needs to execute because missing output(s): %s glob: %s',
                                      self.stack, output_pattern, exception.glob)
                return True

            if dp.is_exists(output_pattern):
                continue

            for output_path in output_paths:
                output_mtime = os.stat(output_path).st_mtime_ns
                if minimal_output_mtime is None or minimal_output_mtime > output_mtime:
                    minimal_output_mtime = output_mtime

        if minimal_output_mtime is None:
            if not self.output_paths:
                Make.logger.debug('%s: need to execute because no output(s) exist', self.stack)
                return True
            Make.logger.debug('%s: no need to execute because some output file(s) exist',
                              self.stack)
            return False

        has_older = False
        if config_path is not None:
            config_mtime = os.stat(config_path).st_mtime_ns
            if config_mtime >= minimal_output_mtime:
                Make.logger.debug('%s: need to execute because of newer config: %s',
                                  self.stack, config_path)
                return True
            has_older = True

        for input_path in self.input_paths:
            if dp.is_exists(input_path):
                continue
            input_mtime = os.stat(input_path).st_mtime_ns
            if input_mtime >= minimal_output_mtime:
                Make.logger.debug('%s: need to execute because of newer input: %s',
                                  self.stack, input_path)
                return True
            has_older = True

        if has_older:
            Make.logger.debug('%s: no need to execute because output(s) are newer', self.stack)
        elif self.input_paths:
            Make.logger.debug('%s: no need to execute because all input(s) exist', self.stack)
        else:
            Make.logger.debug('%s: no need to execute because all output(s) exist', self.stack)
        return False

    def call(self) -> None:
        """
        Actually execute the action.
        """
        if self.delete_stale_outputs:
            self._delete_outputs('stale')
        self.output_paths = []

        try:
            for command in self.run:
                if self.runner == ['shell']:
                    Make.logger.info('%s: run: %s', self.stack, ' '.join(command))
                    completed = subprocess.run(' '.join(command), shell=True)

                else:
                    command = self.runner + command
                    Make.logger.info('%s: run: %s', self.stack, ' '.join(command))
                    completed = subprocess.run(self.runner + command)

                if completed.returncode != 0 and not self.ignore_exit_status:
                    Make.logger.debug('%s: failed with exit status: %s',
                                      self.stack, completed.returncode)
                    raise RuntimeError('%s: failed command: %s' % (self.stack, ' '.join(command)))

        except BaseException:
            self._fail()
            raise

        self._success()

    def _fail(self) -> None:
        if self.delete_failed_outputs:
            for pattern in self.output:
                self.output_paths += glob(optional(pattern))
            self._delete_outputs('failed')

    def _success(self) -> None:
        missing_outputs_patterns: List[str] = []
        did_sleep = False
        for output_pattern in self.output:
            try:
                self.output_paths += self._log_glob('output', output_pattern)
                output_paths = glob(optional(output_pattern))
            except dp.NonOptionalException:
                missing_outputs_patterns.append(output_pattern)
                continue

            if not self.touch_success_outputs:
                continue

            for output_path in output_paths:
                if dp.is_exists(output_path) or os.path.isdir(output_path):
                    continue
                Make.logger.debug('%s: touch output: %s', self.stack, output_path)
                if not did_sleep:
                    did_sleep = True
                    sleep(0.01)
                os.utime(output_path)

        self._raise_if_missing('output', missing_outputs_patterns)

    def _delete_outputs(self, reason: str) -> None:
        for path in self.output_paths:
            if dp.is_precious(path):
                continue
            Make.logger.debug('%s: delete %s output: %s', self.stack, reason, path)
            path = os.path.abspath(path)
            if os.path.isfile(path):
                os.remove(path)
            elif os.path.exists(path):
                shutil.rmtree(path)

            while self.delete_empty_directories:
                path = os.path.dirname(path)
                try:
                    os.rmdir(path)
                    Make.logger.debug('%s: delete empty directory: %s', self.stack, path)
                except BaseException:
                    return

    def _raise_if_missing(self, direction: str, patterns: List[str]) -> None:
        if not patterns:
            return

        pattern = patterns[0]
        expanded = expand(pattern)[0]

        if expanded == pattern:
            raise RuntimeError('Missing %s(s): %s '
                               'for the action step: %s'
                               % (direction, pattern, self.stack))

        raise RuntimeError('Missing %s(s): %s '
                           'for the pattern: %s '
                           'for the action step: %s'
                           % (direction, expanded, pattern, self.stack))

    def _log_glob(self, direction: str, pattern: str) -> List[str]:
        paths = glob(pattern)
        if paths == [pattern]:
            Make.logger.debug('%s: exists %s: %s', self.stack, direction, pattern)
        elif paths:
            Make.logger.debug('%s: glob %s: %s path(s): %s',
                              self.stack, direction, pattern, ' '.join(paths))
        else:
            Make.logger.debug('%s: no %s: %s', self.stack, direction, pattern)
        return paths


def plan(run_help: Strings = None) -> Callable[[Wrapped], Wrapped]:
    """
    Decorate a plan step function.

    If ``run_help`` is given, it is a command to execute to print a help message for the step. The
    pattern ``{step}`` in this command would be replaced by the step name. Use ``{{`` and ``}}`` to
    escape the ``{`` and ``}`` characters.
    """
    def _wrap(wrapped: Wrapped) -> Wrapped:
        return _step(Planner, run_help, wrapped)
    return _wrap


def action(run_help: Strings = None) -> Callable[[Wrapped], Wrapped]:
    """
    Decorate an action step function.

    If ``run_help`` is given, it is a command to execute to print a help message for the step. The
    pattern ``{step}`` in this command would be replaced by the step name. Use ``{{`` and ``}}`` to
    escape the ``{`` and ``}`` characters.
    """
    def _wrap(wrapped: Wrapped) -> Wrapped:
        return _step(Agent, run_help, wrapped)
    return _wrap


def _step(step: type, run_help: Strings, wrapped: Wrapped) -> Wrapped:
    function = _callable_function(wrapped)

    def _wrapper_function(*args: Any, **kwargs: Any) -> Any:
        return Step.call_current(step, function, args, kwargs)

    setattr(_wrapper_function, '_dynamake_wrapped_function', function)

    if run_help:
        run_help = [string.format(step=function.__name__) for string in dp.each_string(run_help)]
    setattr(_wrapper_function, '_dynamake_run_help', run_help)

    positional_argument_names, environment_argument_names = _argument_names(function)
    setattr(function, '_dynamake_positional_argument_names', positional_argument_names)
    setattr(function, '_dynamake_environment_argument_names', environment_argument_names)

    conflicting = Make.step_by_name.get(function.__name__)
    if conflicting is not None:
        conflicting = getattr(conflicting, '_dynamake_wrapped_function')
        assert conflicting is not None
        raise RuntimeError('Conflicting definitions for the step: %s '
                           'in both: %s.%s '
                           'and: %s.%s'
                           % (function.__name__,
                              conflicting.__module__, conflicting.__qualname__,
                              function.__module__, function.__qualname__))
    Make.step_by_name[function.__name__] = _wrapper_function

    return _wrapper_function  # type: ignore


def _callable_function(wrapped: Callable) -> Callable:
    if isinstance(wrapped, staticmethod):
        return wrapped.__func__
    return wrapped


class Env:
    """
    Marker for default for environment parameters.
    """


def env() -> Any:
    """
    Used as a default value for environment parameters.

    When a step uses this as a default value for a parameter,
    and an invocation does not specify an explicit or a configuration value for the parameter,
    then the value will be taken from the nearest parent which has a parameter with the same name.
    """
    return Env()


def _argument_names(function: Callable) -> Tuple[List[str], List[str]]:
    positional_names: List[str] = []
    environment_names: List[str] = []
    for parameter in signature(function).parameters.values():
        if parameter.kind in [Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD]:
            positional_names.append(parameter.name)
        if isinstance(parameter.default, Env):
            environment_names.append(parameter.name)
    return positional_names, environment_names


def expand(*patterns: Strings) -> List[str]:
    """
    Expand the value of the current known parameters for each ``...{name}...`` inside the patterns.

    See :py:func:`dynamake.patterns.expand_strings`.
    """
    return dp.expand_strings(Step.current().wildcards, *patterns)


def glob(*patterns: Strings) -> List[str]:
    """
    Return the path of each existing file matching any of the ``glob`` pattern, using the value of
    the current known parameters for each ``...{name}...`` in the pattern.

    The expanded paths will inherit the annotations of the pattern
    (:py:func:`dynamake.make.optional` and/or :py:func:`dynamake.make.exists`). This will complain
    about any patterns that match nothing unless they are annotated with
    :py:func:`dynamake.make.optional`.

    See :py:func:`dynamake.patterns.glob_strings`.
    """
    return dp.glob_strings(Step.current().wildcards, *patterns)


def extract(pattern: str, *strings: Strings) -> List[Dict[str, Any]]:
    """
    Extract the value of each ``...{*name}...`` in from the strings, using value of the current
    known parameters for each ``...{name}...`` in the pattern.

    It is the caller's responsibility to ensure that all the strings capture the same set of names.
    If they don't, the resulting wildcards dictionaries will have different sets of keys.

    See :py:func:`dynamake.patterns.extract_strings`.
    """
    return dp.extract_strings(Step.current().wildcards, pattern, *strings)


def capture(*patterns: Strings) -> Captured:
    """
    Capture the value of each ``...{*name}...`` in a ``glob`` pattern, using value of the current
    known parameters for each ``...{name}...`` in the pattern.

    It is the caller's responsibility to ensure that all the patterns capture the same set of names.
    If they don't, the resulting wildcards dictionaries will have different sets of keys.

    The expanded paths will inherit the annotations of the pattern
    (:py:func:`dynamake.make.optional` and/or :py:func:`dynamake.make.exists`). This will complain
    about any patterns that match nothing unless they are annotated with
    :py:func:`dynamake.make.optional`.

    See :py:func:`dynamake.patterns.capture_globs`.
    """
    return dp.capture_globs(Step.current().wildcards, *patterns)


class Wild:
    """
    Access the value of a captured parameter (or wildcard inside :py:func:`dynamake.make.foreach`
    and :py:func:`dynamake.make.pareach`).
    """

    def __init__(self, name: str,
                 validate: Union[None, type, Callable[[str, Any], Any]] = None) -> None:
        """
        Create an expand object for the current scope.

        If ``validate`` is passed, it should either be the expected class name, or a function that
        takes the parameter name and value, and validates the value - either returning the valid
        result or raising an exception if the value is not valid.

        It is more convenient to have the validation capture invalid values early than debugging
        strange run-time errors resulting from using an unexpected parameter value type deep inside
        some library code.
        """
        #: The name of the parameter or wildcard to expand.
        self.name = name

        #: How to validate the accessed value.
        self.validate: Optional[Callable[[str, Any], Any]]

        if not isinstance(validate, type):
            self.validate = validate
        else:
            klass: type = validate

            def _validate(name: str, value: Any) -> Any:
                if isinstance(value, klass):
                    return value
                try:
                    return klass(value)
                except BaseException:
                    raise RuntimeError('Invalid value: %s '
                                       'type: %s.%s '
                                       'for the parameter: %s'
                                       % (value,
                                          value.__class__.__module__, value.__class__.__qualname__,
                                          name))
            self.validate = _validate

    def value(self, wildcards: Dict[str, Any]) -> Any:
        """
        Access the value of the expanded parameter or wildcard in the current scope.

        If an expected type was declared, and the value is not of that type, then the
        code tries to convert the type to that class. If that fails, then an error is
        raised.
        """
        if self.name not in wildcards:
            raise RuntimeError('Unknown parameter: %s' % self.name)
        value = wildcards[self.name]
        if self.validate is not None:
            value = self.validate(self.name, value)
        return value


def foreach(wildcards: List[Dict[str, Any]], function: Callable, *args: Any, **kwargs: Any) \
        -> List[Any]:
    """
    Invoke a function for each set of parameters in the specified wildcards, one at a time.

    Any arguments (positional or named) whose value is a string will be
    :py:func:`dynamake.make.expand`-ed using the current known parameters as well as the specified
    wildcards.

    Any arguments (positional or named) whose value is :py:class:`dynamake.make.Wild` will be
    replaced by the current known parameter or wildcard value.
    """
    results = []

    for values in wildcards:
        expanded_values = Step.current().wildcards.copy()
        expanded_values.update(values)

        expanded_args: List[str] = []
        for arg in args:
            if isinstance(arg, str):
                arg = arg.format(**expanded_values)
            elif isinstance(arg, Wild):
                arg = arg.value(expanded_values)
            expanded_args.append(arg)

        expanded_kwargs: Dict[str, Any] = {}
        for name, arg in kwargs.items():
            if isinstance(arg, str):
                arg = arg.format(**expanded_values)
            elif isinstance(arg, Wild):
                arg = arg.value(expanded_values)
            expanded_kwargs[name] = arg

        results.append(function(*expanded_args, **expanded_kwargs))

    return results


def pareach(wildcards: List[Dict[str, Any]], function: Callable, *args: Any, **kwargs: Any) \
        -> List[Any]:
    """
    Similar to :py:func:`dynamake.make.foreach` but invoke the functions in parallel.
    """
    futures = foreach(wildcards, parallel, function, *args, **kwargs)
    results = [future.result() for future in futures]
    return results


def parcall(*steps: Tuple[Callable, Dict[str, Any]]) -> List[Any]:
    """
    Invoke multiple arbitrary functions in parallel.
    """
    futures = [parallel(step, **kwargs) for step, kwargs in steps]
    return [future.result() for future in futures]


def load_config(path: str) -> None:
    """
    Load a YAML configuration file into :py:attr:`dynamake.config.Config.rules`.

    The rules from the loaded files will override any values specified by previously loaded
    rules. In general, the last matching rule wins.

    The configuration YAML file should be empty, or contain a top-level sequence.
    Each entry must be a mapping with two keys: ``when`` and ``then``.
    The ``then`` value should be a mapping from parameter names to their values.
    The ``when`` value should be a mapping of conditions.

    The condition key can be either a parameter name, or ``lambda parameter_name, ...``.
    If the key starts with ``lambda``, the value should be a string containing a
    python boolean expression, which will be evaluated using the specified parameters.

    Otherwise, the condition value should be one of:

    * The exact value of the parameter.

    * A ``!r regexp-pattern`` or a ``!g glob-pattern`` to match the (string) parameter value
      against. While general regular expressions are more powerful, glob patterns are simpler
      and more familiar to most people, as they are used by the shell.

    * A list of alternative values or patterns to match the parameter value against.

    A rule is applicable to a step invocation if all the conditions in its ``when`` mapping
    match the parameters used in the invocation.

    Two additional parameters are also added for the purpose of the matching: ``step`` contains the
    step function name, and the call ``stack`` contains the path of step names ``/top/.../step``.
    This uses ``/`` to separate the step names to allow matching it against a glob pattern, as if it
    was a file path. Conditions on these parameters are evaluated first, and if they reject the
    invocation, no other condition is evaluated.

    It is normally an error for the rest of the conditions to specify a parameter which is not one
    of the arguments of the invoked step function. However, if the condition key ends with ``?``,
    then the condition will silently reject the step instead. This allows specifying rules that
    provide defaults without worrying about applying the rule to the exact list of steps that use
    it.

    Default rules are very useful when used responsibly. However, they have two important
    downsides:

    * Modifying the values in such default rules will trigger the re-computation of all the
      action steps that match them, even if they do not actually depend on the provided values.

    * Default rules silently ignore typos in parameter names.

    It is pretty annoying to modify the configuration file, to adjust some parameter, re-execute
    the full computation, wait a few hours, and then discover this was wasted effort because you
    wrote ``foo: 1`` instead of ``foos: 1`` in the default rule.

    It is therefore recommended to minimize the use of default rules, and when they are used, to
    restrict them to match as few steps as possible, typically by matching against the steps
    call ``stack``.
    """
    Config.rules += Rule.load(path)


def config_param(name: str, default: Any = None) -> Any:
    """
    Access the value of a configuration parameter for the current step.

    It is an error if no default is specified and no value is specified in the loaded configuration.
    """
    return Step.current().config_param(name, default)


def config_file() -> str:
    """
    Access the path of the generated configuration file for the current step.

    If this is invoked, it is assumed the file is passed to some invoked action,
    so no testing is done to ensure all specified parameters are actually used.
    """
    return Step.current().config_file()


def parallel(step: Callable, *args: Any, **kwargs: Any) -> Future:
    """
    Invoke a step in parallel to the main thread.
    """
    return Make.executor.submit(Step.call_in_parallel, Step.current(), step, *args, **kwargs)


def optional_flag(flag: str, *value: Strings) -> List[str]:
    """
    An optional flag for a run command.

    If ``value`` contains only ``None``, returns ``[]``.
    Otherwise, returns a list with the ``flag`` and the (flattened) ``value``.
    """
    values = dp.flatten(*value)
    if values:
        values = [flag] + values
    return values


def pass_flags(*names: Strings) -> List[str]:
    """
    Given some flag names, return a list of command line arguments.

    The command line arguments for the flag ``foo`` will be ``--foo`` followed by the
    expanded value of ``{foo}``.

    If a name with :py:func:`dynamake.patterns.optional`, and it has no value
    in the current context, or its value is ``None``, then the flag is silently omitted.
    """
    flags = []
    for name in dp.each_string(*names):
        if dp.is_optional(name) and Step.current().wildcards.get(name) is None:
            continue
        flags.append('--' + name)
        flags.append('{name}')
    return expand(*flags)


def main(parser: argparse.ArgumentParser, default_step: Optional[Callable] = None) -> None:
    """
    A generic ``main`` function for build scripts.
    """
    if default_step is not None and not hasattr(default_step, '_dynamake_wrapped_function'):
        raise RuntimeError('The function: %s.%s is not a DynaMake step'
                           % (default_step.__module__, default_step.__qualname__))

    _add_arguments(parser, default_step)
    args = parser.parse_args()
    config_values, used_values = _configure_by_arguments(args)
    if not _help_by_arguments(args):
        _call_steps(default_step, args, config_values, used_values)


def _add_arguments(parser: argparse.ArgumentParser, default_step: Optional[Callable]) -> None:
    parser.add_argument('-ls', '--list-steps', action='store_true', help='List all known steps')

    parser.add_argument('-hs', '--help-step', metavar='STEP',
                        help='Describe a specific step and exit')

    parser.add_argument('-c', '--config', metavar='CONFIG.yaml',
                        help='The configuration file to use (default: %s)' % Make.FILE)

    parser.add_argument('-m', '--module', metavar='MODULE', action='append',
                        help='A Python module to load (containing step definitions)')

    parser.add_argument('-ll', '--log_level', metavar='LEVEL', default='INFO',
                        help='The log level to use (default: INFO)')

    parser.add_argument('-dso', '--delete_stale_outputs', metavar='BOOL',
                        type=dp.str2bool, nargs='?', const=True,
                        help='Whether to delete outputs before executing actions '
                             '(default: %s)' % Make.delete_stale_outputs)

    parser.add_argument('-tso', '--touch_success_outputs', metavar='BOOL', nargs='?',
                        type=dp.str2bool, const=True,
                        help='Whether to touch output files after successful actions '
                             '(default: %s)' % Make.touch_success_outputs)

    parser.add_argument('-dfo', '--delete_failed_outputs', metavar='BOOL', nargs='?',
                        type=dp.str2bool, const=True,
                        help='Whether to delete outputs after failed actions '
                             '(default: %s)' % Make.delete_failed_outputs)

    parser.add_argument('-ded', '--delete_empty_directories', metavar='BOOL',
                        type=dp.str2bool, nargs='?', const=True,
                        help='Whether to delete empty directories containing deleted outputs '
                             '(default: %s)' % Make.delete_empty_directories)

    parser.add_argument('-p', '--parameter', metavar='NAME=VALUE', action='append',
                        help='Specify a value for a top-level step parameter')

    default_name = ''
    if default_step is not None:
        default_name = \
            ' (default: %s)' % getattr(default_step, '_dynamake_wrapped_function').__name__
    parser.add_argument('step', metavar='FUNCTION', nargs='*',
                        help='The top-level step function(s) to execute%s' % default_name)


def _configure_by_arguments(args: argparse.Namespace) -> Tuple[Dict[str, Any], Set[str]]:
    if args.config is None and os.path.exists(Make.FILE):
        args.config = Make.FILE
    if args.config is not None:
        load_config(args.config)

    config_values = Config.values_for_context({'stack': '/', 'step': '/'})
    used_values: Set[str] = set()

    def _get(name: str, parser: Callable, default: Any) -> Any:
        used_values.add(name)
        if name in vars(args):
            value = vars(args)[name]
            if value is not None:
                return vars(args)[name]
        value = config_values.get(name, config_values.get(name + '?', default))
        if isinstance(value, str):
            value = parser(value)
        return value

    for module in args.module or []:
        import_module(module)
    for module in _get('modules', (lambda module: [module]), []):
        import_module(module)

    name = sys.argv[0].split('/')[-1]
    if name != '__test':
        if 'command' in vars(args):
            name += ' ' + args.command
        handler = logging.StreamHandler(sys.stderr)
        formatter = \
            logging.Formatter('%(asctime)s - ' + name
                              + ' - %(threadName)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        Make.logger.addHandler(handler)
    Make.logger.setLevel(_get('log_level', str, 'INFO'))

    Make.delete_stale_outputs = \
        _get('delete_stale_outputs', dp.str2bool, Make.delete_stale_outputs)

    Make.touch_success_outputs = \
        _get('touch_success_outputs', dp.str2bool, Make.touch_success_outputs)

    Make.delete_failed_outputs = \
        _get('delete_failed_outputs', dp.str2bool, Make.delete_failed_outputs)

    Make.delete_empty_directories = \
        _get('delete_empty_directories', dp.str2bool, Make.delete_empty_directories)

    def _parse_available_resources(string: str) -> Dict[str, float]:
        raise RuntimeError('Configuration for available resources is not a mapping: %s' % string)

    available_resources(**_get('available_resources', _parse_available_resources, {}))

    return config_values, used_values


def _help_by_arguments(args: argparse.Namespace) -> bool:
    if args.list_steps:
        for step_name, wrapped in sorted(Make.step_by_name.items()):
            function = getattr(wrapped, '_dynamake_wrapped_function')
            if function.__doc__:
                print('%s:\n    %s' % (step_name, dp.first_sentence(function.__doc__)))
            else:
                print('%s' % step_name)
        return True

    if args.help_step:
        step_name = args.help_step
        if step_name not in Make.step_by_name:
            raise RuntimeError('Unknown step: %s' % step_name)
        wrapped = Make.step_by_name[step_name]
        function = getattr(wrapped, '_dynamake_wrapped_function')
        run_help = getattr(wrapped, '_dynamake_run_help')
        if run_help is not None:
            subprocess.run(run_help)
        elif function.__doc__:
            print(dedent(function.__doc__))
        else:
            print('No help available for the step: %s.%s'
                  % (function.__module__, function.__qualname__))
        return True

    return False


def _call_steps(default_step: Optional[Callable],  # pylint: disable=too-many-branches
                args: argparse.Namespace,
                config_values: Dict[str, Any],
                used_values: Set[str]) -> None:
    step_parameters: Dict[str, Any] = {}
    used_parameters: Set[str] = set()

    for parameter in args.parameter or []:
        parts = parameter.split('=')
        if len(parts) != 2:
            raise RuntimeError('Invalid parameter flag: %s' % parameter)
        name, value = parts
        try:
            value = yaml.load(value)
        except BaseException:
            pass
        step_parameters[name] = value

    def _call_step(step_function: Callable) -> None:
        function = getattr(step_function, '_dynamake_wrapped_function')
        kwargs: Dict[str, Any] = {}
        for parameter in signature(function).parameters.values():
            used_values.add(parameter.name)
            if parameter.name in step_parameters:
                kwargs[parameter.name] = step_parameters[parameter.name]
            elif parameter.name in config_values:
                kwargs[parameter.name] = config_values[parameter.name]
            elif parameter.default == parameter.empty or isinstance(parameter.default, Env):
                raise RuntimeError('Missing top-level parameter: %s for the step: /%s'
                                   % (parameter.name, function.__name__))
            used_parameters.add(parameter.name)
        step_function(**kwargs)

    if not args.step and 'steps' in config_values:
        used_values.add('steps')
        steps = config_values['steps']
        if isinstance(steps, str):
            steps = [steps]
        args.step = steps

    Make.logger.info('start')

    if args.step:
        for step_name in args.step:
            if step_name not in Make.step_by_name:
                raise RuntimeError('Unknown step: %s' % step_name)
        steps = [Make.step_by_name[step_name] for step_name in args.step]
    elif default_step is not None:
        steps = [default_step]
    else:
        raise RuntimeError('No step(s) specified')

    for step in steps:
        _call_step(step)

    Make.logger.info('done')

    for parameter_name in step_parameters:
        if parameter_name not in used_parameters:
            raise RuntimeError('Unused top-level step parameter: %s' % parameter_name)

    for parameter_name in config_values:
        if parameter_name not in used_values and not parameter_name.endswith('?'):
            raise RuntimeError('Unused top-level configuration parameter: %s ' % parameter_name)


def reset_make() -> None:
    """
    Reset all the current state, for tests.
    """
    Config.reset()
    Make.reset()
    Step.reset()


reset_make()
