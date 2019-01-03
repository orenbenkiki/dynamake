"""
Utilities for dynamic make.
"""

from typing import Any
from typing import Callable
from typing import Dict
from typing import Tuple
from typing import TypeVar

from .config import Config
from .config import Rule

#: The type of a wrapped function.
Wrapped = TypeVar('Wrapped', bound=Callable)


def step() -> Callable[[Wrapped], Wrapped]:
    """
    Decorate a build step function.
    """
    def _wrap_function(wrapped: Wrapped) -> Wrapped:
        function = _callable_function(wrapped)

        def _wrapper_function(*args: Any, **kwargs: Any) -> Any:
            return Make.call_step(function, args, kwargs)
        return _wrapper_function  # type: ignore
    return _wrap_function


def _callable_function(wrapped: Callable) -> Callable:
    if isinstance(wrapped, staticmethod):
        return wrapped.__func__
    return wrapped


class Make:
    """
    Global build state.
    """
    @staticmethod
    def main(default_step: Wrapped) -> None:
        """
        A main program that executes a build.
        """
        default_step()

    @staticmethod
    def call_step(function: Callable, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Any:
        """
        Invoke the specified build step function.
        """
        return function(*args, **kwargs)


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
