"""
Decorate build steps.
"""

from typing import Callable
from typing import TypeVar

#: The type of a wrapped function.
Wrapped = TypeVar('Wrapped', bound=Callable)

def step() -> Callable[[Wrapped], Wrapped]:
    def _wrap_function(function: Wrapped) -> Wrapped:
        wrapper = klass(function)

        def _wrapper_function(*args: Any, **kwargs: Any) -> Any:
            begin_time = datetime.now()

            global _WITH_OVERRIDE
            _apply_with_override(kwargs, _WITH_OVERRIDE)
            old_override = _WITH_OVERRIDE
            _WITH_OVERRIDE = None
            try:
                context = Context(None, wrapper, args, kwargs)
                try:
                    return context.call()
                finally:
                    context.close(begin_time)
            finally:
                _WITH_OVERRIDE = old_override

        setattr(_wrapper_function, '_wrapped', function)
        return _wrapper_function  # type: ignore

    return _wrap_function


