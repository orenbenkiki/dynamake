"""
Simple pattern matching for step paths.
"""

from typing import List


class Pattern:
    """
    A pattern for matching a '.'-separated path.

    A ``.+.`` will match one or more path element.
    A ``.*.`` will match zero or more path elements.
    A ``.?.`` will match any single path element.
    Otherwise, a ``.something.`` will match the exact ``something`` path element.
    """

    def __init__(self, pattern: str) -> None:
        """
        Initialize the matcher with a '.'-separated pattern.
        """
        self._parts = pattern.split('.')

    def is_match(self, path: List[str]) -> bool:
        """
        Test whether the pattern matches the path.
        """
        return Pattern._is_match(self._parts, path)

    @staticmethod
    def _is_match(parts: List[str], path: List[str]) -> bool:  # pylint: disable=too-many-return-statements
        if not parts:
            return not path

        if not path:
            for part in parts:
                if part != '*':
                    return False
            return True

        if parts[0] == '?':
            return Pattern._is_match(parts[1:], path[1:])

        if parts[0] == '+':
            return Pattern._is_match(parts[1:], path[1:]) or Pattern._is_match(parts, path[1:])

        if parts[0] == '*':
            return Pattern._is_match(parts[1:], path) or Pattern._is_match(parts, path[1:])

        if parts[0] == path[0]:
            return Pattern._is_match(parts[1:], path[1:])

        return False
