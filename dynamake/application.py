"""
Utilities for configurable applications.
"""

from .patterns import *  # pylint: disable=redefined-builtin,wildcard-import,unused-wildcard-import
from argparse import _ArgumentGroup
from argparse import ArgumentParser
from argparse import Namespace
from argparse import RawDescriptionHelpFormatter
from ast import Load
from ast import Name
from ast import NodeVisitor
from ast import parse
from contextlib import contextmanager
from importlib import import_module
from inspect import getsource
from inspect import Parameter
from inspect import signature
from multiprocessing import Pool
from multiprocessing import Value
from textwrap import dedent
from threading import current_thread
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterator
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple

import ctypes
import logging
import os
import random
import sys
import time
import yaml

# pylint: disable=too-many-lines


class Env:
    """
    Marker for default for environment parameters.
    """

    def __init__(self, default_value: Any) -> None:
        """
        Optionally provide a default value for the parameter.
        """
        #: The default value for the parameter.
        self.value = default_value


def env(default_value: Any = Parameter.empty) -> Any:
    """
    Used as a default value for environment parameters.

    When a step uses this as a default value for a parameter,
    and an invocation does not specify an explicit or a configuration value for the parameter,
    then the value will be taken from the nearest parent which has a parameter with the same name.

    If a default value is provided, then it is used if no value is available from either the command
    line or the invocation.
    """
    return Env(default_value)


class Func:  # pylint: disable=too-many-instance-attributes
    """
    Data collected for a configurable function.
    """

    #: The current known functions.
    by_name: Dict[str, 'Func']

    #: The name the functions that use each configurable parameter.
    names_by_parameter: Dict[str, List[str]]

    #: Whether to collect indirect invocations and parameters.
    #:
    #: This is useful in normal applications, allowing to generate a proper help message for each
    #: command (top level function).
    #:
    #: This does not make sense when we run a make-like program, where there is no easy way to tell
    #: which function invokes which other function (as this depends on the file name patterns).
    collect_indirect_invocations: bool

    _is_finalized: bool

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Func.by_name = {}
        Func.names_by_parameter = {}
        Func._is_finalized = False
        Func.collect_indirect_invocations = True

    def __init__(self, wrapped: Callable, is_top: bool) -> None:
        """
        Register a configurable function.
        """

        function = _real_function(wrapped)

        if Func._is_finalized:
            raise RuntimeError('Registering the function: %s.%s after Func.finalize'
                               % (function.__module__, function.__qualname__))

        #: The wrapped real function that will do the work.
        self.wrapped = function

        #: Whether this is a top-level function.
        self.is_top = is_top

        #: The name used to locate the function.
        self.name = function.__name__

        #: The set of other configurable functions it invokes. This starts by collecting all
        #: potential invoked names, and is later filtered to only include configurable function
        #: names.
        self.invoked_function_names = \
            _invoked_names(function) if Func.collect_indirect_invocations else set()

        #: The set of other configurable functions that invoke it. This starts empty and is
        #: later filled.
        self.invoker_function_names: Set[str] = set()

        has_required_arguments, parameter_names, \
            env_parameter_names, parameter_defaults = _parameter_names(function)
        for parameter_name in env_parameter_names:
            Func.names_by_parameter[parameter_name] = \
                Func.names_by_parameter.get(parameter_name, []) + [self.name]

        #: The ordered parameter names.
        self.parameter_names = parameter_names

        #: The override for the parameter defaults.
        self.parameter_defaults = parameter_defaults

        #: Whether the function has non-configurable arguments.
        self.has_required_arguments = has_required_arguments

        #: The list of parameter names the function (directly) configures.
        self.direct_parameter_names = env_parameter_names

        #: The set of parameter names the function (indirectly) depends on. This starts identical to
        #: the direct parameter names and is later expanded to include the names of indirectly
        #: invoked functions.
        self.indirect_parameter_names = env_parameter_names.copy()

        def _wrapper_function(*args: Any, **kwargs: Any) -> Any:
            kwargs = self.invocation_kwargs(*args, **kwargs)
            Prog.logger.log(Prog.TRACE, 'Call: %s.%s', function.__module__, function.__qualname__)
            return function(**kwargs)

        #: The wrapper we place around the real function.
        self.wrapper = _wrapper_function

    def invocation_kwargs(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """
        Return the complete named arguments for an invocation based on the configuration parameter
        values.
        """
        assert Prog.current is not None

        for name, value in zip(self.parameter_names, args):
            if name not in kwargs:
                kwargs[name] = value

        for name in self.direct_parameter_names:
            if name in kwargs:
                continue
            if name in self.parameter_defaults \
                    and name not in Prog.current.explicit_parameters:
                kwargs[name] = self.parameter_defaults[name]
                continue
            if name not in Prog.current.parameter_values:
                raise RuntimeError('Missing value for the required parameter: %s '
                                   'of the function: %s.%s'
                                   % (name, self.wrapped.__module__, self.wrapped.__qualname__))
            kwargs[name] = Prog.current.parameter_values[name]
        return kwargs

    @staticmethod
    def collect(wrapped: Callable, is_top: bool) -> 'Func':
        """
        Collect a configurable function.
        """
        configurable = Func(wrapped, is_top)

        if configurable.name in Func.by_name:
            function = configurable.wrapped
            conflicting = Func.by_name[configurable.name].wrapped
            raise RuntimeError('Conflicting definitions for the function: %s '
                               'in both: %s.%s '
                               'and: %s.%s'
                               % (configurable.name,
                                  conflicting.__module__, conflicting.__qualname__,
                                  function.__module__, function.__qualname__))

        Func.by_name[configurable.name] = configurable
        return configurable

    @staticmethod
    def finalize() -> None:
        """
        Finalize the per-function data once all functions have been collected.

        This conservatively assumes that every mention of a configurable function name inside
        another configurable function means an invocation. It tries to do better by ignoring names
        that are assigned to (that is, are probably just local variable names). This isn't 100% safe
        but seems to work well for simple code.
        """
        if Func._is_finalized:
            return
        Func._is_finalized = True
        if Func.collect_indirect_invocations:
            Func._finalize_invoked_functions()
            Func._finalize_indirect_parameter_names()

    @staticmethod
    def _finalize_invoked_functions() -> None:
        for caller_name, caller in Func.by_name.items():
            invoked_function_names: Set[str] = set()
            for called_name in caller.invoked_function_names:
                called = Func.by_name.get(called_name)
                if called is not None:
                    called.invoker_function_names.add(caller_name)
                    invoked_function_names.add(called_name)
            caller.invoked_function_names = invoked_function_names

    @staticmethod
    def _finalize_indirect_parameter_names() -> None:
        # TODO: This can be made much more efficient.
        current_modified_function_names = set(Func.by_name.keys())
        while current_modified_function_names:
            next_modified_function_names: Set[str] = set()
            for configurable in Func.by_name.values():
                for invoked_name \
                        in current_modified_function_names & configurable.invoked_function_names:
                    new_parameter_names = \
                        Func.by_name[invoked_name].indirect_parameter_names \
                        - configurable.indirect_parameter_names
                    if new_parameter_names:
                        configurable.indirect_parameter_names.update(new_parameter_names)
                        next_modified_function_names.add(configurable.name)
            current_modified_function_names = next_modified_function_names

    @staticmethod
    def top_functions() -> List[str]:
        """
        Return the list of top-level configurable functions.
        """
        return sorted([configurable.name
                       for configurable
                       in Func.by_name.values()
                       if configurable.is_top])


def _real_function(wrapped: Callable) -> Callable:
    if isinstance(wrapped, staticmethod):
        return wrapped.__func__
    return wrapped


def _parameter_names(function: Callable) -> Tuple[bool, List[str], Set[str], Dict[str, Any]]:
    has_required_arguments = False
    parameter_names: List[str] = []
    env_parameter_names: Set[str] = set()
    parameter_defaults: Dict[str, Any] = {}
    for parameter in signature(function).parameters.values():
        parameter_names.append(parameter.name)
        if isinstance(parameter.default, Env):
            env_parameter_names.add(parameter.name)
            if parameter.default.value != Parameter.empty:
                parameter_defaults[parameter.name] = parameter.default.value
        elif parameter.default == Parameter.empty:
            has_required_arguments = True
    return has_required_arguments, parameter_names, env_parameter_names, parameter_defaults


class NamesCollector(NodeVisitor):
    """
    Collect all the names of potential invocations from an AST.
    """

    def __init__(self) -> None:
        """
        Create an empty collector.
        """
        self._names: Set[str] = set()
        self._not_names: Set[str] = set()

    def names(self) -> Set[str]:
        """
        Return the collected potential invocation names.
        """
        return self._names - self._not_names

    def visit_Name(self, node: Name) -> None:  # pylint: disable=invalid-name
        """
        Collect any identifier which might potentially be an invoked function name.
        """
        if isinstance(node.ctx, Load):
            self._names.add(node.id)
        else:
            self._not_names.add(node.id)


def _invoked_names(function: Callable) -> Set[str]:
    collector = NamesCollector()
    lines = dedent(getsource(function)).split('\n')
    while not lines[0].startswith('def ') and not lines[0].startswith('async def '):
        lines = lines[1:]
    source = '\n'.join(lines)
    collector.visit(parse(source))
    return collector.names()


def config(top: bool = False) -> Callable[[Callable], Callable]:
    """
    Decorator for configurable functions.

    If ``top`` is ``True``, this is a top-level function that can be directly invoked from the main
    function.
    """
    def _wrap(wrapped: Callable) -> Callable:
        return Func.collect(wrapped, top).wrapper
    return _wrap


class Param:  # pylint: disable=too-many-instance-attributes
    """
    Describe a configurable parameter used by one or more computation function.
    """

    #: The name of the predefines parameters.
    BUILTIN = ['log_level', 'log_context', 'jobs', 'random_seed']

    def __init__(self, *, name: str, default: Any, parser: Callable[[str], Any], description: str,
                 short: Optional[str] = None, group: Optional[str] = None,
                 order: Optional[int] = None, metavar: Optional[str] = None) -> None:
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

        #: Optional group of parameters (for generating a help message).
        #:
        #: If the group is `global options`, the parameter will be listed in this group
        #: instead of being associated with specific function(s).
        self.group = group or 'optional parameters'

        #: Optional order of parameter (in help message)
        self.order = order

        Prog.add_parameter(self)


class Prog:
    """
    Hold all the configurable parameters for a (hopefully small) program execution.
    """

    #: The default module to load.
    DEFAULT_MODULE: str

    #: The default configuration to load, if any.
    DEFAULT_CONFIG: Optional[str]

    #: The global arguments currently in effect.
    current: 'Prog'

    #: A configured logger for the program.
    logger: logging.Logger

    #: The log level for tracing calls.
    TRACE = (logging.DEBUG + logging.INFO) // 2

    #: A function to invoke before any parallel function call, to setup global state (e.g., random
    #: number generation).
    on_parallel_call: Optional[Callable[[], None]] = None

    _is_test: bool = False

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Prog.DEFAULT_MODULE = 'DynaMods'
        Prog.DEFAULT_CONFIG = None
        Prog.current = Prog()
        Prog.logger = logging.getLogger('prog')

    def __init__(self) -> None:
        """
        Create an empty collection of parameters.
        """

        #: The known parameters.
        self.parameters: Dict[str, Param] = {}

        #: The value for each parameter.
        self.parameter_values: Dict[str, Any] = {}

        #: The names of the explicitly set parameters.
        self.explicit_parameters: Set[str] = set()

    def verify(self) -> None:
        """
        Verify the collection of parameters.
        """
        for parameter_name, function_names in Func.names_by_parameter.items():
            if parameter_name not in self.parameters:
                function = Func.by_name[function_names[0]].wrapped
                raise RuntimeError('An unknown parameter: %s '
                                   'is used by the configurable function: %s.%s'
                                   % (parameter_name, function.__module__, function.__qualname__))

        for parameter_name in self.parameters:
            if parameter_name not in Func.names_by_parameter \
                    and parameter_name not in Param.BUILTIN:
                raise RuntimeError('The parameter: %s is not used by any configurable function'
                                   % parameter_name)

    @staticmethod
    def add_parameter(parameter: Param) -> None:
        """
        Add a parameter to the program.

        This is invoked automatically when a :py:class:`dynamake.application.Param` object is
        created.
        """
        if parameter.name in Prog.current.parameters:
            raise RuntimeError('Multiple definitions for the parameter: %s' % parameter.name)
        Prog.current.parameters[parameter.name] = parameter
        Prog.current.parameter_values[parameter.name] = parameter.default

    def get_parameter(self, name: str) -> Any:
        """
        Access the value of some parameter.
        """
        if name not in self.parameter_values:
            raise RuntimeError('Unknown parameter: %s' % name)
        return self.parameter_values[name]

    @staticmethod
    def load_modules() -> None:
        """
        Load all the modules specified by command line options.

        This needs to be done before we set up the command line options parser, because
        the options depend on the loaded modules. Catch-22. This therefore employs a
        brutish option detection which may not be 100% correct.
        """
        did_import = False
        for option, value in zip(sys.argv, sys.argv[1:]):
            if option in ['-m', '--module']:
                did_import = True
                import_module(value)
        if not did_import and os.path.exists(Prog.DEFAULT_MODULE + '.py'):
            import_module(Prog.DEFAULT_MODULE)

    @staticmethod
    def add_parameters_to_parser(parser: ArgumentParser,
                                 functions: Optional[List[str]] = None) -> None:
        """
        Add a command line flag for each parameter to the parser to allow overriding parameter
        values directly from the command line.

        If a list of functions is provided, it is used instead of the automatic list of top-level
        functions (annotated with ``@config(top=True)``).
        """
        Prog.current._add_parameters_to_parser(parser,  # pylint: disable=protected-access
                                               functions)

    def _add_parameters_to_parser(self, parser: ArgumentParser,
                                  functions: Optional[List[str]] = None) -> None:
        self.add_global_parameters(parser)
        if functions is None:
            functions = Func.top_functions()

        group = parser.add_argument_group('configuration parameters', dedent("""
            The optional configuration parameters are used by internal functions. The
            defaults are overriden by any configuration files given to ``--config`` and
            by the following optional explicit command-line parameters. If the same
            parameter is set in multiple locations, the last command line parameter wins
            over the last loaded configuration file.

            The file may be empty, or contain a mapping from parameter names to values.
            If the name does not end with '?', then the parameter must be one of the
            recognized parameters. Otherwise, if the name is not recognized, it is silently
            ignored.
        """))

        used_parameters: Set[str] = set()
        for function_name in functions:
            func = Prog._verify_function(function_name, False)
            used_parameters.update(func.indirect_parameter_names)

        for name, parameter in self.parameters.items():
            if name in used_parameters:
                text = parameter.description + ' (default: %s)' % parameter.default
                if parameter.short:
                    group.add_argument('--' + name, '-' + parameter.short,
                                       help=text, metavar=parameter.metavar)
                else:
                    group.add_argument('--' + name, help=text, metavar=parameter.metavar)

    @staticmethod
    def add_commands_to_parser(parser: ArgumentParser,
                               functions: Optional[List[str]] = None) -> None:
        """
        Add a command argument each top-level function.

        If a list of functions is provided, it is used instead of the automatic list of top-level
        functions (annotated with ``@config(top=True)``). For each such command argument, add a
        sub-parser with the parameters relevant for the specific function.
        """
        Prog.current._add_commands_to_parser(parser, functions)  # pylint: disable=protected-access

    def _add_commands_to_parser(self, parser: ArgumentParser,  # pylint: disable=too-many-locals
                                functions: Optional[List[str]] = None) -> None:
        verify_reachability = Func.collect_indirect_invocations and functions is None

        self.add_global_parameters(parser)
        if functions is None:
            functions = Func.top_functions()

        subparsers = parser.add_subparsers(dest='command', metavar='COMMAND', title='commands',
                                           help='The specific function to compute, one of:',
                                           description=dedent("""
            Run `%s foo -h` to list the specific parameters for the function `foo`.
        """ % sys.argv[0].split('/')[-1]))
        subparsers.required = True

        for command_name in functions:
            func = Prog._verify_function(command_name, True)
            description = func.wrapped.__doc__
            sentence = first_sentence(description)
            command_parser = subparsers.add_parser(func.name, help=sentence,
                                                   description=description,
                                                   formatter_class=RawDescriptionHelpFormatter)
            self.add_sorted_parameters(command_parser,
                                       predicate=lambda name, func=func:  # type: ignore
                                       name in func.indirect_parameter_names)

        if not verify_reachability:
            return

        for function_name, func in Func.by_name.items():
            if function_name in functions:
                continue
            if not func.invoker_function_names:
                raise RuntimeError('The configurable function: %s.%s '
                                   'is not reachable from the command line'
                                   % (func.wrapped.__module__,
                                      func.wrapped.__qualname__))

    def add_sorted_parameters(self, parser: ArgumentParser, *,
                              predicate: Optional[Callable[[str], bool]] = None,
                              extra_help: Optional[Callable[[str], str]] = None) -> None:
        """
        Add the parameters of the functions that satify the predicate (if any), allowing for
        additional help string for each one.
        """
        keys = [(parameter.group, parameter.order, name)
                for name, parameter in self.parameters.items()
                if (predicate is None or predicate(name)) and parameter.group != 'global options']
        current_group_name: Optional[str] = None
        current_group_arguments = None
        for (group_name, _order, parameter_name) in sorted(keys):
            if current_group_arguments is None or current_group_name != group_name:
                current_group_arguments = parser.add_argument_group(group_name)
                current_group_name = group_name
            parameter = self.parameters[parameter_name]
            text = parameter.description.replace('%', '%%') \
                + ' (default: %s)' % parameter.default
            if extra_help is not None:
                text += extra_help(parameter_name)
            if parameter.short:
                current_group_arguments.add_argument('--' + parameter_name,
                                                     '-' + parameter.short, help=text,
                                                     metavar=parameter.metavar)
            else:
                current_group_arguments.add_argument('--' + parameter_name, help=text,
                                                     metavar=parameter.metavar)

    def add_global_parameters(self, parser: ArgumentParser) -> _ArgumentGroup:
        """
        Add the parameters that are not tied to specific functions.
        """
        Func.finalize()
        Prog.current.verify()

        group = parser.add_argument_group('global options')

        group.add_argument('--config', '-c', metavar='FILE', action='append',
                           help='Load a parameters configuration YAML file')

        group.add_argument('--module', '-m', metavar='MODULE', action='append',
                           help='A Python module to load (containing function definitions)')

        global_parameters = [(parameter.order, parameter.name)
                             for parameter in self.parameters.values()
                             if parameter.group == 'global options']
        for _, parameter_name in sorted(global_parameters):
            parameter = self.parameters[parameter_name]
            text = parameter.description.replace('%', '%%') + ' (default: %s)' % parameter.default
            if parameter.short:
                group.add_argument('--' + parameter_name, '-' + parameter.short,
                                   help=text, metavar=parameter.metavar)
            else:
                group.add_argument('--' + parameter_name, help=text, metavar=parameter.metavar)

        return group

    @staticmethod
    def _verify_function(function_name: str, is_command: bool) -> Func:
        if function_name not in Func.by_name:
            raise RuntimeError('Unknown top function: %s' % function_name)
        configurable = Func.by_name[function_name]
        function = configurable.wrapped
        if is_command and configurable.has_required_arguments:
            raise RuntimeError("Can't directly invoke the function: %s.%s "
                               'since it has required arguments'
                               % (function.__module__, function.__qualname__))
        return configurable

    @staticmethod
    def parse_args(args: Namespace) -> None:
        """
        Update the values based on loaded configuration files and/or explicit command line flags.
        """
        Prog.current._parse_args(args)  # pylint: disable=protected-access

    def _parse_args(self, args: Namespace) -> None:
        if Prog.DEFAULT_CONFIG is not None and os.path.exists(Prog.DEFAULT_CONFIG):
            self.load(Prog.DEFAULT_CONFIG)
        for path in (args.config or []):
            self.load(path)

        for name, parameter in self.parameters.items():
            value = vars(args).get(name)
            if value is not None:
                try:
                    self.parameter_values[name] = parameter.parser(value)
                    self.explicit_parameters.add(name)
                except BaseException:
                    raise RuntimeError('Invalid value: %s for the parameter: %s'
                                       % (vars(args)[name], name))

        random_seed = self.parameter_values.get('random_seed')
        if random_seed is not None:
            if random_seed == 0:
                random_seed = int(time.monotonic() * 1_000_000_000)
                Prog.current.parameter_values['random_seed'] = random_seed
                Prog.logger.info('Using time based random seed: %s', random_seed)
            random.seed(random_seed)

        name = sys.argv[0].split('/')[-1]
        if 'command' in vars(args):
            name += ' ' + args.command

        handler = logging.StreamHandler(sys.stderr)
        log_format = '%(asctime)s - ' + name

        context = self.get_parameter('log_context')
        if context:
            log_format += ' - ' + context

        current_thread().name = '#0'
        log_format += ' - %(threadName)s - %(levelname)s - %(message)s'

        if not Prog._is_test:
            handler.setFormatter(LoggingFormatter(log_format))
            Prog.logger.addHandler(handler)

        Prog.logger.setLevel(self.get_parameter('log_level'))

    @staticmethod
    def call_with_args(args: Namespace) -> Any:
        """
        If a command function was specified on the command line, invoke it with the current
        parameters.
        """
        return Func.by_name[args.command].wrapper()

    def load(self, path: str) -> None:
        """
        Load a configuration file.
        """
        with open(path, 'r') as file:
            data = yaml.full_load(file.read())

        if data is None:
            data = {}

        if not isinstance(data, dict):
            raise RuntimeError('The configuration file: %s '
                               'does not contain a top-level mapping' % path)

        for name, value in data.items():
            is_optional_value = name.endswith('?')
            if is_optional_value:
                name = name[:-1]
                if name in data:
                    raise RuntimeError('Conflicting entries for both: %s '
                                       'and: %s? '
                                       'in the configuration file: %s'
                                       % (name, name, path))

            if name not in self.parameter_values:
                if is_optional_value:
                    continue
                raise RuntimeError('Unknown parameter: %s '
                                   'specified in the configuration file: %s'
                                   % (name, path))

            if isinstance(value, str):
                try:
                    value = self.parameters[name].parser(value)
                except BaseException:
                    raise RuntimeError('Invalid value: %s for the parameter: %s'
                                       % (value, name))

            self.parameter_values[name] = value
            self.explicit_parameters.add(name)


class Parallel:
    """
    Invoke a function in parallel, efficiently.

    ``Pool.map``  insists on pickling and sending all the invocation arguments. This is bad for two
    reasons:

    * It is inefficient. The forked process(es) already have these objects, since the fork happened
      after the list of arguments was created.

    * It is restrictive. You can't simply work around it by giving ``Pool.map`` a ``lambda`` that
      just takes an index and uses it to access whatever-you-want, because pickling this ``lambda``
      fails (mercifully; otherwise it would need to pickle all the data captured by the lambda,
      which would defeat the purpose).

    This class is a workaround. It stashes all the arguments in a global array (yikes), which is
    created before the processes are forked, and then just lets each parallel invocation access this
    array to obtain its arguments.

    Sigh.
    """

    _fork_index: Value
    _process_index: Value
    _function: Optional[Callable]
    _fixed_args: Tuple
    _fixed_kwargs: Dict[str, Any]
    _indexed_kwargs: List[Dict[str, Any]]
    _indexed_overrides: List[Dict[str, Any]]

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Parallel._fork_index = Value(ctypes.c_int32, lock=True)  # type: ignore
        Parallel._fork_index.value = 0
        Parallel._process_index = Value(ctypes.c_int32, lock=True)  # type: ignore
        Parallel._process_index.value = 0
        Parallel._function = None
        Parallel._fixed_args = ()
        Parallel._fixed_kwargs = {}
        Parallel._indexed_kwargs = []
        Parallel._indexed_overrides = []

    @staticmethod
    def _calls(invocations: int,  # pylint: disable=too-many-locals
               function: Callable, *fixed_args: Any,
               kwargs: Optional[Callable[[int], Dict[str, Any]]] = None,
               overrides: Optional[Callable[[int], Dict[str, Any]]] = None,
               **fixed_kwargs: Any) -> List[Any]:
        assert Prog.current is not None

        processes_count = min(processes(), invocations)
        assert processes_count >= 0
        if processes_count == 0:
            return []

        previous_process_index = Parallel._process_index
        previous_function = Parallel._function
        previous_fixed_args = Parallel._fixed_args
        previous_fixed_kwargs = Parallel._fixed_kwargs
        previous_indexed_kwargs = Parallel._indexed_kwargs
        previous_indexed_overrides = Parallel._indexed_overrides

        if processes_count > 1:
            with Parallel._fork_index:  # type: ignore
                Parallel._fork_index.value += 1

        Parallel._process_index = Value(ctypes.c_int32, lock=True)  # type: ignore
        Parallel._process_index.value = 0
        Parallel._function = function
        Parallel._fixed_args = fixed_args
        Parallel._fixed_kwargs = fixed_kwargs
        Parallel._indexed_kwargs = \
            [{} if kwargs is None else kwargs(index) for index in range(invocations)]
        Parallel._indexed_overrides = \
            [{} if overrides is None else overrides(index) for index in range(invocations)]

        random_seed = Prog.current.parameter_values.get('random_seed')
        if random_seed is not None:
            for index, index_overrides in enumerate(Parallel._indexed_overrides):
                index_overrides['random_seed'] = random_seed + index

        try:
            if processes_count == 1:
                return [Parallel._call(index) for index in range(invocations)]

            with Pool(processes_count, Parallel._initialize_process) as pool:
                return pool.map(Parallel._call, range(invocations))
        finally:
            Parallel._process_index = previous_process_index
            Parallel._function = previous_function
            Parallel._fixed_args = previous_fixed_args
            Parallel._fixed_kwargs = previous_fixed_kwargs
            Parallel._indexed_kwargs = previous_indexed_kwargs
            Parallel._indexed_overrides = previous_indexed_overrides

    @staticmethod
    def _call(index: int) -> Any:  # TODO: Appears uncovered since runs in a separate thread.
        assert Parallel._function is not None
        overrides = Parallel._indexed_overrides[index]
        with override(jobs=1, **overrides):
            if Prog.current.on_parallel_call is not None:
                Prog.current.on_parallel_call()
            random_seed = Prog.current.parameter_values.get('random_seed')
            if random_seed is not None:
                random.seed(random_seed)
            return Parallel._function(*Parallel._fixed_args,
                                      **Parallel._fixed_kwargs,
                                      **Parallel._indexed_kwargs[index])

    @staticmethod
    def _initialize_process() -> None:  # TODO: Appears uncovered since runs in a separate thread.
        with Parallel._process_index:  # type: ignore
            Parallel._process_index.value += 1
            process_index = Parallel._process_index.value
        fork_index = Parallel._fork_index.value
        current_thread().name = '#%s.%s' % (fork_index, process_index)


def parallel(invocations: int, function: Callable, *fixed_args: Any,
             kwargs: Optional[Callable[[int], Dict[str, Any]]] = None,
             overrides: Optional[Callable[[int], Dict[str, Any]]] = None,
             **fixed_kwargs: Any) -> List[Any]:
    """
    Invoke a function in parallel.

    Parameters
    ----------
    invocations
        The number of function invocations needed.
    fixed_args
        Positional arguments for the function, that do not depend on the invocation index.
    kwargs
        An optional ``lambda`` taking the invocation index and returning a dictionary of keyword
        arguments which do depend on the index.
    overrides
        An optional ``lambda`` taking the invocation index and returning a dictionary of parameter
        values. Each invocation will be executed under an :py:func:`dynamake.application.override`
        using these values.
    fixed_kwargs
        Other named arguments for the function, that do not depend on the invocation index.

    Returns
    -------
    List[Any]
        The list of results from all the function invocations, in order.
    """
    if invocations == 0:
        return []
    return Parallel._calls(invocations, function,  # pylint: disable=protected-access
                           *fixed_args, kwargs=kwargs, overrides=overrides, **fixed_kwargs)


def serial(*args: Any, **kwargs: Any) -> List[Any]:
    """
    Identical to :py:func:`dynamake.application.parallal` except that runs the invocations in
    serial.

    This is useful when one wants to temporarily convert a parallel call to a serial call,
    for debugging.
    """
    with override(jobs=1):
        return parallel(*args, **kwargs)


@contextmanager
def override(**values: Any) -> Iterator[None]:
    """
    Override configuration parameters for some nested calls.

    Writing:

    .. code-block:: python

        with override(foo=value):
            x = some_wrapped_function(...)

    Will execute all the configurable computations in ``some_wrapped_function``
    as if the parameter ``foo`` was configured to have the specified ``value``.
    """
    for name in values:
        if name not in Prog.current.parameter_values and name not in Param.BUILTIN:
            raise RuntimeError('Unknown override parameter: %s' % name)
    old_values = Prog.current.parameter_values
    old_explicit_parameters = Prog.current.explicit_parameters.copy()
    Prog.current.parameter_values = Prog.current.parameter_values.copy()
    Prog.current.parameter_values.update(values)
    Prog.current.explicit_parameters.update(values.keys())
    try:
        yield None
    finally:
        Prog.current.parameter_values = old_values
        Prog.current.explicit_parameters = old_explicit_parameters


def _define_parameters() -> None:
    default_jobs = os.cpu_count()
    dynamake_jobs = os.getenv('DYNAMAKE_JOBS')
    if dynamake_jobs:
        try:
            from_env = int(dynamake_jobs)
        except ValueError:
            from_env = -1
        if from_env < 0:
            Prog.logger.warn('Ignoring invalid value: %s for: DYNAMAKE_JOBS' % dynamake_jobs)
        else:
            default_jobs = from_env

    Param(name='log_level', short='ll', metavar='STR', default='WARN',
          parser=str, group='global options', description='The log level to use')

    Param(name='log_context', short='lc', metavar='STR', default=None,
          parser=str, group='global options',
          description='Optional context to include in log messages')

    Param(name='jobs', short='j', metavar='INT', default=default_jobs,
          parser=str2int(min=0), group='global options',
          description='The maximal number of parallel threads and/or processes')


def processes() -> int:
    """
    Return the number of parallel processes at the current context.

    This is restricted by the ``--jobs`` command line option, and is reduced to
    one when nested inside a parallel call.
    """
    assert Prog.current is not None
    processes_count = Prog.current.get_parameter('jobs')
    cpus = os.cpu_count()
    assert cpus is not None
    if processes_count == 0:
        return cpus
    return min(processes_count, cpus)


def use_random_seed() -> None:
    """
    Specify that the code in the current module uses random numbers.

    Invoke this at the module's top-level, so it will be invoked when imported, similarly to
    defining the parameters using :py:class:`dynamake.application.Param`.
    """
    if 'random_seed' in Prog.current.parameters:
        return
    Param(name='random_seed', metavar='INT', default=123456, parser=str2int(min=0),
          group='global options', description="""
        The random seed to use. This allows for repeatable execution with identical
        results. If this is zero then the current time will be used, which results in
        an unrepeatable execution.
    """)


def reset_application() -> None:
    """
    Reset all the current state, for tests.
    """
    Stat.reset()
    Func.reset()
    Prog.reset()
    Parallel.reset()
    _define_parameters()


def main(parser: ArgumentParser, functions: Optional[List[str]] = None,
         *, adapter: Optional[Callable[[Namespace], None]] = None) -> None:
    """
    A generic ``main`` function for configurable functions.

    The optional ``adapter`` may perform additional adaptation of the execution environment based on
    the parsed command-line arguments before the actual function(s) are invoked.
    """
    Prog.load_modules()
    Prog.add_commands_to_parser(parser, functions)
    args = parser.parse_args()
    Prog.parse_args(args)
    if adapter is not None:
        adapter(args)
    Prog.call_with_args(args)


logging.addLevelName(Prog.TRACE, 'TRACE')
reset_application()
