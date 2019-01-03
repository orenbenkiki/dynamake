"""
Test the pattern matching.
"""


from typing import List
from unittest import TestCase

import yaml

from dynamake.patterns import glob2re

# pylint: disable=missing-docstring


class TestPatterns(TestCase):

    def test_load_regexp(self) -> None:
        pattern = yaml.load('!r a.*b')
        self.assertEqual(str(pattern), "re.compile('a.*b')")

    def test_load_glob(self) -> None:
        pattern = yaml.load('!g a*b')
        self.assertEqual(str(pattern), "re.compile('a[^/]*b')")

    def test_glob2re(self) -> None:
        self.check_glob2re(glob='', compiled='', match=[''], not_match=['a'])

        self.check_glob2re(glob='a', compiled='a', match=['a'], not_match=['', 'b', '/'])

        self.check_glob2re(glob='?', compiled='[^/]', match=['a', 'b'], not_match=['', '/'])

        self.check_glob2re(glob='*.py', compiled='[^/]*\\\\.py',
                           match=['.py', 'a.py'], not_match=['a_py', '/a.py'])

        self.check_glob2re(glob='foo**bar', compiled='foo.*bar',
                           match=['foobar', 'foo_/baz/_bar'], not_match=['foo', 'bar'])

        self.check_glob2re(glob='foo/**/bar', compiled='foo\\\\/(.*/)?bar',
                           match=['foo/bar', 'foo/baz/bar'], not_match=['foo', 'bar'])

        self.check_glob2re(glob='[a', compiled='\\\\[a', match=['[a'], not_match=[])

        self.check_glob2re(glob='[a-z]', compiled='[a-z]', match=['c'], not_match=['C', '/'])
        self.check_glob2re(glob='[!a-z]', compiled='[^/a-z]', match=['C'], not_match=['c', '/'])
        self.check_glob2re(glob='[^a-z]', compiled='[\\\\^a-z]', match=['c', '^'], not_match=['C'])
        self.check_glob2re(glob='[\\]', compiled='[\\\\\\\\]', match=['\\'], not_match=['/'])

    def check_glob2re(self, glob: str, compiled: str,
                      match: List[str], not_match: List[str]) -> None:
        pattern = glob2re(glob)
        self.assertEqual(str(pattern), "re.compile('" + compiled + "')")
        for text in match:
            self.assertTrue(bool(pattern.fullmatch(text)))
        for text in not_match:
            self.assertFalse(bool(pattern.fullmatch(text)))
