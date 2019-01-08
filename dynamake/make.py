"""
Utilities for dynamic make.
"""

import argparse
import inspect
import logging
import os
import shutil
import subprocess
from abc import abstractmethod
from datetime import datetime
from enum import Enum
from enum import unique
from types import SimpleNamespace
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
from typing import TypeVar

import yaml

import dynamake.patterns as dp

from .config import Config
from .config import Rule
from .patterns import Captured
from .patterns import Strings

#: The type of a wrapped function.
Wrapped = TypeVar('Wrapped', bound=Callable)


@unique
class MissingInputs(Enum):
    """
    Policy for handling missing :py:class:`dynamake.make.Action` inputs
    (that is, input ``glob`` patterns that do not match any existing file path).
    """

    #: Treat any missing input as an error. This is the default behavior.
    forbidden = 0

    #: Allow missing inputs as long as the action does not need to be executed. This allows
    #: intermediate files to be deleted without causing actions to be re-executed.
    assume_up_to_date = 1

    #: Allow missing inputs even if the action needs to be executed. This allows for the (very rare)
    #: case of optional action inputs.
    optional = 2


@unique
class MissingOutputs(Enum):
    """
    Policy for handling missing :py:class:`dynamake.make.Action` outputs
    (that is, output ``glob`` patterns that do not match any existing file path,
    after the action has executed).
    """

    #: Treat any missing output as an error. This is the default behavior.
    forbidden = 0

    #: Allow missing outputs as long as that the action did produce at least one output file. This
    #: allows for the (uncommon) case of some optional action outputs.
    partial = 1

    #: Allow the action to produce no outputs whatsoever, for the (rare) case of completely optional
    #: outputs.
    optional = 2


class Make:  # pylint: disable=too-many-instance-attributes
    """
    Global build state.
    """

    #: Known (wrapped) step functions.
    step_by_name: Dict[str, Callable]

    #: The logger for tracking the build flow.
    logger: logging.Logger

    #: The default policy for handling missing inputs (by default,
    #: :py:attr:`dynamake.make.MissingInputs.forbidden`).
    #:
    #: It is possible to override this on a per-action basis.
    missing_inputs: MissingInputs

    #: The default policy for handling missing outputs (by default,
    #: :py:attr:`dynamake.make.MissingOutputs.forbidden`).
    #:
    #: It is possible to override this on a per-action basis.
    missing_outputs: MissingOutputs

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
        Make.step_by_name = {}
        Make.logger = logging.getLogger('dynamake')
        Make.missing_inputs = MissingInputs.forbidden
        Make.missing_outputs = MissingOutputs.forbidden
        Make.delete_stale_outputs = True
        Make.touch_success_outputs = False
        Make.delete_failed_outputs = True
        Make.delete_empty_directories = False


class Step:  # pylint: disable=too-many-instance-attributes
    """
    A single step function execution state.
    """

    #: The current step.
    current: 'Step'

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Step.current = Planner(None, None, (), {})

    @staticmethod
    def call_current(make: type, function: Callable,
                     args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Any:
        """
        Invoke the specified build step function.
        """
        parent = Step.current
        Step.current = make(parent, function, args, kwargs)
        try:
            return Step.current.call()
        finally:
            Step.current = parent

    def __init__(self, parent: Optional['Step'], function: Optional[Callable],
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

        #: The parent step of this one.
        if parent is None:
            self.parent = self
            self.stack = '/'
            assert isinstance(self, Planner)
        else:
            self.parent = parent
            if parent.stack == '/':
                self.stack = parent.stack + self.name
            else:
                self.stack = parent.stack + '/' + self.name
            if isinstance(parent, Agent):
                raise RuntimeError('The nested step: %s.%s '
                                   'is invoked from an action step: %s.%s'
                                   % (self.function.__module__, self.function.__qualname__,
                                      parent.function.__module__, parent.function.__qualname__))

        #: The current known parameters (expandable wildcards).
        self.wildcards = kwargs.copy()
        if function is not None:
            for name, arg \
                    in zip(getattr(self.function, '_dynamake_positional_argument_names'), args):
                self.wildcards[name] = arg
        self.wildcards['step'] = self.name
        self.wildcards['stack'] = self.stack

        #: The configuration values for the step.
        self.config_values = Config.values_for_context(self.wildcards)

        #: The used configuration values for the step, if configuration is used.
        self.used_params: Set[str] = set()

        #: The path name of the generated configuration file, if used.
        self.config_path: Optional[str] = None

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
                Make.logger.debug('%s: using existing configuration file: %s',
                                  self.stack, self.config_path)
            else:
                if not os.path.exists(Config.DIRECTORY):
                    os.mkdir(Config.DIRECTORY)
                Make.logger.debug('%s: writing new configuration file: %s',
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


Make.reset()


class Agent(Step):
    """
    Implement an action step.
    """

    def _call(self) -> Any:
        result = self.function(*self.args, **self.kwargs)
        if isinstance(result, Action) and result.needs_to_execute:
            result.call()
        return result


class Action(SimpleNamespace):  # pylint: disable=too-many-instance-attributes
    """
    An atomic executable action invoking external programs.
    """

    def __init__(self, *, input: Strings,  # pylint: disable=redefined-builtin
                 output: Strings, run: List[Strings],
                 shell: bool = False,
                 ignore_exit_status: bool = False,
                 missing_inputs: Optional[MissingInputs] = None,
                 missing_outputs: Optional[MissingOutputs] = None,
                 delete_stale_outputs: Optional[bool] = None,
                 touch_success_outputs: Optional[bool] = None,
                 delete_failed_outputs: Optional[bool] = None,
                 delete_empty_directories: Optional[bool] = None,
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
        shell
            If ``True``, the ``run`` may contain arbitrary shell commands, pipelines, redirections,
            wildcard expansions, etc. Quoting unsafe values is the responsibility of the caller.

            By default each command is expected to be a simple direct program execution, and quoting
            is handled automatically.
        ignore_exit_status
            If ``True``, the exit status of the command(s) is ignored. Otherwise, if it is not zero,
            the action is considered a failure.
        missing_inputs
            Optional override for :py:attr:`dynamake.make.Make.missing_inputs` for this action.
        missing_outputs
            Optional override for :py:attr:`dynamake.make.Make.missing_outputs` for this action.
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
        kwargs
            Any additional named parameters are injected into the action object
            (``SimpleNamespace``). This makes it easy to return additional values from an action
            step, to be used by the caller plan step.
        """
        super().__init__(**kwargs)

        #: The expanded names of all the input files.
        self.input = dp.flatten(input)

        #: Before the action is executed, the unexpanded patterns of all the outputs.
        #: After the action is executed, the actual matched paths from all these patterns.
        self.output = dp.flatten(*output)

        #: How to handle missing inputs.
        self.missing_inputs = \
            Make.missing_inputs if missing_inputs is None else missing_inputs

        #: How to handle missing outputs.
        self.missing_outputs = \
            Make.missing_outputs if missing_outputs is None else missing_outputs

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

        #: Whether to use a shell to execute the commands.
        self.shell = shell

        #: Whether to ignore the command(s) exit status.
        self.ignore_exit_status = ignore_exit_status

        if not run:
            run = []

        if run and isinstance(run[0], str):
            run = [run]  # type: ignore

        #: The expanded command(s) to execute.
        self.run = [expand(command) for command in run]

        #: The paths of the existing input files matching the input ``glob`` patterns.
        self.input_paths: List[str]

        #: Input patterns that did not match any existing disk files.
        self.missing_input_patterns: List[str]

        self.input_paths, self.missing_input_patterns = Action._collect_glob(self.input)
        Make.logger.debug('%s: input: %s', Step.current.stack,
                          ' '.join(self.input) or 'None')
        Make.logger.debug('%s: input paths: %s',
                          Step.current.stack, ' '.join(self.input_paths) or 'None')
        if self.missing_inputs == MissingInputs.forbidden:
            Action._raise_missing('input', self.missing_input_patterns)

        #: The path of the existing output files before the execution, matching the output ``glob``
        #: patterns.
        self.output_paths_before = self._collect_outputs()
        Make.logger.debug('%s: output: %s',
                          Step.current.stack, ' '.join(self.output) or 'None')
        Make.logger.debug('%s: output paths before: %s',
                          Step.current.stack, ' '.join(self.output_paths_before) or 'None')

        #: The path of the existing output files after the execution, matching the output ``glob``
        #: patterns.
        self.output_paths_after: List[str] = []

        #: Output patterns that did not match any existing disk files, after the execution.
        self.missing_outputs_patterns: List[str] = []

        #: Whether the action needs to be executed.
        self.needs_to_execute = self._needs_to_execute()

        if self.needs_to_execute \
                and self.missing_inputs == MissingInputs.assume_up_to_date:
            Action._raise_missing('input', self.missing_input_patterns)

    def _needs_to_execute(self) -> bool:
        if not self.output:
            Make.logger.debug('%s: needs to execute because has no outputs',
                              Step.current.stack)
            return True

        minimal_output_mtime = Action._collect_mtime(self.output_paths_before, min)
        if Make.logger.isEnabledFor(logging.DEBUG):
            Make.logger.debug('%s: minimal output mtime: %s',
                              Step.current.stack, _ns2str(minimal_output_mtime))

        if minimal_output_mtime is None:
            # TODO: This is wrong if the next step(s) override Action.missing_inputs.
            if Make.missing_inputs == MissingInputs.forbidden:
                Make.logger.debug('%s: need to execute assuming next step(s) need all inputs',
                                  Step.current.stack)
                return True
            Make.logger.debug('%s: no need to execute assuming next step(s) allow missing inputs',
                              Step.current.stack)
            return False

        maximal_input_mtime = \
            Action._collect_mtime(self.input_paths, max, Step.current.config_path)
        if Make.logger.isEnabledFor(logging.DEBUG):
            Make.logger.debug('%s: maximal input mtime: %s',
                              Step.current.stack, _ns2str(maximal_input_mtime))

        if maximal_input_mtime is None:
            Make.logger.debug('%s: no need to execute ignoring missing inputs',
                              Step.current.stack)
            return False

        if maximal_input_mtime < minimal_output_mtime:
            Make.logger.debug('%s: no need to execute since outputs are newer',
                              Step.current.stack)
            return False

        Make.logger.debug('%s: need to execute since inputs are newer',
                          Step.current.stack)
        return True

    @staticmethod
    def _collect_mtime(paths: List[str], combine: Callable,
                       extra_path: Optional[str] = None) -> Optional[int]:
        collected_mtime = None
        if extra_path is not None:
            collected_mtime = os.stat(extra_path).st_mtime_ns
        for path in paths:
            path_mtime = os.stat(path).st_mtime_ns
            if collected_mtime is None:
                collected_mtime = path_mtime
            else:
                collected_mtime = combine(collected_mtime, path_mtime)
        return collected_mtime

    def call(self) -> None:
        """
        Actually execute the action.
        """
        if self.delete_stale_outputs:
            self._delete_outputs('stale', self.output_paths_before)

        for command in self.run:
            Make.logger.info('%s: run: %s', Step.current.stack, ' '.join(command))
            if self.shell:
                completed = subprocess.run(' '.join(command), shell=True)
            else:
                completed = subprocess.run(command)
            if completed.returncode != 0 and not self.ignore_exit_status:
                Make.logger.debug('%s: failed with exit status: %s',
                                  Step.current.stack, completed.returncode)
                self._fail(command)

        self._success()

    def _fail(self, command: List[str]) -> None:
        self.output_paths_after = self._collect_outputs()

        if self.delete_failed_outputs:
            self._delete_outputs('failed', self.output_paths_after)

        raise RuntimeError('%s: failed command: %s' % (Step.current.stack, ' '.join(command)))

    def _success(self) -> None:
        self.output_paths_after, self.missing_outputs_patterns = Action._collect_glob(self.output)
        Make.logger.debug('%s: output paths after: %s',
                          Step.current.stack, ' '.join(self.output_paths_after) or 'None')

        if self.missing_outputs == MissingOutputs.forbidden \
                or (self.missing_outputs == MissingOutputs.partial and not self.output_paths_after):
            Action._raise_missing('output', self.missing_outputs_patterns)

        if self.touch_success_outputs and self.output_paths_after:
            Make.logger.debug('%s: touch outputs: %s',
                              Step.current.stack, ' '.join(self.output_paths_after))
            for path in self.output_paths_after:
                os.utime(path)

    def _delete_outputs(self, reason: str, paths: List[str]) -> None:
        if not paths:
            return

        Make.logger.debug('%s: delete %s outputs: %s',
                          Step.current.stack, reason, ' '.join(paths))

        for path in paths:
            path = os.path.abspath(path)
            if os.path.isfile(path):
                os.remove(path)
            else:
                shutil.rmtree(path)

            while self.delete_empty_directories:
                path = os.path.dirname(path)
                try:
                    os.rmdir(path)
                except BaseException:
                    return

    @staticmethod
    def _collect_glob(patterns: List[str]) -> Tuple[List[str], List[str]]:
        existing: List[str] = []
        missing: List[str] = []
        for pattern in patterns:
            paths = glob(pattern)
            if paths:
                existing += paths
            else:
                missing.append(pattern)

        return existing, missing

    def _collect_outputs(self) -> List[str]:
        output: List[str] = []
        for pattern in self.output:
            output += glob(pattern)
        return output

    @staticmethod
    def _raise_missing(direction: str, patterns: List[str]) -> None:
        if not patterns:
            return

        pattern = patterns[0]
        expanded = expand(pattern)[0]

        if expanded == pattern:
            raise RuntimeError('Missing %s(s): %s '
                               'for the action step: %s'
                               % (direction, pattern, Step.current.stack))

        raise RuntimeError('Missing %s(s): %s '
                           'for the pattern: %s '
                           'for the action step: %s'
                           % (direction, expanded, pattern, Step.current.stack))


def _ns2str(nanoseconds: Optional[int]) -> str:
    if nanoseconds is None:
        return 'None'
    seconds = nanoseconds // 1e9
    nanoseconds = int(nanoseconds % 1e9)
    return '{}.{:09d}'.format(datetime.fromtimestamp(seconds).strftime('%Y-%m-%d %H:%M:%S'),
                              nanoseconds)


def plan() -> Callable[[Wrapped], Wrapped]:
    """
    Decorate a plan step function.
    """
    return _step(Planner)


def action() -> Callable[[Wrapped], Wrapped]:
    """
    Decorate an action step function.
    """
    return _step(Agent)


def _step(make: type) -> Callable[[Wrapped], Wrapped]:
    def _wrap_function(wrapped: Wrapped) -> Wrapped:
        function = _callable_function(wrapped)

        def _wrapper_function(*args: Any, **kwargs: Any) -> Any:
            return Step.call_current(make, function, args, kwargs)

        setattr(_wrapper_function, '_dynamake_wrapped_function', function)
        setattr(function, '_dynamake_positional_argument_names',
                _positional_argument_names(function))

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

    return _wrap_function


def _callable_function(wrapped: Callable) -> Callable:
    if isinstance(wrapped, staticmethod):
        return wrapped.__func__
    return wrapped


def _positional_argument_names(function: Callable) -> List[str]:
    names: List[str] = []
    for parameter in inspect.signature(function).parameters.values():
        if parameter.kind in [inspect.Parameter.POSITIONAL_ONLY,
                              inspect.Parameter.POSITIONAL_OR_KEYWORD]:
            names.append(parameter.name)
    return names


def expand(*patterns: Strings) -> List[str]:
    """
    Expand the value of the current known parameters for each ``...{name}...`` inside the patterns.

    See :py:func:`dynamake.patterns.expand_strings`.
    """
    return dp.expand_strings(Step.current.wildcards, *patterns)


def glob(*patterns: Strings) -> List[str]:
    """
    Return the path of each existing file matching any of the ``glob`` pattern, using the value of
    the current known parameters for each ``...{name}...`` in the pattern.

    See :py:func:`dynamake.patterns.glob_strings`.
    """
    return dp.glob_strings(Step.current.wildcards, *patterns)


def extract(pattern: str, *strings: Strings) -> List[Dict[str, Any]]:
    """
    Extract the value of each ``...{*name}...`` in from the strings, using value of the current
    known parameters for each ``...{name}...`` in the pattern.

    It is the caller's responsibility to ensure that all the strings capture the same set of names.
    If they don't, the resulting wildcards dictionaries will have different sets of keys.

    See :py:func:`dynamake.patterns.extract_strings`.
    """
    return dp.extract_strings(Step.current.wildcards, pattern, *strings)


def capture(*patterns: Strings) -> Captured:
    """
    Capture the value of each ``...{*name}...`` in a ``glob`` pattern, using value of the current
    known parameters for each ``...{name}...`` in the pattern.

    It is the caller's responsibility to ensure that all the patterns capture the same set of names.
    If they don't, the resulting wildcards dictionaries will have different sets of keys.

    See :py:func:`dynamake.patterns.capture_globs`.
    """
    return dp.capture_globs(Step.current.wildcards, *patterns)


def foreach(wildcards: List[Dict[str, Any]], function: Callable, *args: Any, **kwargs: Any) \
        -> List[Any]:
    """
    Invoke a function for each set of parameters in the specified wildcards, one at a time.

    Any arguments (positional or named) whose value is a string will be
    :py:func:`dynamake.make.expand`-ed using the current known parameters as well as the specified
    wildcards.
    """
    results = []

    for values in wildcards:
        expanded_values = Step.current.wildcards.copy()
        expanded_values.update(values)

        expanded_args: List[str] = []
        for arg in args:
            if isinstance(arg, str):
                expanded_args.append(arg.format(**expanded_values))
            else:
                expanded_args.append(arg)

        expanded_kwargs: Dict[str, Any] = {}
        for name, arg in kwargs.items():
            if isinstance(arg, str):
                expanded_kwargs[name] = arg.format(**expanded_values)
            else:
                expanded_kwargs[name] = arg

        results.append(function(*expanded_args, **expanded_kwargs))

    return results


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
    return Step.current.config_param(name, default)


def config_file() -> str:
    """
    Access the path of the generated configuration file for the current step.

    If this is invoked, it is assumed the file is passed to some invoked action,
    so no testing is done to ensure all specified parameters are actually used.
    """
    return Step.current.config_file()


def main(parser: argparse.ArgumentParser, default_step: Callable) -> None:
    """
    A generic ``main`` function for build scripts.
    """
    if not hasattr(default_step, '_dynamake_wrapped_function'):
        raise RuntimeError('The function: %s.%s is not a DynaMake step'
                           % (default_step.__module__, default_step.__qualname__))

    _add_arguments(parser, default_step)
    args = parser.parse_args()
    _configure_by_arguments(args)
    _call_steps(default_step, args)


def _add_arguments(parser: argparse.ArgumentParser, default_step: Callable) -> None:
    parser.add_argument('-c', '--config', metavar='CONFIG.yaml',
                        help='The configuration file to use (default: Config.yaml)')

    parser.add_argument('-ll', '--log_level', metavar='LEVEL', default='INFO',
                        help='The log level to use (default: INFO)')

    parser.add_argument('-mi', '--missing_inputs', metavar='POLICY', type=_mi2enum,
                        help='default: forbidden; other options: assume_up_to_date, optional')

    parser.add_argument('-mo', '--missing_outputs', metavar='POLICY', type=_mo2enum,
                        help='default: forbidden; other options: partial, optional')

    parser.add_argument('-dso', '--delete_stale_outputs', metavar='BOOL',
                        type=_str2bool, nargs='?', const=True,
                        help='Whether to delete outputs before executing actions '
                             '(default: %s)' % Make.delete_stale_outputs)

    parser.add_argument('-tso', '--touch_success_outputs', metavar='BOOL', nargs='?',
                        type=_str2bool, const=True,
                        help='Whether to touch output files after successful actions '
                             '(default: %s)' % Make.touch_success_outputs)

    parser.add_argument('-dfo', '--delete_failed_outputs', metavar='BOOL', nargs='?',
                        type=_str2bool, const=True,
                        help='Whether to delete outputs after failed actions '
                             '(default: %s)' % Make.delete_failed_outputs)

    parser.add_argument('-ded', '--delete_empty_directories', metavar='BOOL',
                        type=_str2bool, nargs='?', const=True,
                        help='Whether to delete empty directories containing deleted outputs '
                             '(default: %s)' % Make.delete_empty_directories)

    parser.add_argument('-p', '--parameter', metavar='NAME=VALUE', action='append',
                        help='Specify a value for a top-level step parameter')

    parser.add_argument('step', metavar='FUNCTION', nargs='*',
                        help='The top-level step function(s) to execute (default: %s)'
                        % getattr(default_step, '_dynamake_wrapped_function').__name__)


def _str2bool(string: str) -> bool:
    if string.lower() in ['yes', 'true', 't', 'y', '1']:
        return True
    if string.lower() in ['no', 'false', 'f', 'n', '0']:
        return False
    raise argparse.ArgumentTypeError('Boolean value expected.')


def _str2enum(enum: type, string: str) -> Any:
    try:
        return enum[string.lower()]  # type: ignore
    except BaseException:
        raise argparse.ArgumentTypeError('Expected one of: %s'
                                         % ' '.join([value.name for value in enum]))  # type: ignore


def _mi2enum(string: str) -> MissingInputs:
    return _str2enum(MissingInputs, string)


def _mo2enum(string: str) -> MissingOutputs:
    return _str2enum(MissingOutputs, string)


def _configure_by_arguments(args: argparse.Namespace) -> None:
    if args.config is None and os.path.exists('Config.yaml'):
        args.config = 'Config.yaml'
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

    Make.logger.setLevel(_get('log_level', str, 'INFO'))

    Make.missing_inputs = _get('missing_inputs', _mi2enum, Make.missing_inputs)
    Make.missing_outputs = _get('missing_outputs', _mo2enum, Make.missing_outputs)

    Make.delete_stale_outputs = \
        _get('delete_stale_outputs', _str2bool, Make.delete_stale_outputs)

    Make.touch_success_outputs = \
        _get('touch_success_outputs', _str2bool, Make.touch_success_outputs)

    Make.delete_failed_outputs = \
        _get('delete_failed_outputs', _str2bool, Make.delete_failed_outputs)

    Make.delete_empty_directories = \
        _get('delete_empty_directories', _str2bool, Make.delete_empty_directories)

    for parameter_name in config_values:
        if parameter_name not in used_values and not parameter_name.endswith('?'):
            raise RuntimeError('Unused top-level configuration parameter: %s ' % parameter_name)


def _call_steps(default_step: Callable, args: argparse.Namespace) -> None:
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
        for parameter in inspect.signature(function).parameters.values():
            if parameter.name not in step_parameters:
                raise RuntimeError('Missing top-level parameter: %s for the step: /%s'
                                   % (parameter.name, function.__name__))
            kwargs[parameter.name] = step_parameters[parameter.name]
            used_parameters.add(parameter.name)
        step_function(**kwargs)

    if not args.step:
        _call_step(default_step)
    else:
        for step_name in args.step:
            if step_name not in Make.step_by_name:
                raise RuntimeError('Unknown step: %s' % step_name)
        for step_name in args.step:
            _call_step(Make.step_by_name[step_name])

    for parameter_name in step_parameters:
        if parameter_name not in used_parameters:
            raise RuntimeError('Unused top-level step parameter: %s' % parameter_name)
