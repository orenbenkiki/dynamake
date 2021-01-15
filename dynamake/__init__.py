"""
DynaMake module.
"""

# pylint: disable=too-many-lines,redefined-builtin

from .version import version as __version__  # pylint: disable=unused-import
from aiorwlock import RWLock
from argparse import ArgumentParser
from argparse import Namespace
from contextlib import asynccontextmanager
from curses.ascii import isalnum
from datetime import datetime
from glob import glob as glob_files
from importlib import import_module
from inspect import iscoroutinefunction
from sortedcontainers import SortedDict  # type: ignore
from stat import S_ISDIR
from termcolor import colored
from textwrap import dedent
from threading import current_thread
from typing import Any
from typing import AsyncGenerator
from typing import Awaitable
from typing import Callable
from typing import Coroutine
from typing import Dict
from typing import Iterator
from typing import List
from typing import Optional
from typing import overload
from typing import Sequence
from typing import Set
from typing import Tuple
from typing import Union
from typing.re import Pattern  # type: ignore # pylint: disable=import-error
from urllib.parse import quote_plus
from yaml import Dumper
from yaml import Loader
from yaml import Node

import argparse
import asyncio
import logging
import os
import re
import shlex
import shutil
import sys
import yaml

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
        raise RuntimeError(f'Invalid capture pattern:\n{capture}\n{index * " "}^ {reason}')

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

    def _parse_no_star() -> None:
        results.append('{')
        results.append(_parse_name(':}'))
        _regexp = _parse_regexp() or '[^/]*'
        _expect_close()
        results.append('}')

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

        elif char == '{':
            _parse_no_star()

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
        raise RuntimeError(f'Invalid capture pattern:\n{capture}\n{index * " "}^ {reason}')

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


def _fmt_capture(kwargs: Dict[str, Any], capture: str) -> str:  # pylint: disable=too-many-statements
    index = 0
    size = len(capture)
    results: List[str] = []

    def _is_next(expected: str) -> bool:
        nonlocal capture, index, size
        return index < size and capture[index] == expected

    def _invalid(reason: str = '') -> None:
        nonlocal capture, index
        raise RuntimeError(f'Invalid capture pattern:\n{capture}\n{index * " "}^ {reason}')

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

    def _parse_regexp(to_copy: bool) -> None:
        nonlocal capture, index, size

        start_index = index
        while index < size and capture[index] != '}':
            index += 1

        if to_copy:
            results.append(capture[start_index:index])

    while index < size:
        char = capture[index]
        index += 1

        if char == '}' and _is_next('}'):
            results.append('}}')
            index += 1

        elif char == '{' and _is_next('{'):
            results.append('{{')
            index += 1

        elif char == '{':
            stars = 0
            while _is_next('*'):
                index += 1
                stars += 1
            name = _parse_name(':}')
            if name in kwargs:
                results.append(kwargs[name].replace('{', '{{').replace('}', '}}'))
                _parse_regexp(False)
                _expect_close()
            else:
                results.append('{')
                results.append(stars * '*')
                results.append(name)
                _parse_regexp(True)
                _expect_close()
                results.append('}')

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
#: functions that :py:func:`dynamake.flatten` their arguments. This will allow the callers to easily
#: nest lists without worrying about flattening themselves.
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

    #: Whether this was annotated by :py:func:`dynamake.optional`.
    optional = False

    #: Whether this was annotated by :py:func:`dynamake.phony`.
    phony = False

    #: Whether this was annotated by :py:func:`dynamake.exists`.
    exists = False

    #: Whether this was annotated by :py:func:`dynamake.precious`.
    precious = False

    #: Whether this was annotated by :py:func:`dynamake.emphasized`.
    emphasized = False


def _dump_str(dumper: Dumper, data: AnnotatedStr) -> Node:
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)


yaml.add_representer(AnnotatedStr, _dump_str)


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
    Whether a string has been annotated as :py:func:`dynamake.optional`.
    """
    return isinstance(string, AnnotatedStr) and string.optional


def is_exists(string: str) -> bool:
    """
    Whether a string has been annotated as :py:func:`dynamake.exists`-only.
    """
    return isinstance(string, AnnotatedStr) and string.exists


def is_phony(string: str) -> bool:
    """
    Whether a string has been annotated as :py:func:`dynamake.phony`.
    """
    return isinstance(string, AnnotatedStr) and string.phony


def is_precious(string: str) -> bool:
    """
    Whether a string has been annotated as :py:func:`dynamake.precious`.
    """
    return isinstance(string, AnnotatedStr) and string.precious


def is_emphasized(string: str) -> bool:
    """
    Whether a string has been annotated as :py:func:`dynamake.emphasized`.
    """
    return isinstance(string, AnnotatedStr) and string.emphasized


# pylint: disable=function-redefined
# pylint: disable=missing-docstring,pointless-statement,multiple-statements,unused-argument

@overload
def fmt(wildcards: Dict[str, Any], template: str) -> str: ...


@overload
def fmt(wildcards: Dict[str, Any], not_template: NotString) -> List[str]: ...


@overload
def fmt(wildcards: Dict[str, Any],
        first: Strings, second: Strings, *templates: Strings) -> List[str]: ...


# pylint: enable=missing-docstring,pointless-statement,multiple-statements,unused-argument

def fmt(wildcards: Any, *templates: Any) -> Any:  # type: ignore
    """
    Similar to ``str.format``, but will format any number of templates in one call.

    In addition, this will preserve the annotations of the templates, if any.
    """
    results = \
        [copy_annotations(template, template.format(**wildcards))
         for template in each_string(*templates)]
    if len(templates) == 1 and isinstance(templates[0], str):
        assert len(results) == 1
        return results[0]
    return results


# pylint: disable=missing-docstring,pointless-statement,multiple-statements,unused-argument

@overload
def fmt_capture(wildcards: Dict[str, Any], pattern: str) -> str: ...


@overload
def fmt_capture(wildcards: Dict[str, Any], not_pattern: NotString) -> List[str]: ...


@overload
def fmt_capture(wildcards: Dict[str, Any],
                first: Strings, second: Strings, *patterns: Strings) -> List[str]: ...


# pylint: enable=missing-docstring,pointless-statement,multiple-statements,unused-argument

def fmt_capture(kwargs: Any, *patterns: Any) -> Any:  # type: ignore
    """
    Format one or more capture patterns using the specified values.

    This is different from invoking ``pattern.format(**kwargs)`` on each pattern because ``format``
    would be confused by the ``{*name}`` captures in the pattern(s). In contrast, ``fmt_capture``
    will expand such directives, as long as the ``name`` does not start with ``_``.
    """
    results = [copy_annotations(pattern, _fmt_capture(kwargs, pattern))
               for pattern in each_string(*patterns)]
    if len(patterns) == 1 and isinstance(patterns[0], str):
        assert len(results) == 1
        return results[0]
    return results


# pylint: disable=missing-docstring,pointless-statement,multiple-statements,unused-argument


def fmts(wildcards_list: List[Dict[str, Any]], *templates: Strings) -> List[str]:
    """
    Similar to :py:func:`dynamake.fmt`, except expands the ``templates`` for each of the provided
    ``wildcards``.
    """
    results: List[Strings] = []
    assert results is not None
    for wildcards in wildcards_list:
        results.append(fmt(wildcards, *templates))
    return list(each_string(*results))


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
    Return the strings, replacing any that were :py:func:`dynamake.emphasized` by a colored version.
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
            super().__init__(f'No files matched the non-optional glob pattern: {glob}')
        else:
            super().__init__(f'No files matched the non-optional glob: {glob} pattern: {capture}')

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

          .. note::

            Do not use the ``{*foo:**}`` form. There's some special treatment for the ``{**foo}``
            form as it can expand to the empty string. In particular, it is expected to always be
            used between ``/`` characters, as in ``.../{**foo}/...``, and may expand to either no
            directory name, a single directory name, or a sequence of directory names.

        If a pattern is not annotated with :py:func:`dynamake.optional` and it matches no existing
        files, an error is raised.

    Returns
    -------
    Captured
        The list of existing file paths that match the patterns, and the list of dictionaries with
        the captured values for each such path. The annotations (:py:class:`dynamake.AnnotatedStr`)
        of the pattern are copied to the paths expanded from the pattern.
    """
    captured = Captured()
    for pattern in each_string(*patterns):
        regexp = capture2re(pattern)
        glob = capture2glob(pattern)
        some_paths = Stat.glob(glob)

        if not some_paths and not is_optional(pattern):
            raise NonOptionalException(glob, pattern)

        # Sorted to make tests deterministic.
        for path in sorted(some_paths):
            path = copy_annotations(pattern, clean_path(path))
            captured.paths.append(path)
            captured.wildcards.append(_capture_string(pattern, regexp, path))

    return captured


def glob_paths(*patterns: Strings) -> List[str]:
    """
    Similar to :py:func:`dynamake.glob_capture`, but just return the list of matching paths,
    ignoring any extracted values.
    """
    paths: List[str] = []

    for pattern in each_string(*patterns):
        glob = capture2glob(pattern)
        some_paths = Stat.glob(glob)

        if not some_paths and not is_optional(pattern):
            raise NonOptionalException(glob, pattern)

        for path in some_paths:
            paths.append(clean_path(path))

    return sorted(paths)


def glob_extract(*patterns: Strings) -> List[Dict[str, Any]]:
    """
    Similar to :py:func:`dynamake.glob_capture`, but just return the list of extracted wildcards
    dictionaries, ignoring the matching paths.
    """
    return glob_capture(*patterns).wildcards


def match_extract(pattern: str, *strings: Strings) -> List[Dict[str, Any]]:
    """
    Similar to :py:func:`dynamake.glob_extract`, except that it uses just one capture pattern and
    apply it to some string(s).
    """
    regexp = capture2re(pattern)
    return [_capture_string(pattern, regexp, string) for string in each_string(*strings)]


def _capture_string(pattern: str, regexp: Pattern, string: str) -> Dict[str, Any]:
    match = re.fullmatch(regexp, string)
    if not match:
        raise RuntimeError(f'The string: {string} does not match the capture pattern: {pattern}')

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
            raise argparse.ArgumentTypeError(  # pylint: disable=raise-missing-from
                'Expected one of: '
                + ' '.join([value.name for value in enum]))  # type: ignore
    return _parse


class RangeParam:
    """
    A range for a numeric argument.
    """

    def __init__(self,  # pylint: disable=too-many-arguments
                 min: Optional[float] = None,
                 max: Optional[float] = None,
                 step: Optional[int] = None,  # pylint: disable=redefined-outer-name
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
        raise argparse.ArgumentTypeError(  # pylint: disable=raise-missing-from
            f'Expected {parser.__name__} value')

    if not range.is_valid(value):
        raise argparse.ArgumentTypeError(f'Expected {parser.__name__} value, '
                                         f'where {range.text()}')

    return value


def str2float(min: Optional[float] = None,
              max: Optional[float] = None,
              step: Optional[int] = None,  # pylint: disable=redefined-outer-name
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
            step: Optional[int] = None,  # pylint: disable=redefined-outer-name
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
            raise argparse.ArgumentTypeError('Expected one of: ' + ' '.join(options))
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
    while next_path.endswith('/'):
        next_path = next_path[:-1]
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
            index_path = Stat._cache.keys()[index]
            if os.path.commonpath([path, index_path]) != path:
                return
            Stat._cache.popitem(index)

    @staticmethod
    def rmdir(path: str) -> None:
        """
        Remove an empty directory.
        """
        Stat.forget(path)
        os.rmdir(path)

    @staticmethod
    def remove(path: str) -> None:
        """
        Force remove of a file or a directory.
        """
        if Stat.isfile(path):
            Stat.forget(path)
            os.remove(path)
        elif Stat.exists(path):
            Stat.forget(path)
            shutil.rmtree(path)

    @staticmethod
    def touch(path: str) -> None:
        """
        Set the last modified time of a file (or a directory) to now.
        """
        Stat.forget(path)
        os.utime(path)

    @staticmethod
    def mkdir_create(path: str) -> None:
        """
        Create a new directory.
        """
        Stat.forget(path)
        os.makedirs(path, exist_ok=False)

    @staticmethod
    def mkdir_exists(path: str) -> None:
        """
        Ensure a directory exists.
        """
        if not Stat.exists(path):
            Stat.forget(path)
            os.makedirs(path, exist_ok=True)


#: The log level for tracing calls.
FILE = (1 * logging.DEBUG + 3 * logging.INFO) // 4
logging.addLevelName(FILE, 'FILE')

#: The log level for logging the reasons for action execution.
WHY = (2 * logging.DEBUG + 2 * logging.INFO) // 4
logging.addLevelName(WHY, 'WHY')

#: The log level for tracing calls.
TRACE = (3 * logging.DEBUG + 1 * logging.INFO) // 4
logging.addLevelName(TRACE, 'TRACE')

#: The default module to load for steps and parameter definitions.
DEFAULT_MODULE = 'DynaMake'

#: The default parameter configuration YAML file to load.
DEFAULT_CONFIG = 'DynaMake.yaml'

_is_test: bool = False


def _dict_to_str(values: Dict[str, Any]) -> str:
    return ','.join([f'{quote_plus(name)}={quote_plus(str(value))}'
                     for name, value in sorted(values.items())])


class Parameter:  # pylint: disable=too-many-instance-attributes
    """
    Describe a configurable build parameter.
    """

    #: The current known parameters.
    by_name: Dict[str, 'Parameter']

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Parameter.by_name = {}

    def __init__(self, *, name: str, default: Any, parser: Callable[[str], Any], description: str,
                 short: Optional[str] = None, order: Optional[int] = None,
                 metavar: Optional[str] = None) -> None:
        """
        Create and register a parameter description.
        """

        #: The unique name of the parameter.
        self.name = name

        #: The unique short name of the parameter.
        self.short = short

        #: The value to use if the parameter is not explicitly configured.
        self.default = default

        #: How to parse the parameter value from a string (command line argument).
        self.parser = parser

        #: A description of the parameter for help messages.
        self.description = description

        #: Optional name of the command line parameter value (``metavar`` in ``argparse``).
        self.metavar = metavar

        #: Optional order of parameter (in help message)
        self.order = order

        #: The effective value of the parameter.
        self.value = default

        if name in Parameter.by_name:
            raise RuntimeError(f'Multiple definitions for the parameter: {name}')
        Parameter.by_name[name] = self

    @staticmethod
    def add_to_parser(parser: ArgumentParser) -> None:
        """
        Add a command line flag for each parameter to the parser to allow
        overriding parameter values directly from the command line.
        """
        parser.add_argument('--config', '-c', metavar='FILE', action='append',
                            help='Load a parameters configuration YAML file')
        parameters = [(parameter.order, parameter.name, parameter)
                      for parameter in Parameter.by_name.values()]
        for _, _, parameter in sorted(parameters):
            text = parameter.description.replace('%', '%%') + f' (default: {parameter.default})'
            if parameter.short:
                parser.add_argument('--' + parameter.name, '-' + parameter.short,
                                    help=text, metavar=parameter.metavar)
            else:
                parser.add_argument('--' + parameter.name, help=text, metavar=parameter.metavar)

    @staticmethod
    def parse_args(args: Namespace) -> None:
        """
        Update the values based on loaded configuration files and/or explicit
        command line flags.
        """
        if os.path.exists(DEFAULT_CONFIG):
            Parameter.load_config(DEFAULT_CONFIG)
        for path in (args.config or []):
            Parameter.load_config(path)

        for name, parameter in Parameter.by_name.items():
            value = vars(args).get(name)
            if value is not None:
                try:
                    parameter.value = parameter.parser(value)
                except BaseException:
                    raise RuntimeError(  # pylint: disable=raise-missing-from
                        f'Invalid value: {vars(args)[name]} for the parameter: {name}')

        name = sys.argv[0].split('/')[-1]

    @staticmethod
    def load_config(path: str) -> None:
        """
        Load a configuration file.
        """
        with open(path, 'r') as file:
            data = yaml.safe_load(file.read())

        if data is None:
            data = {}

        if not isinstance(data, dict):
            raise RuntimeError(f'The configuration file: {path} '
                               f'does not contain a top-level mapping')

        for name, value in data.items():
            parameter = Parameter.by_name.get(name)
            if parameter is None:
                raise RuntimeError(f'Unknown parameter: {name} '
                                   f'specified in the configuration file: {path}')

            if isinstance(value, str):
                try:
                    value = parameter.parser(value)
                except BaseException:
                    raise RuntimeError(  # pylint: disable=raise-missing-from
                        f'Invalid value: {value} '
                        f'for the parameter: {name} '
                        f'specified in the configuration file: {path}')

            parameter.value = value


#: The shell to use to execute commands.
shell_executable: Parameter

#: The number of jobs to run in parallel.
jobs: Parameter

#: The level of messages to log.
log_level: Parameter

#: Whether to log (level INFO) skipped actions (by default, ``False``).
log_skipped_actions: Parameter

#: Whether to rebuild outputs if the actions have changed (by default, ``True``).
rebuild_changed_actions: Parameter

#: The directory to keep persistent state in.
persistent_directory: Parameter

#: Whether to stop the script if any action fails (by default, ``True``).
#
#: If this is ``False``, then the build will continue to execute unrelated
#: actions. In all cases, actions that have already started will be allowed to
#: end normally.
failure_aborts_build: Parameter

#: Whether to remove old output files before executing an action (by default,
#: ``True``).
remove_stale_outputs: Parameter

# Whether to touch output files on a successful action to ensure they are newer
# than the input file(s) (by default, ``False``).
#
#: In these modern times, this is mostly unneeded as we use the nanosecond
#: modification time, which pretty much guarantees that output files will be
#: newer than input files. In the "bad old days", files created within a second
#: of each other had the same modification time, which would confuse the build
#: tools.
#
#: This might still be needed if an output is a directory (not a file) and
#: `remove_stale_outputs` is ``False``, since otherwise the ``mtime`` of an
#: existing directory will not necessarily be updated to reflect the fact the
#: action was executed. In general it is not advised to depend on the ``mtime``
#: of directories; it is better to specify a glob matching the expected files
#: inside them, or use an explicit timestamp file.
touch_success_outputs: Parameter

#: Whether to remove output files on a failing action (by default, ``True``).
remove_failed_outputs: Parameter

#: Whether to (try to) remove empty directories when deleting the last file in
#: them (by default, ``False``).
remove_empty_directories: Parameter

#: The default prefix to add to shell commands.
default_shell_prefix: Parameter


def _define_parameters() -> None:
    # pylint: disable=invalid-name

    global jobs
    jobs = Parameter(  #
        name='jobs',
        short='j',
        metavar='INT',
        default=-1,
        parser=str2int(),
        description="""
            The number of jobs to run in parallel. Use 0 for unlimited
            parallelism, 1 for serial jobs execution, and a negative number for
            a fraction of the logical processors in the system (-1 for one per
            logical processor, -2 for one per two logical processors, etc.).
        """)

    global log_level
    log_level = Parameter(  #
        name='log_level',
        short='ll',
        metavar='STR',
        default='WARN',
        parser=str,
        description='The log level to use')

    global log_skipped_actions
    log_skipped_actions = Parameter(  #
        name='log_skipped_actions',
        short='lsa',
        metavar='BOOL',
        default=False,
        parser=str2bool,
        description='Whether to log (level INFO) skipped actions')

    global rebuild_changed_actions
    rebuild_changed_actions = Parameter(  #
        name='rebuild_changed_actions',
        short='rca',
        metavar='BOOL',
        default=True,
        parser=str2bool,
        description='Whether to rebuild outputs if the actions have changed')

    global persistent_directory
    persistent_directory = Parameter(  #
        name='persistent_directory',
        short='pp',
        metavar='STR',
        default='.dynamake',
        parser=str,
        description="""
            The directory to keep persistent data in, if
            rebuild_changed_actions is  True.
        """)

    global failure_aborts_build
    failure_aborts_build = Parameter(  #
        name='failure_aborts_build',
        short='fab',
        metavar='BOOL',
        default=True,
        parser=str2bool,
        description='Whether to stop the script if any action fails')

    global remove_stale_outputs
    remove_stale_outputs = Parameter(  #
        name='remove_stale_outputs',
        short='dso',
        metavar='BOOL',
        default=True,
        parser=str2bool,
        description='Whether to remove old output files before executing an action')

    global touch_success_outputs
    touch_success_outputs = Parameter(  #
        name='touch_success_outputs',
        short='tso',
        metavar='BOOL',
        default=False,
        parser=str2bool,
        description="""
            Whether to touch output files on a successful action to ensure they
            are newer than the input file(s)
        """)

    global remove_failed_outputs
    remove_failed_outputs = Parameter(  #
        name='remove_failed_outputs',
        short='dfo',
        metavar='BOOL',
        default=True,
        parser=str2bool,
        description='Whether to remove output files on a failing action')

    global remove_empty_directories
    remove_empty_directories = Parameter(  #
        name='remove_empty_directories',
        short='ded',
        metavar='BOOL',
        default=False,
        parser=str2bool,
        description='Whether to remove empty directories when deleting the last file in them')

    global shell_executable
    shell_executable = Parameter(  #
        name='shell',
        short='s',
        metavar='STR',
        default='/bin/bash',
        parser=str,
        description='The shell to use to execute commands.')

    global default_shell_prefix
    default_shell_prefix = Parameter(  #
        name='default_shell_prefix',
        short='dsp',
        metavar='STR',
        default='set -eou pipefail;',
        parser=str,
        description='Default prefix to add to shell actions')
    # pylint: enable=invalid-name


class Resources:
    """
    Restrict parallelism using some resources.
    """

    #: The total amount of each resource.
    total: Dict[str, int]

    #: The unused amount of each resource.
    available: Dict[str, int]

    #: The default amount used by each action.
    default: Dict[str, int]

    #: A condition for synchronizing between the async actions.
    condition: asyncio.Condition

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Resources.total = dict(jobs=os.cpu_count() or 1)
        Resources.available = Resources.total.copy()
        Resources.default = dict(jobs=1)
        Resources.condition = asyncio.Condition()

    @staticmethod
    def effective(requested: Dict[str, int]) -> Dict[str, int]:
        """
        Return the effective resource amounts given the explicitly requested
        amounts.

        Negative amounts specify a fraction of the total amount (-1 for all
        of the total amount, -2 for half the total amount, etc.).
        """
        amounts: Dict[str, int] = {}

        for name, amount in sorted(requested.items()):
            total = Resources.total.get(name)
            if total is None:
                raise RuntimeError(f'Requested the unknown resource: {name}')
            if amount == 0 or total <= 0:
                continue
            if amount < 0:
                amount = max(total // -amount, 1)
            if amount > total:
                raise RuntimeError(f'The requested resource: {name} amount: {amount} '
                                   f'is greater than the total amount: {total}')
            amounts[name] = amount

        for name, total in Resources.total.items():
            if name in requested or total <= 0:
                continue
            amount = Resources.default[name]
            if amount < 0:
                amount = max(total // -amount, 1)
            amounts[name] = amount

        return amounts

    @staticmethod
    def have(amounts: Dict[str, int]) -> bool:
        """
        Return whether there are available resource to cover the requested
        amounts.
        """
        for name, amount in amounts.items():
            if amount > Resources.available[name]:
                return False
        return True

    @staticmethod
    def grab(amounts: Dict[str, int]) -> None:
        """
        Take ownership of some resource amounts.
        """
        for name, amount in amounts.items():
            assert 0 <= amount <= Resources.available[name]
            Resources.available[name] -= amount

    @staticmethod
    def free(amounts: Dict[str, int]) -> None:
        """
        Release ownership of some resource amounts.
        """
        for name, amount in amounts.items():
            assert 0 <= amount <= Resources.total[name] - Resources.available[name]
            Resources.available[name] += amount


def resource_parameters(**default_amounts: int) -> None:
    """
    Declare additional resources for controlling parallel action execution.

    Each resource should have been declared as a :py:class:`Parameter`.  The
    value given here is the default amount of the resource used by each action
    that does not specify an explicit value.
    """
    for name, amount in default_amounts.items():
        total = Resources.total.get(name)
        if total is None:
            parameter = Parameter.by_name.get(name)
            if parameter is None:
                raise RuntimeError(f'Unknown resource parameter: {name}')
            total = int(parameter.value)
            Resources.total[name] = total
            Resources.available[name] = total

        if amount > total:
            raise RuntimeError(f'The default amount: {amount} '
                               f'of the resource: {name} '
                               f'is greater than the total amount: {total}')

        Resources.default[name] = amount


class StepException(Exception):
    """
    Indicates a step has aborted and its output must not be used by other
    steps.
    """


class RestartException(Exception):
    """
    Indicates a step needs to be re-run, this time executing all actions.
    """


class Step:
    """
    A build step.
    """

    #: The current known steps.
    by_name: Dict[str, 'Step']

    #: The step for building any output capture pattern.
    by_regexp: List[Tuple[Pattern, 'Step']]

    _is_finalized: bool

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Step.by_name = {}
        Step.by_regexp = []
        Step._is_finalized = False

    def __init__(self, function: Callable, output: Strings, priority: int) -> None:
        """
        Register a build step function.
        """
        #: The wrapped function that implements the step.
        self.function = function

        while hasattr(function, '__func__'):
            function = getattr(function, '__func__')

        if Step._is_finalized:
            raise RuntimeError(f'Late registration of the step: '
                               f'{function.__module__}.{function.__qualname__}')

        if not iscoroutinefunction(function):
            raise RuntimeError(f'The step function: '
                               f'{function.__module__}.{function.__qualname__}'
                               f' is not a coroutine')

        #: The name of the step.
        self.name = function.__name__

        #: The outputs generated by the step.
        self.output: List[str] = []

        #: The priority allowing overriding steps.
        self.priority = priority

        for capture in each_string(output):
            capture = clean_path(capture)
            self.output.append(capture)
            Step.by_regexp.append((capture2re(capture), self))

        if not self.output:
            raise RuntimeError(f'The step function: '
                               f'{function.__module__}.{function.__qualname__}'
                               f' specifies no output')

        if self.name in Step.by_name:
            conflicting = Step.by_name[self.name].function
            raise RuntimeError(f'Conflicting definitions for the step: {self.name} '
                               f'in both: '
                               f'{conflicting.__module__}.{conflicting.__qualname__}'
                               f' and: '
                               f'{function.__module__}.{function.__qualname__}')
        Step.by_name[self.name] = self


class UpToDate:
    """
    Data for each up-to-date target.
    """

    def __init__(self, producer: str, mtime_ns: int = 0) -> None:
        """
        Record a new up-to-date target.
        """
        #: The step (and parameters) that updated the target.
        self.producer = producer

        #: The modified time of the target (in nanoseconds).
        #:
        #: This is negative until we know the correct time.
        self.mtime_ns = mtime_ns

    def into_data(self) -> Dict[str, Any]:
        """
        Serialize for dumping to YAML.
        """
        data = dict(producer=self.producer)
        if self.mtime_ns > 0:
            data['mtime'] = str(_datetime_from_nanoseconds(self.mtime_ns))
        return data

    @staticmethod
    def from_data(data: Dict[str, str]) -> 'UpToDate':
        """
        Load from YAML data.
        """
        producer = data['producer']
        mtime_str = data.get('mtime')
        if mtime_str is None:
            mtime_ns = 0
        else:
            mtime_ns = _nanoseconds_from_datetime_str(mtime_str)
        return UpToDate(producer, mtime_ns)


class PersistentAction:
    """
    An action taken during step execution.

    We can persist this to ensure the action taken in a future invocation is
    identical, to trigger rebuild if the list of actions changes.
    """

    def __init__(self, previous: Optional['PersistentAction'] = None) -> None:
        #: The executed command.
        self.command: Optional[List[str]] = None

        #: The time the command started execution.
        self.start: Optional[datetime] = None

        #: The time the command ended execution.
        self.end: Optional[datetime] = None

        #: The up-to-date data for each input.
        self.required: Dict[str, UpToDate] = {}

        #: The previous action of the step, if any.
        self.previous = previous

    def require(self, path: str, up_to_date: UpToDate) -> None:
        """
        Add a required input to the action.
        """
        self.required[path] = up_to_date

    def run_action(self, command: List[str]) -> None:
        """
        Set the executed command of the action.
        """
        self.command = [word for word in command if not is_phony(word)]
        self.start = datetime.now()

    def done_action(self) -> None:
        """
        Record the end time of the command.
        """
        self.end = datetime.now()

    def is_empty(self) -> bool:
        """
        Whether this action has any additional information over its
        predecessor.
        """
        return self.command is None and not self.required

    def into_data(self) -> List[Dict[str, Any]]:
        """
        Serialize for dumping to YAML.
        """
        if self.previous:
            data = self.previous.into_data()
        else:
            data = []

        datum: Dict[str, Any] = dict(required={name: up_to_date.into_data()
                                               for name, up_to_date in self.required.items()})

        if self.command is None:
            assert self.start is None
            assert self.end is None
        else:
            assert self.start is not None
            assert self.end is not None
            datum['command'] = self.command
            datum['start'] = str(self.start)
            datum['end'] = str(self.end)

        data.append(datum)
        return data

    @staticmethod
    def from_data(data: List[Dict[str, Any]]) -> List['PersistentAction']:
        """
        Construct the data from loaded YAML.
        """
        if not data:
            return [PersistentAction()]

        datum = data[-1]
        data = data[:-1]

        if data:
            actions = PersistentAction.from_data(data)
            action = PersistentAction(actions[-1])
            actions.append(action)
        else:
            action = PersistentAction()
            actions = [action]

        action.required = {name: UpToDate.from_data(up_to_date)
                           for name, up_to_date in datum['required'].items()}

        if 'command' in datum:
            action.command = datum['command']
            action.start = _datetime_from_str(datum['start'])
            action.end = _datetime_from_str(datum['end'])

        return actions


class LoggingFormatter(logging.Formatter):  # pragma: no cover
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


#: The log level for tracing calls.
logging.TRACE = (3 * logging.DEBUG + 1 * logging.INFO) // 4  # type: ignore
logging.addLevelName(logging.TRACE, 'TRACE')  # type: ignore

#: The log level for logging the reasons for action execution.
logging.WHY = (2 * logging.DEBUG + 2 * logging.INFO) // 4  # type: ignore
logging.addLevelName(logging.WHY, 'WHY')  # type: ignore

#: The log level for tracing calls.
logging.FILE = (1 * logging.DEBUG + 3 * logging.INFO) // 4  # type: ignore
logging.addLevelName(logging.FILE, 'FILE')  # type: ignore


class Logger:
    """
    Customized logging.
    """

    _logger: logging.Logger

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Logger._logger = logging.getLogger('dynamake')
        Logger._logger.setLevel('DEBUG')
        logging.getLogger('asyncio').setLevel('WARN')

    @staticmethod
    def setup(logger_name: str) -> None:
        """
        Set up the global logger.
        """
        Logger._logger = logging.getLogger(logger_name)
        logging.getLogger('asyncio').setLevel('WARN')

        if not _is_test:
            # pragma: no cover
            handler = logging.StreamHandler(sys.stderr)
            log_format = '%(asctime)s - dynamake - %(levelname)s - %(message)s'
            handler.setFormatter(LoggingFormatter(log_format))
            Logger._logger.addHandler(handler)

        Logger._logger.setLevel(log_level.value)

    @staticmethod
    def isEnabledFor(level: int) -> bool:  # pylint: disable=invalid-name
        """
        Test whether logging is enabled for the specified level.
        """
        return Logger._logger.isEnabledFor(level)

    @staticmethod
    def log(level: int, message: str, *args: Any) -> None:
        """
        Log a ``level`` ``message``.
        """
        try:
            message = message % args
        except TypeError:
            raise RuntimeError(  # pylint: disable=raise-missing-from
                f'mismatch between format: {message} '
                f'and args: {args}')
        message = f'{Invocation.current.log} - {message}'
        Logger._logger.log(level, message)

    @staticmethod
    def debug(message: str, *args: Any) -> None:
        """
        Log a ``DEBUG`` level ``message``.
        """
        Logger.log(logging.DEBUG, message, *args)

    @staticmethod
    def trace(message: str, *args: Any) -> None:
        """
        Log a ``TRACE`` level ``message``.
        """
        Logger.log(logging.TRACE, message, *args)  # type: ignore

    @staticmethod
    def why(message: str, *args: Any) -> None:
        """
        Log a ``WHY`` level ``message``.
        """
        Logger.log(logging.WHY, message, *args)  # type: ignore

    @staticmethod
    def file(message: str, *args: Any) -> None:
        """
        Log a ``FILE`` level ``message``.
        """
        Logger.log(logging.FILE, message, *args)  # type: ignore

    @staticmethod
    def info(message: str, *args: Any) -> None:
        """
        Log an ``INFO`` level ``message``.
        """
        Logger.log(logging.INFO, message, *args)

    @staticmethod
    def warning(message: str, *args: Any) -> None:
        """
        Log a ``WARN`` level ``message``.
        """
        Logger.log(logging.WARN, message, *args)

    @staticmethod
    def error(message: str, *args: Any) -> None:
        """
        Log an ``ERROR`` level ``message``.
        """
        Logger.log(logging.ERROR, message, *args)

    @staticmethod
    def critical(message: str, *args: Any) -> None:  # pragma: no cover
        """
        Log a ``CRITICAL`` level ``message``.
        """
        Logger.log(logging.CRITICAL, message, *args)


class Invocation:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """
    An active invocation of a build step.
    """

    #: The active invocations.
    active: Dict[str, 'Invocation']

    #: The current invocation.
    current: 'Invocation'

    #: The top-level invocation.
    top: 'Invocation'

    #: The paths for phony targets.
    phony: Set[str]

    #: The origin and time of targets that were built or otherwise proved to be up-to-date so far.
    up_to_date: Dict[str, UpToDate]

    #: The files that failed to build and must not be used by other steps.
    poisoned: Set[str]

    #: A running counter of the executed actions.
    actions_count: int

    #: A running counter of the skipped actions.
    skipped_count: int

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        Invocation.active = {}
        Invocation.current = None  # type: ignore
        Invocation.top = Invocation(None)
        Invocation.top._become_current()  # pylint: disable=protected-access
        Invocation.up_to_date = {}
        Invocation.phony = set()
        Invocation.poisoned = set()
        Invocation.actions_count = 0
        Invocation.skipped_count = 0

    def __init__(self,  # pylint: disable=too-many-statements
                 step: Optional[Step],  # pylint: disable=redefined-outer-name
                 **kwargs: Any) -> None:
        """
        Track the invocation of an async step.
        """
        #: The parent invocation, if any.
        self.parent: Optional[Invocation] = Invocation.current

        #: The step being invoked.
        self.step = step

        #: The arguments to the invocation.
        self.kwargs = kwargs

        #: The full name (including parameters) of the invocation.
        self.name = 'make'
        if self.step is not None:
            self.name = self.step.name
        args_string = _dict_to_str(kwargs)
        if args_string:
            self.name += '/'
            self.name += args_string

        assert (self.parent is None) == (step is None)

        #: How many sub-invocations were created so far.
        self.sub_count = 0

        if self.parent is None:
            #: A short unique stack to identify invocations in the log.
            self.stack: str = '#0'
        else:
            self.parent.sub_count += 1
            if self.parent.stack == '#0':
                self.stack = '#' + str(self.parent.sub_count)
            else:
                self.stack = self.parent.stack + '.' + str(self.parent.sub_count)

        #: The full name used for logging.
        self.log = self.name
        if Logger.isEnabledFor(logging.TRACE):  # type: ignore
            self.log = self.stack + ' - ' + self.name

        self._verify_no_loop()

        #: A condition variable to wait on for this invocation.
        self.condition: Optional[asyncio.Condition] = None

        #: The required input targets (phony or files) the invocations depends on.
        self.required: List[str] = []

        #: The newest input file, if any.
        self.newest_input_path: Optional[str] = None

        #: The modification time of the newest input file, if any.
        self.newest_input_mtime_ns = 0

        #: The queued async actions for creating the input files.
        self.async_actions: List[Coroutine] = []

        #: The output files that existed prior to the invocation.
        self.initial_outputs: List[str] = []

        #: The phony outputs, if any.
        self.phony_outputs: List[str] = []

        #: The built outputs, if any.
        self.built_outputs: List[str] = []

        #: A pattern for some missing output file(s), if any.
        self.missing_output: Optional[str] = None

        #: A path for some missing old built output file, if any.
        self.abandoned_output: Optional[str] = None

        #: The oldest existing output file path, or None if some output files are missing.
        self.oldest_output_path: Optional[str] = None

        #: The modification time of the oldest existing output path.
        self.oldest_output_mtime_ns = 0

        #: The reason to abort this invocation, if any.
        self.exception: Optional[StepException] = None

        #: The old persistent actions (from the disk) for ensuring rebuild when actions change.
        self.old_persistent_actions: List[PersistentAction] = []

        #: The old list of outputs (from the disk) for ensuring complete dynamic outputs.
        self.old_persistent_outputs: List[str] = []

        #: The new persistent actions (from the code) for ensuring rebuild when actions change.
        self.new_persistent_actions: List[PersistentAction] = []

        #: Whether we already decided to run actions.
        self.must_run_action = False

        #: Whether we actually skipped all actions so far.
        self.did_skip_actions = False

        #: Whether we actually run any actions.
        self.did_run_actions = False

        #: Whether we should remove stale outputs before running the next action.
        self.should_remove_stale_outputs = remove_stale_outputs.value

    def _restart(self) -> None:
        self.required = []
        self.newest_input_path = None
        self.newest_input_mtime_ns = 0

        assert not self.async_actions

        self.abandoned_output = None
        self.oldest_output_path = None

        assert self.exception is None

        if self.new_persistent_actions:
            self.new_persistent_actions = [PersistentAction()]

        self.must_run_action = True
        self.did_skip_actions = False
        assert self.should_remove_stale_outputs == remove_stale_outputs.value

    def _verify_no_loop(self) -> None:
        call_chain = [self.name]
        parent = self.parent
        while parent is not None:
            call_chain.append(parent.name)
            if self.name == parent.name:
                raise RuntimeError('step invokes itself: ' + ' -> '.join(reversed(call_chain)))
            parent = parent.parent

    def read_old_persistent_actions(self) -> None:
        """
        Read the old persistent data from the disk file.

        These describe the last successful build of the outputs.
        """
        path = os.path.join(persistent_directory.value, self.name + '.actions.yaml')
        if not os.path.exists(path):
            Logger.why(f'Must run actions because missing the persistent actions: {path}')
            self.must_run_action = True
            return

        try:
            with open(path, 'r') as file:
                data = yaml.full_load(file.read())
            self.old_persistent_actions = PersistentAction.from_data(data['actions'])
            self.old_persistent_outputs = data['outputs']
            Logger.debug(f'Read the persistent actions: {path}')

        except BaseException:  # pylint: disable=broad-except
            Logger.warning(f'Must run actions '
                           f'because read the invalid persistent actions: {path}')
            self.must_run_action = True

    def remove_old_persistent_data(self) -> None:
        """
        Remove the persistent data from the disk in case the build failed.
        """
        path = os.path.join(persistent_directory.value, self.name + '.actions.yaml')
        if os.path.exists(path):
            Logger.debug(f'Remove the persistent actions: {path}')
            os.remove(path)

        if '/' not in self.name:
            return
        try:
            os.rmdir(os.path.dirname(path))
        except OSError:
            pass

    def write_new_persistent_actions(self) -> None:
        """
        Write the new persistent data into the disk file.

        This is only done on a successful build.
        """
        path = os.path.join(persistent_directory.value, self.name + '.actions.yaml')
        Logger.debug(f'Write the persistent actions: {path}')

        os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(path, 'w') as file:
            data = dict(actions=self.new_persistent_actions[-1].into_data(),
                        outputs=self.built_outputs)
            file.write(yaml.dump(data))

    def log_and_abort(self, message: str, *args: Any) -> None:
        """
        Abort the invocation for some reason.
        """
        Logger.error(message, *args)
        return self.abort(message, *args)

    def abort(self, message: str, *args: Any) -> None:
        """
        Abort the invocation for some reason.
        """
        message = message % args
        message = f'{Invocation.current.log} - {message}'
        self.exception = StepException(message)
        if failure_aborts_build.value:
            raise self.exception

    def require(self, path: str) -> None:
        """
        Require a file to be up-to-date before executing any actions or
        completing the current invocation.
        """
        self._become_current()

        path = clean_path(path)

        Logger.debug(f'Build the required: {path}')

        self.required.append(path)

        if path in Invocation.poisoned:
            self.abort(f'The required: {path} has failed to build')
            return

        up_to_date = Invocation.up_to_date.get(path)
        if up_to_date is not None:
            Logger.debug(f'The required: {path} was built')
            if self.new_persistent_actions:
                self.new_persistent_actions[-1].require(path, UpToDate(up_to_date.producer))
            return

        step, kwargs = self.producer_of(path)  # pylint: disable=redefined-outer-name
        if kwargs is None:
            return

        if step is None:
            stat = Stat.try_stat(path)
            if stat is None:
                if is_optional(path):
                    Logger.debug(f'The optional required: {path} '
                                 f"does not exist and can't be built")
                else:
                    self.log_and_abort(f"Don't know how to make the required: {path}")
                return
            Logger.debug(f'The required: {path} is a source file')
            up_to_date = UpToDate('', stat.st_mtime_ns)
            Invocation.up_to_date[path] = up_to_date
            if self.new_persistent_actions:
                self.new_persistent_actions[-1].require(path, up_to_date)
            return

        invocation = Invocation(step, **kwargs)
        if self.new_persistent_actions:
            self.new_persistent_actions[-1].require(path, UpToDate(invocation.name))
        Logger.debug(f'The required: {path} '
                     f'will be produced by the spawned: {invocation.log}')
        self.async_actions.append(asyncio.Task(invocation.run()))  # type: ignore

    def producer_of(self,  # pylint: disable=too-many-locals
                    path: str) -> Tuple[Optional[Step], Optional[Dict[str, Any]]]:
        """
        Find the unique step, if any, that produces the file.

        Also returns the keyword arguments needed to invoke the step function
        (deduced from the path).
        """
        kwargs: Dict[str, Any] = {}
        producer: Optional[Step] = None

        producers: List[Tuple[int, str, re.Match, Step]] = []

        for (regexp, step) in Step.by_regexp:  # pylint: disable=redefined-outer-name
            match = re.fullmatch(regexp, path)
            if not match:
                continue

            producers.append((-step.priority, step.name, match, step))

        producers = sorted(producers)

        if Logger.isEnabledFor(logging.DEBUG) and len(producers) > 1:
            for _, _, _, candidate in producers:
                Logger.debug(f'candidate producer: {candidate.name} '
                             f'priority: {candidate.priority}')

        if len(producers) > 1:
            first_priority, first_name, _, _ = producers[0]
            second_priority, second_name, _, _ = producers[1]

            if second_priority == first_priority:
                self.log_and_abort(f'The output: {path} '
                                   f'may be created by both the step: {first_name} '
                                   f'and the step: {second_name} '
                                   f'at the same priority: {first_priority}')
                return None, None

        if len(producers) > 0:
            _, _, match, producer = producers[0]
            for name, value in match.groupdict().items():
                if name[0] != '_':
                    kwargs[name] = str(value or '')

        return producer, kwargs

    async def run(self) -> Optional[BaseException]:  # pylint: disable=too-many-branches,too-many-statements
        """
        Actually run the invocation.
        """
        active = Invocation.active.get(self.name)
        if active is not None:
            return await self.done(self.wait_for(active))

        self._become_current()
        Logger.trace('Call')

        if rebuild_changed_actions.value:
            self.new_persistent_actions.append(PersistentAction())
            self.read_old_persistent_actions()

        assert self.name not in Invocation.active
        Invocation.active[self.name] = self
        self.collect_initial_outputs()

        try:
            assert self.step is not None
            try:
                await self.done(self.step.function(**self.kwargs))
            except RestartException:
                self._restart()
                await self.done(self.step.function(**self.kwargs))
            await self.done(self.sync())
            await self.done(self.collect_final_outputs())

        except StepException as exception:  # pylint: disable=broad-except
            self.exception = exception

        finally:
            self._become_current()

        if self.exception is None:
            assert not self.async_actions
            if self.new_persistent_actions:
                if len(self.new_persistent_actions) > 1 \
                        and self.new_persistent_actions[-1].is_empty():
                    self.new_persistent_actions.pop()

                if not self.did_skip_actions:
                    self.write_new_persistent_actions()
                elif len(self.new_persistent_actions) < len(self.old_persistent_actions):
                    Logger.warning('Skipped some action(s) '
                                   'even though changed to remove some final action(s)')

            if self.did_run_actions:
                Logger.trace('Done')
            elif self.did_skip_actions:
                Logger.trace('Skipped')
            else:
                Logger.trace('Complete')

        else:
            while self.async_actions:
                try:
                    await self.done(self.async_actions.pop())
                except StepException:
                    pass
            self.poison_all_outputs()
            self.remove_old_persistent_data()
            Logger.trace('Fail')

        del Invocation.active[self.name]
        if self.condition is not None:
            await self.done(self.condition.acquire())
            self.condition.notify_all()
            self.condition.release()

        if self.exception is not None and failure_aborts_build.value:
            raise self.exception

        return self.exception

    async def wait_for(self, active: 'Invocation') -> Optional[BaseException]:
        """
        Wait until the invocation is done.

        This is used by other invocations that use this invocation's output(s)
        as their input(s).
        """
        self._become_current()

        Logger.debug(f'Paused by waiting for: {active.log}')

        if active.condition is None:
            active.condition = asyncio.Condition()

        await self.done(active.condition.acquire())
        await self.done(active.condition.wait())
        active.condition.release()

        Logger.debug(f'Resumed by completion of: {active.log}')

        return active.exception

    def collect_initial_outputs(self) -> None:  # pylint: disable=too-many-branches
        """
        Check which of the outputs already exist and what their modification
        times are, to be able to decide whether actions need to be run to
        create or update them.
        """
        assert self.step is not None
        missing_outputs = []
        for pattern in sorted(self.step.output):
            formatted_pattern = fmt_capture(self.kwargs, pattern)
            if is_phony(formatted_pattern):
                self.phony_outputs.append(formatted_pattern)
                Invocation.phony.add(formatted_pattern)
                continue

            try:
                paths = glob_paths(formatted_pattern)
                if not paths:
                    Logger.debug(f'Nonexistent optional output(s): {pattern}')
                else:
                    for path in paths:
                        self.initial_outputs.append(path)
                        if path == pattern:
                            Logger.debug(f'Existing output: {path}')
                        else:
                            Logger.debug(f'Existing output: {pattern} -> {path}')
            except NonOptionalException:
                Logger.debug(f'Nonexistent required output(s): {pattern}')
                self.missing_output = formatted_pattern
                missing_outputs.append(capture2re(formatted_pattern))

        if self.new_persistent_actions:
            for path in self.old_persistent_outputs:
                if path in self.initial_outputs:
                    continue

                was_reported = False
                for regexp in missing_outputs:
                    if re.fullmatch(regexp, path):
                        was_reported = True
                        break

                if was_reported:
                    continue

                if Stat.exists(path):
                    Logger.debug(f'Changed to abandon the output: {path}')
                    self.abandoned_output = path
                else:
                    Logger.debug(f'Missing the old built output: {path}')
                    self.missing_output = path

                Stat.forget(path)

        if self.must_run_action \
                or self.phony_outputs \
                or self.missing_output is not None \
                or self.abandoned_output is not None:
            return

        for output_path in sorted(self.initial_outputs):
            if is_exists(output_path):
                continue
            output_mtime_ns = Stat.stat(output_path).st_mtime_ns
            if self.oldest_output_path is None or self.oldest_output_mtime_ns > output_mtime_ns:
                self.oldest_output_path = output_path
                self.oldest_output_mtime_ns = output_mtime_ns

        if Logger.isEnabledFor(logging.DEBUG) and self.oldest_output_path is not None:
            Logger.debug(f'Oldest output: {self.oldest_output_path} '
                         f'time: {_datetime_from_nanoseconds(self.oldest_output_mtime_ns)}')

    async def collect_final_outputs(self) -> None:  # pylint: disable=too-many-branches
        """
        Ensure that all the (required) outputs were actually created and are
        newer than all input files specified so far.

        If successful, this marks all the outputs as up-to-date so that steps
        that depend on them will immediately proceed.
        """
        self._become_current()

        missing_outputs = False
        assert self.step is not None

        did_sleep = False

        for pattern in sorted(self.step.output):  # pylint: disable=too-many-nested-blocks
            formatted_pattern = fmt_capture(self.kwargs, pattern)
            if is_phony(pattern):
                Invocation.up_to_date[formatted_pattern] = \
                    UpToDate(self.name, self.newest_input_mtime_ns + 1)
                continue

            try:
                paths = glob_paths(formatted_pattern)
                if not paths:
                    Logger.debug(f'Did not make the optional output(s): {pattern}')
                else:
                    for path in paths:
                        self.built_outputs.append(path)

                        if touch_success_outputs.value:
                            if not did_sleep:
                                await self.done(asyncio.sleep(1.0))
                                did_sleep = True
                            Logger.file(f'Touch the output: {path}')
                            Stat.touch(path)

                        mtime_ns = Stat.stat(path).st_mtime_ns
                        Invocation.up_to_date[path] = UpToDate(self.name, mtime_ns)

                        if Logger.isEnabledFor(logging.DEBUG):
                            if path == formatted_pattern:
                                Logger.debug(f'Has the output: {path} '
                                             f'time: {_datetime_from_nanoseconds(mtime_ns)}')
                            else:
                                Logger.debug(f'Has the output: {pattern} -> {path} '
                                             f'time: {_datetime_from_nanoseconds(mtime_ns)}')

            except NonOptionalException:
                self._become_current()
                Logger.error(f'Missing the output(s): {pattern}')
                missing_outputs = True
                break

        if missing_outputs:
            self.abort('Missing some output(s)')

    def remove_stale_outputs(self) -> None:
        """
        Delete stale outputs before running a action.

        This is only done before running the first action of a step.
        """
        for path in sorted(self.initial_outputs):
            if self.should_remove_stale_outputs and not is_precious(path):
                Logger.file(f'Remove the stale output: {path}')
                Invocation.remove_output(path)
            else:
                Stat.forget(path)

        self.should_remove_stale_outputs = False

    @staticmethod
    def remove_output(path: str) -> None:
        """
        Remove an output file, and possibly the directories that became empty
        as a result.
        """
        try:
            Stat.remove(path)
            while remove_empty_directories.value:
                path = os.path.dirname(path)
                Stat.rmdir(path)
                Logger.file(f'Remove the empty directory: {path}')
        except OSError:
            pass

    def poison_all_outputs(self) -> None:
        """
        Mark all outputs as poisoned for a failed step.

        Typically also removes them.
        """
        assert self.step is not None

        for pattern in sorted(self.step.output):
            formatted_pattern = fmt_capture(self.kwargs, optional(pattern))
            if is_phony(formatted_pattern):
                Invocation.poisoned.add(formatted_pattern)
                continue
            for path in glob_paths(optional(formatted_pattern)):
                Invocation.poisoned.add(path)
                if remove_failed_outputs.value and not is_precious(path):
                    Logger.file(f'Remove the failed output: {path}')
                    Invocation.remove_output(path)

    def should_run_action(self) -> bool:  # pylint: disable=too-many-return-statements
        """
        Test whether all (required) outputs already exist, and are newer than
        all input files specified so far.
        """
        if self.must_run_action:
            return True

        if self.phony_outputs:
            # Either no output files (pure action) or missing output files.
            Logger.why(f'Must run actions to satisfy the phony output: {self.phony_outputs[0]}')
            return True

        if self.missing_output is not None:
            Logger.why(f'Must run actions to create the missing output(s): {self.missing_output}')
            return True

        if self.abandoned_output is not None:
            Logger.why(f'Must run actions '
                       f'because changed to abandon the output: {self.abandoned_output}')
            return True

        if self.new_persistent_actions:
            # Compare with last successful build action.
            index = len(self.new_persistent_actions) - 1
            if index >= len(self.old_persistent_actions):
                Logger.why('Must run actions because changed to add action(s)')
                return True
            new_action = self.new_persistent_actions[index]
            old_action = self.old_persistent_actions[index]
            if Invocation.different_actions(old_action, new_action):
                return True

        # All output files exist:

        if self.newest_input_path is None:
            # No input files (pure computation).
            Logger.debug('Can skip actions '
                         'because all the outputs exist and there are no newer inputs')
            return False

        # There are input files:

        if self.oldest_output_path is not None \
                and self.oldest_output_mtime_ns <= self.newest_input_mtime_ns:
            # Some output file is not newer than some input file.
            Logger.why(f'Must run actions because the output: {self.oldest_output_path} '
                       f'is not newer than the input: {self.newest_input_path}')
            return True

        # All output files are newer than all input files.
        Logger.debug('Can skip actions '
                     'because all the outputs exist and are newer than all the inputs')
        return False

    @staticmethod
    def different_actions(old_action: PersistentAction, new_action: PersistentAction) -> bool:
        """
        Check whether the new action is different from the last build action.
        """
        if Invocation.different_required(old_action.required, new_action.required):
            return True

        if old_action.command != new_action.command:
            if old_action.command is None:
                old_action_kind = 'a phony command'
            else:
                old_action_kind = 'the command: ' + ' '.join(old_action.command)

            if new_action.command is None:
                new_action_kind = 'a phony command'
            else:
                new_action_kind = 'the command: ' + ' '.join(new_action.command)

            Logger.why(f'Must run actions because changed {old_action_kind} '
                       f'into {new_action_kind}')
            return True

        return False

    @staticmethod
    def different_required(old_required: Dict[str, UpToDate],
                           new_required: Dict[str, UpToDate]) -> bool:
        """
        Check whether the required inputs of the new action are different from
        the required inputs of the last build action.
        """
        for new_path in sorted(new_required.keys()):
            if new_path not in old_required:
                Logger.why(f'Must run actions because changed to require: {new_path}')
                return True

        for old_path in sorted(old_required.keys()):
            if old_path not in new_required:
                Logger.why(f'Must run actions because changed to not require: {old_path}')
                return True

        for path in sorted(new_required.keys()):
            old_up_to_date = old_required[path]
            new_up_to_date = new_required[path]
            if old_up_to_date.producer != new_up_to_date.producer:
                Logger.why(f'Must run actions because the producer of the required: {path} '
                           f'has changed from: {old_up_to_date.producer or "source file"} '
                           f'into: {new_up_to_date.producer or "source file"}')
                return True
            if not is_exists(path) and old_up_to_date.mtime_ns != new_up_to_date.mtime_ns:
                Logger.why(f'Must run actions '
                           f'because the modification time of the required: {path} '
                           f'has changed from: '
                           f'{_datetime_from_nanoseconds(old_up_to_date.mtime_ns)} '
                           f'into: '
                           f'{_datetime_from_nanoseconds(new_up_to_date.mtime_ns)}')
                return True

        return False

    async def run_action(self,  # pylint: disable=too-many-branches,too-many-statements
                         kind: str, runner: Callable, *command: Strings, **resources: int) -> None:
        """
        Spawn a action to actually create some files.
        """
        self._become_current()

        await self.done(self.sync())

        run_parts = []
        persistent_parts = []
        log_parts = []
        is_silent = None
        for part in each_string(*command):
            if is_silent is None:
                if part.startswith('@'):
                    is_silent = True
                    if part == '@':
                        continue
                    part = part[1:]
                else:
                    is_silent = False

            run_parts.append(part)
            if not is_phony(part):
                persistent_parts.append(part)

            if kind != 'shell':
                part = copy_annotations(part, shlex.quote(part))
            log_parts.append(color(part))

        log_command = ' '.join(log_parts)

        if self.exception is not None:
            Logger.debug(f"Can't run: {log_command}")
            raise self.exception

        if self.new_persistent_actions:
            self.new_persistent_actions[-1].run_action(persistent_parts)

        if not self.should_run_action():
            if not log_skipped_actions.value:
                level = logging.DEBUG
            elif is_silent:
                level = logging.FILE  # type: ignore
            else:
                level = logging.INFO
            Logger.log(level, f'Skip: {log_command}')
            self.did_skip_actions = True
            if self.new_persistent_actions:
                self.new_persistent_actions.append(  #
                    PersistentAction(self.new_persistent_actions[-1]))
            Invocation.skipped_count += 1
            return

        if self.did_skip_actions:
            self.must_run_action = True
            Logger.debug('Must restart step to run skipped action(s)')
            raise RestartException('To run skipped action(s)')

        self.must_run_action = True
        self.did_run_actions = True

        Invocation.actions_count += 1

        resources = Resources.effective(resources)
        if resources:
            await self.done(self._use_resources(resources))

        try:
            self.remove_stale_outputs()

            self.oldest_output_path = None

            if is_silent:
                Logger.file(f'Run: {log_command}')
            else:
                Logger.info(f'Run: {log_command}')

            sub_process = await self.done(runner(*run_parts))
            exit_status = await self.done(sub_process.wait())

            if self.new_persistent_actions:
                persistent_action = self.new_persistent_actions[-1]
                persistent_action.done_action()
                self.new_persistent_actions.append(PersistentAction(persistent_action))

            if exit_status != 0:
                self.log_and_abort(f'Failure: {log_command}')
                return

            Logger.trace(f'Success: {log_command}')
        finally:
            self._become_current()
            if resources:
                if Logger.isEnabledFor(logging.DEBUG):
                    Logger.debug('Free resources: ' + _dict_to_str(resources))
                Resources.free(resources)
                if Logger.isEnabledFor(logging.DEBUG):
                    Logger.debug('Available resources: ' + _dict_to_str(Resources.available))
                await self.done(Resources.condition.acquire())
                Resources.condition.notify_all()
                Resources.condition.release()

    async def _use_resources(self, amounts: Dict[str, int]) -> None:
        self._become_current()

        while True:
            if Resources.have(amounts):
                if Logger.isEnabledFor(logging.DEBUG):
                    Logger.debug('Grab resources: ' + _dict_to_str(amounts))
                Resources.grab(amounts)
                if Logger.isEnabledFor(logging.DEBUG):
                    Logger.debug('Available resources: ' + _dict_to_str(Resources.available))
                return

            if Logger.isEnabledFor(logging.DEBUG):
                Logger.debug('Available resources: ' + _dict_to_str(Resources.available))
                Logger.debug('Paused by waiting for resources: ' + _dict_to_str(amounts))

            await self.done(Resources.condition.acquire())
            await self.done(Resources.condition.wait())

            Resources.condition.release()

    async def sync(self) -> Optional[BaseException]:  # pylint: disable=too-many-branches
        """
        Wait until all the async actions queued so far are complete.

        This is implicitly called before running a action.
        """
        self._become_current()

        if self.async_actions:
            Logger.debug('Sync')
            results: List[Optional[StepException]] = \
                await self.done(asyncio.gather(*self.async_actions))
            if self.exception is None:
                for exception in results:
                    if exception is not None:
                        self.exception = exception
                        break
            self.async_actions = []

        Logger.debug('Synced')

        failed_inputs = False
        for path in sorted(self.required):
            if path in Invocation.poisoned \
                    or (not is_optional(path) and path not in Invocation.up_to_date):
                if self.exception is None:
                    level = logging.ERROR
                else:
                    level = logging.DEBUG
                Logger.log(level, f'The required: {path} has failed to build')
                Invocation.poisoned.add(path)
                failed_inputs = True
                continue

            if path not in Invocation.up_to_date:
                assert is_optional(path)
                continue

            Logger.debug(f'Has the required: {path}')

            if is_exists(path):
                continue

            if path in Invocation.phony:
                mtime_ns = Invocation.up_to_date[path].mtime_ns
            else:
                mtime_ns = Stat.stat(path).st_mtime_ns

            if self.newest_input_path is None or self.newest_input_mtime_ns < mtime_ns:
                self.newest_input_path = path
                self.newest_input_mtime_ns = mtime_ns

        if failed_inputs:
            self.abort('Failed to build the required target(s)')

        if self.exception is not None:
            return self.exception

        for action in self.new_persistent_actions:
            for name, partial_up_to_date in action.required.items():
                full_up_to_date = Invocation.up_to_date.get(name)
                if full_up_to_date is None:
                    partial_up_to_date.mtime_ns = 0
                else:
                    assert full_up_to_date.producer == partial_up_to_date.producer
                    partial_up_to_date.mtime_ns = full_up_to_date.mtime_ns

        if Logger.isEnabledFor(logging.DEBUG) and self.oldest_output_path is not None:
            if self.newest_input_path is None:
                Logger.debug('No inputs')
            else:
                Logger.debug(f'Newest input: {self.newest_input_path} '
                             f'time: {_datetime_from_nanoseconds(self.newest_input_mtime_ns)}')

        return None

    async def done(self, awaitable: Awaitable) -> Any:
        """
        Await some non-DynaMake function.
        """
        result = await awaitable
        self._become_current()
        return result

    def _become_current(self) -> None:
        Invocation.current = self
        current_thread().name = self.stack


_QUANTIZED_OF_NANOSECONDS: Dict[int, float] = {}
_NANOSECONDS_OF_QUANTIZED: Dict[str, int] = {}


def _datetime_from_str(string: str) -> datetime:
    return datetime.strptime(string, '%Y-%m-%d %H:%M:%S.%f')


def _datetime_from_nanoseconds(nanoseconds: int) -> str:
    if not _is_test:  # pylint: disable=protected-access
        # pragma: no cover
        seconds = datetime.fromtimestamp(nanoseconds // 1_000_000_000).strftime('%Y-%m-%d %H:%M:%S')
        fraction = '%09d' % (nanoseconds % 1_000_000_000)
        return seconds + '.' + fraction

    global _QUANTIZED_OF_NANOSECONDS
    quantized = _QUANTIZED_OF_NANOSECONDS.get(nanoseconds, None)
    if quantized is not None:
        return str(quantized)

    higher_nanoseconds = None
    higher_quantized = None
    lower_nanoseconds = None
    lower_quantized = None

    for old_nanoseconds, old_quantized in _QUANTIZED_OF_NANOSECONDS.items():
        if old_nanoseconds < nanoseconds:
            if lower_nanoseconds is None or lower_nanoseconds < old_nanoseconds:
                lower_nanoseconds = old_nanoseconds
                lower_quantized = old_quantized
        if old_nanoseconds > nanoseconds:
            if higher_nanoseconds is None or higher_nanoseconds < old_nanoseconds:
                higher_nanoseconds = nanoseconds
                higher_quantized = old_quantized

    if lower_quantized is None:
        if higher_quantized is None:
            quantized = 1
        else:
            quantized = higher_quantized - 1
    else:
        if higher_quantized is None:
            quantized = lower_quantized + 1
        else:
            quantized = (lower_quantized + higher_quantized) / 2

    _QUANTIZED_OF_NANOSECONDS[nanoseconds] = quantized
    _NANOSECONDS_OF_QUANTIZED[str(quantized)] = nanoseconds
    return str(quantized)


def _nanoseconds_from_datetime_str(string: str) -> int:
    if _is_test:  # pylint: disable=protected-access
        return _NANOSECONDS_OF_QUANTIZED[string]
    seconds_string, nanoseconds_string = string.split('.')

    seconds_datetime = _datetime_from_str(seconds_string + '.0')
    seconds = int(seconds_datetime.timestamp())

    nanoseconds_string = (nanoseconds_string + 9 * '0')[:9]
    nanoseconds = int(nanoseconds_string)

    return seconds * 1_000_000_000 + nanoseconds


def _reset_test_dates() -> None:
    global _QUANTIZED_OF_NANOSECONDS
    global _NANOSECONDS_OF_QUANTIZED
    _QUANTIZED_OF_NANOSECONDS = {}
    _NANOSECONDS_OF_QUANTIZED = {}


def step(output: Strings, priority: int = 0) -> Callable[[Callable], Callable]:
    """
    Decorate a build step functions.

    The ``priority`` (default: 0) is used to pick between multiple steps
    providing the same output. This is typically used to define low-priority
    steps with pattern outputs and high-priority steps which override them for
    specific output(s).
    """
    def _wrap(wrapped: Callable) -> Callable:
        Step(wrapped, output, priority)
        return wrapped
    return _wrap


def require(*paths: Strings) -> None:
    """
    Require an input file for the step.

    This queues an async build of the input file using the appropriate step,
    and immediately returns.
    """
    for path in each_string(*paths):
        Invocation.current.require(path)


async def sync() -> Optional[BaseException]:
    """
    Wait until all the input files specified so far are built.

    This is invoked automatically before running actions.
    """
    current = Invocation.current
    return await current.done(current.sync())


async def shell(*command: Strings, prefix: Optional[Strings] = None,
                **resources: int) -> None:
    """
    Execute a shell command.

    The caller is responsible for all quotations. If the first character of the
    command is ``@`` then it is "silent", that is, it is logged in the FILE
    level and not the INFO level.

    This first waits until all input files requested so far are ready.

    The shell command is only executed after any ``resources`` are obtained.
    This can be used to ensure a bounded total amount used by of any resource
    declared by ``resource_parameters``.

    If ``prefix`` is specified, it is silently added to the command. By default this
    is the value of the :py:const:`default_shell_prefix` parameter.
    """
    current = Invocation.current
    if prefix is None:
        prefix = default_shell_prefix.value

    def _run_shell(*parts: Strings) -> Any:
        assert prefix is not None
        return asyncio.create_subprocess_shell(' '.join(flatten(prefix, *parts)),
                                               executable=shell_executable.value)
    await current.done(current.run_action('shell', _run_shell, *command, **resources))


async def spawn(*command: Strings, **resources: int) -> None:
    """
    Execute an external program with arguments.

    If the first character of the command is ``@`` then it is "silent", that
    is, it is logged in the FILE level and not the INFO level.

    This first waits until all input files requested so far are ready.
    """
    current = Invocation.current
    await current.done(current.run_action('spawn', asyncio.create_subprocess_exec,
                                          *command, **resources))


def log_prefix() -> str:
    """
    A prefix for log messages.
    """
    return Invocation.current.log


def make(parser: ArgumentParser, *,
         default_targets: Strings = 'all',
         logger_name: str = 'dynamake',
         adapter: Optional[Callable[[Namespace], None]] = None) -> None:
    """
    A generic ``main`` function for ``DynaMake``.

    If no explicit targets are given, will build the ``default_targets``
    (default: ``all``).

    Uses the ``logger_name`` (default: ``dynamake``) to create the global :py:class:`Logger`.

    The optional ``adapter`` may perform additional adaptation of the execution environment based on
    the parsed command-line arguments before the actual function(s) are invoked.
    """
    default_targets = flatten(default_targets)

    _load_modules()

    parser.add_argument('TARGET', nargs='*',
                        help=f'The file or target to make (default: {" ".join(default_targets)})')

    parser.add_argument('--module', '-m', metavar='MODULE', action='append',
                        help='A Python module to load (containing function definitions)')

    Parameter.add_to_parser(parser)

    parser.add_argument('--list_steps', '-ls', default=False, action='store_true',
                        help='List all the build steps and their targets, and exit.')

    args = parser.parse_args()
    Parameter.parse_args(args)

    Logger.setup(logger_name)

    if adapter is not None:
        adapter(args)

    _compute_jobs()

    if args.list_steps:
        _list_steps()
    else:
        _build_targets([path for path in args.TARGET if path is not None]
                       or flatten(default_targets))


def _load_modules() -> None:
    # TODO: This needs to be done before we set up the command line options
    # parser, because the options depend on the loaded modules. Catch-22. This
    # therefore employs a brutish option detection which may not be 100% correct.
    did_import = False
    for option, value in zip(sys.argv, sys.argv[1:]):
        if option in ['-m', '--module']:
            did_import = True
            import_module(value)
    if not did_import and os.path.exists(DEFAULT_MODULE + '.py'):
        import_module(DEFAULT_MODULE)


def _compute_jobs() -> None:
    if jobs.value < 0:
        jobs.value = (os.cpu_count() or 1) // -jobs.value
        if jobs.value < 1:
            jobs.value = 1
    Resources.available['jobs'] = Resources.total['jobs'] = jobs.value


def _list_steps() -> None:
    is_first = True
    steps = [(step.priority, step.name, step) for step in Step.by_name.values()]
    for _, _, step in sorted(steps):  # pylint: disable=redefined-outer-name
        if not is_first:
            print()
        is_first = False

        doc = step.function.__doc__
        if doc:
            print('# ' + dedent(doc).strip().replace('\n', '\n# '))
        print(f'{step.name}:')
        print(f'  priority: {step.priority}')
        print('  outputs:')
        for output in sorted(step.output):
            properties = []
            if is_exists(output):
                properties.append('exists')
            if is_optional(output):
                properties.append('optional')
            if is_phony(output):
                properties.append('phony')
            if is_precious(output):
                properties.append('precious')
            if properties:
                print(f'  - {output}: [{", ".join(properties)}]')
            else:
                print(f'  - {output}')


def _build_targets(targets: List[str]) -> None:
    Logger.trace('Targets: ' + ' '.join(targets))
    if Logger.isEnabledFor(logging.DEBUG):
        for value in Resources.available.values():
            if value > 0:
                Logger.debug('Available resources: ' + _dict_to_str(Resources.available))
                break
    try:
        for target in targets:
            require(target)
        result: Optional[BaseException] = \
            asyncio.get_event_loop().run_until_complete(Invocation.top.sync())
    except StepException as exception:  # pylint: disable=broad-except
        result = exception
        Invocation.current = Invocation.top

    if result is not None:
        Logger.error('Fail')
        if _is_test:  # pylint: disable=protected-access
            raise result
        sys.exit(1)

    if Invocation.actions_count > 0:
        status = 'Done'
    elif Invocation.skipped_count > 0:
        status = 'Skipped'
    else:
        status = 'Complete'
    Logger.trace(status)


class RwLocks:
    """
    Maintain read-write lock to ensure actions that modify data do not step on each other.
    """

    #: Locks for for each named data.
    by_name: Dict[str, RWLock] = {}

    #: Read and modify lockers for each name data.
    lockers: Dict[str, Tuple[Set[str], Set[str]]]

    #: Read and modify waiters for each name data.
    waiters: Dict[str, Tuple[Set[str], Set[str]]]

    @staticmethod
    def reset() -> None:
        """
        Reset all the current state, for tests.
        """
        RwLocks.by_name = {}
        RwLocks.lockers = {}
        RwLocks.waiters = {}

    @staticmethod
    def get(name: str) -> RWLock:
        """
        Get the ``RWLock`` for a specific named data.
        """
        lock = RwLocks.by_name.get(name)
        if lock is None:
            lock = RwLocks.by_name[name] = RWLock()
        return lock

    @staticmethod
    @asynccontextmanager
    async def reading(names: List[str]) -> AsyncGenerator:
        """
        Async context for actions that read some data which might be accessed by other actions.
        """
        assert len(names) > 0
        name = names[0]
        RwLocks.become_waiter(0, name)
        async with context(RwLocks.get(name).reader_lock):
            RwLocks.become_locker(0, name)
            try:
                if len(names) == 1:
                    yield
                else:
                    async with RwLocks.reading(names[1:]):
                        yield
            finally:
                RwLocks.become_nothing(0, name)

    @staticmethod
    @asynccontextmanager
    async def modifying(names: List[str]) -> AsyncGenerator:
        """
        Async context for actions that modify some data which might be accessed by other actions.
        """
        assert len(names) > 0
        name = names[0]
        RwLocks.become_waiter(1, name)
        async with context(RwLocks.get(name).writer_lock):
            RwLocks.become_locker(1, name)
            try:
                if len(names) == 1:
                    yield
                else:
                    async with RwLocks.modifying(names[1:]):
                        yield
            finally:
                RwLocks.become_nothing(1, name)

    @staticmethod
    def become_waiter(index: int, name: str) -> None:
        """
        Record the fact we are waiting to lock some data.
        """
        action = ('read', 'modify')[index]

        if Logger.isEnabledFor(logging.DEBUG):
            Logger.debug(f'Will {action} data: {name}')
            RwLocks.log_status(name)

        lockers_status = RwLocks.lockers.get(name)
        if lockers_status is not None and name in lockers_status[index]:
            raise RuntimeError(f'{Invocation.current.log} has already locked {action} {name}')

        waiters_status = RwLocks.waiters.get(name)
        if waiters_status is None:
            RwLocks.waiters[name] = waiters_status = (set(), set())
        if Invocation.current.log in waiters_status[index]:
            raise RuntimeError(f'{Invocation.current.log} is already waiting to {action} {name}')
        waiters_status[index].add(Invocation.current.log)

    @staticmethod
    def become_locker(index: int, name: str) -> None:
        """
        Record the fact we are no longer waiting and actually are locking some data.
        """
        action = ('read', 'modify')[index]

        if Logger.isEnabledFor(logging.DEBUG):
            Logger.debug(f'Lock {action} data: {name}')
            RwLocks.log_status(name, am_waiter=True)

        waiters_status = RwLocks.waiters[name]
        assert Invocation.current.log in waiters_status[index]
        waiters_status[index].remove(Invocation.current.log)

        lockers_status = RwLocks.lockers.get(name)
        if lockers_status is None:
            RwLocks.lockers[name] = lockers_status = (set(), set())
        assert Invocation.current.log not in lockers_status[index]
        lockers_status[index].add(Invocation.current.log)

    @staticmethod
    def become_nothing(index: int, name: str) -> None:
        """
        Record the fact we have released the lock.
        """
        action = ('read', 'modify')[index]

        if Logger.isEnabledFor(logging.DEBUG):
            Logger.debug(f'Unlock {action} data: {name}')
            RwLocks.log_status(name, am_locker=True)

        waiters_status = RwLocks.waiters[name]
        assert Invocation.current.log not in waiters_status[index]

        lockers_status = RwLocks.lockers[name]
        assert Invocation.current.log in lockers_status[index]
        lockers_status[index].remove(Invocation.current.log)

    @staticmethod
    def log_status(name: str, am_locker: bool = False,  # pylint: disable=too-many-branches
                   am_waiter: bool = False) -> None:
        """
        Log the state of locks for a specific data.
        """
        seen_locker = False

        lockers = RwLocks.lockers.get(name)
        if lockers is not None:
            readers, modifiers = lockers

            for reader in readers:
                if reader == Invocation.current.log:
                    assert not seen_locker
                    seen_locker = True
                else:
                    Logger.debug(f'step: {reader} is reading data: {name}')

            for modifier in modifiers:
                if modifier == Invocation.current.log:
                    assert not seen_locker
                    seen_locker = True
                else:
                    Logger.debug(f'step: {modifier} is modifying data: {name}')

        assert seen_locker == am_locker

        seen_waiter = False

        waiters = RwLocks.waiters.get(name)
        if waiters is not None:
            readers, modifiers = waiters

            for reader in sorted(readers):
                if reader == Invocation.current.log:
                    assert not seen_waiter
                    seen_waiter = True
                else:
                    Logger.debug(f'step: {reader} is waiting to read data: {name}')

            for modifier in sorted(modifiers):
                if modifier == Invocation.current.log:
                    assert not seen_waiter
                    seen_waiter = True
                else:
                    Logger.debug(f'step: {modifier} is waiting to modify data: {name}')

        assert seen_waiter == am_waiter


@asynccontextmanager
async def reading(*names: Strings) -> AsyncGenerator:
    """
    Async context for actions that reads some data which might be accessed by other actions.
    """
    await sync()
    async with RwLocks.reading(sorted(flatten(*names))):
        yield


@asynccontextmanager
async def modifying(*names: Strings) -> AsyncGenerator:
    """
    Async context for actions that modify some data which might be accessed by other actions.

    This first :py:func:`sync`-s to ensure all required files exists.
    """
    await sync()
    async with RwLocks.modifying(sorted(flatten(*names))):
        yield


def reset(is_test: bool = False, reset_test_times: bool = False) -> None:
    """
    Reset all the current state, for tests.
    """
    Parameter.reset()
    _define_parameters()

    Resources.reset()
    Step.reset()
    Logger.reset()
    Invocation.reset()
    Stat.reset()
    RwLocks.reset()

    if is_test:
        global _is_test  # pylint: disable=invalid-name
        _is_test = True

    if reset_test_times:
        _reset_test_dates()


reset()


def step_kwargs() -> Dict[str, Any]:
    """
    Return the named arguments of the current step.

    These are the captured names extracted from the output file(s) that the current
    step was invoked to build.
    """
    return Invocation.current.kwargs


async def done(awaitable: Awaitable) -> Any:
    """
    Await some non-DynaMake async function.
    """
    return await Invocation.current.done(awaitable)


@asynccontextmanager
async def context(wrapped: AsyncGenerator) -> AsyncGenerator:
    """
    Await some non-DynaMake async context.
    """
    invocation = Invocation.current
    async with wrapped:  # type: ignore
        invocation._become_current()  # pylint: disable=protected-access
        yield()
    invocation._become_current()  # pylint: disable=protected-access


def can_make(path: str) -> bool:
    """
    Test whether there are steps for creating the specified ``path``.
    """
    for (regexp, _) in Step.by_regexp:
        if re.fullmatch(regexp, path):
            return True

    return False


def try_require(path: str) -> bool:
    """
    If there are steps to create the specified ``path``, ``require`` it, and return
    ``True``; otherwise, return ``False``.
    """
    if can_make(path):
        require(path)
        return True

    return False
