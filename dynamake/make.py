"""
Utilities for dynamic make.
"""

from typing import Any
from typing import Callable
from typing import Dict
from typing import Tuple
from typing import TypeVar

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
