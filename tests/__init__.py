"""
Common utilities for tests.
"""

from dynamake.application import Prog
from dynamake.application import reset_application
from dynamake.make import reset_make
from dynamake.make import reset_test_dates
from testfixtures import StringComparison  # type: ignore
from textwrap import dedent
from unittest import TestCase

import asyncio
import logging
import os
import shutil
import sys
import tempfile

# pylint: disable=missing-docstring


StringComparison.strip = lambda self: self


def undent(content: str) -> str:
    content = dedent(content)
    if content and content[0] == '\n':
        content = content[1:]
    return content


def write_file(path: str, content: str = '') -> None:
    with open(path, 'w') as file:
        file.write(undent(content))


def _exit(status: int) -> None:
    raise RuntimeError('System exit status: %s' % status)


class TestWithReset(TestCase):

    def setUp(self) -> None:
        reset_test_dates()
        reset_application()
        reset_make()
        Prog.is_test = True
        Prog.logger.setLevel('DEBUG')
        logging.getLogger('asyncio').setLevel('WARN')


class TestWithFiles(TestWithReset):

    def setUp(self) -> None:
        super().setUp()
        sys.argv = ['dynamake']
        self.maxDiff = None  # pylint: disable=invalid-name
        if sys.path[0] != os.getcwd():
            sys.path.insert(0, os.getcwd())
        self.previous_directory = os.getcwd()
        self.temporary_directory = tempfile.mkdtemp()
        os.chdir(os.path.expanduser(self.temporary_directory))
        sys.path.insert(0, os.getcwd())
        self.exit = sys.exit
        sys.exit = _exit  # type: ignore

    def tearDown(self) -> None:
        pending = asyncio.Task.all_tasks()
        asyncio.get_event_loop().run_until_complete(asyncio.gather(*pending))
        os.chdir(self.previous_directory)
        shutil.rmtree(self.temporary_directory)
        sys.exit = self.exit

    def expect_file(self, path: str, expected: str) -> None:
        with open(path, 'r') as file:
            actual = file.read()
            self.assertEqual(actual, undent(expected))
