"""
Test the make utilities.
"""

# pylint: disable=too-many-lines

from dynamake.application import Prog
from dynamake.make import config_file
from dynamake.make import config_param
from dynamake.make import done
from dynamake.make import env
from dynamake.make import make
from dynamake.make import override
from dynamake.make import Param
from dynamake.make import require
from dynamake.make import reset_make
from dynamake.make import resource_parameters
from dynamake.make import run
from dynamake.make import shell
from dynamake.make import spawn
from dynamake.make import step
from dynamake.make import StepException
from dynamake.make import sync
from dynamake.make import with_config
from dynamake.patterns import optional
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
import asyncio
import logging
import os
import sys

# pylint: disable=missing-docstring,too-many-public-methods,no-self-use
# pylint: disable=blacklisted-name,too-few-public-methods


class TestMake(TestWithReset):

    def test_normal_function(self) -> None:
        def define() -> None:
            @step(output='all')
            def function() -> None:  # pylint: disable=unused-variable
                pass

        self.assertRaisesRegex(RuntimeError,
                               'test_normal_function.<locals>.define.<locals>.function '
                               'is not a coroutine',
                               define)

    def test_no_output(self) -> None:
        def define() -> None:
            @step(output=None)
            async def function() -> None:  # pylint: disable=unused-variable
                pass

        self.assertRaisesRegex(RuntimeError,
                               'test_no_output.<locals>.define.<locals>.function '
                               'specifies no output',
                               define)

    def test_call_step(self) -> None:
        called_function = False

        @step(output='none')
        async def function() -> None:
            nonlocal called_function
            called_function = True

        run(function())
        self.assertTrue(called_function)

    def test_call_static_method(self) -> None:
        class Klass:
            called_static_method = False

            @step(output='none')
            @staticmethod
            async def static_method() -> None:
                Klass.called_static_method = True

        run(Klass.static_method())
        self.assertTrue(Klass.called_static_method)

    def test_conflicting_steps(self) -> None:
        @step(output='none')
        async def function() -> None:  # pylint: disable=unused-variable
            pass

        def _register() -> None:
            @step(output='none')
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

        @step(output='use')
        async def use_env(foo: int = env(), bar: int = env(), baz: int = env()) -> None:
            self.assertEqual(foo, 0)
            self.assertEqual(bar, 2)
            self.assertEqual(baz, 3)

        @step(output='set')
        async def set_env(foo: int = env()) -> None:  # pylint: disable=unused-argument
            self.assertEqual(foo, 1)
            with override(baz=3):
                await use_env(bar=2)

        run(set_env(1))

    def test_missing_env_parameters(self) -> None:
        @step(output='use')
        async def use_env(foo: int = env()) -> None:  # pylint: disable=unused-argument
            pass

        @step(output='none')
        async def no_env() -> None:
            use_env()

        self.assertRaisesRegex(RuntimeError,
                               'Missing .* parameter: foo .* '
                               'function: .*.test_missing_env_parameters.<locals>.use_env',
                               run, no_env())

    def test_bad_resources(self) -> None:
        self.assertRaisesRegex(RuntimeError,
                               'Unknown parameter: foo',
                               resource_parameters, foo=1)

        self.assertRaisesRegex(RuntimeError,
                               '.* amount: 1000000 .* resource: jobs .* greater .* amount:',
                               resource_parameters, jobs=1000000)


class TestMain(TestWithFiles):

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
                self.assertRaisesRegex(BaseException, error, make, argparse.ArgumentParser())

        if log is not None:
            captured_log.check(*log)

    def test_no_op(self) -> None:
        def _register() -> None:
            @step(output=phony('all'))
            async def no_op() -> None:  # pylint: disable=unused-variable
                pass

        sys.argv += ['--jobs', '0']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] no_op'),
            ('dynamake', 'TRACE', '[.1] no_op: call'),
            ('dynamake', 'WHY',
             '[.1] no_op: must run actions because missing the persistent actions: '
             '.dynamake/no_op.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] no_op: synced'),
            ('dynamake', 'DEBUG',
             '[.1] no_op: write the persistent actions: .dynamake/no_op.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] no_op: done'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_multiple_producers(self) -> None:
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

        sys.argv += ['--jobs', '0']
        sys.argv += ['--foo', '2']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions because missing the persistent actions: '
             '.dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo.1.1'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo.1.1 will be produced by '
             'the spawned: [.1.1] make_foos/major=1'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foos/major=1: call'),
            ('dynamake', 'WHY',
             '[.1.1] make_foos/major=1: must run actions '
             'because missing the persistent actions: .dynamake/make_foos/major=1.actions.yaml'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: missing the output(s): foo.{*major}.{*_minor}'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: synced'),
            ('dynamake', 'INFO', '[.1.1] make_foos/major=1: run: touch foo.1.0'),
            ('dynamake', 'TRACE', '[.1.1] make_foos/major=1: success: touch foo.1.0'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: synced'),
            ('dynamake', 'INFO', '[.1.1] make_foos/major=1: run: touch foo.1.1'),
            ('dynamake', 'TRACE', '[.1.1] make_foos/major=1: success: touch foo.1.1'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: synced'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: has '
             'the output: foo.{*major}.{*_minor} -> foo.1.0 time: 1'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: has '
             'the output: foo.{*major}.{*_minor} -> foo.1.1 time: 2'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: write '
             'the persistent actions: .dynamake/make_foos/major=1.actions.yaml'),
            ('dynamake', 'TRACE', '[.1.1] make_foos/major=1: done'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo.1.1'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: write the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        # Do not rebuild without reason.

        sys.argv += ['--log_skipped_actions', 'true']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: read the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo.1.1'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo.1.1 will be produced by '
             'the spawned: [.1.1] make_foos/major=1'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foos/major=1: call'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: read '
             'the persistent actions: .dynamake/make_foos/major=1.actions.yaml'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: exists output: foo.{*major}.{*_minor} -> foo.1.0'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: exists output: foo.{*major}.{*_minor} -> foo.1.1'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: oldest output: foo.1.0 time: 1'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: newest input: None time: 1'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: can skip actions '
             'because all the outputs exist and there are no newer inputs'),
            ('dynamake', 'INFO', '[.1.1] make_foos/major=1: skip: touch foo.1.0'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: newest input: None time: 1'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: can skip actions '
             'because all the outputs exist and there are no newer inputs'),
            ('dynamake', 'INFO', '[.1.1] make_foos/major=1: skip: touch foo.1.1'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: newest input: None time: 1'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: has '
             'the output: foo.{*major}.{*_minor} -> foo.1.0 time: 1'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: has '
             'the output: foo.{*major}.{*_minor} -> foo.1.1 time: 2'),
            ('dynamake', 'TRACE', '[.1.1] make_foos/major=1: done'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo.1.1'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: write the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        # Rebuild when some outputs are missing.

        os.remove('foo.1.0')

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: read the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo.1.1'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo.1.1 will be produced by '
             'the spawned: [.1.1] make_foos/major=1'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foos/major=1: call'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: read '
             'the persistent actions: .dynamake/make_foos/major=1.actions.yaml'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: exists output: foo.{*major}.{*_minor} -> foo.1.1'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: missing the old built output: foo.1.0'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: synced'),
            ('dynamake', 'WHY',
             '[.1.1] make_foos/major=1: must run actions to create '
             'the missing output(s): foo.1.0'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: remove the stale output: foo.1.1'),
            ('dynamake', 'INFO', '[.1.1] make_foos/major=1: run: touch foo.1.0'),
            ('dynamake', 'TRACE', '[.1.1] make_foos/major=1: success: touch foo.1.0'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: synced'),
            ('dynamake', 'INFO', '[.1.1] make_foos/major=1: run: touch foo.1.1'),
            ('dynamake', 'TRACE', '[.1.1] make_foos/major=1: success: touch foo.1.1'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: synced'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: has '
             'the output: foo.{*major}.{*_minor} -> foo.1.0 time: 3'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: has '
             'the output: foo.{*major}.{*_minor} -> foo.1.1 time: 4'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: write '
             'the persistent actions: .dynamake/make_foos/major=1.actions.yaml'),
            ('dynamake', 'TRACE', '[.1.1] make_foos/major=1: done'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo.1.1'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: write the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        os.remove('foo.1.0')

        # But do not rebuild if not using persistent state.

        sys.argv += ['--rebuild_changed_actions', 'false']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo.1.1'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo.1.1 will be produced by '
             'the spawned: [.1.1] make_foos/major=1'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foos/major=1: call'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: exists output: foo.{*major}.{*_minor} -> foo.1.1'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: oldest output: foo.1.1 time: 4'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: newest input: None time: 1'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: can skip actions '
             'because all the outputs exist and there are no newer inputs'),
            ('dynamake', 'INFO', '[.1.1] make_foos/major=1: skip: touch foo.1.0'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: newest input: None time: 1'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: can skip actions '
             'because all the outputs exist and there are no newer inputs'),
            ('dynamake', 'INFO', '[.1.1] make_foos/major=1: skip: touch foo.1.1'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: newest input: None time: 1'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: has '
             'the output: foo.{*major}.{*_minor} -> foo.1.1 time: 4'),
            ('dynamake', 'TRACE', '[.1.1] make_foos/major=1: done'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo.1.1'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        # This can cause a build to fail:

        os.remove('foo.1.1')
        write_file('foo.1.0', '!\n')

        self.check(_register, error='make_all: failed to build the required target.s.', log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo.1.1'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo.1.1 will be produced by '
             'the spawned: [.1.1] make_foos/major=1'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foos/major=1: call'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: exists output: foo.{*major}.{*_minor} -> foo.1.0'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: oldest output: foo.1.0 time: 5'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: newest input: None time: 1'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: can skip actions '
             'because all the outputs exist and there are no newer inputs'),
            ('dynamake', 'INFO', '[.1.1] make_foos/major=1: skip: touch foo.1.0'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: newest input: None time: 1'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: can skip actions '
             'because all the outputs exist and there are no newer inputs'),
            ('dynamake', 'INFO', '[.1.1] make_foos/major=1: skip: touch foo.1.1'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foos/major=1: newest input: None time: 1'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foos/major=1: has the output: foo.{*major}.{*_minor} -> foo.1.0 time: 5'),
            ('dynamake', 'TRACE', '[.1.1] make_foos/major=1: done'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'ERROR',
             '[.1] make_all: the required: foo.1.1 has failed to build'),
            ('dynamake', 'DEBUG', '[.1] make_all: remove '
             'the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: fail'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

    def test_copy(self) -> None:
        def _register() -> None:
            @step(output='bar')
            async def copy_foo_to_bar() -> None:  # pylint: disable=unused-variable
                require('foo')
                await spawn('cp', 'foo', 'bar')

        sys.argv += ['--jobs', '0']
        sys.argv += ['--rebuild_changed_actions', 'false', 'bar']

        write_file('foo', '!\n')

        # Build due to missing output.

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: bar'),
            ('dynamake', 'DEBUG', '[.] make: build the required: bar'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: bar will be produced by '
             'the spawned: [.1] copy_foo_to_bar'),
            ('dynamake', 'TRACE', '[.1] copy_foo_to_bar: call'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: missing the output(s): bar'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: build the required: foo'),
            ('dynamake', 'DEBUG',
             '[.1] copy_foo_to_bar: the required: foo is a source file'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: synced'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: has the required: foo'),
            ('dynamake', 'WHY',
             '[.1] copy_foo_to_bar: must run actions to create the missing output(s): bar'),
            ('dynamake', 'INFO', '[.1] copy_foo_to_bar: run: cp foo bar'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] copy_foo_to_bar: success: cp foo bar'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: synced'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: has the output: bar time: 1'),
            ('dynamake', 'TRACE', '[.1] copy_foo_to_bar: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: bar'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        self.expect_file('bar', '!\n')

        # Skip existing up-to-date output.

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: bar'),
            ('dynamake', 'DEBUG', '[.] make: build the required: bar'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: bar will be produced by the spawned: [.1] copy_foo_to_bar'),
            ('dynamake', 'TRACE', '[.1] copy_foo_to_bar: call'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: exists output: bar'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: oldest output: bar time: 1'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: build the required: foo'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: the required: foo is a source file'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: synced'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: newest input: foo time: 0'),
            ('dynamake', 'DEBUG',
             '[.1] copy_foo_to_bar: can skip actions '
             'because all the outputs exist and are newer than all the inputs'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: skip: cp foo bar'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: synced'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: newest input: foo time: 0'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: has the output: bar time: 1'),
            ('dynamake', 'TRACE', '[.1] copy_foo_to_bar: done'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: bar'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        self.expect_file('bar', '!\n')

        write_file('foo', '?\n')

        # Rebuild out-of-date output.

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: bar'),
            ('dynamake', 'DEBUG', '[.] make: build the required: bar'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: bar will be produced by '
             'the spawned: [.1] copy_foo_to_bar'),
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
             '[.1] copy_foo_to_bar: must run actions '
             'because the output: bar is not newer than the input: foo'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: remove the stale output: bar'),
            ('dynamake', 'INFO', '[.1] copy_foo_to_bar: run: cp foo bar'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] copy_foo_to_bar: success: cp foo bar'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: synced'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] copy_foo_to_bar: has the output: bar time: 3'),
            ('dynamake', 'TRACE', '[.1] copy_foo_to_bar: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: bar'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        self.expect_file('bar', '?\n')

    def test_require_active(self) -> None:
        sys.argv += ['--jobs', '0']
        sys.argv += ['--rebuild_changed_actions', 'false']

        def _register() -> None:
            @step(output=phony('all'))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require('foo')
                await done(asyncio.sleep(0.1))
                require('bar')

            @step(output='foo')
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell('sleep 0.2 ; touch foo')

            @step(output=phony('bar'))
            async def make_bar() -> None:  # pylint: disable=unused-variable
                require('foo')
                await shell('touch bar')

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo will be produced by the spawned: [.1.1] make_foo'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: call'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: missing the output(s): foo'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'WHY',
             '[.1.1] make_foo: must run actions to create the missing output(s): foo'),
            ('dynamake', 'INFO', '[.1.1] make_foo: run: sleep 0.2 ; touch foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: bar'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: bar will be produced by '
             'the spawned: [.1.2] make_bar'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'TRACE', '[.1.2] make_bar: call'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: build the required: foo'),
            ('dynamake', 'DEBUG',
             '[.1.2] make_bar: the required: foo will be produced by '
             'the spawned: [.1.2.1] make_foo'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: sync'),
            ('dynamake', 'DEBUG',
             '[.1.2.1] make_foo: paused by waiting for: [.1.1] make_foo'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: success: sleep 0.2 ; touch foo'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: has the output: foo time: 1'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: done'),
            ('dynamake', 'DEBUG',
             '[.1.2.1] make_foo: resumed by completion of: [.1.1] make_foo'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: synced'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: has the required: foo'),
            ('dynamake', 'WHY',
             '[.1.2] make_bar: must run actions to satisfy the phony output: bar'),
            ('dynamake', 'INFO', '[.1.2] make_bar: run: touch bar'),
            ('dynamake', 'TRACE', '[.1.2] make_bar: success: touch bar'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: synced'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: has the required: foo'),
            ('dynamake', 'TRACE', '[.1.2] make_bar: done'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: bar'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_missing_output(self) -> None:
        def _register() -> None:
            @step(output='all')
            async def no_op() -> None:  # pylint: disable=unused-variable
                pass

        sys.argv += ['--jobs', '0']
        sys.argv += ['--rebuild_changed_actions', 'false']

        self.check(_register, error='no_op: missing some output.s.', log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] no_op'),
            ('dynamake', 'TRACE', '[.1] no_op: call'),
            ('dynamake', 'DEBUG', '[.1] no_op: missing the output(s): all'),
            ('dynamake', 'DEBUG', '[.1] no_op: synced'),
            ('dynamake', 'ERROR', '[.1] no_op: missing the output(s): all'),
            ('dynamake', 'TRACE', '[.1] no_op: fail'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

    def test_remove_empty_directories(self) -> None:
        def _register() -> None:
            @step(output=['foo/bar', 'foo/baz'])
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell('mkdir -p foo')
                await shell('touch foo/bar')

        os.makedirs('foo')
        write_file('foo/baz', 'z')

        sys.argv += ['--jobs', '0']
        sys.argv += ['--rebuild_changed_actions', 'false']
        sys.argv += ['--remove_empty_directories', 'true', 'foo/bar']

        self.check(_register, error='make_foo: missing some output.s.', log=[
            ('dynamake', 'TRACE', '[.] make: foo/bar'),
            ('dynamake', 'DEBUG', '[.] make: build the required: foo/bar'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: foo/bar will be produced by '
             'the spawned: [.1] make_foo'),
            ('dynamake', 'TRACE', '[.1] make_foo: call'),
            ('dynamake', 'DEBUG', '[.1] make_foo: missing the output(s): foo/bar'),
            ('dynamake', 'DEBUG', '[.1] make_foo: exists output: foo/baz'),
            ('dynamake', 'DEBUG', '[.1] make_foo: synced'),
            ('dynamake', 'WHY',
             '[.1] make_foo: must run actions to create the missing output(s): foo/bar'),
            ('dynamake', 'DEBUG', '[.1] make_foo: remove the stale output: foo/baz'),
            ('dynamake', 'DEBUG', '[.1] make_foo: remove the empty directory: foo'),
            ('dynamake', 'INFO', '[.1] make_foo: run: mkdir -p foo'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_foo: success: mkdir -p foo'),
            ('dynamake', 'DEBUG', '[.1] make_foo: synced'),
            ('dynamake', 'INFO', '[.1] make_foo: run: touch foo/bar'),
            ('dynamake', 'TRACE', '[.1] make_foo: success: touch foo/bar'),
            ('dynamake', 'DEBUG', '[.1] make_foo: synced'),
            ('dynamake', 'DEBUG', '[.1] make_foo: has the output: foo/bar time: 1'),
            ('dynamake', 'ERROR', '[.1] make_foo: missing the output(s): foo/baz'),
            ('dynamake', 'DEBUG', '[.1] make_foo: remove the failed output: foo/bar'),
            ('dynamake', 'DEBUG', '[.1] make_foo: remove the empty directory: foo'),
            ('dynamake', 'TRACE', '[.1] make_foo: fail'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

    def test_remove_stale_outputs(self) -> None:
        def _register() -> None:
            @step(output=['foo', phony('all')])
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell('echo @ > foo')

        write_file('foo', '!\n')

        sys.argv += ['--jobs', '0']
        sys.argv += ['--rebuild_changed_actions', 'false']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_foo'),
            ('dynamake', 'TRACE', '[.1] make_foo: call'),
            ('dynamake', 'DEBUG', '[.1] make_foo: exists output: foo'),
            ('dynamake', 'DEBUG', '[.1] make_foo: synced'),
            ('dynamake', 'WHY',
             '[.1] make_foo: must run actions to satisfy the phony output: all'),
            ('dynamake', 'DEBUG', '[.1] make_foo: remove the stale output: foo'),
            ('dynamake', 'INFO', '[.1] make_foo: run: echo @ > foo'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_foo: success: echo @ > foo'),
            ('dynamake', 'DEBUG', '[.1] make_foo: synced'),
            ('dynamake', 'DEBUG', '[.1] make_foo: has the output: foo time: 1'),
            ('dynamake', 'TRACE', '[.1] make_foo: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        sys.argv += ['--remove_stale_outputs', 'false']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by '
             'the spawned: [.1] make_foo'),
            ('dynamake', 'TRACE', '[.1] make_foo: call'),
            ('dynamake', 'DEBUG', '[.1] make_foo: exists output: foo'),
            ('dynamake', 'DEBUG', '[.1] make_foo: synced'),
            ('dynamake', 'WHY',
             '[.1] make_foo: must run actions to satisfy the phony output: all'),
            ('dynamake', 'INFO', '[.1] make_foo: run: echo @ > foo'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_foo: success: echo @ > foo'),
            ('dynamake', 'DEBUG', '[.1] make_foo: synced'),
            ('dynamake', 'DEBUG', '[.1] make_foo: has the output: foo time: 2'),
            ('dynamake', 'TRACE', '[.1] make_foo: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_phony_dependencies(self) -> None:
        def _register() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                require('foo')
                await shell('touch all')

            @step(output=phony('foo'))
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell('true')

        sys.argv += ['--jobs', '0']
        sys.argv += ['--rebuild_changed_actions', 'false']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing the output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo will be produced by the spawned: [.1.1] '
             'make_foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: call'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'WHY',
             '[.1.1] make_foo: must run actions to satisfy the phony output: foo'),
            ('dynamake', 'INFO', '[.1.1] make_foo: run: true'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: success: true'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: done'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions '
             'because has rebuilt the required phony: foo'),
            ('dynamake', 'INFO', '[.1] make_all: run: touch all'),
            ('dynamake', 'TRACE', '[.1] make_all: success: touch all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 1'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_failed_action(self) -> None:
        def _register() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell('false')

        sys.argv += ['--jobs', '0']
        sys.argv += ['--rebuild_changed_actions', 'false']

        self.check(_register, error='make_all: failure: false', log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing the output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions to create the missing output(s): all'),
            ('dynamake', 'INFO', '[.1] make_all: run: false'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'ERROR', '[.1] make_all: failure: false'),
            ('dynamake', 'TRACE', '[.1] make_all: fail'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

    def test_continue_on_failure(self) -> None:
        def _register() -> None:
            @step(output=phony('all'))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require('foo')
                await done(asyncio.sleep(0.2))
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

        sys.argv += ['--jobs', '0']
        sys.argv += ['--rebuild_changed_actions', 'false']
        sys.argv += ['--failure_aborts_build', 'false']

        self.check(_register, error='failed to build the required target.s.', log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo will be produced by the spawned: [.1.1] make_foo'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: call'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: missing the output(s): foo'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: build the required: baz'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foo: the required: baz will be produced by '
             'the spawned: [.1.1.1] make_baz'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: sync'),
            ('dynamake', 'TRACE', '[.1.1.1] make_baz: call'),
            ('dynamake', 'DEBUG', '[.1.1.1] make_baz: missing the output(s): baz'),
            ('dynamake', 'DEBUG', '[.1.1.1] make_baz: synced'),
            ('dynamake', 'WHY',
             '[.1.1.1] make_baz: must run actions to create the missing output(s): baz'),
            ('dynamake', 'INFO', '[.1.1.1] make_baz: run: touch baz'),
            ('dynamake', 'TRACE', '[.1.1.1] make_baz: success: touch baz'),
            ('dynamake', 'DEBUG', '[.1.1.1] make_baz: synced'),
            ('dynamake', 'INFO', '[.1.1.1] make_baz: run: false'),
            ('dynamake', 'ERROR', '[.1.1.1] make_baz: failure: false'),
            ('dynamake', 'DEBUG', '[.1.1.1] make_baz: synced'),
            ('dynamake', 'DEBUG', '[.1.1.1] make_baz: has the output: baz time: 1'),
            ('dynamake', 'DEBUG', '[.1.1.1] make_baz: remove the failed output: baz'),
            ('dynamake', 'TRACE', '[.1.1.1] make_baz: fail'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: the required: baz has failed to build'),
            ('dynamake', 'DEBUG', "[.1.1] make_foo: can't run: touch foo"),
            ('dynamake', 'TRACE', '[.1.1] make_foo: fail'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: bar'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: bar will be produced by '
             'the spawned: [.1.2] make_bar'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'TRACE', '[.1.2] make_bar: call'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: missing the output(s): bar'),
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
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

    def test_remove_failed_outputs(self) -> None:
        def _register() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                require('foo')
                await shell('echo @ > all ; false')

        write_file('foo', '!\n')

        sys.argv += ['--jobs', '0']
        sys.argv += ['--rebuild_changed_actions', 'false']

        self.check(_register, error='make_all: failure: echo @ > all ; false', log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing the output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: the required: foo is a source file'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions to create the missing output(s): all'),
            ('dynamake', 'INFO', '[.1] make_all: run: echo @ > all ; false'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'ERROR', '[.1] make_all: failure: echo @ > all ; false'),
            ('dynamake', 'DEBUG', '[.1] make_all: remove the failed output: all'),
            ('dynamake', 'TRACE', '[.1] make_all: fail'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

        sys.argv += ['--remove_failed_outputs', 'false']

        write_file('all', '?\n')
        sleep(0.1)
        write_file('foo', '!\n')

        self.check(_register, error='make_all: failure: echo @ > all ; false', log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: exists output: all'),
            ('dynamake', 'DEBUG', '[.1] make_all: oldest output: all time: 1'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: the required: foo is a source file'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: newest input: foo time: 2'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions '
             'because the output: all is not newer than the input: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: remove the stale output: all'),
            ('dynamake', 'INFO', '[.1] make_all: run: echo @ > all ; false'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
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

        sys.argv += ['--jobs', '0']
        sys.argv += ['--rebuild_changed_actions', 'false']
        sys.argv += ['--touch_success_outputs', 'true']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing the output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions to create the missing output(s): all'),
            ('dynamake', 'INFO', '[.1] make_all: run: touch all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: success: touch all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: touch the output: all'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 1'),
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

        sys.argv += ['--jobs', '0']
        sys.argv += ['--rebuild_changed_actions', 'false']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo will be produced by the spawned: [.1.1] '
             'make_foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: call'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: missing the output(s): foo'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'WHY',
             '[.1.1] make_foo: must run actions to create the missing output(s): foo'),
            ('dynamake', 'INFO', '[.1.1] make_foo: run: touch foo'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: success: touch foo'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: has the output: foo time: 1'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: done'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: bar'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: bar will be produced by the spawned: [.1.2] '
             'make_bar'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'TRACE', '[.1.2] make_bar: call'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: missing the output(s): bar'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: build the required: foo'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: the required: foo was built'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: synced'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: has the required: foo'),
            ('dynamake', 'WHY',
             '[.1.2] make_bar: must run actions to create the missing output(s): bar'),
            ('dynamake', 'INFO', '[.1.2] make_bar: run: touch bar'),
            ('dynamake', 'TRACE', '[.1.2] make_bar: success: touch bar'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: synced'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: has the output: bar time: 2'),
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

        sys.argv += ['--jobs', '0']
        sys.argv += ['--rebuild_changed_actions', 'false']

        self.check(_register, error="don't know how to make the required: foo", log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing the output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'ERROR',
             "[.1] make_all: don't know how to make the required: foo"),
            ('dynamake', 'TRACE', '[.1] make_all: fail'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

        sys.argv += ['foo']

        self.check(_register, error="don't know how to make the required: foo", log=[
            ('dynamake', 'TRACE', '[.] make: foo'),
            ('dynamake', 'DEBUG', '[.] make: build the required: foo'),
            ('dynamake', 'ERROR', "[.] make: don't know how to make the required: foo"),
        ])

    def test_slow_outputs(self) -> None:
        def _register() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell('(sleep 1 ; touch all) &')

        sys.argv += ['--jobs', '0']
        sys.argv += ['--rebuild_changed_actions', 'false']
        sys.argv += ['--wait_nfs_outputs', 'true']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing the output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions to create the missing output(s): all'),
            ('dynamake', 'INFO', '[.1] make_all: run: (sleep 1 ; touch all) &'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: success: (sleep 1 ; touch all) &'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'WARNING',
             '[.1] make_all: waited: 1.5 seconds for the output: all'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 1'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_optional_output(self) -> None:

        sys.argv += ['--jobs', '0']
        sys.argv += ['--rebuild_changed_actions', 'false']

        def _register_without() -> None:
            @step(output=phony('all'))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require('foo')

            @step(output='foo')
            async def make_foo() -> None:  # pylint: disable=unused-variable
                pass

        self.check(_register_without, error='missing some output.s.', log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo will be produced by the spawned: [.1.1] make_foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: call'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: missing the output(s): foo'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'ERROR', '[.1.1] make_foo: missing the output(s): foo'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: fail'),
            ('dynamake', 'TRACE', '[.1] make_all: fail'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

        def _register_with_input() -> None:
            @step(output=phony('all'))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require(optional('foo'))

            @step(output='foo')
            async def make_foo() -> None:  # pylint: disable=unused-variable
                pass

        self.check(_register_without, error='missing some output.s.', log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo will be produced by the spawned: [.1.1] make_foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: call'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: missing the output(s): foo'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'ERROR', '[.1.1] make_foo: missing the output(s): foo'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: fail'),
            ('dynamake', 'TRACE', '[.1] make_all: fail'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

        def _register_with_output() -> None:
            @step(output=phony('all'))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require('foo')

            @step(output=optional('foo'))
            async def make_foo() -> None:  # pylint: disable=unused-variable
                pass

        self.check(_register_with_output, error='failed to build', log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo will be produced by the spawned: [.1.1] make_foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: call'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: done'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'ERROR', '[.1] make_all: the required: foo has failed to build'),
            ('dynamake', 'TRACE', '[.1] make_all: fail'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

        def _register_with_both() -> None:
            @step(output=phony('all'))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require(optional('foo'))

            @step(output=optional('foo'))
            async def make_foo() -> None:  # pylint: disable=unused-variable
                pass

        self.check(_register_with_both, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo will be produced by '
             'the spawned: [.1.1] make_foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: call'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: done'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_remove_persistent_data(self) -> None:
        def _register() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell('false')

        os.mkdir('.dynamake')
        write_file('.dynamake/make_all.actions.yaml', '*invalid')

        sys.argv += ['--jobs', '0']

        self.check(_register, error='make_all: failure: false', log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'WARNING',
             '[.1] make_all: must run actions because read '
             'the invalid persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing the output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'INFO', '[.1] make_all: run: false'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'ERROR', '[.1] make_all: failure: false'),
            ('dynamake', 'DEBUG', '[.1] make_all: remove '
             'the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: fail'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

        self.check(_register, error='make_all: failure: false', log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions because missing '
             'the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing the output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'INFO', '[.1] make_all: run: false'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'ERROR', '[.1] make_all: failure: false'),
            ('dynamake', 'TRACE', '[.1] make_all: fail'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

    def test_remove_parameterized_persistent_data(self) -> None:
        def _register() -> None:
            @step(output='{*name}')
            async def make_all(**kwargs: str) -> None:  # pylint: disable=unused-variable,unused-argument
                await shell('false')

        os.makedirs('.dynamake/make_all')
        write_file('.dynamake/make_all/name=all.actions.yaml', '*invalid')

        sys.argv += ['--jobs', '0']

        self.check(_register, error='make_all/name=all: failure: false', log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all/name=all'),
            ('dynamake', 'TRACE', '[.1] make_all/name=all: call'),
            ('dynamake', 'WARNING',
             '[.1] make_all/name=all: must run actions because read '
             'the invalid persistent actions: .dynamake/make_all/name=all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all/name=all: missing the output(s): {*name}'),
            ('dynamake', 'DEBUG', '[.1] make_all/name=all: synced'),
            ('dynamake', 'INFO', '[.1] make_all/name=all: run: false'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'ERROR', '[.1] make_all/name=all: failure: false'),
            ('dynamake', 'DEBUG', '[.1] make_all/name=all: remove '
             'the persistent actions: .dynamake/make_all/name=all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all/name=all: fail'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

        self.check(_register, error='make_all/name=all: failure: false', log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all/name=all'),
            ('dynamake', 'TRACE', '[.1] make_all/name=all: call'),
            ('dynamake', 'WHY',
             '[.1] make_all/name=all: must run actions because missing '
             'the persistent actions: .dynamake/make_all/name=all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all/name=all: missing the output(s): {*name}'),
            ('dynamake', 'DEBUG', '[.1] make_all/name=all: synced'),
            ('dynamake', 'INFO', '[.1] make_all/name=all: run: false'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'ERROR', '[.1] make_all/name=all: failure: false'),
            ('dynamake', 'TRACE', '[.1] make_all/name=all: fail'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

    def test_add_final_action(self) -> None:
        def _register_without() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell('touch all')

        sys.argv += ['--jobs', '0']

        self.check(_register_without, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions because missing '
             'the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing the output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'INFO', '[.1] make_all: run: touch all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: success: touch all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 1'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: write the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        def _register_with() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell('touch all')
                await shell('true')

        self.check(_register_with, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: read the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: exists output: all'),
            ('dynamake', 'DEBUG', '[.1] make_all: oldest output: all time: 1'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: newest input: None time: 0'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: can skip actions because all '
             'the outputs exist and there are no newer inputs'),
            ('dynamake', 'DEBUG', '[.1] make_all: skip: touch all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: newest input: None time: 0'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions since it has changed to add action(s)'),
            ('dynamake', 'INFO', '[.1] make_all: run: true'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: success: true'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 1'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_abandon_early_action(self) -> None:
        def _register_with() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell('true')
                await shell('touch all')

        sys.argv += ['--jobs', '0']

        self.check(_register_with, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions because missing '
             'the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing the output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'INFO', '[.1] make_all: run: true'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: success: true'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'INFO', '[.1] make_all: run: touch all'),
            ('dynamake', 'TRACE', '[.1] make_all: success: touch all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 1'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: write the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        def _register_without() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell('touch all')

        self.check(_register_without, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: read the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: exists output: all'),
            ('dynamake', 'DEBUG', '[.1] make_all: oldest output: all time: 1'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: newest input: None time: 0'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions because it has changed '
             'the shell command: true into the shell command: touch all'),
            ('dynamake', 'DEBUG', '[.1] make_all: remove the stale output: all'),
            ('dynamake', 'INFO', '[.1] make_all: run: touch all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: success: touch all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 2'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: write the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_abandon_output(self) -> None:
        def _register_with() -> None:
            @step(output=['all', 'foo'])
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell('touch all ; sleep 0.1 ; touch foo')

        sys.argv += ['--jobs', '0']

        self.check(_register_with, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions because missing the persistent actions: '
             '.dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing the output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing the output(s): foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'INFO', '[.1] make_all: run: touch all ; sleep 0.1 ; touch foo'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: success: touch all ; sleep 0.1 ; touch foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 1'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: foo time: 2'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: write the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        os.remove('all')

        self.check(_register_with, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: read the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing the output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: exists output: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions to create the missing output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: remove the stale output: foo'),
            ('dynamake', 'INFO', '[.1] make_all: run: touch all ; sleep 0.1 ; touch foo'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: success: touch all ; sleep 0.1 ; touch foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 3'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: foo time: 4'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: write the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        def _register_without() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell('touch all')

        self.check(_register_without, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: read the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: exists output: all'),
            ('dynamake', 'DEBUG', '[.1] make_all: changed to abandon the output: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions since it has changed to abandon the output: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: remove the stale output: all'),
            ('dynamake', 'INFO', '[.1] make_all: run: touch all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: success: touch all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 5'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: write the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_abandon_final_action(self) -> None:
        def _register_with() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell('touch all')
                await shell('true')

        sys.argv += ['--jobs', '0']

        self.check(_register_with, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions because missing '
             'the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing the output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'INFO', '[.1] make_all: run: touch all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: success: touch all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'INFO', '[.1] make_all: run: true'),
            ('dynamake', 'TRACE', '[.1] make_all: success: true'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 1'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: write the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        def _register_without() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell('touch all')

        self.check(_register_without, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: read the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: exists output: all'),
            ('dynamake', 'DEBUG', '[.1] make_all: oldest output: all time: 1'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: newest input: None time: 0'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: can skip actions because all '
             'the outputs exist and there are no newer inputs'),
            ('dynamake', 'DEBUG', '[.1] make_all: skip: touch all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: newest input: None time: 0'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 1'),
            ('dynamake', 'WARNING',
             '[.1] make_all: skipped some action(s) even though it has changed '
             'to remove some final action(s)'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_add_required(self) -> None:
        def _register_without() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell('touch all')

        write_file('foo', '!\n')
        sleep(0.1)

        sys.argv += ['--jobs', '0']

        self.check(_register_without, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions because missing '
             'the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing the output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'INFO', '[.1] make_all: run: touch all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: success: touch all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 1'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: write the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        def _register_with() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                require('foo')
                await shell('touch all')

        self.check(_register_with, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: read the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: exists output: all'),
            ('dynamake', 'DEBUG', '[.1] make_all: oldest output: all time: 1'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: the required: foo is a source file'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: newest input: foo time: 0'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions because it has changed to require: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: remove the stale output: all'),
            ('dynamake', 'INFO', '[.1] make_all: run: touch all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: success: touch all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 2'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: write the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_remove_required(self) -> None:
        def _register_with() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                require('foo')
                await shell('touch all')

        write_file('foo', '!\n')

        sys.argv += ['--jobs', '0']

        self.check(_register_with, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions because missing '
             'the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing the output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: the required: foo is a source file'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'INFO', '[.1] make_all: run: touch all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: success: touch all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 1'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: write the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        def _register_without() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell('touch all')

        self.check(_register_without, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: read the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: exists output: all'),
            ('dynamake', 'DEBUG', '[.1] make_all: oldest output: all time: 1'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: newest input: None time: 0'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions because it has changed to not require: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: remove the stale output: all'),
            ('dynamake', 'INFO', '[.1] make_all: run: touch all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: success: touch all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 2'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: write the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_change_required_producer(self) -> None:
        def _register_without() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                require('foo')
                await shell('touch all')

        write_file('foo', '!\n')

        sys.argv += ['--jobs', '0']

        self.check(_register_without, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions because missing '
             'the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing the output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: the required: foo is a source file'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'INFO', '[.1] make_all: run: touch all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: success: touch all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 1'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: write the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        def _register_with() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                require('foo')
                await shell('touch all')

            @step(output='foo')
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell('touch foo')

        self.check(_register_with, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: read the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: exists output: all'),
            ('dynamake', 'DEBUG', '[.1] make_all: oldest output: all time: 1'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo will be produced by the spawned: [.1.1] make_foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: call'),
            ('dynamake', 'WHY',
             '[.1.1] make_foo: must run actions because missing '
             'the persistent actions: .dynamake/make_foo.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: exists output: foo'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: remove the stale output: foo'),
            ('dynamake', 'INFO', '[.1.1] make_foo: run: touch foo'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: success: touch foo'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: has the output: foo time: 2'),
            ('dynamake', 'DEBUG',
             '[.1.1] make_foo: write the persistent actions: .dynamake/make_foo.actions.yaml'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: done'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: newest input: foo time: 2'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions because the producer of '
             'the required: foo has changed from: source file into: make_foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: remove the stale output: all'),
            ('dynamake', 'INFO', '[.1] make_all: run: touch all'),
            ('dynamake', 'TRACE', '[.1] make_all: success: touch all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 3'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: write the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_config_param(self) -> None:
        def _register() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                foo = config_param('foo', '0')
                await shell('echo %s > all' % foo)

        sys.argv += ['--jobs', '0']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions because missing the persistent actions: '
             '.dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing the output(s): all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'INFO', '[.1] make_all: run: echo 0 > all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: success: echo 0 > all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 1'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: write the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        self.expect_file('all', '0\n')

        write_file('DynaMake.yaml', '- { when: { step: make_all }, then: { foo: 1 } }\n')
        write_file('conf.yaml', '- { when: { step: make_all }, then: { foo: 2 } }\n')

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: read the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: exists output: all'),
            ('dynamake', 'DEBUG', '[.1] make_all: oldest output: all time: 1'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: newest input: None time: 0'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions because it has changed '
             'the shell command: echo 0 > all into the shell command: echo 1 > all'),
            ('dynamake', 'DEBUG', '[.1] make_all: remove the stale output: all'),
            ('dynamake', 'INFO', '[.1] make_all: run: echo 1 > all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: success: echo 1 > all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 2'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: write the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        self.expect_file('all', '1\n')

        sys.argv += ['--step_config', 'conf.yaml']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: read the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: exists output: all'),
            ('dynamake', 'DEBUG', '[.1] make_all: oldest output: all time: 2'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: newest input: None time: 0'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions because it has changed '
             'the shell command: echo 1 > all into the shell command: echo 2 > all'),
            ('dynamake', 'DEBUG', '[.1] make_all: remove the stale output: all'),
            ('dynamake', 'INFO', '[.1] make_all: run: echo 2 > all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1] make_all: success: echo 2 > all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 3'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: write the persistent actions: .dynamake/make_all.actions.yaml'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        self.expect_file('all', '2\n')

    def test_config_file(self) -> None:
        def _register() -> None:
            @step(output='all')
            async def make_all() -> None:  # pylint: disable=unused-variable
                config_file()
                await shell('echo', with_config(), '> all')

        sys.argv += ['--jobs', '0']
        sys.argv += ['--rebuild_changed_actions', 'false']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: missing the output(s): all'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions because creating '
             'the missing persistent configuration: .dynamake/make_all.config.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'INFO',
             '[.1] make_all: run: echo --config .dynamake/make_all.config.yaml > all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE',
             '[.1] make_all: success: echo --config .dynamake/make_all.config.yaml > all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 1'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        self.expect_file('.dynamake/make_all.config.yaml', '{}\n')

        write_file('DynaMake.yaml', '- { when: { step: make_all }, then: { foo: 1 } }\n')
        write_file('conf.yaml', '- { when: { step: make_all }, then: { foo: 2 } }\n')

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: exists output: all'),
            ('dynamake', 'DEBUG', '[.1] make_all: oldest output: all time: 1'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions because changed '
             'the persistent configuration: .dynamake/make_all.config.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: from the old persistent configuration:\n{}\n'),
            ('dynamake', 'DEBUG', '[.1] make_all: to the new persistent configuration:\nfoo: 1\n'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: newest input: None time: 0'),
            ('dynamake', 'DEBUG', '[.1] make_all: remove the stale output: all'),
            ('dynamake', 'INFO',
             '[.1] make_all: run: echo --config .dynamake/make_all.config.yaml > all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE',
             '[.1] make_all: success: echo --config .dynamake/make_all.config.yaml > all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 2'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        self.expect_file('.dynamake/make_all.config.yaml', 'foo: 1\n')

        sys.argv += ['--step_config', 'conf.yaml']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: exists output: all'),
            ('dynamake', 'DEBUG', '[.1] make_all: oldest output: all time: 2'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions because changed '
             'the persistent configuration: .dynamake/make_all.config.yaml'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: from the old persistent configuration:\nfoo: 1\n'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: to the new persistent configuration:\nfoo: 2\n'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: newest input: None time: 0'),
            ('dynamake', 'DEBUG', '[.1] make_all: remove the stale output: all'),
            ('dynamake', 'INFO',
             '[.1] make_all: run: echo --config .dynamake/make_all.config.yaml > all'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE',
             '[.1] make_all: success: echo --config .dynamake/make_all.config.yaml > all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 3'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        self.expect_file('.dynamake/make_all.config.yaml', 'foo: 2\n')

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: exists output: all'),
            ('dynamake', 'DEBUG', '[.1] make_all: oldest output: all time: 3'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: use '
             'the same persistent configuration: .dynamake/make_all.config.yaml'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: newest input: None time: 0'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: can skip actions '
             'because all the outputs exist and there are no newer inputs'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: skip: echo --config .dynamake/make_all.config.yaml > all'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: newest input: None time: 0'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the output: all time: 3'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

        self.expect_file('.dynamake/make_all.config.yaml', 'foo: 2\n')

    def test_resources(self) -> None:
        def _register() -> None:
            @step(output=phony('all'))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require('foo')
                await done(asyncio.sleep(0.1))
                require('bar')

            @step(output='foo')
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell('sleep 0.2 ; touch foo')

            @step(output='bar')
            async def make_bar() -> None:  # pylint: disable=unused-variable
                await shell('touch bar')

        os.environ['DYNAMAKE_JOBS'] = '1'
        sys.argv += ['--rebuild_changed_actions', 'false']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: available resources: jobs=1'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo will be produced by '
             'the spawned: [.1.1] make_foo'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: call'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: missing the output(s): foo'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'WHY',
             '[.1.1] make_foo: must run actions to create the missing output(s): foo'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: grab resources: jobs=1'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: available resources: jobs=0'),
            ('dynamake', 'INFO', '[.1.1] make_foo: run: sleep 0.2 ; touch foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: bar'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: bar will be produced by '
             'the spawned: [.1.2] make_bar'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'TRACE', '[.1.2] make_bar: call'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: missing the output(s): bar'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: synced'),
            ('dynamake', 'WHY',
             '[.1.2] make_bar: must run actions to create the missing output(s): bar'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: available resources: jobs=0'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: paused by waiting for resources: jobs=1'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: success: sleep 0.2 ; touch foo'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: free resources: jobs=1'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: available resources: jobs=1'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: has the output: foo time: 1'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: done'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: grab resources: jobs=1'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: available resources: jobs=0'),
            ('dynamake', 'INFO', '[.1.2] make_bar: run: touch bar'),
            ('dynamake', 'TRACE', '[.1.2] make_bar: success: touch bar'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: free resources: jobs=1'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: available resources: jobs=1'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: synced'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: has the output: bar time: 2'),
            ('dynamake', 'TRACE', '[.1.2] make_bar: done'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: bar'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_custom_resources(self) -> None:
        def _register() -> None:
            Param(name='foo', default=2, parser=int, description='foo')

            resource_parameters(foo=1)

            @step(output=phony('all'))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require('foo')
                await done(asyncio.sleep(0.1))
                require('bar')

            @step(output='foo')
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell('sleep 0.2 ; touch foo', foo=2)

            @step(output='bar')
            async def make_bar() -> None:  # pylint: disable=unused-variable
                await shell('touch bar', jobs=0)

        sys.argv += ['--jobs', '8']
        sys.argv += ['--rebuild_changed_actions', 'false']

        self.check(_register, log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: available resources: foo=2,jobs=8'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: foo'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: foo will be produced by '
             'the spawned: [.1.1] make_foo'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: call'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: missing the output(s): foo'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'WHY',
             '[.1.1] make_foo: must run actions to create the missing output(s): foo'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: grab resources: foo=2,jobs=1'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: available resources: foo=0,jobs=7'),
            ('dynamake', 'INFO', '[.1.1] make_foo: run: sleep 0.2 ; touch foo'),
            ('dynamake', 'DEBUG', '[.1] make_all: build the required: bar'),
            ('dynamake', 'DEBUG',
             '[.1] make_all: the required: bar will be produced by '
             'the spawned: [.1.2] make_bar'),
            ('dynamake', 'DEBUG', '[.1] make_all: sync'),
            ('dynamake', 'TRACE', '[.1.2] make_bar: call'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: missing the output(s): bar'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: synced'),
            ('dynamake', 'WHY',
             '[.1.2] make_bar: must run actions to create the missing output(s): bar'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: available resources: foo=0,jobs=7'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: paused by waiting for resources: foo=1'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: success: sleep 0.2 ; touch foo'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: free resources: foo=2,jobs=1'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: available resources: foo=2,jobs=8'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: synced'),
            ('dynamake', 'DEBUG', '[.1.1] make_foo: has the output: foo time: 1'),
            ('dynamake', 'TRACE', '[.1.1] make_foo: done'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: grab resources: foo=1'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: available resources: foo=1,jobs=8'),
            ('dynamake', 'INFO', '[.1.2] make_bar: run: touch bar'),
            ('dynamake', 'TRACE', '[.1.2] make_bar: success: touch bar'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: free resources: foo=1'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: available resources: foo=2,jobs=8'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: synced'),
            ('dynamake', 'DEBUG', '[.1.2] make_bar: has the output: bar time: 2'),
            ('dynamake', 'TRACE', '[.1.2] make_bar: done'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: bar'),
            ('dynamake', 'DEBUG', '[.1] make_all: has the required: foo'),
            ('dynamake', 'TRACE', '[.1] make_all: done'),
            ('dynamake', 'DEBUG', '[.] make: synced'),
            ('dynamake', 'DEBUG', '[.] make: has the required: all'),
            ('dynamake', 'TRACE', '[.] make: done'),
        ])

    def test_unknown_resources(self) -> None:
        def _register() -> None:
            @step(output=phony('all'))
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell('true', foo=2)

        sys.argv += ['--jobs', '0']
        sys.argv += ['--rebuild_changed_actions', 'false']

        self.check(_register, error='unknown resource: foo', log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'WHY', '[.1] make_all: must run actions to satisfy the phony output: all'),
            ('dynamake', 'TRACE', '[.1] make_all: fail'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])

    def test_request_too_much_resources(self) -> None:
        def _register() -> None:
            @step(output=phony('all'))
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell('true', jobs=1000000)

        sys.argv += ['--jobs', '8']
        sys.argv += ['--rebuild_changed_actions', 'false']

        self.check(_register, error='resource: jobs amount: 1000000 .* greater .* amount:', log=[
            ('dynamake', 'TRACE', '[.] make: all'),
            ('dynamake', 'DEBUG', '[.] make: available resources: jobs=8'),
            ('dynamake', 'DEBUG', '[.] make: build the required: all'),
            ('dynamake', 'DEBUG',
             '[.] make: the required: all will be produced by the spawned: [.1] make_all'),
            ('dynamake', 'TRACE', '[.1] make_all: call'),
            ('dynamake', 'DEBUG', '[.1] make_all: synced'),
            ('dynamake', 'WHY',
             '[.1] make_all: must run actions to satisfy the phony output: all'),
            ('dynamake', 'TRACE', '[.1] make_all: fail'),
            ('dynamake', 'DEBUG', '[.] make: sync'),
            ('dynamake', 'TRACE', '[.] make: fail'),
        ])
