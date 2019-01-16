"""
Test the make utilities.
"""

# pylint: disable=too-many-lines

import argparse
import os
import sys
import threading
from concurrent.futures import wait
from datetime import datetime
from time import sleep
from typing import Any
from typing import Dict
from typing import List

from testfixtures import LogCapture
from testfixtures import StringComparison

from dynamake.make import Action
from dynamake.make import Captured
from dynamake.make import Make
from dynamake.make import Step
from dynamake.make import Wild
from dynamake.make import action
from dynamake.make import available_resources
from dynamake.make import capture
from dynamake.make import config_file
from dynamake.make import config_param
from dynamake.make import exists
from dynamake.make import expand
from dynamake.make import extract
from dynamake.make import foreach
from dynamake.make import glob
from dynamake.make import load_config
from dynamake.make import main
from dynamake.make import optional
from dynamake.make import parallel
from dynamake.make import parcall
from dynamake.make import pareach
from dynamake.make import plan
from dynamake.make import precious
from tests import TestWithFiles
from tests import TestWithReset
from tests import write_file

# pylint: disable=missing-docstring,too-many-public-methods,no-self-use
# pylint: disable=blacklisted-name,too-few-public-methods


class TestMake(TestWithReset):

    def test_call_function(self) -> None:
        called_function = False

        @action()
        def function() -> None:
            nonlocal called_function
            called_function = True

        function()
        self.assertTrue(called_function)

    def test_conflicting_function(self) -> None:
        @action()
        def function() -> None:  # pylint: disable=unused-variable
            pass

        def _register() -> None:
            @action()
            def function() -> None:  # pylint: disable=unused-variable
                pass

        self.assertRaisesRegex(RuntimeError,
                               'Conflicting .* step: function .* '
                               'both: .*.test_conflicting_function.<locals>.function '
                               'and: .*._register.<locals>.function',
                               _register)

    def test_call_static_method(self) -> None:
        class Klass:
            called_static_method = False

            @action()
            @staticmethod
            def static_method() -> None:
                Klass.called_static_method = True

        Klass.static_method()
        self.assertTrue(Klass.called_static_method)

    def test_action_in_plan(self) -> None:
        @action()
        def tactics() -> None:
            self.assertEqual(Step.current().stack, '/strategy/tactics')
            self.assertEqual(Step.current().name, 'tactics')

        @plan()
        def strategy() -> None:
            self.assertEqual(Step.current().stack, '/strategy')
            self.assertEqual(Step.current().name, 'strategy')
            tactics()

        strategy()

    def test_action_in_action(self) -> None:
        @action()
        def tactics() -> None:
            pass

        @action()
        def strategy() -> None:
            tactics()

        self.assertRaisesRegex(RuntimeError,
                               r'nested step: .*\.tactics .* '
                               r'invoked from .* action step: .*\.strategy',
                               strategy)

    def test_action_in_plan_in_plan(self) -> None:
        @action()
        def tactics() -> None:
            self.assertEqual(Step.current().stack, '/strategy/scheme/tactics')
            self.assertEqual(Step.current().name, 'tactics')

        @plan()
        def scheme() -> None:
            self.assertEqual(Step.current().stack, '/strategy/scheme')
            self.assertEqual(Step.current().name, 'scheme')
            tactics()

        @plan()
        def strategy() -> None:
            self.assertEqual(Step.current().stack, '/strategy')
            self.assertEqual(Step.current().name, 'strategy')
            scheme()

        strategy()

    def test_parallel_plan(self) -> None:
        main_thread = threading.current_thread().name
        left_thread = main_thread
        right_thread = main_thread

        @action()
        def left() -> None:
            sleep(0.01)
            nonlocal left_thread
            left_thread = threading.current_thread().name

        @action()
        def right() -> None:
            sleep(0.01)
            nonlocal right_thread
            right_thread = threading.current_thread().name

        @plan()
        def both() -> None:
            left_future = parallel(left)
            right_future = parallel(right)
            wait([left_future, right_future])

        both()

        self.assertNotEqual(left_thread, main_thread)
        self.assertNotEqual(right_thread, main_thread)
        self.assertNotEqual(left_thread, right_thread)

    def test_parcall_plan(self) -> None:
        main_thread = threading.current_thread().name
        left_thread = main_thread
        right_thread = main_thread

        @action()
        def left() -> None:
            sleep(0.01)
            nonlocal left_thread
            left_thread = threading.current_thread().name

        @action()
        def right() -> None:
            sleep(0.01)
            nonlocal right_thread
            right_thread = threading.current_thread().name

        @plan()
        def both() -> None:
            parcall((left, [], {}), (right, [], {}))

        both()

        self.assertNotEqual(left_thread, main_thread)
        self.assertNotEqual(right_thread, main_thread)
        self.assertNotEqual(left_thread, right_thread)

    def test_pareach_plan(self) -> None:
        main_thread = threading.current_thread().name

        @action()
        def in_parallel() -> str:
            sleep(0.01)
            return threading.current_thread().name

        @plan()
        def every() -> List[str]:
            return pareach([{}, {}], in_parallel)

        threads = every()

        self.assertEqual(len(threads), 2)
        self.assertNotEqual(threads[0], main_thread)
        self.assertNotEqual(threads[1], main_thread)
        self.assertNotEqual(threads[0], threads[1])

    def test_resources_plan(self) -> None:
        available_resources(foo=2)

        @action()
        def foo(amount: float) -> Action:
            return Action(input=[], output=[], run=['sleep', '1'], resources={'foo': amount})

        @plan()
        def foos() -> None:
            parcall((foo, [1], {}),
                    (foo, [1], {}),
                    (foo, [3], {}))

        start_time = datetime.now()
        foos()
        duration = (datetime.now() - start_time).total_seconds()
        self.assertTrue(duration > 1.5)  # Did not run all three in parallel.
        self.assertTrue(duration < 2.5)  # Did run first two in parallel.

    def test_negative_resources(self) -> None:
        available_resources(foo=2)

        @action()
        def foo(amount: float) -> Action:
            return Action(input=[], output=[], run=['true'], resources={'foo': amount})

        @plan()
        def foos() -> None:
            parcall((foo, [1], {}),
                    (foo, [-1], {}))

        self.assertRaisesRegex(RuntimeError,
                               'Negative amount: -1 .* resource: foo .* step: /foos/foo',
                               foos)

    def test_unknown_resources(self) -> None:
        @action()
        def foo(amount: float) -> Action:
            return Action(input=[], output=[], run=['sleep', '1'], resources={'foo': amount})

        @plan()
        def foos() -> None:
            parcall((foo, [1], {}),
                    (foo, [1], {}),
                    (foo, [3], {}))

        self.assertRaisesRegex(RuntimeError,
                               'Unknown resource: foo .* step: /foos/foo',
                               foos)

    def test_expand_in_step(self) -> None:
        @plan()
        def expander(foo: str, *, bar: str) -> List[str]:  # pylint: disable=unused-argument
            return expand('{foo}/{bar}')

        self.assertEqual(expander('a', bar='b'), ['a/b'])

    def test_extract_in_step(self) -> None:
        @plan()
        def extractor(foo: str, *, bar: str) -> List[Dict[str, Any]]:  # pylint: disable=unused-argument
            return extract('{foo}/{bar}.{*baz}', 'a/b.c')

        self.assertEqual(extractor('a', bar='b'), [{'baz': 'c'}])

    def test_foreach_in_step(self) -> None:
        def collect(*args: str, **kwargs: Dict[str, Any]) -> str:
            return '%s %s' % (args, kwargs)

        @plan()
        def expander(foo: str, bar: int, *, baz: str, vaz: int) -> List[str]:  # pylint: disable=unused-argument
            return foreach([{'wild': 2}], collect, '{foo}', bar,
                           baz='{baz}', vaz=vaz, wild='{wild}')

        self.assertEqual(expander('a', 0, baz='b', vaz=1),
                         ["('a', 0) {'baz': 'b', 'vaz': 1, 'wild': '2'}"])

    def test_wild_in_foreach(self) -> None:
        def collect(foo: int, *, bar: int) -> int:
            return foo + bar

        @plan()
        def expander(foo: int) -> List[str]:  # pylint: disable=unused-argument
            return foreach([{'bar': 2}], collect, Wild('foo'), bar=Wild('bar'))

        self.assertEqual(expander(1), [3])

    def test_valid_wild_in_foreach(self) -> None:
        def collect(foo: int, *, bar: int) -> int:
            return foo + bar

        @plan()
        def expander(foo: str) -> List[str]:  # pylint: disable=unused-argument
            return foreach([{'bar': 2}], collect, Wild('foo', int), bar=Wild('bar', int))

        self.assertEqual(expander('1'), [3])

    def test_valid_wild_function_in_foreach(self) -> None:
        def allow(name: str, value: Any) -> Any:  # pylint: disable=unused-argument
            return value

        def collect(foo: int, *, bar: int) -> int:
            return foo + bar

        @plan()
        def expander(foo: int) -> List[str]:  # pylint: disable=unused-argument
            return foreach([{'bar': 2}], collect, Wild('foo', allow), bar=Wild('bar', int))

        self.assertEqual(expander(1), [3])

    def test_unknown_wild_in_foreach(self) -> None:
        def collect(foo: int, *, bar: int) -> int:
            return foo + bar

        @plan()
        def expander(foo: str) -> List[str]:  # pylint: disable=unused-argument
            return foreach([{'bar': 2}], collect, Wild('foo'), bar=Wild('baz'))

        self.assertRaisesRegex(RuntimeError,
                               r'Unknown parameter: baz',
                               expander, 1)

    def test_invalid_wild_klass_in_foreach(self) -> None:
        def collect(foo: int, *, bar: int) -> int:
            return foo + bar

        @plan()
        def expander(foo: str) -> List[str]:  # pylint: disable=unused-argument
            return foreach([{'bar': 2}], collect, Wild('foo', int), bar=Wild('bar', int))

        self.assertRaisesRegex(RuntimeError,
                               r'Invalid value: x type: builtins.str .* parameter: foo',
                               expander, 'x')

    def test_invalid_wild_function_in_foreach(self) -> None:
        def forbid(name: str, value: Any) -> None:
            raise RuntimeError('Invalid parameter: %s value: %s' % (name, value))

        def collect(foo: int, *, bar: int) -> int:
            return foo + bar

        @plan()
        def expander(foo: str) -> List[str]:  # pylint: disable=unused-argument
            return foreach([{'bar': 2}], collect, Wild('foo', forbid), bar=Wild('bar', int))

        self.assertRaisesRegex(RuntimeError,
                               r'Invalid parameter: foo value: 1',
                               expander, 1)

    def test_empty_action(self) -> None:
        @action()
        def empty() -> Action:
            return Action(input=[], output=[], run=[])

        with LogCapture() as log:
            empty()

        log.check(('dynamake', 'DEBUG', '/empty: input(s): None'),
                  ('dynamake', 'DEBUG', '/empty: output(s): None'),
                  ('dynamake', 'DEBUG', '/empty: needs to execute because has no outputs'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/empty: use resource: steps amount: 1.0 .*')))

    def test_true_action(self) -> None:
        @action()
        def empty() -> Action:
            return Action(input=[], output=[], run='true')

        with LogCapture() as log:
            empty()

        log.check(('dynamake', 'DEBUG', '/empty: input(s): None'),
                  ('dynamake', 'DEBUG', '/empty: output(s): None'),
                  ('dynamake', 'DEBUG', '/empty: needs to execute because has no outputs'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/empty: use resource: steps amount: 1.0 .*')),
                  ('dynamake', 'INFO', '/empty: run: true'))

    def test_forbidden_missing_input(self) -> None:
        @action()
        def missing() -> Action:
            return Action(input=['missing.txt'], output=[], run=[])

        self.assertRaisesRegex(RuntimeError,
                               r'Missing input\(s\): missing.txt .* step: /missing',
                               missing)

    def test_needed_missing_input(self) -> None:
        @action()
        def missing() -> Action:
            return Action(input=['missing.txt'], output=['output.txt'], run=[])

        self.assertRaisesRegex(RuntimeError,
                               r'Missing input\(s\): missing.txt .* step: /missing',
                               missing)

    def test_optional_missing_input(self) -> None:
        @action()
        def missing() -> Action:
            return Action(input=[optional('missing.txt')], output=[], run=[])

        with LogCapture() as log:
            missing()

        log.check(('dynamake', 'DEBUG', '/missing: input(s): missing.txt'),
                  ('dynamake', 'DEBUG', '/missing: output(s): None'),
                  ('dynamake', 'DEBUG', '/missing: no input: missing.txt'),
                  ('dynamake', 'DEBUG', '/missing: needs to execute because has no outputs'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/missing: use resource: steps amount: 1.0 .*')))

    def test_forbidden_missing_output(self) -> None:
        @action()
        def missing(prefix: str) -> Action:  # pylint: disable=unused-argument
            return Action(input=[], output=['{prefix}.txt'], run=[])

        self.assertRaisesRegex(RuntimeError,
                               r'Missing output\(s\): output.txt'
                               r' .* pattern: \{prefix\}.txt .* step: /missing',
                               missing, 'output')

    def test_optional_missing_output(self) -> None:
        @action()
        def missing() -> Action:
            return Action(input=[], output=[optional('output.txt')], run=[])

        with LogCapture() as log:
            missing()

        log.check(('dynamake', 'DEBUG', '/missing: input(s): None'),
                  ('dynamake', 'DEBUG', '/missing: output(s): output.txt'),
                  ('dynamake', 'DEBUG', '/missing: no output: output.txt'),
                  ('dynamake', 'DEBUG', '/missing: need to execute because no output(s) exist'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/missing: use resource: steps amount: 1.0 .*')),
                  ('dynamake', 'DEBUG', '/missing: no output: output.txt'))

    def test_main_default_step(self) -> None:
        @action()
        def do_nothing() -> Action:
            return Action(input=[], output=[], run=[])

        sys.argv = ['__test', '-ll', 'DEBUG']
        with LogCapture() as log:
            main(argparse.ArgumentParser(), do_nothing)

        log.check(('dynamake', 'INFO', 'start'),
                  ('dynamake', 'DEBUG', '/do_nothing: input(s): None'),
                  ('dynamake', 'DEBUG', '/do_nothing: output(s): None'),
                  ('dynamake', 'DEBUG', '/do_nothing: needs to execute because has no outputs'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/do_nothing: use resource: steps amount: 1.0 .*')),
                  ('dynamake', 'INFO', 'done'))

    def test_main_non_default_step(self) -> None:
        @action()
        def do_nothing() -> Action:
            return Action(input=[], output=[], run=[])

        @action()
        def do_something() -> Action:  # pylint: disable=unused-variable
            return Action(input=[], output=[], run=[])

        sys.argv = ['__test', '-ll', 'DEBUG', 'do_something']

        with LogCapture() as log:
            main(argparse.ArgumentParser(), do_nothing)

        log.check(('dynamake', 'INFO', 'start'),
                  ('dynamake', 'DEBUG', '/do_something: input(s): None'),
                  ('dynamake', 'DEBUG', '/do_something: output(s): None'),
                  ('dynamake', 'DEBUG', '/do_something: needs to execute because has no outputs'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/do_something: use resource: steps amount: 1.0 .*')),
                  ('dynamake', 'INFO', 'done'))

    def test_non_step_function(self) -> None:
        def do_nothing() -> Action:
            return Action(input=[], output=[], run=[])

        sys.argv = ['__test']

        self.assertRaisesRegex(RuntimeError,
                               r'function: .*.test_non_step_function.<locals>.do_nothing',
                               main, argparse.ArgumentParser(), do_nothing)

    def test_missing_step_function(self) -> None:
        @action()
        def do_nothing() -> Action:
            return Action(input=[], output=[], run=[])

        sys.argv = ['__test', 'do_something']

        self.assertRaisesRegex(RuntimeError,
                               r'unknown step: do_something',
                               main, argparse.ArgumentParser(), do_nothing)

    def test_main_flags(self) -> None:
        @action()
        def do_nothing() -> Action:
            return Action(input=[], output=[], run=[])

        sys.argv = ['__test',
                    '-tso',
                    '-dso', 'f',
                    '-ded', 't']
        main(argparse.ArgumentParser(), do_nothing)

        self.assertTrue(Make.touch_success_outputs)
        self.assertFalse(Make.delete_stale_outputs)
        self.assertTrue(Make.delete_empty_directories)

    def test_main_parameters(self) -> None:
        collected = 'bar'

        @action()
        def do_nothing(foo: str) -> Action:
            nonlocal collected
            collected = foo
            return Action(input=[], output=[], run=[])

        sys.argv = ['__test', '-p', 'foo=baz']

        main(argparse.ArgumentParser(), do_nothing)

        self.assertEqual(collected, 'baz')

    def test_invalid_main_parameters(self) -> None:
        @action()
        def do_nothing(foo: str) -> Action:  # pylint: disable=unused-argument
            return Action(input=[], output=[], run=[])

        sys.argv = ['__test', '-p', 'foo']

        self.assertRaisesRegex(RuntimeError,
                               r'Invalid parameter flag: foo',
                               main, argparse.ArgumentParser(), do_nothing)

    def test_unused_main_parameters(self) -> None:
        @action()
        def do_nothing(foo: str) -> Action:  # pylint: disable=unused-argument
            return Action(input=[], output=[], run=[])

        sys.argv = ['__test', '-p', 'foo=[', '-p', 'bar=baz']

        self.assertRaisesRegex(RuntimeError,
                               r'Unused top-level .* parameter: bar',
                               main, argparse.ArgumentParser(), do_nothing)

    def test_missing_main_parameters(self) -> None:
        @action()
        def do_nothing(foo: str) -> Action:  # pylint: disable=unused-argument
            return Action(input=[], output=[], run=[])

        sys.argv = ['__test']

        self.assertRaisesRegex(RuntimeError,
                               r'Missing top-level parameter: foo .* step: /do_nothing',
                               main, argparse.ArgumentParser(), do_nothing)


class TestFiles(TestWithFiles):

    def test_capture(self) -> None:
        @plan()
        def captor(foo: str) -> Captured:  # pylint: disable=unused-argument
            return capture(optional('{foo}.{*bar}'))

        captured = captor('x')
        self.assertEqual(captured.paths, [])
        self.assertEqual(captured.wildcards, [])

        write_file('x.a')

        captured = captor('x')
        self.assertEqual(captured.paths, ['x.a'])
        self.assertEqual(captured.wildcards, [{'bar': 'a'}])

    def test_glob(self) -> None:
        @plan()
        def globber(foo: str) -> List[str]:  # pylint: disable=unused-argument
            return glob(optional('{foo}.*'))

        self.assertEqual(globber('x'), [])

        write_file('x.a')

        self.assertEqual(globber('x'), ['x.a'])

    def test_allow_no_inputs(self) -> None:
        @action()
        def missing() -> Action:
            return Action(input=[], output=['output.txt'], run=[])

        write_file('output.txt')

        with LogCapture() as log:
            missing()

        log.check(('dynamake', 'DEBUG', '/missing: input(s): None'),
                  ('dynamake', 'DEBUG', '/missing: output(s): output.txt'),
                  ('dynamake', 'DEBUG', '/missing: exists output: output.txt'),
                  ('dynamake', 'DEBUG', '/missing: no need to execute because all output(s) exist'))

    def test_ignore_exists_input_time(self) -> None:
        @action()
        def existing() -> Action:
            return Action(input=[exists('exists.txt')], output=['output.txt'], run=[])

        write_file('output.txt')
        sleep(0.01)
        write_file('exists.txt')

        with LogCapture() as log:
            existing()

        log.check(('dynamake', 'DEBUG', '/existing: input(s): exists.txt'),
                  ('dynamake', 'DEBUG', '/existing: output(s): output.txt'),
                  ('dynamake', 'DEBUG', '/existing: exists input: exists.txt'),
                  ('dynamake', 'DEBUG', '/existing: exists output: output.txt'),
                  ('dynamake', 'DEBUG', '/existing: no need to execute because all input(s) exist'))

    def test_ignore_exists_output_time(self) -> None:
        @action()
        def existing() -> Action:
            return Action(input=['input.txt'], output=[exists('exists.txt')], run=[])

        write_file('input.txt')
        sleep(0.01)
        write_file('exists.txt')

        with LogCapture() as log:
            existing()

        log.check(('dynamake', 'DEBUG', '/existing: input(s): input.txt'),
                  ('dynamake', 'DEBUG', '/existing: output(s): exists.txt'),
                  ('dynamake', 'DEBUG', '/existing: exists input: input.txt'),
                  ('dynamake', 'DEBUG', '/existing: exists output: exists.txt'),
                  ('dynamake', 'DEBUG',
                   '/existing: no need to execute because some output file(s) exist'))

    def test_execute_for_missing_output(self) -> None:
        @action()
        def touch() -> Action:
            return Action(input=['input.txt'], output=['output.txt'], run=['touch', 'output.txt'])

        write_file('input.txt')

        with LogCapture() as log:
            touch()

        log.check(('dynamake', 'DEBUG', '/touch: input(s): input.txt'),
                  ('dynamake', 'DEBUG', '/touch: output(s): output.txt'),
                  ('dynamake', 'DEBUG', '/touch: exists input: input.txt'),
                  ('dynamake', 'DEBUG',
                   '/touch: needs to execute because missing output(s): output.txt'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/touch: use resource: steps amount: 1.0 .*')),
                  ('dynamake', 'INFO', '/touch: run: touch output.txt'),
                  ('dynamake', 'DEBUG', '/touch: exists output: output.txt'))

    def test_shell_for_missing_output(self) -> None:
        @action()
        def echo() -> Action:
            return Action(input=['input.txt'], output=['output.txt'],
                          run=['echo', '>', 'output.txt'], runner='shell')

        write_file('input.txt')

        with LogCapture() as log:
            echo()

        log.check(('dynamake', 'DEBUG', '/echo: input(s): input.txt'),
                  ('dynamake', 'DEBUG', '/echo: output(s): output.txt'),
                  ('dynamake', 'DEBUG', '/echo: exists input: input.txt'),
                  ('dynamake', 'DEBUG',
                   '/echo: needs to execute because missing output(s): output.txt'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/echo: use resource: steps amount: 1.0 .*')),
                  ('dynamake', 'INFO', '/echo: run: echo > output.txt'),
                  ('dynamake', 'DEBUG', '/echo: exists output: output.txt'))

    def test_skip_for_old_input(self) -> None:
        @action()
        def touch() -> Action:
            return Action(input=['???.txt'], output=['output.txt'], run=['touch', 'output.txt'])

        write_file('foo.txt')
        write_file('bar.txt')
        sleep(0.01)
        write_file('output.txt')

        with LogCapture() as log:
            touch()

        log.check(('dynamake', 'DEBUG', '/touch: input(s): ???.txt'),
                  ('dynamake', 'DEBUG', '/touch: output(s): output.txt'),
                  ('dynamake', 'DEBUG', '/touch: glob input: ???.txt path(s): bar.txt foo.txt'),
                  ('dynamake', 'DEBUG', '/touch: exists output: output.txt'),
                  ('dynamake', 'DEBUG', '/touch: no need to execute because output(s) are newer'))

    def test_run_for_old_output(self) -> None:
        @action()
        def touch() -> Action:
            return Action(input=['input.txt'], output=['output.txt'], run=['touch', 'output.txt'])

        write_file('output.txt')
        sleep(0.01)
        write_file('input.txt')

        with LogCapture() as log:
            touch()

        log.check(('dynamake', 'DEBUG', '/touch: input(s): input.txt'),
                  ('dynamake', 'DEBUG', '/touch: output(s): output.txt'),
                  ('dynamake', 'DEBUG', '/touch: exists input: input.txt'),
                  ('dynamake', 'DEBUG', '/touch: exists output: output.txt'),
                  ('dynamake', 'DEBUG',
                   '/touch: need to execute because of newer input: input.txt'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/touch: use resource: steps amount: 1.0 .*')),
                  ('dynamake', 'DEBUG', '/touch: delete stale output: output.txt'),
                  ('dynamake', 'INFO', '/touch: run: touch output.txt'),
                  ('dynamake', 'DEBUG', '/touch: exists output: output.txt'))

    def test_remove_before_run(self) -> None:
        @action()
        def fail() -> Action:
            return Action(input=['input.txt'], output=['output.txt'], run=['false'])

        write_file('output.txt')
        sleep(0.01)
        write_file('input.txt')

        with LogCapture() as log:
            self.assertRaisesRegex(RuntimeError,
                                   r'/fail: .* command: false',
                                   fail)

        log.check(('dynamake', 'DEBUG', '/fail: input(s): input.txt'),
                  ('dynamake', 'DEBUG', '/fail: output(s): output.txt'),
                  ('dynamake', 'DEBUG', '/fail: exists input: input.txt'),
                  ('dynamake', 'DEBUG', '/fail: exists output: output.txt'),
                  ('dynamake', 'DEBUG', '/fail: need to execute because of newer input: input.txt'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/fail: use resource: steps amount: 1.0 .*')),
                  ('dynamake', 'DEBUG', '/fail: delete stale output: output.txt'),
                  ('dynamake', 'INFO', '/fail: run: false'),
                  ('dynamake', 'DEBUG', '/fail: failed with exit status: 1'))

        self.assertFalse(os.path.exists('output.txt'))

    def test_remove_after_fail(self) -> None:
        @action()
        def fail() -> Action:
            return Action(input=['input.txt'], output=['output.txt'],
                          run=[['touch', 'output.txt'], ['false']])

        write_file('output.txt')
        sleep(0.01)
        write_file('input.txt')

        with LogCapture() as log:
            self.assertRaisesRegex(RuntimeError,
                                   r'/fail: .* command: false',
                                   fail)

        log.check(('dynamake', 'DEBUG', '/fail: input(s): input.txt'),
                  ('dynamake', 'DEBUG', '/fail: output(s): output.txt'),
                  ('dynamake', 'DEBUG', '/fail: exists input: input.txt'),
                  ('dynamake', 'DEBUG', '/fail: exists output: output.txt'),
                  ('dynamake', 'DEBUG', '/fail: need to execute because of newer input: input.txt'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/fail: use resource: steps amount: 1.0 .*')),
                  ('dynamake', 'DEBUG', '/fail: delete stale output: output.txt'),
                  ('dynamake', 'INFO', '/fail: run: touch output.txt'),
                  ('dynamake', 'INFO', '/fail: run: false'),
                  ('dynamake', 'DEBUG', '/fail: failed with exit status: 1'),
                  ('dynamake', 'DEBUG', '/fail: delete failed output: output.txt'))

        self.assertFalse(os.path.exists('output.txt'))

    def test_keep_output(self) -> None:
        @action()
        def fail() -> Action:
            return Action(input=['input.txt'], output=['output.txt'], run=['false'],
                          delete_stale_outputs=False, delete_failed_outputs=False)

        write_file('output.txt')
        sleep(0.01)
        write_file('input.txt')

        with LogCapture() as log:
            self.assertRaisesRegex(RuntimeError,
                                   r'/fail: .* command: false',
                                   fail)

        log.check(('dynamake', 'DEBUG', '/fail: input(s): input.txt'),
                  ('dynamake', 'DEBUG', '/fail: output(s): output.txt'),
                  ('dynamake', 'DEBUG', '/fail: exists input: input.txt'),
                  ('dynamake', 'DEBUG', '/fail: exists output: output.txt'),
                  ('dynamake', 'DEBUG', '/fail: need to execute because of newer input: input.txt'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/fail: use resource: steps amount: 1.0 .*')),
                  ('dynamake', 'INFO', '/fail: run: false'),
                  ('dynamake', 'DEBUG', '/fail: failed with exit status: 1'))

        self.assertTrue(os.path.exists('output.txt'))

    def test_keep_success_precious(self) -> None:
        @action()
        def keeper() -> Action:
            return Action(input=['input.txt'], output=[precious('output.txt')], run=['true'])

        write_file('output.txt', 'a\n')
        sleep(0.01)
        write_file('input.txt')

        with LogCapture() as log:
            keeper()

        log.check(('dynamake', 'DEBUG', '/keeper: input(s): input.txt'),
                  ('dynamake', 'DEBUG', '/keeper: output(s): output.txt'),
                  ('dynamake', 'DEBUG', '/keeper: exists input: input.txt'),
                  ('dynamake', 'DEBUG', '/keeper: exists output: output.txt'),
                  ('dynamake', 'DEBUG',
                   '/keeper: need to execute because of newer input: input.txt'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/keeper: use resource: steps amount: 1.0 .*')),
                  ('dynamake', 'INFO', '/keeper: run: true'),
                  ('dynamake', 'DEBUG', '/keeper: exists output: output.txt'))

        self.expect_file('output.txt', 'a\n')

    def test_keep_fail_precious(self) -> None:
        @action()
        def keeper() -> Action:
            return Action(input=['input.txt'], output=[precious('output.txt')], run=['false'])

        write_file('output.txt', 'a\n')
        sleep(0.01)
        write_file('input.txt')

        with LogCapture() as log:
            self.assertRaisesRegex(RuntimeError,
                                   r'/keeper: .* command: false',
                                   keeper)

        log.check(('dynamake', 'DEBUG', '/keeper: input(s): input.txt'),
                  ('dynamake', 'DEBUG', '/keeper: output(s): output.txt'),
                  ('dynamake', 'DEBUG', '/keeper: exists input: input.txt'),
                  ('dynamake', 'DEBUG', '/keeper: exists output: output.txt'),
                  ('dynamake', 'DEBUG',
                   '/keeper: need to execute because of newer input: input.txt'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/keeper: use resource: steps amount: 1.0 .*')),
                  ('dynamake', 'INFO', '/keeper: run: false'),
                  ('dynamake', 'DEBUG', '/keeper: failed with exit status: 1'))

        self.expect_file('output.txt', 'a\n')

    def test_delete_dir(self) -> None:
        @action()
        def mkdir() -> Action:
            return Action(input=['input.txt'],
                          output=['output.dir', 'output.txt'],
                          run=[['mkdir', 'output.dir'], ['touch', 'output.txt']])

        os.mkdir('output.dir')
        write_file('output.dir/output.txt')
        sleep(0.01)
        write_file('input.txt')

        with LogCapture() as log:
            mkdir()

        log.check(('dynamake', 'DEBUG', '/mkdir: input(s): input.txt'),
                  ('dynamake', 'DEBUG', '/mkdir: output(s): output.dir output.txt'),
                  ('dynamake', 'DEBUG', '/mkdir: exists input: input.txt'),
                  ('dynamake', 'DEBUG', '/mkdir: exists output: output.dir'),
                  ('dynamake', 'DEBUG',
                   '/mkdir: needs to execute because missing output(s): output.txt'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/mkdir: use resource: steps amount: 1.0 .*')),
                  ('dynamake', 'DEBUG', '/mkdir: delete stale output: output.dir'),
                  ('dynamake', 'INFO', '/mkdir: run: mkdir output.dir'),
                  ('dynamake', 'INFO', '/mkdir: run: touch output.txt'),
                  ('dynamake', 'DEBUG', '/mkdir: exists output: output.dir'),
                  ('dynamake', 'DEBUG', '/mkdir: exists output: output.txt'))

        self.assertFalse(os.path.exists('output.dir/output.txt'))

    def test_touch_file(self) -> None:
        @action()
        def toucher() -> Action:
            return Action(input=['input.txt'], output=['output.txt'],
                          run=[],
                          delete_stale_outputs=False,
                          touch_success_outputs=True)

        write_file('output.txt')
        sleep(0.01)
        write_file('input.txt')

        with LogCapture() as log:
            toucher()

        log.check(('dynamake', 'DEBUG', '/toucher: input(s): input.txt'),
                  ('dynamake', 'DEBUG', '/toucher: output(s): output.txt'),
                  ('dynamake', 'DEBUG', '/toucher: exists input: input.txt'),
                  ('dynamake', 'DEBUG', '/toucher: exists output: output.txt'),
                  ('dynamake', 'DEBUG',
                   '/toucher: need to execute because of newer input: input.txt'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/toucher: use resource: steps amount: 1.0 .*')),
                  ('dynamake', 'DEBUG', '/toucher: exists output: output.txt'),
                  ('dynamake', 'DEBUG', '/toucher: touch output: output.txt'))

        self.assertTrue(os.stat('input.txt').st_mtime_ns < os.stat('output.txt').st_mtime_ns)

    def test_no_touch_dir(self) -> None:
        @action()
        def mkdir() -> Action:
            return Action(input=['input.txt'], output=['output.dir'],
                          run=['mkdir', '-p', 'output.dir'],
                          delete_stale_outputs=False,
                          touch_success_outputs=True)

        os.mkdir('output.dir')
        write_file('output.dir/output.txt')
        sleep(0.01)
        write_file('input.txt')

        with LogCapture() as log:
            mkdir()

        log.check(('dynamake', 'DEBUG', '/mkdir: input(s): input.txt'),
                  ('dynamake', 'DEBUG', '/mkdir: output(s): output.dir'),
                  ('dynamake', 'DEBUG', '/mkdir: exists input: input.txt'),
                  ('dynamake', 'DEBUG', '/mkdir: exists output: output.dir'),
                  ('dynamake', 'DEBUG',
                   '/mkdir: need to execute because of newer input: input.txt'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/mkdir: use resource: steps amount: 1.0 .*')),
                  ('dynamake', 'INFO', '/mkdir: run: mkdir -p output.dir'),
                  ('dynamake', 'DEBUG', '/mkdir: exists output: output.dir'))

        self.assertTrue(os.path.exists('output.dir/output.txt'))

    def test_delete_empty_dir(self) -> None:
        @action()
        def fail() -> Action:
            return Action(input=['input.txt'], output=['output.dir/output.txt'], run=['false'],
                          delete_empty_directories=True)

        os.mkdir('output.dir')
        write_file('output.dir/output.txt')
        sleep(0.01)
        write_file('input.txt')

        with LogCapture() as log:
            self.assertRaisesRegex(RuntimeError,
                                   r'/fail: .* command: false',
                                   fail)

        log.check(('dynamake', 'DEBUG', '/fail: input(s): input.txt'),
                  ('dynamake', 'DEBUG', '/fail: output(s): output.dir/output.txt'),
                  ('dynamake', 'DEBUG', '/fail: exists input: input.txt'),
                  ('dynamake', 'DEBUG', '/fail: exists output: output.dir/output.txt'),
                  ('dynamake', 'DEBUG', '/fail: need to execute because of newer input: input.txt'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/fail: use resource: steps amount: 1.0 .*')),
                  ('dynamake', 'DEBUG', '/fail: delete stale output: output.dir/output.txt'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/fail: delete empty directory: .*/output.dir')),
                  ('dynamake', 'INFO', '/fail: run: false'),
                  ('dynamake', 'DEBUG', '/fail: failed with exit status: 1'))

        self.assertFalse(os.path.exists('output.dir'))

    def test_use_strict_param(self) -> None:
        @action()
        def use_param() -> Action:
            return Action(input=[], output=[], run=[], foo=config_param('foo'))

        write_file('config.yaml', '- { when: {}, then: { foo: 1 } }')
        load_config('config.yaml')

        result = use_param()

        self.assertEqual(result.foo, 1)

    def test_use_optional_param(self) -> None:
        @action()
        def use_param() -> Action:
            return Action(input=[], output=[], run=[], foo=config_param('foo'))

        write_file('config.yaml', '- { when: {}, then: { "foo?": 1 } }')
        load_config('config.yaml')

        result = use_param()

        self.assertEqual(result.foo, 1)

    def test_not_use_strict_param(self) -> None:
        @action()
        def not_use_param() -> Action:
            return Action(input=[], output=[], run=[])

        write_file('config.yaml', '- { when: {}, then: { foo: 1 } }')
        load_config('config.yaml')

        self.assertRaisesRegex(RuntimeError,
                               r'Unused .* parameter: foo .* step: /not_use_param',
                               not_use_param)

    def test_not_use_optional_param(self) -> None:
        @action()
        def not_use_param() -> Action:
            return Action(input=[], output=[], run=[])

        write_file('config.yaml', '- { when: {}, then: { "foo?": 1 } }')
        load_config('config.yaml')

        with LogCapture() as log:
            not_use_param()

        log.check(('dynamake', 'DEBUG', '/not_use_param: input(s): None'),
                  ('dynamake', 'DEBUG', '/not_use_param: output(s): None'),
                  ('dynamake', 'DEBUG', '/not_use_param: needs to execute because has no outputs'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/not_use_param: use resource: steps amount: 1.0 .*')))

    def test_use_config_file(self) -> None:
        @action()
        def use_file() -> Action:
            config_file()  # Test multiple invocations.
            return Action(input=[], output=['output.yaml'],
                          run=['cp', config_file(), 'output.yaml'])

        write_file('config.yaml', '- { when: {}, then: { foo: 1 } }')
        load_config('config.yaml')

        with LogCapture() as log:
            use_file()
        self.expect_file('output.yaml', '{foo: 1}\n')

        log.check(('dynamake', 'DEBUG',
                   '/use_file: write new config: '
                   '.dynamake/config.48aaf62e-3246-dea5-ae11-ab57f68e4508.yaml'),
                  ('dynamake', 'DEBUG', '/use_file: input(s): None'),
                  ('dynamake', 'DEBUG', '/use_file: output(s): output.yaml'),
                  ('dynamake', 'DEBUG',
                   '/use_file: needs to execute because missing output(s): output.yaml'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/use_file: use resource: steps amount: 1.0 .*')),
                  ('dynamake', 'INFO', '/use_file: run: '
                   'cp .dynamake/config.48aaf62e-3246-dea5-ae11-ab57f68e4508.yaml output.yaml'),
                  ('dynamake', 'DEBUG', '/use_file: exists output: output.yaml'))

        write_file('config.yaml', '- { when: { step: use_file }, then: { foo: 1 } }')
        load_config('config.yaml')

        with LogCapture() as log:
            use_file()

        log.check(('dynamake', 'DEBUG', '/use_file: use existing config: '
                   '.dynamake/config.48aaf62e-3246-dea5-ae11-ab57f68e4508.yaml'),
                  ('dynamake', 'DEBUG', '/use_file: input(s): None'),
                  ('dynamake', 'DEBUG', '/use_file: output(s): output.yaml'),
                  ('dynamake', 'DEBUG', '/use_file: exists output: output.yaml'),
                  ('dynamake', 'DEBUG',
                   '/use_file: no need to execute because output(s) are newer'))

        write_file('config.yaml', '- { when: {}, then: { foo: 2 } }')
        load_config('config.yaml')

        with LogCapture() as log:
            use_file()
        self.expect_file('output.yaml', '{foo: 2}\n')

        log.check(('dynamake', 'DEBUG', '/use_file: write new config: '
                   '.dynamake/config.48aaf62e-3246-dea5-ae11-ab57f68e4508.yaml'),
                  ('dynamake', 'DEBUG', '/use_file: input(s): None'),
                  ('dynamake', 'DEBUG', '/use_file: output(s): output.yaml'),
                  ('dynamake', 'DEBUG', '/use_file: exists output: output.yaml'),
                  ('dynamake', 'DEBUG', '/use_file: need to execute because of newer config: '
                   '.dynamake/config.48aaf62e-3246-dea5-ae11-ab57f68e4508.yaml'),
                  ('dynamake', 'DEBUG',
                   StringComparison('/use_file: use resource: steps amount: 1.0 .*')),
                  ('dynamake', 'DEBUG', '/use_file: delete stale output: output.yaml'),
                  ('dynamake', 'INFO', '/use_file: run: cp '
                   '.dynamake/config.48aaf62e-3246-dea5-ae11-ab57f68e4508.yaml output.yaml'),
                  ('dynamake', 'DEBUG', '/use_file: exists output: output.yaml'))

    def test_main_config(self) -> None:
        @action()
        def do_nothing() -> Action:
            return Action(input=[], output=[], run=[])

        sys.argv = ['__test', '-tso']

        write_file('Config.yaml', """
            - when: {step: /}
              then:
                touch_success_outputs: False
                delete_stale_outputs: False
                delete_empty_directories: True
        """)

        main(argparse.ArgumentParser(), do_nothing)

        self.assertTrue(Make.touch_success_outputs)
        self.assertFalse(Make.delete_stale_outputs)
        self.assertTrue(Make.delete_empty_directories)

    def test_unused_main_config(self) -> None:
        @action()
        def do_nothing() -> Action:
            return Action(input=[], output=[], run=[])

        sys.argv = ['__test', '-tso']

        write_file('Config.yaml', """
            - when: {stack: /}
              then:
                foo: True
        """)

        self.assertRaisesRegex(RuntimeError,
                               r'Unused top-level .* parameter: foo',
                               main, argparse.ArgumentParser(), do_nothing)
