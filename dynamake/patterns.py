"""
Allow simple loading of regular expressions in configuration YAML files.
"""

from .stat import Stat
from curses.ascii import isalnum
from datetime import datetime
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
from typing.re import Pattern  # pylint: disable=import-error
from yaml import Loader
from yaml import Node

import argparse
import logging
import re
import yaml


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


def expand_strings(wildcards: Dict[str, Any], *patterns: Strings) -> List[str]:
    """
    Given some wildcards values and a pattern (Python format string),
    generate one output per pattern using these values.
    """
    return [copy_annotations(pattern, pattern.format(**wildcards))
            for pattern
            in each_string(*patterns)]


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


class AnnotatedStr(str):
    """
    A wrapper containing optional annotations.
    """

    #: Whether this was annotated by :py:func:`dynamake.patterns.optional`.
    optional = False

    #: Whether this was annotated by :py:func:`dynamake.patterns.exists`.
    exists = False

    #: Whether this was annotated by :py:func:`dynamake.patterns.precious`.
    precious = False

    #: Whether this was annotated by :py:func:`dynamake.patterns.emphasized`.
    emphasized = False


# pylint: disable=function-redefined
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
# pylint: enable=function-redefined


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


def color(*strings: Strings) -> List[str]:
    """
    Return the strings, replacing any that were :py:func:`dynamake.patterns.emphasized` by a colored
    version.
    """
    result = []
    for string in each_string(*strings):
        if is_emphasized(string):
            result.append(colored(string, attrs=['bold']))
        else:
            result.append(string)
    return result


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
        target.precious = source.precious
        target.emphasized = source.emphasized
    return target


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
        (:py:func:`dynamake.patterns.optional` and/or :py:func:`dynamake.patterns.exists`) of the
        pattern are copied to the paths expanded from the pattern.
    """
    captured = Captured()
    for capture in each_string(*patterns):
        regexp = capture2re(capture).format(**wildcards)
        glob = capture2glob(capture).format(**wildcards)
        paths = Stat.glob(glob)

        if not paths and not is_optional(capture):
            raise NonOptionalException(glob, capture)

        # Sorted to make tests deterministic.
        for path in sorted(paths):
            path = copy_annotations(capture, path)
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
    results: List[str] = []
    for capture in each_string(*patterns):
        glob = capture2glob(capture).format(**wildcards)
        paths = Stat.glob(glob)

        if not paths and not is_optional(capture):
            raise NonOptionalException(glob, capture)

        # Sorted to make tests deterministic.
        for path in sorted(paths):
            results.append(copy_annotations(capture, path))

    return results


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
        values[name] = str(value or '')
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


# pylint: disable=redefined-builtin

class Range:
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


def _str2range(string: str, parser: Callable[[str], Union[int, float]], range: Range) \
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
    :py:func:`dynamake.pattern.Range`.
    """
    def _parse(string: str) -> float:
        return _str2range(string, float,
                          Range(min=min, max=max, step=step,
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
    :py:func:`dynamake.pattern.Range`.
    """
    def _parse(string: str) -> int:
        return _str2range(string, int,  # type: ignore
                          Range(min=min, max=max, step=step,
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
