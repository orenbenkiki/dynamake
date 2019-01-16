"""
Utilities for configurable applications.
"""

import ctypes
import logging
import re
import sys
from argparse import ArgumentParser
from argparse import Namespace
from argparse import RawDescriptionHelpFormatter
from ast import Load
from ast import Name
from ast import NodeVisitor
from ast import parse
from inspect import Parameter
from inspect import getsource
from inspect import signature
from multiprocessing import Pool
from multiprocessing import Value
from textwrap import dedent
from threading import current_thread
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
from typing import TypeVar

import yaml

from .patterns import first_sentence

#: The type of a wrapped function.
Wrapped = TypeVar('Wrapped', bound=Callable)


class Func:
    """
    Data collected for a configurable function.
    """

    #: The current known functions.
    by_name: Dict[str, 'Func']

    #: The name of one function that uses each configurable parameter.
    name_by_parameter: Dict[str, str]

    _is_finalized: bool

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Func.by_name = {}
        Func.name_by_parameter = {}
        Func._is_finalized = False

    def __init__(self, wrapped: Wrapped) -> None:
        """
        Register a configurable function.
        """

        function = _real_function(wrapped)

        if Func._is_finalized:
            raise RuntimeError('Registering the function: %s.%s after Func.finalize'
                               % (function.__module__, function.__qualname__))

        #: The real function that will do the work.
        self.function = function

        #: The name used to locate the function.
        self.name = function.__name__

        #: The set of other configurable functions it invokes. This starts by collecting all
        #: potential invoked names, and is later filtered to only include configurable function
        #: names.
        self.invoked_function_names = _invoked_names(function)

        has_positional_arguments, parameter_names = _parameter_names(function)
        for parameter_name in parameter_names:
            Func.name_by_parameter[parameter_name] = self.name

        #: Whether the function has non-configurable arguments.
        self.has_positional_arguments = has_positional_arguments

        #: The list of parameter names the function (directly) configures.
        self.direct_parameter_names = parameter_names

        #: The set of parameter names the function (indirectly) depends on. This starts identical to
        #: the direct parameter names and is later expanded to include the names of indirectly
        #: invoked functions.
        self.indirect_parameter_names = parameter_names.copy()

        def _wrapper_function(*args: Any, **kwargs: Any) -> Any:
            assert Prog.current is not None
            for name in parameter_names:
                if name not in kwargs:
                    kwargs[name] = Prog.current.get(name, function)
            Prog.logger.log(Prog.TRACE, 'call: %s.%s', function.__module__, function.__qualname__)
            return function(*args, **kwargs)

        #: The wrapper we place around the real function.
        self.wrapper = _wrapper_function

    @staticmethod
    def collect(wrapped: Wrapped) -> 'Func':
        """
        Collect a configurable function.
        """
        configurable = Func(wrapped)

        if configurable.name in Func.by_name:
            function = configurable.function
            conflicting = Func.by_name[configurable.name].function
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
        Func._finalize_invoked_functions()
        Func._finalize_indirect_parameter_names()

    @staticmethod
    def _finalize_invoked_functions() -> None:
        for configurable in Func.by_name.values():
            invoked_function_names: Set[str] = set()
            for name in configurable.invoked_function_names:
                if name in Func.by_name:
                    invoked_function_names.add(name)
            configurable.invoked_function_names = invoked_function_names

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


def _real_function(wrapped: Wrapped) -> Callable:
    if isinstance(wrapped, staticmethod):
        return wrapped.__func__
    return wrapped


def _parameter_names(function: Callable) -> Tuple[bool, Set[str]]:
    has_positional_arguments = False
    parameter_names: Set[str] = set()
    for parameter in signature(function).parameters.values():
        if parameter.kind == Parameter.KEYWORD_ONLY:
            parameter_names.add(parameter.name)
        else:
            has_positional_arguments = True
    return has_positional_arguments, parameter_names


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


_DECORATOR_LINE = re.compile(r'(?m)^[@].*\n')


def _invoked_names(function: Callable) -> Set[str]:
    collector = NamesCollector()
    source = re.sub(_DECORATOR_LINE, '', dedent(getsource(function)))
    collector.visit(parse(source))
    return collector.names()


def config(wrapped: Wrapped) -> Wrapped:
    """
    Decorator for configurable functions.
    """
    return Func.collect(wrapped).wrapper  # type: ignore


class Param:
    """
    Describe a configurable parameter used by one or more computation function.
    """

    def __init__(self, *, name: str, default: Any, parser: Callable[[str], Any], description: str,
                 metavar: Optional[str] = None) -> None:
        """
        Create and register a parameter description.
        """
        #: The unique name of the parameter.
        self.name = name

        #: The value to use if the parameter is not explicitly configured.
        self.default = default

        #: How to parse the parameter value from a string (command line argument).
        self.parser = parser

        #: A description of the parameter for help messages.
        self.description = description

        #: Optional name of the command line parameter value (``metavar`` in ``argparse``).
        self.metavar = metavar

        Prog.add_parameter(self)


class Prog:
    """
    Hold all the configurable parameters for a (hopefully small) program execution.
    """

    #: The global arguments currently in effect.
    current: 'Prog'

    #: A configured logger for the progam.
    logger: logging.Logger

    #: The log level for tracing calls.
    TRACE = (logging.DEBUG + logging.INFO) // 2

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Prog.current = Prog()
        Prog.logger = logging.getLogger('prog')

    def __init__(self) -> None:
        """
        Create an empty collection of parameters.
        """

        #: The known parameters.
        self.parameters: Dict[str, Param] = {}

        #: The value for each parameter.
        self.values: Dict[str, Any] = {}

    def verify(self) -> None:
        """
        Verify the collection of parameters.
        """
        for parameter_name, function_name in Func.name_by_parameter.items():
            if parameter_name not in self.parameters:
                function = Func.by_name[function_name].function
                raise RuntimeError('Missing the parameter: %s of the configurable function: %s.%s'
                                   % (parameter_name, function.__module__, function.__qualname__))

        for parameter_name in self.parameters:
            if parameter_name not in Func.name_by_parameter:
                raise RuntimeError('Unused parameter: %s' % parameter_name)

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
        Prog.current.values[parameter.name] = parameter.default

    def get(self, name: str, function: Callable) -> Any:
        """
        Access the value of some parameter.
        """
        if name not in self.values:
            raise RuntimeError('Unknown parameter: %s used by the function: %s.%s'
                               % (name, function.__module__, function.__qualname__))
        return self.values[name]

    @staticmethod
    def add_to_parser(parser: ArgumentParser,
                      functions: Optional[List[str]] = None) -> None:
        """
        Add a command line flag for each parameter to the parser to allow overriding parameter
        values directly from the command line.

        If a list of functions is provided, the program is assumed to take as its 1st parameter
        the name of the function to invoke, and will only accept command line parameters that
        are used by that function.
        """
        Prog.current.verify()
        Prog.current._add_to_parser(parser, functions)  # pylint: disable=protected-access

    def _add_to_parser(self, parser: ArgumentParser,
                       functions: Optional[List[str]] = None) -> None:
        parser.add_argument('-c', '--config', metavar='FILE', action='append',
                            help='Load a parameters configuration YAML file.')
        parser.add_argument('-ll', '--log_level', metavar='LEVEL', default='INFO',
                            help='The log level to use (default: INFO)')

        if functions:
            self._add_sub_commands_parameters(parser, functions)
        else:
            self._add_simple_parameters(parser)

    def _add_simple_parameters(self, parser: ArgumentParser) -> None:
        configurable = parser.add_argument_group('configuration parameters', dedent("""
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
        for name, parameter in self.parameters.items():
            if parameter.default is None:
                text = parameter.description + ' (default: None)'
            else:
                text = parameter.description + ' (default: %s)' % parameter.default
            configurable.add_argument('--' + name, help=text)

    def _add_sub_commands_parameters(self, parser: ArgumentParser, functions: List[str]) -> None:
        Func.finalize()
        subparsers = parser.add_subparsers(dest='command', metavar='COMMAND', title='commands',
                                           help='The specific function to compute, one of:',
                                           description=dedent("""
            Run `%s foo -h` to list the specific parameters for the function `foo`.
        """ % sys.argv[0].split('/')[-1]))
        subparsers.required = True
        for command_name in functions:
            if command_name not in Func.by_name:
                raise RuntimeError('Unknown command function: %s' % command_name)
            configurable = Func.by_name[command_name]
            function = configurable.function
            if configurable.has_positional_arguments:
                raise RuntimeError("Can't directly invoke the function: %s.%s "
                                   'since it has positional arguments'
                                   % (function.__module__, function.__qualname__))
            description = function.__doc__
            sentence = first_sentence(description)
            command_parser = subparsers.add_parser(configurable.name, help=sentence,
                                                   description=description,
                                                   formatter_class=RawDescriptionHelpFormatter)
            for name, parameter in self.parameters.items():
                if name in configurable.indirect_parameter_names:
                    text = parameter.description + ' (default: %s)' % parameter.default
                    command_parser.add_argument('--' + name, help=text,  # type: ignore
                                                metavar=parameter.metavar)

    @staticmethod
    def parse_args(args: Namespace) -> None:
        """
        Update the values based on loaded configuration files and/or explicit command line flags.
        """
        Prog.current._parse_args(args)  # pylint: disable=protected-access

    def _parse_args(self, args: Namespace) -> None:
        for path in (args.config or []):
            self.load(path)

        for name, parameter in self.parameters.items():
            value = vars(args).get(name)
            if value is not None:
                try:
                    self.values[name] = parameter.parser(value)
                except BaseException:
                    raise RuntimeError('Invalid value: %s for the parameter: %s'
                                       % (vars(args)[name], name))

        name = sys.argv[0].split('/')[-1]
        if 'command' in vars(args):
            name += ' ' + args.command
        handler = logging.StreamHandler(sys.stderr)
        formatter = \
            logging.Formatter('%(asctime)s - ' + name
                              + ' - %(threadName)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        Prog.logger.addHandler(handler)
        Prog.logger.setLevel(vars(args).get('log_level', 'INFO'))

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
            data = yaml.load(file.read())

        if data is None:
            data = {}

        if not isinstance(data, dict):
            raise RuntimeError('The configuration file: %s '
                               'does not contain a top-level mapping' % path)

        for name, value in data.items():
            is_optional = name.endswith('?')
            if is_optional:
                name = name[:-1]
                if name in data:
                    raise RuntimeError('Conflicting entries for both: %s '
                                       'and: %s? '
                                       'in the configuration file: %s'
                                       % (name, name, path))

            if name not in self.values:
                if is_optional:
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

            self.values[name] = value


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

    _process_index: Value
    _function: Optional[Callable]
    _fixed_args: Tuple
    _fixed_kwargs: Dict[str, Any]
    _indexed_kwargs: List[Dict[str, Any]]

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Parallel._process_index = Value(ctypes.c_int32, lock=True)  # type: ignore
        Parallel._process_index.value = 0
        Parallel._function = None
        Parallel._fixed_args = ()
        Parallel._fixed_kwargs = {}
        Parallel._indexed_kwargs = []

    @staticmethod
    def call(processes: int, invocations: int, function: Callable, *fixed_args: Any,
             kwargs: Optional[Callable[[int], Dict[str, Any]]] = None,
             **fixed_kwarg: Any) -> List[Any]:
        """
        Invoke a function in parallel.

        Parameters
        ----------
        processeses
            The number of processes to fork.
        invocations
            The number of function invocations needed.
        fixed_args
            Positional arguments for the function, that do not depend on the invocation index.
        kwargs
            An optional ``lambda`` taking the invocation index and returning a dictionary of keyword
            arguments which do depend on the index.
        fixed_kwarg
            Other named arguments for the function, that do not depend on the invocation index.

        Returns
        -------
        List[Any]
            The list of results from all the function invocations, in order.
        """
        previous_function = Parallel._function
        previous_fixed_args = Parallel._fixed_args
        previous_fixed_kwargs = Parallel._fixed_kwargs
        previous_indexed_kwargs = Parallel._indexed_kwargs

        Parallel._function = function
        Parallel._fixed_args = fixed_args
        Parallel._fixed_kwargs = fixed_kwarg
        Parallel._indexed_kwargs = \
            [{} if kwargs is None else kwargs(index) for index in range(invocations)]

        try:
            with Pool(processes, Parallel._initialize_process) as pool:
                return pool.map(Parallel._call, range(invocations))
        finally:
            Parallel._function = previous_function
            Parallel._fixed_args = previous_fixed_args
            Parallel._fixed_kwargs = previous_fixed_kwargs
            Parallel._indexed_kwargs = previous_indexed_kwargs

    @staticmethod
    def _call(index: int) -> Any:  # TODO: Appears uncovered since runs in a separate thread.
        assert Parallel._function is not None
        return Parallel._function(*Parallel._fixed_args,
                                  **Parallel._fixed_kwargs,
                                  **Parallel._indexed_kwargs[index])

    @staticmethod
    def _initialize_process() -> None:  # TODO: Appears uncovered since runs in a separate thread.
        with Parallel._process_index:  # type: ignore
            Parallel._process_index.value += 1
            process_index = Parallel._process_index.value
        current_thread().name = 'ForkThread-%s' % process_index


def reset_application() -> None:
    """
    Reset all the current state, for tests.
    """
    Func.reset()
    Prog.reset()
    Parallel.reset()


logging.addLevelName(Prog.TRACE, 'TRACE')
reset_application()
