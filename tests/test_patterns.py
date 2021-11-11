"""
Test the pattern matching.
"""


import argparse
import re
from enum import Enum
from typing import Callable
from typing import List

import yaml  # type: ignore

from dynamake import NonOptionalException
from dynamake import capture2glob
from dynamake import capture2re
from dynamake import copy_annotations
from dynamake import exists
from dynamake import expand
from dynamake import flatten
from dynamake import fmt_capture
from dynamake import glob2re
from dynamake import glob_capture
from dynamake import glob_extract
from dynamake import glob_fmt
from dynamake import glob_paths
from dynamake import is_exists
from dynamake import is_optional
from dynamake import optional
from dynamake import precious
from dynamake import str2bool
from dynamake import str2choice
from dynamake import str2enum
from dynamake import str2float
from dynamake import str2int
from dynamake import str2list
from dynamake import str2optional
from tests import TestWithFiles
from tests import TestWithReset
from tests import write_file

# pylint: disable=missing-docstring


class TestAnnotations(TestWithReset):
    def test_annotations(self) -> None:
        self.assertFalse(is_optional("x"))
        self.assertFalse(is_exists("x"))

        self.assertEqual(optional(["x"]), ["x"])
        self.assertTrue(is_optional(optional("x")))
        self.assertFalse(is_exists(optional("x")))

        self.assertEqual(exists(["x"]), ["x"])
        self.assertFalse(is_optional(exists("x")))
        self.assertTrue(is_exists(exists("x")))

        self.assertTrue(is_exists(optional(exists("x"))))

        self.assertEqual(precious(["x"]), ["x"])
        self.assertEqual(precious("x"), "x")

    def test_copy_annotations(self) -> None:
        annotated = optional("foo")
        unannotated = "bar"
        copied = copy_annotations(annotated, unannotated)

        self.assertTrue(is_optional(annotated))
        self.assertFalse(is_optional(unannotated))
        self.assertTrue(is_optional(copied))
        self.assertEqual(copied, unannotated)

        annotated = exists(annotated)
        copied = copy_annotations(annotated, copied)

        self.assertTrue(is_optional(annotated))
        self.assertTrue(is_exists(annotated))
        self.assertTrue(is_optional(copied))
        self.assertTrue(is_exists(copied))
        self.assertEqual(copied, unannotated)

    def test_flatten(self) -> None:
        self.assertEqual(flatten("a", ["b", ["c"]]), ["a", "b", "c"])


class TestConversions(TestWithReset):
    def test_load_regexp(self) -> None:
        pattern = yaml.full_load("!r a.*b")
        self.assertEqual(str(pattern), "re.compile('a.*b')")

    def test_load_glob(self) -> None:
        pattern = yaml.full_load("!g a*b")
        self.assertEqual(str(pattern), "re.compile('a[^/]*b')")

    def test_glob2re(self) -> None:
        self.check_2re(glob2re, string="", compiled="", match=[""], not_match=["a"])

        self.check_2re(glob2re, string="a", compiled="a", match=["a"], not_match=["", "b", "/"])

        self.check_2re(glob2re, string="?", compiled="[^/]", match=["a", "b"], not_match=["", "/"])

        self.check_2re(
            glob2re, string="*.py", compiled="[^/]*\\\\.py", match=[".py", "a.py"], not_match=["a_py", "/a.py"]
        )

        self.check_2re(
            glob2re, string="foo**bar", compiled="foo.*bar", match=["foobar", "foo_/baz/_bar"], not_match=["foo", "bar"]
        )

        self.check_2re(
            glob2re,
            string="foo/**/bar",
            compiled="foo/(.*/)?bar",
            match=["foo/bar", "foo/baz/bar"],
            not_match=["foo", "bar"],
        )

        self.check_2re(glob2re, string="[a", compiled="\\\\[a", match=["[a"], not_match=[])

        self.check_2re(glob2re, string="[a-z]", compiled="[a-z]", match=["c"], not_match=["C", "/"])

        self.check_2re(glob2re, string="[!a-z]", compiled="[^/a-z]", match=["C"], not_match=["c", "/"])

        self.check_2re(glob2re, string="[^a-z]", compiled="[\\\\^a-z]", match=["c", "^"], not_match=["C"])

        self.check_2re(glob2re, string="[\\]", compiled="[\\\\\\\\]", match=["\\"], not_match=["/"])

    def test_capture2re(self) -> None:
        self.check_2re(capture2re, string="", compiled="", match=[""], not_match=["a"])
        self.check_2re(capture2re, string="{{}}{foo}", compiled="{{}}{foo}", match=[], not_match=[])

        self.check_2re(
            capture2re,
            string="foo{*bar}baz",
            compiled=r"foo(?P<bar>[^/]*)baz",
            match=["foobaz", "foobarbaz"],
            not_match=["", "foo/baz", "foobar/baz"],
        )

        self.check_2re(
            capture2re,
            string=r"foo{**bar}baz",
            compiled=r"foo(?P<bar>.*)baz",
            match=["foobaz", "foo/baz", "foo/bar/baz"],
            not_match=[""],
        )

        self.check_2re(
            capture2re,
            string=r"foo/{**bar}/baz",
            compiled=r"foo/(?:(?P<bar>.*)/)?baz",
            match=["foo/baz", "foo/bar/baz"],
            not_match=["", "foobaz", "foo/barbaz"],
        )

        self.check_2re(
            capture2re,
            string="foo{*bar:[0-9]}baz",
            compiled=r"foo(?P<bar>[0-9])baz",
            match=["foo1baz"],
            not_match=["foo12baz", "fooQbaz"],
        )

    def test_fmt_capture(self) -> None:
        self.assertEqual(
            fmt_capture(dict(foo="x", bar="y"), "{foo}.{*bar:[a-z]}.{*_baz}.{**_vaz:[a-z]}.{{txt}}"),
            "x.y.{*_baz}.{**_vaz:[a-z]}.{{txt}}",
        )

        self.assertEqual(fmt_capture({}, "foo", "bar"), ["foo", "bar"])

        self.assertRaisesRegex(RuntimeError, "empty captured name", fmt_capture, {}, "{}")

        self.assertRaisesRegex(RuntimeError, "missing }", fmt_capture, dict(foo="x"), "{foo")

        self.assertRaisesRegex(RuntimeError, "invalid captured name character", fmt_capture, {}, "{$")

    def test_nonterminated_capture(self) -> None:
        self.assertRaisesRegex(
            RuntimeError, re.escape("pattern:\n" "foo{*bar\n" "        ^ missing }"), capture2re, "foo{*bar"
        )

    def test_invalid_capture_name(self) -> None:
        self.assertRaisesRegex(
            RuntimeError,
            re.escape("pattern:\n" "foo{*bar+}baz\n" "        ^ invalid captured name character"),
            capture2re,
            "foo{*bar+}baz",
        )

    def test_empty_capture_name(self) -> None:
        self.assertRaisesRegex(
            RuntimeError, re.escape("pattern:\n" "foo{*}bar\n" "     ^ empty captured name"), capture2re, "foo{*}bar"
        )

    def test_empty_capture_regexp(self) -> None:
        self.assertRaisesRegex(
            RuntimeError,
            re.escape("pattern:\n" "foo{*bar:}baz\n" "         ^ empty captured regexp"),
            capture2re,
            "foo{*bar:}baz",
        )

    def check_2re(  # pylint: disable=too-many-arguments
        self,
        parser: Callable[[str], str],
        string: str,
        compiled: str,
        match: List[str],
        not_match: List[str],
    ) -> None:
        pattern = re.compile(parser(string))
        self.assertEqual(str(pattern), "re.compile('" + compiled + "')")
        for text in match:
            self.assertTrue(bool(pattern.fullmatch(text)), text)
        for text in not_match:
            self.assertFalse(bool(pattern.fullmatch(text)), text)

    def test_capture_to_glob(self) -> None:
        self.assertEqual(capture2glob(""), "")
        self.assertEqual(capture2glob("a"), "a")
        self.assertEqual(capture2glob("{{}}"), "{}")
        self.assertEqual(capture2glob("{foo}{*bar:[0-9]}baz"), "{foo}[0-9]baz")
        self.assertEqual(capture2glob("foo/{**bar}/baz"), "foo/**/baz")
        self.assertRaisesRegex(
            RuntimeError, re.escape("pattern:\n" "foo{*bar\n" "        ^ missing }"), capture2glob, "foo{*bar"
        )


class TestParamParsers(TestWithReset):
    def test_str2bool(self) -> None:
        self.assertTrue(str2bool("t"))
        self.assertFalse(str2bool("n"))
        self.assertRaisesRegex(argparse.ArgumentTypeError, "Boolean value expected.", str2bool, "maybe")

    def test_str2enum(self) -> None:
        class Foo(Enum):
            bar = 1  # pylint: disable=blacklisted-name

        self.assertEqual(str2enum(Foo)("bar"), Foo.bar)
        self.assertRaisesRegex(argparse.ArgumentTypeError, "Expected one of: bar", str2enum(Foo), "baz")

    def test_str2range(self) -> None:
        self.assertEqual(str2int()("12"), 12)
        self.assertEqual(str2float()("12"), 12.0)
        self.assertEqual(str2float()("1.2"), 1.2)

        self.assertEqual(str2int(min=2)("2"), 2)
        self.assertEqual(str2int(min=2, include_min=False)("3"), 3)

        self.assertEqual(str2int(max=2)("2"), 2)
        self.assertEqual(str2int(max=2, include_max=False)("1"), 1)

        self.assertEqual(str2int(step=2)("4"), 4)
        self.assertEqual(str2int(min=1, step=2)("3"), 3)

        self.assertRaisesRegex(argparse.ArgumentTypeError, "Expected int value", str2int(), "x")

        self.assertRaisesRegex(argparse.ArgumentTypeError, "Expected int value, where 2 <= value", str2int(min=2), "1")

        self.assertRaisesRegex(
            argparse.ArgumentTypeError, "Expected int value, where 2 < value", str2int(min=2, include_min=False), "2"
        )

        self.assertRaisesRegex(
            argparse.ArgumentTypeError, "Expected int value, where value % 2 == 0", str2int(step=2), "3"
        )

        self.assertRaisesRegex(
            argparse.ArgumentTypeError,
            "Expected int value, where 3 <= value and value % 2 == 1",
            str2int(min=3, step=2),
            "4",
        )

        self.assertRaisesRegex(
            argparse.ArgumentTypeError, "Expected float value, where value <= 2", str2float(max=2), "3"
        )

        self.assertRaisesRegex(
            argparse.ArgumentTypeError,
            "Expected float value, where value < 2",
            str2float(max=2, include_max=False),
            "2",
        )

    def test_str2choice(self) -> None:
        self.assertEqual(str2choice(["foo", "bar"])("foo"), "foo")

        self.assertRaisesRegex(
            argparse.ArgumentTypeError, "Expected one of: foo bar", str2choice(["foo", "bar"]), "baz"
        )

    def test_str2list(self) -> None:
        self.assertEqual(str2list(str2bool)("y n"), [True, False])

        self.assertRaisesRegex(argparse.ArgumentTypeError, "Boolean value expected.", str2list(str2bool), "y x n")

    def test_str2optional(self) -> None:
        self.assertTrue(str2optional(str2bool)("y"))
        self.assertTrue(str2optional(str2bool)("None") is None)

        self.assertRaisesRegex(argparse.ArgumentTypeError, "Boolean value expected.", str2optional(str2bool), "Maybe")


class TestGlob(TestWithFiles):
    def test_no_match(self) -> None:
        captured = glob_capture(optional("foo.txt"))
        self.assertEqual(captured.paths, [])
        self.assertEqual(captured.wildcards, [])
        self.assertEqual(glob_paths(optional("foo.txt")), [])
        self.assertEqual(glob_extract(optional("foo.txt")), [])

        self.assertRaisesRegex(
            NonOptionalException, "No files matched .* non-optional .* pattern: foo.txt", glob_capture, "foo.txt"
        )

    def test_glob_fmt(self) -> None:
        write_file("x.txt", "")
        self.assertEqual(glob_fmt("{*foo}.txt", "{foo}.csv"), ["x.csv"])

    def test_expand(self) -> None:
        actual = expand("x.{a}.{b}", "y.{a}.{b}", a="A", b=["B", "b"])
        self.assertEqual(sorted(actual), sorted(["x.A.b", "y.A.b", "x.A.B", "y.A.B"]))
