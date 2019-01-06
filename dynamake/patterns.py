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
#: nested types.
Strings = Union[str,
                List[str],
                List[List[str]],
                List[List[List[str]]],
                List[List[List[List[str]]]]]


def foreach_string(*args: Strings) -> Iterator[str]:
    """
    Flatten an arbitrarily nested list of strings into a simple list for processing.
    """
    for strings in args:
        if isinstance(strings, str):
            yield strings
        else:
            yield from foreach_string(*strings)


def expand_strings(wildcards: List[Dict[str, Any]], *patterns: Strings) -> Strings:
    """
    Given a list of wildcard dictionaries, and a pattern (Python format string),
    generate one output per dictionary using the pattern.
    """
    return [pattern.format(**values)
            for values in wildcards for pattern in foreach_string(*patterns)]


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


def capture_strings(wildcards: Dict[str, Any], capture: str, *strings: Strings) \
        -> List[Dict[str, Any]]:
    """
    Given a capture pattern containing ``...{name}...{*captured_name}...``, and some strings which
    must match the pattern, return a list of dictionaries containing the captured values for each
    string.

    Parameters
    ----------
    wildcards
        Provide the values for expanding ``...{name}...``.
    capture
        The pattern containing ``...{name}...`` and ``...{*captured_name}...``.
    strings
        The strings to capture values froms.

    Returns
    -------
    List[Dict[str, Any]]
        A list of the dictionary of values for the captured names from each string.
    """
    regexp = capture2re(capture).format(**wildcards)
    return [_capture_string(capture, regexp, string) for string in foreach_string(*strings)]


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


def capture_glob(wildcards: Dict[str, Any], capture: str) -> List[Dict[str, Any]]:
    """
    Given a glob pattern containing ``...{name}...{*captured_name}...``,
    return a list of dictionaries containing the captured values for each
    existing file that matches the pattern.

    Parameters
    ----------
    wildcards
        Provide the values for expanding ``...{name}...``.
    capture
        The pattern containing ``...{name}...`` and ``...{*captured_name}...``. This serves both to
        specify the glob pattern (where ``{*captured_name}`` is converted to ``*`` and
        ``{**captured_name}`` is converted to ``**``, and the specify the keys in the dictionary to
        fill with the captured matching parts of the existing file names that match this glob
        pattern.

    Returns
    -------
    List[Dict[str, Any]]
        A list of the dictionary of values for the captured names from each existing file
        that matches the capture glob pattern.
    """
    return capture_strings(wildcards, capture,
                           glob_files(capture2glob(capture).format(**wildcards)))
