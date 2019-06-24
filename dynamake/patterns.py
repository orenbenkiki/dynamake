"""
Utility functions for dealing with strings.

This is somewhat of a mixed bag and should arguably be split into several modules. The ``Stat``
functionality really doesn't belong here, but has cyclical dependencies with ``clean_path`` and
``glob``.
"""

from curses.ascii import isalnum
from datetime import datetime
from glob import glob as glob_files
from sortedcontainers import SortedDict  # type: ignore
from stat import S_ISDIR
from termcolor import colored
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterator
from typing import List
from typing import Optional
from typing import overload
from typing import Sequence
from typing import Union
from typing.re import Pattern  # type: ignore # pylint: disable=import-error
from yaml import Loader
from yaml import Node

import argparse
import logging
import os
import re
import shutil
import yaml

# pylint: disable=too-many-lines,redefined-builtin


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


def glob2re(glob: str) -> str:  # pylint: disable=too-many-branches
    """
    Translate a ``glob`` pattern to the equivalent ``re.Pattern`` (as a string).

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


yaml.add_constructor('!g', _load_glob, Loader=yaml.FullLoader)


def _load_regexp(loader: Loader, node: Node) -> Pattern:
    return re.compile(loader.construct_scalar(node))


yaml.add_constructor('!r', _load_regexp, Loader=yaml.FullLoader)

#: An arbitrarily nested list of strings.
#:
#: This should really have been ``Strings = Union[None, str, List[Strings]]`` but ``mypy`` can't
#: handle nested types. Therefore, do not use this as a return type; as much as possible, return a
#: concrete type (``str``, ``List[str]``, etc.). Instead use ``Strings`` as an argument type, for
#: functions that :py:func:`dynamake.patterns.flatten` their arguments. This will allow the callers
#: to easily nest lists without worrying about flattening themselves.
Strings = Union[None,
                str,
                Sequence[str],
                Sequence[Sequence[str]],
                Sequence[Sequence[Sequence[str]]],
                Sequence[Sequence[Sequence[Sequence[str]]]]]


#: Same as ``Strings`` but without the actual ``str`` type, for ``overload`` specifications.
NotString = Union[None,
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
        elif strings is not None:
            yield from each_string(*strings)


def flatten(*args: Strings) -> List[str]:
    """
    Flatten an arbitrarily nested list of strings into a simple list for processing.
    """
    return list(each_string(*args))


class AnnotatedStr(str):
    """
    A wrapper containing optional annotations.
    """

    #: Whether this was annotated by :py:func:`dynamake.patterns.optional`.
    optional = False

    #: Whether this was annotated by :py:func:`dynamake.patterns.phony`.
    phony = False

    #: Whether this was annotated by :py:func:`dynamake.patterns.exists`.
    exists = False

    #: Whether this was annotated by :py:func:`dynamake.patterns.precious`.
    precious = False

    #: Whether this was annotated by :py:func:`dynamake.patterns.emphasized`.
    emphasized = False


def copy_annotations(source: str, target: str) -> str:
    """
    Copy the annotations from one string to another.

    Returns the annotated target string.
    """
    if isinstance(source, AnnotatedStr):
        if not isinstance(target, AnnotatedStr):
            target = AnnotatedStr(target)
        target.optional = source.optional
        target.exists = source.exists
        target.phony = source.phony
        target.precious = source.precious
        target.emphasized = source.emphasized
    return target


def is_optional(string: str) -> bool:
    """
    Whether a string has been annotated as :py:func:`dynamake.patterns.optional`.
    """
    return isinstance(string, AnnotatedStr) and string.optional


def is_exists(string: str) -> bool:
    """
    Whether a string has been annotated as :py:func:`dynamake.patterns.exists`-only.
    """
    return isinstance(string, AnnotatedStr) and string.exists


def is_phony(string: str) -> bool:
    """
    Whether a string has been annotated as :py:func:`dynamake.patterns.phony`.
    """
    return isinstance(string, AnnotatedStr) and string.phony


def is_precious(string: str) -> bool:
    """
    Whether a string has been annotated as :py:func:`dynamake.patterns.precious`.
    """
    return isinstance(string, AnnotatedStr) and string.precious


def is_emphasized(string: str) -> bool:
    """
    Whether a string has been annotated as :py:func:`dynamake.patterns.emphasized`.
    """
    return isinstance(string, AnnotatedStr) and string.emphasized


# pylint: disable=function-redefined
# pylint: disable=missing-docstring,pointless-statement,multiple-statements,unused-argument

@overload
def fmt(wildcards: Dict[str, Any], string: str) -> str: ...


@overload
def fmt(wildcards: Dict[str, Any], not_string: NotString) -> List[str]: ...


@overload
def fmt(wildcards: Dict[str, Any],
        first: Strings, second: Strings, *strings: Strings) -> List[str]: ...


# pylint: enable=missing-docstring,pointless-statement,multiple-statements,unused-argument

def fmt(wildcards: Any, *strings: Any) -> Any:  # type: ignore
    """
    Similar to ``str.format``, but will format any number of strings in one call.

    In addition, this will preserve the annotations of the strings, if any.
    """
    results = \
        [copy_annotations(string, string.format(**wildcards)) for string in each_string(*strings)]
    if len(strings) == 1 and isinstance(strings[0], str):
        assert len(results) == 1
        return results[0]
    return results


# pylint: disable=missing-docstring,pointless-statement,multiple-statements,unused-argument

@overload
def optional(pattern: str) -> str: ...


@overload
def optional(not_string: NotString) -> List[str]: ...


@overload
def optional(first: Strings, second: Strings, *patterns: Strings) -> List[str]: ...

# pylint: enable=missing-docstring,pointless-statement,multiple-statements,unused-argument


def optional(*patterns: Any) -> Any:  # type: ignore
    """
    Annotate patterns as optional (for use in action ``input`` and/or ``output``).

    An optional input is allowed not to exist before the action is executed.
    This is useful if the action responds to the files but can execute without them.

    An optional output is allowed not to exist after the action is executed.
    This is useful to ensure such outputs are removed following a failed execution,
    or before a new execution.
    """
    strings: List[str] = []
    for pattern in each_string(*patterns):
        if not isinstance(pattern, AnnotatedStr):
            pattern = AnnotatedStr(pattern)
        pattern.optional = True
        strings.append(pattern)
    if len(patterns) == 1 and isinstance(patterns[0], str):
        assert len(strings) == 1
        return strings[0]
    return strings

# pylint: disable=missing-docstring,pointless-statement,multiple-statements,unused-argument


@overload
def exists(pattern: str) -> str: ...


@overload
def exists(not_string: NotString) -> List[str]: ...


@overload
def exists(first: Strings, second: Strings, *patterns: Strings) -> List[str]: ...


# pylint: enable=missing-docstring,pointless-statement,multiple-statements,unused-argument


def exists(*patterns: Any) -> Any:  # type: ignore
    """
    Annotate patterns as exist-only (for use in action ``input`` and/or ``output``).

    An exist-only input is only required to exist, but its modification date is ignored.
    Directories are always treated this way because modification date on directories
    is unreliable.

    An exist-only output is not touched following the execution, that is, the action
    ensures the file will exist, but may choose to leave it unmodified.
    """
    strings: List[str] = []
    for pattern in each_string(*patterns):
        if not isinstance(pattern, AnnotatedStr):
            pattern = AnnotatedStr(pattern)
        pattern.exists = True
        strings.append(pattern)
    if len(patterns) == 1 and isinstance(patterns[0], str):
        assert len(strings) == 1
        return strings[0]
    return strings


# pylint: disable=missing-docstring,pointless-statement,multiple-statements,unused-argument

@overload
def phony(pattern: str) -> str: ...


@overload
def phony(not_string: NotString) -> List[str]: ...


@overload
def phony(first: Strings, second: Strings, *patterns: Strings) -> List[str]: ...


# pylint: enable=missing-docstring,pointless-statement,multiple-statements,unused-argument

def phony(*patterns: Any) -> Any:  # type: ignore
    """
    Annotate patterns as phony (for use in action ``input`` and/or ``output``).

    A phony target does not exist as a disk file. When required as an input, its producer
    step is always executed, and the dependent step always executes its sub-processes.
    """
    strings: List[str] = []
    for pattern in each_string(*patterns):
        if not isinstance(pattern, AnnotatedStr):
            pattern = AnnotatedStr(pattern)
        pattern.phony = True
        strings.append(pattern)
    if len(patterns) == 1 and isinstance(patterns[0], str):
        assert len(strings) == 1
        return strings[0]
    return strings


# pylint: disable=missing-docstring,pointless-statement,multiple-statements,unused-argument

@overload
def precious(pattern: str) -> str: ...


@overload
def precious(not_string: NotString) -> List[str]: ...


@overload
def precious(first: Strings, second: Strings, *patterns: Strings) -> List[str]: ...

# pylint: enable=missing-docstring,pointless-statement,multiple-statements,unused-argument


def precious(*patterns: Any) -> Any:  # type: ignore
    """
    Annotate patterns as precious (for use in action ``output``).

    A precious output is never deleted. This covers both deletion of "stale" outputs before an
    action is run and deletion of "failed" outputs after an action has failed.
    """
    strings: List[str] = []
    for pattern in each_string(*patterns):
        if not isinstance(pattern, AnnotatedStr):
            pattern = AnnotatedStr(pattern)
        pattern.precious = True
        strings.append(pattern)
    if len(patterns) == 1 and isinstance(patterns[0], str):
        assert len(strings) == 1
        return strings[0]
    return strings


# pylint: disable=missing-docstring,pointless-statement,multiple-statements,unused-argument

@overload
def emphasized(pattern: str) -> str: ...


@overload
def emphasized(not_string: NotString) -> List[str]: ...


@overload
def emphasized(first: Strings, second: Strings, *patterns: Strings) -> List[str]: ...


# pylint: enable=missing-docstring,pointless-statement,multiple-statements,unused-argument

def emphasized(*patterns: Any) -> Any:  # type: ignore
    """
    Annotate patterns as emphasized (impacts logging command lines).

    Emphasized text in a command line is printed in color when logged. Strategic use of this makes
    it easier to figure out the actual actions in the text soup of all the command line flags.
    """
    strings: List[str] = []
    for pattern in each_string(*patterns):
        if not isinstance(pattern, AnnotatedStr):
            pattern = AnnotatedStr(pattern)
        pattern.emphasized = True
        strings.append(pattern)
    if len(patterns) == 1 and isinstance(patterns[0], str):
        assert len(strings) == 1
        return strings[0]
    return strings

# pylint: disable=missing-docstring,pointless-statement,multiple-statements,unused-argument


@overload
def color(string: str) -> str: ...


@overload
def color(not_string: NotString) -> List[str]: ...


@overload
def color(first: Strings, second: Strings, *strings: Strings) -> List[str]: ...

# pylint: enable=missing-docstring,pointless-statement,multiple-statements,unused-argument


def color(*strings: Any) -> Any:  # type: ignore
    """
    Return the strings, replacing any that were :py:func:`dynamake.patterns.emphasized` by a colored
    version.
    """
    results: List[str] = []
    for string in each_string(*strings):
        if is_emphasized(string):
            results.append(copy_annotations(string, colored(string, attrs=['bold'])))
        else:
            results.append(string)
    if len(strings) == 1 and isinstance(strings[0], str):
        assert len(results) == 1
        return results[0]
    return results


# pylint: disable=missing-docstring,pointless-statement,multiple-statements,unused-argument


@overload
def match_fmt(pattern: str, template: str, string: str) -> str: ...


@overload
def match_fmt(pattern: str, template: str, not_string: NotString) -> List[str]: ...


@overload
def match_fmt(pattern: str, template: str,
              first: Strings, second: Strings, *strings: Strings) -> List[str]: ...

# pylint: enable=missing-docstring,pointless-statement,multiple-statements,unused-argument


def match_fmt(pattern: Any, template: Any, *strings: Any) -> Any:  # type: ignore
    """
    for each of the ``strings``, capture it using the ``pattern`` and use the extracted values to
    format the ``template``.
    """
    results: List[str] = []
    for string in each_string(strings):
        wildcards = match_extract(pattern, string)
        results.append(copy_annotations(string, template.format(**wildcards[0])))
    if len(strings) == 1 and isinstance(strings[0], str):
        assert len(results) == 1
        return results[0]
    return results


# pylint: enable=function-redefined


class Captured:
    """
    The results of operations using a capture pattern.

    A capture pattern is similar to a glob pattern. However, all wildcard matches must
    be specified inside ``{...}`` as follows:

    * ``{*name}`` has the same effect as ``*``. The matching substring will be captured
      using the key ``name``.

    * ``/{**name}/`` has the same effect as ``/**/``. The matching substring will be captured
      using the key ``name``.

    If ``name`` starts with ``_`` then the matching substring will be discarded instead of being
    captured.

    If ``name`` is followed by ``:``, it must be followed by the actual glob pattern. That is,
    ``{*name}`` is a shorthand for ``{*name:*}`` and ``{**name}`` is shorthand for ``{*name:**}``.
    This allows using arbitrary match patterns (for example ``{*digit:[0-9]}`` will capture a single
    decimal digit).
    """

    def __init__(self) -> None:
        """
        Create an empty capture results.
        """

        #: The list of existing paths that matched the capture pattern.
        self.paths: List[str] = []

        #: The list of wildcard values captured from the matched paths.
        self.wildcards: List[Dict[str, Any]] = []


class NonOptionalException(Exception):
    """
    Exception when an non-optional pattern did not match any disk files.
    """

    def __init__(self, glob: str, capture: str) -> None:
        """
        Create a new exception when no disk files matched the pattern.
        """
        if capture == glob:
            super().__init__('No files matched the non-optional glob pattern: %s' % (glob))
        else:
            super().__init__('No files matched the non-optional glob: %s pattern: %s'
                             % (glob, capture))

        #: The glob pattern that failed to match.
        self.glob = glob


def glob_capture(*patterns: Strings) -> Captured:
    """
    Given capture pattern, return the :py:class:`dynamake.pattern.Captured` information (paths and
    captured values).

    Parameters
    ----------
    capture
        The pattern may contain ``...{*captured_name}...``, as well as normal
        ``glob`` patterns (``*``, ``**``). The ``...{name}..`` is expanded using the provided
        ``wildcards``. The ``*`` and ``**`` are ``glob``-ed. A capture expression will cause the
        matching substring to be collected in a list of dictionaries (one per matching existing path
        name). Valid capture patterns are:

        * ``...{*captured_name}...`` is treated as if it was a ``*`` glob pattern, and the matching
          zero or more characters are entered into the dictionary under the ``captured_name`` key.

        * ``...{*captured_name:pattern}...`` is similar but allows you to explicitly specify the
          glob pattern
          capture it under the key ``foo``.

        * ``...{**captured_name}...`` is a shorthand for ``...{*captured_name:**}...``. That is, it
          acts similarly to ``...{*captured_name}...`` except that the glob pattern is ``**``.

        If a pattern is not annotated with :py:func:`dynamake.patterns.optional` and it matches no
        existing files, an error is raised.

    Returns
    -------
    Captured
        The list of existing file paths that match the patterns, and the list of dictionaries with
        the captured values for each such path. The annotations
        (:py:class:`dynamake.patterns.AnnotatedStr`) of the pattern are copied to the paths expanded
        from the pattern.
    """
    return fmt_glob_capture({}, *patterns)


def glob_paths(*patterns: Strings) -> List[str]:
    """
    Similar to :py:func:`dynamake.patterns.glob_capture`, but just return the list of matching
    paths, ignoring any extracted values.
    """
    return glob_capture(*patterns).paths


def glob_extract(*patterns: Strings) -> List[Dict[str, Any]]:
    """
    Similar to :py:func:`dynamake.patterns.glob_capture`, but just return the list of extracted
    wildcards dictionaries, ignoring the matching paths.
    """
    return glob_capture(*patterns).wildcards


def fmt_glob_capture(wildcards: Dict[str, Any], *patterns: Strings) -> Captured:
    """
    Similar to :py:func:`dynamake.patterns.glob_capture`, but :py:func:`dynamake.patterns.fmt`
    each pattern first.

    This is not equivalent to ``glob_capture(fmt(wildcards, patterns))`` because ``fmt`` will be
    confused by the ``{*captured_name}`` in the patterns. Instead, each pattern is first converted
    to a clean ``glob`` pattern, eliminating the ``{*captured_name}`` parts, and only then is the
    ``fmt`` applied.
    """
    captured = Captured()
    for pattern in each_string(*patterns):
        regexp = capture2re(pattern)
        glob = capture2glob(pattern)
        if wildcards:
            regexp = regexp.format(**wildcards)
            glob = glob.format(**wildcards)
        paths = Stat.glob(glob)

        if not paths and not is_optional(pattern):
            raise NonOptionalException(glob, pattern)

        # Sorted to make tests deterministic.
        for path in sorted(paths):
            path = copy_annotations(pattern, path)
            captured.paths.append(path)
            captured.wildcards.append(_capture_string(pattern, regexp, path))

    return captured


def fmt_glob_paths(wildcards: Dict[str, Any], *patterns: Strings) -> List[str]:
    """
    Similar to :py:func:`dynamake.patterns.fmt_glob_capture`, but just return the list of
    matching paths, ignoring any extracted values.

    The annotations of each pattern are copied to the paths expanded from the pattern.
    """
    return fmt_glob_capture(wildcards, *patterns).paths


def fmt_glob_extract(wildcards: Dict[str, Any], *patterns: Strings) -> List[Dict[str, Any]]:
    """
    Similar to :py:func:`dynamake.patterns.fmt_glob_capture`, but just return the list of
    extracted wildcards dictionaries, ignoring the matching paths.
    """
    return fmt_glob_capture(wildcards, *patterns).wildcards


def match_extract(pattern: str, *strings: Strings) -> List[Dict[str, Any]]:
    """
    Similar to :py:func:`dynamake.patterns.glob_extract`, except that it uses just one capture
    pattern and apply it to some string(s).
    """
    return fmt_match_extract({}, pattern, *strings)


def fmt_match_extract(wildcards: Dict[str, Any],
                      pattern: str, *strings: Strings) -> List[Dict[str, Any]]:
    """
    Similar to :py:func:`dynamake.patterns.match_extract`, but :py:func:`dynamake.patterns.fmt`
    the pattern first.

    This is not equivalent to ``match_extract(fmt(wildcards, pattern), ...)`` because ``fmt`` will
    be confused by the ``{*captured_name}`` in the patterns. Instead, the pattern is first converted
    to a clean ``re`` pattern, eliminating the ``{*captured_name}`` parts, and only then is the
    ``fmt`` applied.
    """
    regexp = capture2re(pattern)
    if wildcards:
        regexp = regexp.format(**wildcards)
    return [_capture_string(pattern, regexp, string) for string in each_string(*strings)]


def _capture_string(pattern: str, regexp: Pattern, string: str) -> Dict[str, Any]:
    match = re.fullmatch(regexp, string)
    if not match:
        raise RuntimeError('The string: %s does not match the capture pattern: %s'
                           % (string, pattern))

    values = match.groupdict()
    for name, value in values.items():
        if name and name[0] != '_':
            values[name] = str(value or '')
    return values


def glob_fmt(pattern: str, *templates: Strings) -> List[str]:
    """
    For each file that matches the capture ``pattern``, extract its wildcards,
    then use them to format each of the ``templates``.
    """
    results: List[str] = []
    for wildcards in glob_extract(pattern):
        for template in each_string(*templates):
            results.append(copy_annotations(template, template.format(**wildcards)))
    return results


def fmt_glob_fmt(wildcards: Dict[str, Any], pattern: str, *templates: Strings) -> List[str]:
    """
    Similar to :py:func:`dynamake.patterns.glob_fmt`, but :py:func:`dynamake.patterns.fmt`
    the pattern first.

    This is not equivalent to ``glob_fmt(fmt(wildcards, pattern), ...)`` because ``fmt`` will be
    confused by the ``{*captured_name}`` in the patterns. Instead, the pattern is first converted to
    a clean ``re`` pattern, eliminating the ``{*captured_name}`` parts, and only then is the ``fmt``
    applied.

    In addition, the values in the ``wildcards`` are also available to the ``templates``. If these
    values are also extracted by the ``pattern``, then the extracted values will be used instead.
    """
    results: List[str] = []
    for extracted_wildcards in fmt_glob_extract(wildcards, pattern):
        for key, value in wildcards.items():
            if key not in extracted_wildcards:
                extracted_wildcards[key] = value
        for template in each_string(*templates):
            results.append(copy_annotations(template, template.format(**extracted_wildcards)))
    return results


_SPACES = re.compile(r'\s+')
_DOT_SUFFIX = re.compile('[.](com|net|org|io|gov|[0-9])')
_PREFIX_DOT = re.compile('(Mr|St|Mrs|Ms|Dr|Inc|Ltd|Jr|Sr|Co)[.]')
_FINAL_ACRONYM = re.compile('([A-Za-z])[.][ ]+'
                            '(?:Mr|Mrs|Ms|Dr|He |She |It |They |Their '
                            '|Our |We |But |However |That |This |Wherever).*')
_THREE_ACRONYM = re.compile('([A-Za-z])[.]([A-Za-z])[.]([A-Za-z])[.]')
_TWO_ACRONYM = re.compile('([A-Za-z])[.]([A-Za-z])[.]')
_ONE_ACRONYM = re.compile(' ([A-Za-z])[.] ')


def first_sentence(text: Optional[str]) -> Optional[str]:
    """
    Return the first sentence in documentation text.

    Very loosely based on `<https://stackoverflow.com/a/31505798/63376>`_.
    """
    if text is None:
        return text

    text = ' ' + text + '  '
    text = text.replace('\n', ' ')
    text = re.sub(_SPACES, ' ', text)

    for pattern, fixed in [(_DOT_SUFFIX, r'<prd>\1'),
                           (_FINAL_ACRONYM, r'\1<prd>'),
                           (_PREFIX_DOT, r'\1<prd>'),
                           (_THREE_ACRONYM, r'\1<prd>\2<prd>\3<prd>'),
                           (_TWO_ACRONYM, r'\1<prd>\2<prd>'),
                           (_ONE_ACRONYM, r' \1<prd> ')]:
        text = re.sub(pattern, fixed, text)

    for raw, fixed in [('wrt.', 'wrt<prd>'),
                       ('vs.', 'vs<prd>'),
                       ('M.Sc.', 'M<prd>Sc<prd>'),
                       ('Ph.D.', 'Ph<prd>D<prd>'),
                       ('...', '<prd><prd><prd>'),
                       ('."', '".'),
                       ('!"', '"!'),
                       ('?"', '"?')]:
        text = text.replace(raw, fixed)

    for terminator in '.?!':
        index = text.find(terminator)
        if index > 0:
            text = text[:index + 1]

    text = text.replace('<prd>', '.')

    return text.strip()


def str2bool(string: str) -> bool:
    """
    Parse a boolean command line argument.
    """
    if string.lower() in ['yes', 'true', 't', 'y', '1']:
        return True
    if string.lower() in ['no', 'false', 'f', 'n', '0']:
        return False
    raise argparse.ArgumentTypeError('Boolean value expected.')


def str2enum(enum: type) -> Callable[[str], Any]:
    """
    Return a parser for an enum command line argument.
    """
    def _parse(string: str) -> Any:
        try:
            return enum[string.lower()]  # type: ignore
        except BaseException:
            raise argparse.ArgumentTypeError('Expected one of: %s'  #
                                             % ' '.join([value.name
                                                         for value
                                                         in enum]))  # type: ignore
    return _parse


class RangeParam:
    """
    A range for a numeric argument.
    """

    def __init__(self,  # pylint: disable=too-many-arguments
                 min: Optional[float] = None,
                 max: Optional[float] = None,
                 step: Optional[int] = None,
                 include_min: bool = True,
                 include_max: bool = True) -> None:
        """
        Create a range for numeric arguments.
        """
        #: The optional minimal allowed value.
        self.min = min

        #: The optional maximal allowed value.
        self.max = max

        #: The optional step between values.
        self.step = step

        #: Whether the minimal value is allowed.
        self.include_min = include_min

        #: Whether the maximal value is allowd.
        self.include_max = include_max

    def is_valid(self, value: Union[float, int]) -> bool:
        """
        Test whether a value is valid.
        """
        if self.min is not None:
            if self.include_min:
                if value < self.min:
                    return False
            else:
                if value <= self.min:
                    return False

        if self.max is not None:
            if self.include_max:
                if value > self.max:
                    return False
            else:
                if value >= self.max:
                    return False

        if self.step is None:
            return True

        if self.min is not None:
            value -= self.min
        return (value % self.step) == 0

    def text(self) -> str:
        """
        Return text for an error message.
        """
        text = []

        if self.min is not None:
            text.append(str(self.min))
            if self.include_min:
                text.append('<=')
            else:
                text.append('<')

        if self.min is not None or self.max is not None:
            text.append('value')

        if self.max is not None:
            if self.include_max:
                text.append('<=')
            else:
                text.append('<')
            text.append(str(self.max))

        if self.step is not None:
            if self.min is not None or self.max is not None:
                text.append('and')
            text.extend(['value %', str(self.step), '=='])
            if self.min is None:
                text.append('0')
            else:
                text.append(str(self.min % self.step))

        return ' '.join(text)


def _str2range(string: str, parser: Callable[[str], Union[int, float]], range: RangeParam) \
        -> Union[int, float]:
    try:
        value = parser(string)
    except BaseException:
        raise argparse.ArgumentTypeError('Expected %s value' % parser.__name__)

    if not range.is_valid(value):
        raise argparse.ArgumentTypeError('Expected %s value, where %s'
                                         % (parser.__name__, range.text()))

    return value


def str2float(min: Optional[float] = None,
              max: Optional[float] = None,
              step: Optional[int] = None,
              include_min: bool = True,
              include_max: bool = True) -> Callable[[str], float]:
    """
    Return a parser that accepts a float argument in the specified
    :py:func:`dynamake.pattern.RangeParam`.
    """
    def _parse(string: str) -> float:
        return _str2range(string, float,
                          RangeParam(min=min, max=max, step=step,
                                     include_min=include_min,
                                     include_max=include_max))
    return _parse


def str2int(min: Optional[int] = None,
            max: Optional[int] = None,
            step: Optional[int] = None,
            include_min: bool = True,
            include_max: bool = True) -> Callable[[str], int]:
    """
    Return a parser that accepts an int argument in the specified
    :py:func:`dynamake.pattern.RangeParam`.
    """
    def _parse(string: str) -> int:
        return _str2range(string, int,  # type: ignore
                          RangeParam(min=min, max=max, step=step,
                                     include_min=include_min,
                                     include_max=include_max))
    return _parse


def str2choice(options: List[str]) -> Callable[[str], str]:
    """
    Return a parser that accepts a string argument which is one of the options.
    """
    def _parse(string: str) -> str:
        if string not in options:
            raise argparse.ArgumentTypeError('Expected one of: %s' % ' '.join(options))
        return string

    return _parse


def str2list(parser: Callable[[str], Any]) -> Callable[[str], List[Any]]:
    """
    Parse an argument which is a list of strings, where each must be parsed on its own.
    """
    def _parse(string: str) -> List[Any]:
        return [parser(entry) for entry in string.split()]
    return _parse


def str2optional(parser: Callable[[str], Any]) -> Callable[[str], Optional[Any]]:
    """
    Parse an argument which also takes the special value ``None``.
    """
    def _parse(string: str) -> Optional[Any]:
        if string.lower() == 'none':
            return None
        return parser(string)
    return _parse


class LoggingFormatter(logging.Formatter):
    """
    A formatter that uses a decimal point for milliseconds.
    """

    def formatTime(self, record: Any, datefmt: Optional[str] = None) -> str:
        """
        Format the time.
        """
        record_datetime = datetime.fromtimestamp(record.created)
        if datefmt is not None:
            return record_datetime.strftime(datefmt)

        seconds = record_datetime.strftime('%Y-%m-%d %H:%M:%S')
        return '%s.%03d' % (seconds, record.msecs)


def clean_path(path: str) -> str:
    """
    Return a clean and hopefully "canonical" path.

    We do not use absolute paths everywhere (as that would mess up the match patterns).
    Instead we just convert each `//` to a single `/`. Perhaps more is needed.
    """
    previous_path = ''
    next_path = path
    while next_path != previous_path:
        previous_path = next_path
        next_path = copy_annotations(path, next_path.replace('//', '/'))

    return next_path


class Stat:
    """
    Cache stat calls for better performance.
    """

    _cache: SortedDict

    @staticmethod
    def reset() -> None:
        """
        Clear the cached data.
        """
        Stat._cache = SortedDict()

    @staticmethod
    def stat(path: str) -> os.stat_result:
        """
        Return the ``stat`` data for a file.
        """
        return Stat._result(path, throw=True)  # type: ignore

    @staticmethod
    def try_stat(path: str) -> Optional[os.stat_result]:
        """
        Return the ``stat`` data for a file.
        """
        result = Stat._result(path, throw=False)
        if isinstance(result, BaseException):
            return None
        return result

    @staticmethod
    def exists(path: str) -> bool:
        """
        Test whether a file exists on disk.
        """
        result = Stat._result(path, throw=False)
        return not isinstance(result, BaseException)

    @staticmethod
    def isfile(path: str) -> bool:
        """
        Whether a file exists and is not a directory.
        """
        result = Stat._result(path, throw=False)
        return not isinstance(result, BaseException) and not S_ISDIR(result.st_mode)

    @staticmethod
    def isdir(path: str) -> bool:
        """
        Whether a file exists and is a directory.
        """
        result = Stat._result(path, throw=False)
        return not isinstance(result, BaseException) and S_ISDIR(result.st_mode)

    @staticmethod
    def _result(path: str, *, throw: bool) -> Union[BaseException, os.stat_result]:
        path = clean_path(path)
        result = Stat._cache.get(path)

        if result is not None and (not throw or not isinstance(result, BaseException)):
            return result

        try:
            result = os.stat(path)
        except OSError as exception:
            result = exception

        Stat._cache[path] = result

        if throw and isinstance(result, BaseException):
            raise result

        return result

    @staticmethod
    def glob(pattern: str) -> List[str]:
        """
        Fast glob through the cache.

        If the pattern is a file name we know about, we can just return the result without touching
        the file system.
        """

        path = clean_path(pattern)
        result = Stat._cache.get(path)

        if isinstance(result, BaseException):
            return []

        if result is None:
            paths = glob_files(pattern, recursive=True)
            if paths != [pattern]:
                return [clean_path(path) for path in paths]
            result = Stat._result(pattern, throw=False)
            assert not isinstance(result, BaseException)

        return [pattern]

    @staticmethod
    def forget(path: str) -> None:
        """
        Forget the cached ``stat`` data about a file. If it is a directory,
        also forget all the data about any files it contains.
        """
        path = clean_path(path)
        index = Stat._cache.bisect_left(path)
        while index < len(Stat._cache):
            index_path = Stat._cache.iloc[index]
            if os.path.commonpath([path, index_path]) != path:
                return
            Stat._cache.popitem(index)

    @staticmethod
    def rmdir(path: str) -> None:
        """
        Remove an empty directory.
        """
        os.rmdir(path)
        Stat.forget(path)

    @staticmethod
    def remove(path: str) -> None:
        """
        Force remove of a file or a directory.
        """
        if Stat.isfile(path):
            os.remove(path)
        elif Stat.exists(path):
            shutil.rmtree(path)
        Stat.forget(path)

    @staticmethod
    def touch(path: str) -> None:
        """
        Set the last modified time of a file (or a directory) to now.
        """
        os.utime(path)
        Stat.forget(path)

    @staticmethod
    def mkdir_create(path: str) -> None:
        """
        Create a new directory.
        """
        os.makedirs(path, exist_ok=False)
        Stat.forget(path)

    @staticmethod
    def mkdir_exists(path: str) -> None:
        """
        Ensure a directory exists.
        """
        if not Stat.exists(path):
            os.makedirs(path, exist_ok=True)
            Stat.forget(path)


Stat.reset()
