"""
Test the make utilities.
"""

# pylint: disable=too-many-lines

from dynamake.application import Prog
from dynamake.make import env
from dynamake.make import make
from dynamake.make import override
from dynamake.make import Param
from dynamake.make import require
from dynamake.make import reset_make
from dynamake.make import run
from dynamake.make import shell
from dynamake.make import spawn
from dynamake.make import step
from dynamake.make import StepException
from dynamake.make import sync
from dynamake.patterns import phony
from dynamake.stat import Stat
from testfixtures import LogCapture  # type: ignore
from tests import TestWithFiles
from tests import TestWithReset
from tests import write_file
from time import sleep
from typing import Callable
from typing import List
from typing import Optional
from typing import Tuple

import argparse
import logging
import os
import sys

# pylint: disable=missing-docstring,too-many-public-methods,no-self-use
# pylint: disable=blacklisted-name,too-few-public-methods


class TestMake(TestWithReset):

    def test_normal_function(self) -> None:
        def define() -> None:
            @step(output=None)
            def function() -> None:  # pylint: disable=unused-variable
                pass

        self.assertRaisesRegex(RuntimeError,
                               'test_normal_function.<locals>.define.<locals>.function '
                               'is not a coroutine',
                               define)

    def test_call_step(self) -> None:
        called_function = False

        @step(output=None)
        async def function() -> None:
            nonlocal called_function
            called_function = True

        run(function())
        self.assertTrue(called_function)

    def test_call_static_method(self) -> None:
        class Klass:
            called_static_method = False

            @step(output=None)
            @staticmethod
            async def static_method() -> None:
                Klass.called_static_method = True

        run(Klass.static_method())
        self.assertTrue(Klass.called_static_method)

    def test_conflicting_steps(self) -> None:
        @step(output=None)
        async def function() -> None:  # pylint: disable=unused-variable
            pass

        def _register() -> None:
            @step(output=None)
            def function() -> None:  # pylint: disable=unused-variable
                pass

        self.assertRaisesRegex(RuntimeError,
                               'Conflicting .* function: function .* '
                               'both: .*.test_conflicting_steps.<locals>.function '
                               'and: .*._register.<locals>.function',
                               _register)

    def test_use_env_parameters(self) -> None:
        Param(name='foo', default=0, parser=int, description='foo')
        Param(name='bar', default=0, parser=int, description='bar')
        Param(name='baz', default=0, parser=int, description='baz')

        @step(output=None)
        async def use_env(foo: int = env(), bar: int = env(), baz: int = env()) -> None:
            self.assertEqual(foo, 0)
            self.assertEqual(bar, 2)
            self.assertEqual(baz, 3)

        @step(output=None)
        async def set_env(foo: int = env()) -> None:  # pylint: disable=unused-argument
            self.assertEqual(foo, 1)
            with override(baz=3):
                await use_env(bar=2)

        run(set_env(1))

    def test_missing_env_parameters(self) -> None:
        @step(output=None)
        async def use_env(foo: int = env()) -> None:  # pylint: disable=unused-argument
            pass

        @step(output=None)
        async def no_env() -> None:
            use_env()

        self.assertRaisesRegex(RuntimeError,
                               'Missing .* parameter: foo .* '
                               'function: .*.test_missing_env_parameters.<locals>.use_env',
                               run, no_env())


class TestMain(TestWithFiles):

    def test_no_op(self) -> None:
        def _register() -> None:
            @step(output=phony('all'))
            async def no_op() -> None:  # pylint: disable=unused-variable
                pass

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: call with: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] no_op'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] no_op: call'),
            ('dynamake', 'DEBUG', '[.1] no_op: synced'),
            ('dynamake', 'TRACE', '[.1] no_op: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_multiple_produces(self) -> None:
        def _register() -> None:
            @step(output=phony('all'))
            async def foo() -> None:  # pylint: disable=unused-variable
                pass

            @step(output=phony('all'))
            async def bar() -> None:  # pylint: disable=unused-variable
                pass

        self.assertRaisesRegex(StepException,
                               'output: all .* step: foo .* step: bar',
                               self.check, _register)

    def test_generate_many(self) -> None:
        def _register() -> None:
            Param(name='foo', default=1, parser=int, description='foo')

            @step(output=phony('all'))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require('foo.1.1')

            @step(output='foo.{*major}.{*_minor}')
            async def make_foos(major: str, foo: int = env()) -> None:  # pylint: disable=unused-variable
                for index in range(0, foo):
                    await shell('touch foo.{major}.{index}'.format(major=major, index=index))

        sys.argv += ['--foo', '2']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: call with: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo.1.1'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo.1.1 will be produced '
             'by the spawned: [.1.1] make_foos'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'TRACE', "[.1.1] make_foos: call with: major=1"),
            ('dynamake', 'DEBUG', '[.1.1] make_foos: missing output(s): foo.{*major}.{*_minor}'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos: synced'),
            ('dynamake', 'WHY',
             '[.1.1] make_foos: must run processes '
             'to create missing output(s): foo.{*major}.{*_minor}'),
            ('dynamake', 'INFO', '[.1.1] make_foos: run: touch foo.1.0'),
            ('dynamake', 'TRACE', '[.1.1] make_foos: success: touch foo.1.0'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos: synced'),
            ('dynamake', 'WHY',
             '[.1.1] make_foos: must run processes '
             'to create missing output(s): foo.{*major}.{*_minor}'),
            ('dynamake', 'INFO', '[.1.1] make_foos: run: touch foo.1.1'),
            ('dynamake', 'TRACE', '[.1.1] make_foos: success: touch foo.1.1'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos: synced'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos: has output: foo.{*major}.{*_minor} -> foo.1.0 time: 1'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos: has output: foo.{*major}.{*_minor} -> foo.1.1 time: 2'),
            ('dynamake', 'TRACE', '[.1.1] make_foos: done'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo.1.1'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        # Rebuild when all outputs exist.

        sys.argv += ['--log_skipped_actions', 'true']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: call with: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo.1.1'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo.1.1 will be produced '
             'by the spawned: [.1.1] make_foos'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foos: call with: major=1'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos: exists output: foo.{*major}.{*_minor} -> foo.1.0'),
            ('dynamake',
             'DEBUG',
             '[.1.1] make_foos: exists output: foo.{*major}.{*_minor} -> foo.1.1'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos: oldest output: foo.1.0 time: 1'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos: newest input: None time: 1'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos: can skip processes '
             'because all outputs exist and there are no inputs'),
            ('dynamake', 'INFO', '[.1.1] make_foos: skip: touch foo.1.0'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos: newest input: None time: 1'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos: can skip processes '
             'because all outputs exist and there are no inputs'),
            ('dynamake', 'INFO', '[.1.1] make_foos: skip: touch foo.1.1'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos: newest input: None time: 1'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos: has output: foo.{*major}.{*_minor} -> foo.1.0 time: 1'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos: has output: foo.{*major}.{*_minor} -> foo.1.1 time: 2'),
            ('dynamake', 'TRACE', '[.1.1] make_foos: done'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo.1.1'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        # TODO: Rebuild when some outputs are missing.

    def test_copy(self) -> None:
        def _register() -> None:
            @step(output='bar')
            async def copy_foo_to_bar() -> None:  # pylint: disable=unused-variable
                require('foo')
                await spawn('cp', 'foo', 'bar')

        sys.argv += ['bar']

        # Build missing output.

        write_file('foo', '!\n')

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: call with: bar'),
            ('dynamake', 'DEBUG', '[.] make: build the required: bar'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: bar will be produced by the spawned: [.1] copy_foo_to_bar'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] copy_foo_to_bar: call'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: missing output(s): bar'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: build the required: foo'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: the required: foo is a source file'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: synced'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: has the required: foo'),
            ('dynamake', 'WHY',
             '[.1] copy_foo_to_bar: must run processes to create missing output(s): bar'),
            ('dynamake', 'INFO', '[.1] copy_foo_to_bar: run: cp foo bar'),
            ('dynamake', 'TRACE', '[.1] copy_foo_to_bar: success: cp foo bar'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: synced'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: has output: bar time: 1'),
            ('dynamake', 'TRACE', '[.1] copy_foo_to_bar: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: bar'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        self.expect_file('bar', '!\n')

        # Skip existing up-to-date output.

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: call with: bar'),
            ('dynamake', 'DEBUG', '[.] make: build the required: bar'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: bar will be produced by the spawned: [.1] copy_foo_to_bar'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] copy_foo_to_bar: call'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: exists output: bar'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: oldest output: bar time: 1'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: build the required: foo'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: the required: foo is a source file'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: synced'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: newest input: foo time: 0'),
            ('dynamake', 'DEBUG',
             '[.1] copy_foo_to_bar: skip processes '
             'because all outputs exist and are newer than all inputs'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: skip: cp foo bar'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: synced'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: newest input: foo time: 0'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: has output: bar time: 1'),
            ('dynamake', 'TRACE', '[.1] copy_foo_to_bar: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: bar'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        self.expect_file('bar', '!\n')

        write_file('foo', '?\n')

        # Build out-of-date output.

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: call with: bar'),
            ('dynamake', 'DEBUG', '[.] make: build the required: bar'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: bar will be produced by the spawned: [.1] copy_foo_to_bar'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] copy_foo_to_bar: call'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: exists output: bar'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: oldest output: bar time: 1'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: build the required: foo'),
            ('dynamake', 'DEBUG',
             '[.1] copy_foo_to_bar: the required: foo is a source file'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: synced'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: newest input: foo time: 2'),
            ('dynamake', 'WHY',
             '[.1] copy_foo_to_bar: must run processes '
             'because the output: bar is not newer than the input: foo'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: remove stale output: bar'),
            ('dynamake', 'INFO', '[.1] copy_foo_to_bar: run: cp foo bar'),
            ('dynamake', 'TRACE', '[.1] copy_foo_to_bar: success: cp foo bar'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: synced'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: newest input: foo time: 2'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: has output: bar time: 3'),
            ('dynamake', 'TRACE', '[.1] copy_foo_to_bar: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: bar'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        self.expect_file('bar', '?\n')

    def test_require_active(self) -> None:
        did_bar = False

        def _register() -> None:
            nonlocal did_bar
            did_bar = False

            @step(output=phony('all'))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require('foo')
                require('bar')

            @step(output='foo')
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell('sleep 0.2 ; touch foo')

            @step(output=phony('bar'))
            async def make_bar() -> None:  # pylint: disable=unused-variable
                nonlocal did_bar
                assert not did_bar
                did_bar = True
                require('baz')
                require('foo')

            @step(output='baz')
            async def make_baz() -> None:  # pylint: disable=unused-variable
                require('foo')
                await shell('sleep 0.1 ; touch baz')

        self.check(_register)

        assert did_bar

    def test_missing_output(self) -> None:
        def _register() -> None:
            @step(output='all')
            async def no_op() -> None:  # pylint: disable=unused-variable
                pass

        self.check(_register, error='no_op: missing output.s.', log=[
            ('dynamake', 'TRACE', '[.] make: call with: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] no_op'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] no_op: call'),
            ('dynamake', 'DEBUG', '[.1] no_op: missing output(s): all'),
            ('dynamake', 'DEBUG', '[.1] no_op: synced'),
            ('dynamake', 'ERROR', '[.1] no_op: missing output(s): all'),
            ('dynamake', 'TRACE', '[.1] no_op: fail'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

    def test_delete_empty_directories(self) -> None:
        def _register() -> None:
            @step(output=['foo/bar', 'foo/baz'])
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell('mkdir -p foo')
                await shell('touch foo/bar')

        os.makedirs('foo')
        write_file('foo/baz', 'z')

        sys.argv += ['--delete_empty_directories', 'true', 'foo/bar']

        self.check(_register, error='make_foo: missing output.s.', log=[
            ('dynamake', 'TRACE', '[.] make: call with: foo/bar'),
            ('dynamake', 'DEBUG', '[.] make: build the required: foo/bar'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: foo/bar will be produced by the spawned: [.1] make_foo'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_foo: call'),
            ('dynamake', 'DEBUG', '[.1] make_foo: missing output(s): foo/bar'),
            ('dynamake', 'DEBUG', '[.1] make_foo: exists output: foo/baz'),
            ('dynamake', 'DEBUG', '[.1] make_foo: synced'),
            ('dynamake', 'WHY',
             '[.1] make_foo: must run processes to create missing output(s): foo/bar'),
            ('dynamake', 'DEBUG', '[.1] make_foo: remove stale output: foo/baz'),
            ('dynamake', 'DEBUG', '[.1] make_foo: remove empty directory: foo'),
            ('dynamake', 'INFO', '[.1] make_foo: run: mkdir -p foo'),
            ('dynamake', 'TRACE', '[.1] make_foo: success: mkdir -p foo'),
            ('dynamake', 'DEBUG', '[.1] make_foo: synced'),
            ('dynamake', 'WHY',
             '[.1] make_foo: must run processes to create missing output(s): foo/bar'),
            ('dynamake', 'INFO', '[.1] make_foo: run: touch foo/bar'),
            ('dynamake', 'TRACE', '[.1] make_foo: success: touch foo/bar'),
            ('dynamake', 'DEBUG', '[.1] make_foo: synced'),
            ('dynamake', 'DEBUG', '[.1] make_foo: has output: foo/bar time: 1'),
            ('dynamake', 'ERROR', '[.1] make_foo: missing output(s): foo/baz'),
            ('dynamake', 'DEBUG', '[.1] make_foo: remove failed output: foo/bar'),
            ('dynamake', 'DEBUG', '[.1] make_foo: remove empty directory: foo'),
            ('dynamake', 'TRACE', '[.1] make_foo: fail'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

    def test_delete_stale_outputs(self) -> None:
        def _register() -> None:
            @step(output=['foo', phony('all')])
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell('echo @ > foo')

        write_file('foo', '!\n')

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: call with: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_foo'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_foo: call'),
            ('dynamake', 'DEBUG', '[.1] make_foo: exists output: foo'),
            ('dynamake', 'DEBUG', '[.1] make_foo: synced'),
            ('dynamake', 'WHY',
             '[.1] make_foo: must run processes to ensure phony output: all'),
            ('dynamake', 'DEBUG', '[.1] make_foo: remove stale output: foo'),
            ('dynamake', 'INFO', '[.1] make_foo: run: echo @ > foo'),
            ('dynamake', 'TRACE', '[.1] make_foo: success: echo @ > foo'),
            ('dynamake', 'DEBUG', '[.1] make_foo: synced'),
            ('dynamake', 'DEBUG', '[.1] make_foo: has output: foo time: 1'),
            ('dynamake', 'TRACE', '[.1] make_foo: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        sys.argv += ['--delete_stale_outputs', 'false']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: call with: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_foo'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_foo: call'),
            ('dynamake', 'DEBUG', '[.1] make_foo: exists output: foo'),
            ('dynamake', 'DEBUG', '[.1] make_foo: synced'),
            ('dynamake', 'WHY',
             '[.1] make_foo: must run processes to ensure phony output: all'),
            ('dynamake', 'INFO', '[.1] make_foo: run: echo @ > foo'),
            ('dynamake', 'TRACE', '[.1] make_foo: success: echo @ > foo'),
            ('dynamake', 'DEBUG', '[.1] make_foo: synced'),
            ('dynamake', 'DEBUG', '[.1] make_foo: has output: foo time: 2'),
            ('dynamake', 'TRACE', '[.1] make_foo: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_phony_depenencies(self) -> None:
        def _register() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                require('foo')
                await shell('touch all')

            @step(output=phony('foo'))
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell('true')

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: call with: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo will be produced by the spawned: [.1.1] make_foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: call'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'WHY', '[.1.1] make_foo: must run processes to ensure phony output: foo'),
            ('dynamake', 'INFO', '[.1.1] make_foo: run: true'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: success: true'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: done'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run processes because rebuilt the required phony: foo'),
            ('dynamake', 'INFO', '[.1] make_all: run: touch all'),
            ('dynamake', 'TRACE', '[.1] make_all: success: touch all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: has output: all time: 1'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_failed_sub_process(self) -> None:
        def _register() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell('false')

        self.check(_register, error='make_all: failure: false', log=[
            ('dynamake', 'TRACE', '[.] make: call with: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run processes to create missing output(s): all'),
            ('dynamake', 'INFO', '[.1] make_all: run: false'),
            ('dynamake', 'ERROR', '[.1] make_all: failure: false'),
            ('dynamake', 'TRACE', '[.1] make_all: fail'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

    def test_continue_on_failure(self) -> None:
        def _register() -> None:
            @step(output=phony('all'))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require('foo')
                await sync()
                require('bar')
                await shell('true')

            @step(output='foo')
            async def make_foo() -> None:  # pylint: disable=unused-variable
                require('baz')
                await shell('touch foo')

            @step(output='bar')
            async def make_bar() -> None:  # pylint: disable=unused-variable
                require('baz')
                await shell('touch bar')

            @step(output='baz')
            async def make_baz() -> None:  # pylint: disable=unused-variable
                await shell('touch baz')
                await shell('false')

        sys.argv += ['--failure_aborts_build', 'false']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: call with: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo will be produced by the spawned: [.1.1] make_foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: call'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: missing output(s): foo'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: build the required: baz'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foo: the required: baz will be produced '
             'by the spawned: [.1.1.1] make_baz'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: sync'),
            ('dynamake', 'TRACE', '[.1.1.1] make_baz: call'),
            ('dynamake', 'DEBUG', '[.1.1.1] make_baz: missing output(s): baz'),
            ('dynamake', 'DEBUG', '[.1.1.1] make_baz: synced'),
            ('dynamake', 'WHY',
             '[.1.1.1] make_baz: must run processes to create missing output(s): baz'),
            ('dynamake', 'INFO', '[.1.1.1] make_baz: run: touch baz'),
            ('dynamake', 'TRACE', '[.1.1.1] make_baz: success: touch baz'),
            ('dynamake', 'DEBUG', '[.1.1.1] make_baz: synced'),
            ('dynamake', 'WHY',
             '[.1.1.1] make_baz: must run processes to create missing output(s): baz'),
            ('dynamake', 'INFO', '[.1.1.1] make_baz: run: false'),
            ('dynamake', 'ERROR', '[.1.1.1] make_baz: failure: false'),
            ('dynamake', 'DEBUG', '[.1.1.1] make_baz: synced'),
            ('dynamake', 'DEBUG', '[.1.1.1] make_baz: has output: baz time: 1'),
            ('dynamake', 'DEBUG', '[.1.1.1] make_baz: remove failed output: baz'),
            ('dynamake', 'TRACE', '[.1.1.1] make_baz: fail'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: the required: baz has failed to build'),
            ('dynamake', 'DEBUG', "[.1.1] make_foo: can't run: touch foo"),
            ('dynamake', 'TRACE', '[.1.1] make_foo: fail'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: the required: foo has failed to build'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: bar'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: bar will be produced '
             'by the spawned: [.1.2] make_bar'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'TRACE', '[.1.2] make_bar: call'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: missing output(s): bar'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: build the required: baz'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: synced'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: the required: baz has failed to build'),
            ('dynamake', 'DEBUG', "[.1.2] make_bar: can't run: touch bar"),
            ('dynamake', 'TRACE', '[.1.2] make_bar: fail'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: the required: bar has failed to build'),
            ('dynamake', 'DEBUG', '[.1] make_all: the required: foo has failed to build'),
            ('dynamake', 'DEBUG', "[.1] make_all: can't run: true"),
            ('dynamake', 'TRACE', '[.1] make_all: fail'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: the required: all has failed to build'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_delete_failed_outputs(self) -> None:
        def _register() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                require('foo')
                await shell('echo @ > all ; false')

        write_file('foo', '!\n')

        self.check(_register, error='make_all: failure: echo @ > all ; false', log=[
            ('dynamake', 'TRACE', '[.] make: call with: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: the required: foo is a source file'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run processes to create missing output(s): all'),
            ('dynamake', 'INFO', '[.1] make_all: run: echo @ > all ; false'),
            ('dynamake', 'ERROR', '[.1] make_all: failure: echo @ > all ; false'),
            ('dynamake', 'DEBUG', '[.1] make_all: remove failed output: all'),
            ('dynamake', 'TRACE', '[.1] make_all: fail'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

        sys.argv += ['--delete_failed_outputs', 'false']

        write_file('all', '?\n')
        sleep(0.1)
        write_file('foo', '!\n')

        self.check(_register, error='make_all: failure: echo @ > all ; false', log=[
            ('dynamake', 'TRACE', '[.] make: call with: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: exists output: all'),
            ('dynamake', 'DEBUG', '[.1] make_all: oldest output: all time: 1'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: the required: foo is a source file'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: newest input: foo time: 2'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run processes '
             'because the output: all is not newer than the input: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: remove stale output: all'),
            ('dynamake', 'INFO', '[.1] make_all: run: echo @ > all ; false'),
            ('dynamake', 'ERROR', '[.1] make_all: failure: echo @ > all ; false'),
            ('dynamake', 'TRACE', '[.1] make_all: fail'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

        self.expect_file('all', '@\n')

    def test_touch_success_outputs(self) -> None:
        def _register() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell('touch all')

        sys.argv += ['--touch_success_outputs', 'true']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: call with: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run processes to create missing output(s): all'),
            ('dynamake', 'INFO', '[.1] make_all: run: touch all'),
            ('dynamake', 'TRACE', '[.1] make_all: success: touch all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: touch output: all'),
            ('dynamake', 'DEBUG', '[.1] make_all: has output: all time: 1'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_built_dependencies(self) -> None:
        def _register() -> None:
            @step(output=phony('all'))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require('foo')
                await sync()
                require('bar')

            @step(output='foo')
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell('touch foo')

            @step(output='bar')
            async def make_bar() -> None:  # pylint: disable=unused-variable
                require('foo')
                await shell('touch bar')

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: call with: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo will be produced by the spawned: [.1.1] '
             'make_foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: call'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: missing output(s): foo'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'WHY',
             '[.1.1] make_foo: must run processes to create missing output(s): foo'),
            ('dynamake', 'INFO', '[.1.1] make_foo: run: touch foo'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: success: touch foo'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: has output: foo time: 1'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: done'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: bar'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: bar will be produced by the spawned: [.1.2] '
             'make_bar'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'TRACE', '[.1.2] make_bar: call'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: missing output(s): bar'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: build the required: foo'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: the required: foo was built'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: synced'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: has the required: foo'),
            ('dynamake', 'WHY',
             '[.1.2] make_bar: must run processes to create missing output(s): bar'),
            ('dynamake', 'INFO', '[.1.2] make_bar: run: touch bar'),
            ('dynamake', 'TRACE', '[.1.2] make_bar: success: touch bar'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: synced'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: has output: bar time: 2'),
            ('dynamake', 'TRACE', '[.1.2] make_bar: done'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: bar'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_missing_input(self) -> None:
        def _register() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                require('foo')

        self.check(_register, error="don't know how to make the required: foo", log=[
            ('dynamake', 'TRACE', '[.] make: call with: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'ERROR', "[.1] make_all: don't know how to make the required: foo"),
            ('dynamake', 'TRACE', '[.1] make_all: fail'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

        sys.argv += ['foo']

        self.check(_register, error="don't know how to make the required: foo", log=[
            ('dynamake', 'TRACE', '[.] make: call with: foo'),
            ('dynamake', 'DEBUG', '[.] make: build the required: foo'),
            ('dynamake', 'ERROR', "[.] make: don't know how to make the required: foo"),
        ])

    def test_slow_outputs(self) -> None:
        def _register() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell('(sleep 1 ; touch all) &')

        sys.argv += ['--wait_nfs_outputs', 'true']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: call with: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run processes to create missing output(s): all'),
            ('dynamake', 'INFO', '[.1] make_all: run: (sleep 1 ; touch all) &'),
            ('dynamake', 'TRACE', '[.1] make_all: success: (sleep 1 ; touch all) &'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'WARNING', '[.1] make_all: waited: 1.5 seconds for the output: all'),
            ('dynamake', 'DEBUG', '[.1] make_all: has output: all time: 1'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def check(self, register: Callable, *, error: Optional[str] = None,
              log: Optional[List[Tuple[str, str, str]]] = None) -> None:
        reset_make()
        Stat.reset()
        Prog.is_test = True
        logging.getLogger('asyncio').setLevel('WARN')
        register()

        sys.argv += ['--log_level', 'DEBUG']

        with LogCapture() as captured_log:
            if error is None:
                make(argparse.ArgumentParser())
            else:
                self.assertRaisesRegex(StepException, error, make, argparse.ArgumentParser())

        if log is not None:
            captured_log.check(*log)
