"""
Test the pattern matching.
"""


import re
from typing import Callable
from typing import List

import yaml

from dynamake.patterns import capture2glob
from dynamake.patterns import capture2re
from dynamake.patterns import capture_globs
from dynamake.patterns import expand_strings
from dynamake.patterns import extract_strings
from dynamake.patterns import flatten
from dynamake.patterns import glob2re
from dynamake.patterns import glob_strings
from tests import TestWithFiles
from tests import TestWithReset
from tests import write_file

# pylint: disable=missing-docstring


class TestPatterns(TestWithReset):

    def test_flatten(self) -> None:
        self.assertEqual(flatten('a', ['b', ['c']]), ['a', 'b', 'c'])

    def test_load_regexp(self) -> None:
        pattern = yaml.load('!r a.*b')
        self.assertEqual(str(pattern), "re.compile('a.*b')")

    def test_load_glob(self) -> None:
        pattern = yaml.load('!g a*b')
        self.assertEqual(str(pattern), "re.compile('a[^/]*b')")

    def test_expand_strings(self) -> None:
        self.assertEqual(expand_strings({'a': 1}, ['{a}.foo', '{a}.bar']), ['1.foo', '1.bar'])

    def test_glob2re(self) -> None:
        self.check_2re(glob2re, string='', compiled='', match=[''], not_match=['a'])

        self.check_2re(glob2re, string='a', compiled='a', match=['a'], not_match=['', 'b', '/'])

        self.check_2re(glob2re, string='?', compiled='[^/]', match=['a', 'b'], not_match=['', '/'])

        self.check_2re(glob2re, string='*.py', compiled='[^/]*\\\\.py',
                       match=['.py', 'a.py'], not_match=['a_py', '/a.py'])

        self.check_2re(glob2re, string='foo**bar', compiled='foo.*bar',
                       match=['foobar', 'foo_/baz/_bar'], not_match=['foo', 'bar'])

        self.check_2re(glob2re, string='foo/**/bar', compiled='foo/(.*/)?bar',
                       match=['foo/bar', 'foo/baz/bar'], not_match=['foo', 'bar'])

        self.check_2re(glob2re, string='[a', compiled='\\\\[a', match=['[a'], not_match=[])

        self.check_2re(glob2re, string='[a-z]', compiled='[a-z]', match=['c'], not_match=['C', '/'])

        self.check_2re(glob2re, string='[!a-z]', compiled='[^/a-z]',
                       match=['C'], not_match=['c', '/'])

        self.check_2re(glob2re, string='[^a-z]', compiled='[\\\\^a-z]',
                       match=['c', '^'], not_match=['C'])

        self.check_2re(glob2re, string='[\\]', compiled='[\\\\\\\\]', match=['\\'], not_match=['/'])

    def test_capture2re(self) -> None:
        self.check_2re(capture2re, string='', compiled='', match=[''], not_match=['a'])
        self.check_2re(capture2re, string='{{}}{foo}', compiled='{{}}{foo}', match=[], not_match=[])

        self.check_2re(capture2re, string='foo{*bar}baz', compiled=r'foo(?P<bar>[^/]*)baz',
                       match=['foobaz', 'foobarbaz'], not_match=['', 'foo/baz', 'foobar/baz'])

        self.check_2re(capture2re, string=r'foo{**bar}baz', compiled=r'foo(?P<bar>.*)baz',
                       match=['foobaz', 'foo/baz', 'foo/bar/baz'], not_match=[''])

        self.check_2re(capture2re, string=r'foo/{**bar}/baz', compiled=r'foo/(?:(?P<bar>.*)/)?baz',
                       match=['foo/baz', 'foo/bar/baz'], not_match=['', 'foobaz', 'foo/barbaz'])

        self.check_2re(capture2re, string='foo{*bar:[0-9]}baz', compiled=r'foo(?P<bar>[0-9])baz',
                       match=['foo1baz'], not_match=['foo12baz', 'fooQbaz'])

    def test_nonterminated_capture(self) -> None:
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               re.escape('pattern:\n'
                                         'foo{*bar\n'
                                         '        ^ missing }'),
                               capture2re, 'foo{*bar')

    def test_invalid_capture_name(self) -> None:
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               re.escape('pattern:\n'
                                         'foo{*bar+}baz\n'
                                         '        ^ invalid captured name character'),
                               capture2re, 'foo{*bar+}baz')

    def test_empty_capture_name(self) -> None:
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               re.escape('pattern:\n'
                                         'foo{*}bar\n'
                                         '     ^ empty captured name'),
                               capture2re, 'foo{*}bar')

    def test_empty_capture_regexp(self) -> None:
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               re.escape('pattern:\n'
                                         'foo{*bar:}baz\n'
                                         '         ^ empty captured regexp'),
                               capture2re, 'foo{*bar:}baz')

    def check_2re(self, parser: Callable[[str], str],  # pylint: disable=too-many-arguments
                  string: str, compiled: str,
                  match: List[str], not_match: List[str]) -> None:
        pattern = re.compile(parser(string))
        self.assertEqual(str(pattern), "re.compile('" + compiled + "')")
        for text in match:
            self.assertTrue(bool(pattern.fullmatch(text)), text)
        for text in not_match:
            self.assertFalse(bool(pattern.fullmatch(text)), text)

    def test_capture_to_glob(self) -> None:
        self.assertEqual(capture2glob(''), '')
        self.assertEqual(capture2glob('a'), 'a')
        self.assertEqual(capture2glob('{{}}'), '{}')
        self.assertEqual(capture2glob('{foo}{*bar:[0-9]}baz'), '{foo}[0-9]baz')
        self.assertEqual(capture2glob('foo/{**bar}/baz'), 'foo/**/baz')
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               re.escape('pattern:\n'
                                         'foo{*bar\n'
                                         '        ^ missing }'),
                               capture2glob, 'foo{*bar')

    def test_extract_strings(self) -> None:
        self.assertEqual(extract_strings({'foo': 'x'}, '{foo}/{*bar}.txt', 'x/@a.txt'),
                         [{'bar': '@a'}])

        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               r'string: x/y.png .* pattern: {foo}/{\*bar}.txt',
                               extract_strings, {'foo': 'x'}, '{foo}/{*bar}.txt', 'x/y.png')


class TestGlob(TestWithFiles):

    def test_no_match(self) -> None:
        captured = capture_globs({'foo': 'x'}, '{foo}.txt')
        self.assertEqual(captured.paths, [])
        self.assertEqual(captured.wildcards, [])
        self.assertEqual(glob_strings({'foo': 'x'}, '{foo}.txt'), [])

    def test_no_capture(self) -> None:
        write_file('x.txt', '')
        captured = capture_globs({'foo': 'x'}, '{foo}.txt')
        self.assertEqual(captured.paths, ['x.txt'])
        self.assertEqual(captured.wildcards, [{}])
        self.assertEqual(glob_strings({'foo': 'x'}, '{foo}.txt'), ['x.txt'])

    def test_capture_string(self) -> None:
        write_file('x.txt', '')
        captured = capture_globs({}, '{*foo}.txt')
        self.assertEqual(captured.paths, ['x.txt'])
        self.assertEqual(captured.wildcards, [{'foo': 'x'}])
        self.assertEqual(glob_strings({}, '{*foo}.txt'), ['x.txt'])

    def test_capture_int(self) -> None:
        write_file('12.txt', '')
        captured = capture_globs({}, '{*foo}.txt')
        self.assertEqual(captured.paths, ['12.txt'])
        self.assertEqual(captured.wildcards, [{'foo': 12}])
        self.assertEqual(glob_strings({}, '{*foo}.txt'), ['12.txt'])
