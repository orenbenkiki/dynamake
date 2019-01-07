"""
Allow simple loading of regular expressions in configuration YAML files.
"""

import re
from curses.ascii import isalnum
from glob import glob as glob_files
from typing import Any
from typing import Dict
from typing import Iterator
from typing import List
from typing import Sequence
from typing import Union
from typing.re import Pattern  # pylint: disable=import-error

import yaml
from yaml import Loader
from yaml import Node


def glob2re(glob: str) -> str:  # pylint: disable=too-many-branches
    """
    Translate a ``glob`` pattern to the equivalent ``re.Pattern``.

    This is subtly different from ``fnmatch.translate`` since we use it to match the result of a
    successful ``glob`` rather than to actually perform the ``glob``.
    """
    index = 0
    size = len(glob)
    results: List[str] = []

    while index < size:
        char = glob[index]
        index += 1

        if char == '*':
            if index < size and glob[index] == '*':
                index += 1
                if results and results[-1] == '/' and index < size and glob[index] == '/':
                    results.append('(.*/)?')
                    index += 1
                else:
                    results.append('.*')
            else:
                results.append('[^/]*')

        elif char == '?':
            results.append('[^/]')

        elif char == '[':
            end_index = index
            while end_index < size and glob[end_index] != ']':
                end_index += 1

            if end_index >= size:
                results.append('\\[')

            else:
                characters = glob[index:end_index].replace('\\', '\\\\')
                index = end_index + 1

                results.append('[')

                if characters[0] == '!':
                    results.append('^/')
                    characters = characters[1:]
                elif characters[0] == '^':
                    results.append('\\')

                results.append(characters)
                results.append(']')

        elif char in '{}/':
            results.append(char)

        else:
            results.append(re.escape(char))

    return ''.join(results)


def _load_glob(loader: Loader, node: Node) -> Pattern:
    return re.compile(glob2re(loader.construct_scalar(node)))


yaml.add_constructor('!g', _load_glob)


def _load_regexp(loader: Loader, node: Node) -> Pattern:
    return re.compile(loader.construct_scalar(node))


yaml.add_constructor('!r', _load_regexp)

#: An arbitrarily nested list of strings.
#:
#: This should really have been ``Strings = Union[str, List[Strings]]`` but ``mypy`` can't handle
#: nested types. Therefore, do not use this as a return type; as much as possible, return a concrete
#: type (``str``, ``List[str]``, etc.). Instead use ``Strings`` as an argument type, for functions
#: that :py:func:`dynamake.patterns.flatten` their arguments. This will allow the callers to easily
#: nest lists without worrying about flattening themselves.
Strings = Union[str,
                Sequence[str],
                Sequence[Sequence[str]],
                Sequence[Sequence[Sequence[str]]],
                Sequence[Sequence[Sequence[Sequence[str]]]]]


def each_string(*args: Strings) -> Iterator[str]:
    """
    Iterate on all strings in an arbitrarily nested list of strings.
    """
    for strings in args:
        if isinstance(strings, str):
            yield strings
        else:
            yield from each_string(*strings)


def flatten(*args: Strings) -> List[str]:
    """
    Flatten an arbitrarily nested list of strings into a simple list for processing.
    """
    return list(each_string(*args))


def expand_strings(wildcards: Dict[str, Any], *patterns: Strings) -> List[str]:
    """
    Given some wildcards values and a pattern (Python format string),
    generate one output per pattern using these values.
    """
    return [pattern.format(**wildcards) for pattern in each_string(*patterns)]


class Captured:
    """
    The results of a :py:func:`dynamake.make.capture` operation.
    """

    def __init__(self) -> None:
        """
        Create an empty capture results.
        """

        #: The list of existing paths that matched the capture pattern.
        self.paths: List[str] = []

        #: The list of wildcard values captured from the matched paths.
        self.wildcards: List[Dict[str, Any]] = []


def capture_globs(wildcards: Dict[str, Any], *patterns: Strings) -> Captured:
    """
    Given a glob pattern containing ``...{name}...{*captured_name}...``,
    return a list of dictionaries containing the captured values for each
    existing file that matches the pattern.

    Parameters
    ----------
    wildcards
        Provide the values for expanding ``...{name}...``.
    capture
        The pattern may contain ``...{name}...``, ``...{*captured_name}...``, as well as normal
        ``glob`` patterns (``*``, ``**``). The ``...{name}..`` is
        expanded using the provided ``wildcards``. The ``*`` and
        ``**`` are ``glob``-ed. A capture expression will cause the matching substring to be
        collected in a list of dictionaries (one per matching existing path name). Valid capture
        patterns are:

        * ``...{*captured_name}...`` is treated as if it was a ``*`` glob pattern, and the matching
          zero or more characters are entered into the dictionary under the ``captured_name`` key.

        * ``...{*captured_name:pattern}...`` is similar but allows you to explicitly specify the
          glob pattern (e.g., ``...{*foo:*.py}...`` will capture the glob pattern ``*.py`` and
          capture it under the key ``foo``.

        * ``...{**captured_name}...`` is a shorthand for ``...{*captured_name:**}...``. That is, it
          acts similarly to ``...{*captured_name}...`` except that the glob pattern is ``**``.

    Returns
    -------
    Captured
        The list of existing file paths that match the patterns, and the list of dictionaries with
        the captured values for each such path.
    """

    captured = Captured()
    for capture in each_string(*patterns):
        regexp = capture2re(capture).format(**wildcards)
        glob = capture2glob(capture).format(**wildcards)
        # Sorted to make tests deterministic.
        for path in sorted(glob_files(glob)):
            captured.paths.append(path)
            captured.wildcards.append(_capture_string(capture, regexp, path))
    return captured


def glob_strings(wildcards: Dict[str, Any], *patterns: Strings) -> List[str]:
    """
    Similar to :py:func:`dynamake.patterns.capture_globs`, except that it just returns
    the list of existing paths that match any of the expanded ``glob`` patterns,
    without doing any capturing of sub-strings. Using ``...{*captured_name}...`` is
    still allowed, but has no effect beyond being interpreted as a glob pattern.
    """
    paths: List[str] = []
    for capture in each_string(*patterns):
        glob = capture2glob(capture).format(**wildcards)
        # Sorted to make tests deterministic.
        paths += sorted(glob_files(glob))
    return paths


def extract_strings(wildcards: Dict[str, Any], capture: str, *strings: Strings) \
        -> List[Dict[str, Any]]:
    """
    Similar to :py:func:`dynamake.patterns.capture_globs`, except that it just captures
    the values of the ``...{*captured_name}...`` from the expanded strings, without
    any call to ``glob`` to discover existing file paths.

    Returns
    -------
    List[Dict[str, Any]]
        A list of the dictionary of values for the captured names from each string.
    """
    regexp = capture2re(capture).format(**wildcards)
    return [_capture_string(capture, regexp, string) for string in each_string(*strings)]


def _capture_string(capture: str, regexp: Pattern, string: str) -> Dict[str, Any]:
    match = re.fullmatch(regexp, string)
    if not match:
        raise RuntimeError('The string: %s does not match the capture pattern: %s'
                           % (string, capture))

    values = match.groupdict()
    for name, value in values.items():
        try:
            values[name] = yaml.load(value)
        except BaseException:
            pass
    return values


_REGEXP_ERROR_POSITION = re.compile(r'(.*) at position (\d+)')


def capture2re(capture: str) -> str:  # pylint: disable=too-many-statements
    """
    Translate a capture pattern to the equivalent ``re.Pattern``.
    """
    index = 0
    size = len(capture)
    results: List[str] = []

    def _is_next(expected: str) -> bool:
        nonlocal capture, index, size
        return index < size and capture[index] == expected

    def _invalid(reason: str = '') -> None:
        nonlocal capture, index
        raise RuntimeError('Invalid capture pattern:\n%s\n%s^ %s' % (capture, index * ' ', reason))

    def _expect_close() -> None:
        if not _is_next('}'):
            _invalid('missing }')
        nonlocal index
        index += 1

    def _parse_name(terminators: str) -> str:
        nonlocal capture, index, size
        start_index = index
        while index < size and capture[index] not in terminators:
            if capture[index] != '_' and not isalnum(capture[index]):
                _invalid('invalid captured name character')
            index += 1
        if index == start_index:
            _invalid('empty captured name')
        return capture[start_index:index]

    def _parse_regexp() -> str:
        nonlocal capture, index, size

        if not _is_next(':'):
            return ''
        index += 1

        start_index = index
        while index < size and capture[index] != '}':
            index += 1

        if index == start_index:
            _invalid('empty captured regexp')

        return glob2re(capture[start_index:index])

    def _parse_two_stars() -> None:
        name = _parse_name('}')
        regexp = _parse_regexp() or '.*'
        _expect_close()

        nonlocal capture, index, size, results
        if results and results[-1] == '/' and index < size and capture[index] == '/':
            index += 1
            _append_regexp(name, regexp, '(?:', '/)?')
        else:
            _append_regexp(name, regexp)

    def _parse_one_star() -> None:
        name = _parse_name(':}')
        regexp = _parse_regexp() or '[^/]*'
        _expect_close()
        _append_regexp(name, regexp)

    def _append_regexp(name: str, regexp: str, prefix: str = '', suffix: str = '') -> None:
        nonlocal results
        results.append(prefix)
        results.append('(?P<')
        results.append(name)
        results.append('>')
        results.append(regexp)
        results.append(')')
        results.append(suffix)

    while index < size:
        char = capture[index]
        index += 1

        if char == '}' and _is_next('}'):
            results.append('}}')
            index += 1

        elif char == '{' and _is_next('{'):
            results.append('{{')
            index += 1

        elif char == '{' and _is_next('*'):
            index += 1
            if _is_next('*'):
                index += 1
                _parse_two_stars()
            else:
                _parse_one_star()

        elif char in '{}/':
            results.append(char)

        else:
            results.append(re.escape(char))

    return ''.join(results)


def capture2glob(capture: str) -> str:  # pylint: disable=too-many-statements
    """
    Translate a capture pattern to the equivalent ``glob`` pattern.
    """
    index = 0
    size = len(capture)
    results: List[str] = []

    def _is_next(expected: str) -> bool:
        nonlocal capture, index, size
        return index < size and capture[index] == expected

    def _invalid(reason: str = '') -> None:
        nonlocal capture, index
        raise RuntimeError('Invalid capture pattern:\n%s\n%s^ %s' % (capture, index * ' ', reason))

    def _parse_glob(glob: str, terminators: str) -> None:
        nonlocal capture, index, size
        while index < size and capture[index] not in terminators:
            index += 1
        if index < size and capture[index] == ':':
            index += 1
            start_index = index
            while index < size and capture[index] != '}':
                index += 1
            glob = capture[start_index:index]
        if not _is_next('}'):
            _invalid('missing }')
        index += 1
        results.append(glob)

    while index < size:
        char = capture[index]
        index += 1

        if char == '}' and _is_next('}'):
            results.append('}')
            index += 1

        elif char == '{' and _is_next('{'):
            results.append('{')
            index += 1

        elif char == '{' and _is_next('*'):
            index += 1
            if _is_next('*'):
                index += 1
                _parse_glob('**', '}')
            else:
                _parse_glob('*', ':}')

        else:
            results.append(char)

    return ''.join(results)
