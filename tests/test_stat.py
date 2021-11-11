"""
Test the stat caching.
"""

import os
import shutil

from dynamake import Stat
from tests import TestWithFiles
from tests import write_file

# pylint: disable=missing-docstring,too-many-public-methods,no-self-use


class TestStat(TestWithFiles):
    def test_missing(self) -> None:
        self.assertFalse(Stat.exists("foo"))
        self.assertFalse(Stat.isfile("foo"))
        self.assertFalse(Stat.isdir("foo"))
        self.assertRaisesRegex(FileNotFoundError, "No such file or directory: 'foo'", Stat.stat, "foo")

    def test_file(self) -> None:
        write_file("foo")
        self.assertTrue(Stat.exists("foo"))
        self.assertTrue(Stat.isfile("foo"))
        self.assertFalse(Stat.isdir("foo"))
        Stat.stat("foo")

    def test_dir(self) -> None:
        os.mkdir("foo")
        self.assertTrue(Stat.exists("foo"))
        self.assertFalse(Stat.isfile("foo"))
        self.assertTrue(Stat.isdir("foo"))
        Stat.stat("foo")

    def test_forget_file(self) -> None:
        self.assertFalse(Stat.exists("foo"))
        write_file("foo")
        self.assertFalse(Stat.exists("foo"))
        Stat.forget("foo")
        self.assertTrue(Stat.exists("foo"))

    def test_forget_dir(self) -> None:
        self.assertFalse(Stat.exists("foo"))
        self.assertFalse(Stat.exists("foo/bar"))
        os.mkdir("foo")
        write_file("foo/bar")
        self.assertFalse(Stat.exists("foo"))
        self.assertFalse(Stat.exists("foo/bar"))
        Stat.forget("foo")
        self.assertTrue(Stat.exists("foo"))
        self.assertTrue(Stat.exists("foo/bar"))
        shutil.rmtree("foo")
        self.assertTrue(Stat.exists("foo"))
        self.assertTrue(Stat.exists("foo/bar"))
        Stat.forget("foo")
        self.assertFalse(Stat.exists("foo"))
        self.assertFalse(Stat.exists("foo/bar"))

    def test_glob(self) -> None:
        os.mkdir("foo")
        self.assertTrue(Stat.exists("foo"))
        self.assertEqual(Stat.glob("foo"), ["foo"])
        os.rmdir("foo")
        self.assertTrue(Stat.exists("foo"))
        self.assertEqual(Stat.glob("foo"), ["foo"])
        Stat.forget("foo")
        self.assertEqual(Stat.glob("foo"), [])
        self.assertFalse(Stat.exists("foo"))
        self.assertEqual(Stat.glob("foo"), [])
