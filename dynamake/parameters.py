"""
Common parameter handling.
"""

# pylint: disable=too-many-lines

from inspect import Parameter
from typing import Any


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
