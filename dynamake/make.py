"""
Utilities for dynamic make.
"""

import inspect
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import TypeVar

import dynamake.patterns as dp

from .config import Config
from .config import Rule
from .patterns import Strings

#: The type of a wrapped function.
Wrapped = TypeVar('Wrapped', bound=Callable)


class Make:
    """
    Global build state.
    """

    #: The current build state.
    current: 'Make'

    @staticmethod
    def call_step(make: type, function: Callable,
                  args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Any:
        """
        Invoke the specified build step function.
        """
        parent = Make.current
        Make.current = make(parent, function, args, kwargs)
        try:
            return Make.current.call()
        finally:
            Make.current = parent

    def __init__(self, parent: Optional['Make'], function: Optional[Callable],
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

        #: The current known parameters (expandable wildcards).
        self.wildcards = kwargs.copy()
        if function is not None:
            for name, arg \
                    in zip(getattr(self.function, '_dynamake_positional_argument_names'), args):
                self.wildcards[name] = arg

        #: The name of the current step function.
        self.step = '' if function is None else function.__name__

        #: The caller invocation.
        self.parent: Make

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
                self.stack = parent.stack + self.step
            else:
                self.stack = parent.stack + '/' + self.step
            if isinstance(parent, Agent):
                raise RuntimeError('The nested step: %s.%s '
                                   'is invoked from an action step: %s.%s'
                                   % (self.function.__module__, self.function.__qualname__,
                                      parent.function.__module__, parent.function.__qualname__))

    def call(self) -> Any:
        """
        Invoke the build action.
        """
        return self.function(*self.args, **self.kwargs)

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Make.current = Planner(None, None, (), {})


class Planner(Make):
    """
    Implement a planning step.
    """


class Agent(Make):
    """
    Implement an action step.
    """


Make.reset()


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
        setattr(function, '_dynamake_positional_argument_names',
                _positional_argument_names(function))

        def _wrapper_function(*args: Any, **kwargs: Any) -> Any:
            return Make.call_step(make, function, args, kwargs)
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


def expand(pattern: str) -> str:
    """
    Expand the value of the current known parameters for each ``...{name}...`` inside the string.
    """
    return pattern.format(**Make.current.wildcards)


def glob(*pattern: Strings) -> List[Dict[str, Any]]:
    """
    Capture the value of each ``...{*name}...`` in a ``glob`` pattern, using value of the current
    known parameters for each ``...{name}...`` in the pattern.
    """
    return dp.capture_glob(Make.current.wildcards, *pattern)


def foreach(wildcards: List[Dict[str, Any]], function: Callable, *args: Any, **kwargs: Any) \
        -> List[Any]:
    """
    Invoke a function for each combination of the specified wildcards.

    Any arguments (positional or named) whose value is a string will be
    :py:func:`dynamake.make.expand`-ed using the current known parameters.
    """
    results = []

    for values in wildcards:
        expanded_values = Make.current.wildcards.copy()
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
