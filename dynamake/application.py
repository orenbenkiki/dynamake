"""
Utilities for configurable applications.
"""

from argparse import ArgumentParser
from argparse import Namespace
from inspect import Parameter
from inspect import signature
from textwrap import dedent
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Tuple
from typing import TypeVar

import yaml


class ApplicationParameters:
    """
    Hold all the configurable parameters for a (hopefully small) program execution.
    """

    #: The global arguments currently in effect.
    #: This is typically set in the ``main`` function.
    current: 'ApplicationParameters' = None  # type: ignore

    def __init__(self, parameters: Dict[str, Tuple[Any, Callable[[str], Any], str]]) -> None:
        """
        Create a collection of parameters.

        Each parameter is a tuple containing its default value, a function for parsing its value
        from a string, and a description for the help message.
        """

        #: The known parameters.
        self.parameters = parameters

        #: The value for each parameter.
        self.values: Dict[str, Any] = {name: parameter[0] for name, parameter in parameters.items()}

    def get(self, name: str, function: Callable) -> Any:
        """
        Access the value of some parameter.
        """
        if name not in self.values:
            raise RuntimeError('Unknown parameter: %s used by the function: %s.%s'
                               % (name, function.__module__, function.__qualname__))
        return self.values[name]

    def add_to_parser(self, parser: ArgumentParser) -> None:
        """
        Add a command line flag for each parameter to the parser to allow overriding parameter
        values directly from the command line.
        """
        parser.add_argument('--config', metavar='FILE', action='append',
                            help='Load a parameters configuration YAML file.')

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

    def parse_args(self, args: Namespace) -> None:
        """
        Update the values based on loaded configuration files and/or explicit command line flags.
        """
        for path in (args.config or []):
            self.load(path)

        for name, (_, parser, _) in self.parameters.items():
            value = vars(args)[name]
            if value is not None:
                try:
                    self.values[name] = parser(value)
                except BaseException:
                    raise RuntimeError('Invalid value: %s for the parameter: %s'
                                       % (vars(args)[name], name))

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

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        ApplicationParameters.current = ApplicationParameters({})


ApplicationParameters.reset()


#: The type of a wrapped function.
Wrapped = TypeVar('Wrapped', bound=Callable)


def config(wrapped: Wrapped) -> Wrapped:
    """
    Decorator for configurable functions.
    """
    function = _real_function(wrapped)
    parameter_names = _parameter_names(function)

    def _wrapped_function(*args: Any, **kwargs: Any) -> Any:
        for name in parameter_names:
            if name not in kwargs:
                kwargs[name] = ApplicationParameters.current.get(name, function)
        return function(*args, **kwargs)

    return _wrapped_function  # type: ignore


def _real_function(wrapped: Wrapped) -> Callable:
    if isinstance(wrapped, staticmethod):
        return wrapped.__func__
    return wrapped


def _parameter_names(function: Callable) -> List[str]:
    parameter_names: List[str] = []
    for parameter in signature(function).parameters.values():
        if parameter.kind == Parameter.KEYWORD_ONLY:
            parameter_names.append(parameter.name)
    return parameter_names
