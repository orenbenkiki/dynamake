"""
Utilities for configurable applications.
"""

import re
import sys
from argparse import ArgumentParser
from argparse import Namespace
from ast import Load
from ast import Name
from ast import NodeVisitor
from ast import parse
from inspect import Parameter
from inspect import getsource
from inspect import signature
from textwrap import dedent
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


class ConfigurableFunction:
    """
    Data collected for a configurable function.
    """

    #: The current known functions.
    by_name: Dict[str, 'ConfigurableFunction']

    #: The name of one function that uses each configurable parameter.
    name_by_parameter: Dict[str, str]

    _is_finalized: bool

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        ConfigurableFunction.by_name = {}
        ConfigurableFunction.name_by_parameter = {}
        ConfigurableFunction._is_finalized = False

    def __init__(self, wrapped: Wrapped) -> None:
        """
        Register a configurable function.
        """

        function = _real_function(wrapped)

        if ConfigurableFunction._is_finalized:
            raise RuntimeError('Registering the function: %s.%s after ConfigurableFunction.finalize'
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
            ConfigurableFunction.name_by_parameter[parameter_name] = self.name

        #: Whether the function has non-configurable arguments.
        self.has_positional_arguments = has_positional_arguments

        #: The list of parameter names the function (directly) configures.
        self.direct_parameter_names = parameter_names

        #: The set of parameter names the function (indirectly) depends on. This starts identical to
        #: the direct parameter names and is later expanded to include the names of indirectly
        #: invoked functions.
        self.indirect_parameter_names = parameter_names.copy()

        def _wrapper_function(*args: Any, **kwargs: Any) -> Any:
            assert AppParams.current is not None
            for name in parameter_names:
                if name not in kwargs:
                    kwargs[name] = AppParams.current.get(name, function)
            return function(*args, **kwargs)

        #: The wrapper we place around the real function.
        self.wrapper = _wrapper_function

    @staticmethod
    def collect(wrapped: Wrapped) -> 'ConfigurableFunction':
        """
        Collect a configurable function.
        """
        configurable = ConfigurableFunction(wrapped)

        if configurable.name in ConfigurableFunction.by_name:
            function = configurable.function
            conflicting = ConfigurableFunction.by_name[configurable.name].function
            raise RuntimeError('Conflicting definitions for the function: %s '
                               'in both: %s.%s '
                               'and: %s.%s'
                               % (configurable.name,
                                  conflicting.__module__, conflicting.__qualname__,
                                  function.__module__, function.__qualname__))

        ConfigurableFunction.by_name[configurable.name] = configurable
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
        if ConfigurableFunction._is_finalized:
            return
        ConfigurableFunction._is_finalized = True
        ConfigurableFunction._finalize_invoked_functions()
        ConfigurableFunction._finalize_indirect_parameter_names()

    @staticmethod
    def _finalize_invoked_functions() -> None:
        for configurable in ConfigurableFunction.by_name.values():
            invoked_function_names: Set[str] = set()
            for name in configurable.invoked_function_names:
                if name in ConfigurableFunction.by_name:
                    invoked_function_names.add(name)
            configurable.invoked_function_names = invoked_function_names

    @staticmethod
    def _finalize_indirect_parameter_names() -> None:
        # TODO: This can be made much more efficient.
        current_modified_function_names = set(ConfigurableFunction.by_name.keys())
        while current_modified_function_names:
            next_modified_function_names: Set[str] = set()
            for configurable in ConfigurableFunction.by_name.values():
                for invoked_name \
                        in current_modified_function_names & configurable.invoked_function_names:
                    new_parameter_names = \
                        ConfigurableFunction.by_name[invoked_name].indirect_parameter_names \
                        - configurable.indirect_parameter_names
                    if new_parameter_names:
                        configurable.indirect_parameter_names.update(new_parameter_names)
                        next_modified_function_names.add(configurable.name)
            current_modified_function_names = next_modified_function_names


ConfigurableFunction.reset()


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
    return ConfigurableFunction.collect(wrapped).wrapper  # type: ignore


class AppParams:
    """
    Hold all the configurable parameters for a (hopefully small) program execution.
    """

    #: The global arguments currently in effect.
    #: This is typically set in the ``main`` function.
    current: Optional['AppParams']

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        AppParams.current = None

    def __init__(self, **parameters: Tuple[Any, Callable[[str], Any], str]) -> None:
        """
        Create a collection of parameters.

        Each parameter is a tuple containing its default value, a function for parsing its value
        from a string, and a description for the help message.
        """

        #: The known parameters.
        self.parameters = parameters

        #: The value for each parameter.
        self.values: Dict[str, Any] = {name: parameter[0] for name, parameter in parameters.items()}

        for parameter_name, function_name in ConfigurableFunction.name_by_parameter.items():
            if parameter_name not in self.parameters:
                function = ConfigurableFunction.by_name[function_name].function
                raise RuntimeError('Missing the parameter: %s of the configurable function: %s.%s'
                                   % (parameter_name, function.__module__, function.__qualname__))

        for parameter_name in self.parameters:
            if parameter_name not in ConfigurableFunction.name_by_parameter:
                raise RuntimeError('Unused parameter: %s' % parameter_name)

    def get(self, name: str, function: Callable) -> Any:
        """
        Access the value of some parameter.
        """
        if name not in self.values:
            raise RuntimeError('Unknown parameter: %s used by the function: %s.%s'
                               % (name, function.__module__, function.__qualname__))
        return self.values[name]

    def add_to_parser(self, parser: ArgumentParser,
                      functions: Optional[List[str]] = None) -> None:
        """
        Add a command line flag for each parameter to the parser to allow overriding parameter
        values directly from the command line.

        If a list of functions is provided, the program is assumed to take as its 1st parameter
        the name of the function to invoke, and will only accept command line parameters that
        are used by that function.
        """
        parser.add_argument('--config', metavar='FILE', action='append',
                            help='Load a parameters configuration YAML file.')

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
        for name, (default, _, description) in self.parameters.items():
            configurable.add_argument('--' + name, help=description + ' (default: %s)' % default)

    def _add_sub_commands_parameters(self, parser: ArgumentParser, functions: List[str]) -> None:
        ConfigurableFunction.finalize()
        subparsers = parser.add_subparsers(dest='command', help=dedent("""
            The specific function to compute. Run `%s foo -h` to list the specific
            parameters for the function `foo`.
        """ % sys.argv[0].split('/')[-1]))
        for command_name in functions:
            if command_name not in ConfigurableFunction.by_name:
                raise RuntimeError('Unknown command function: %s' % command_name)
            configurable = ConfigurableFunction.by_name[command_name]
            function = configurable.function
            if configurable.has_positional_arguments:
                raise RuntimeError("Can't directly invoke the function: %s.%s "
                                   'since it has positional arguments'
                                   % (function.__module__, function.__qualname__))
            description = function.__doc__
            sentence = first_sentence(description)
            command_parser = \
                subparsers.add_parser(configurable.name, help=sentence, description=description)
            for name, (default, _, description) in self.parameters.items():
                if name in configurable.indirect_parameter_names:
                    command_parser.add_argument('--' + name,
                                                help=description + ' (default: %s)' % default)

    def parse_args(self, args: Namespace) -> None:
        """
        Update the values based on loaded configuration files and/or explicit command line flags.
        """
        for path in (args.config or []):
            self.load(path)

        for name, (_, parser, _) in self.parameters.items():
            value = vars(args).get(name)
            if value is not None:
                try:
                    self.values[name] = parser(value)
                except BaseException:
                    raise RuntimeError('Invalid value: %s for the parameter: %s'
                                       % (vars(args)[name], name))

    @staticmethod
    def call_with_args(args: Namespace) -> Any:
        """
        If a command function was specified on the command line, invoke it with the current
        parameters.
        """
        return ConfigurableFunction.by_name[args.command].wrapper()

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
                    value = self.parameters[name][1](value)
                except BaseException:
                    raise RuntimeError('Invalid value: %s for the parameter: %s'
                                       % (value, name))

            self.values[name] = value


AppParams.reset()
