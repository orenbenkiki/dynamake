"""
Test the make utilities.
"""

import os
from time import sleep
from typing import Any
from typing import Dict
from typing import List

from testfixtures import LogCapture
from testfixtures import StringComparison

from dynamake.make import Action
from dynamake.make import Captured
from dynamake.make import Make
from dynamake.make import MissingInputs
from dynamake.make import MissingOutputs
from dynamake.make import action
from dynamake.make import capture
from dynamake.make import expand
from dynamake.make import extract
from dynamake.make import foreach
from dynamake.make import glob
from dynamake.make import plan
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
            self.assertEqual(Make.current.stack, '/strategy/tactics')
            self.assertEqual(Make.current.step, 'tactics')

        @plan()
        def strategy() -> None:
            self.assertEqual(Make.current.stack, '/strategy')
            self.assertEqual(Make.current.step, 'strategy')
            tactics()

        strategy()

    def test_action_in_action(self) -> None:

        @action()
        def tactics() -> None:
            pass

        @action()
        def strategy() -> None:
            tactics()

        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               r'nested step: .*\.tactics .* '
                               r'invoked from .* action step: .*\.strategy',
                               strategy)

    def test_action_in_plan_in_plan(self) -> None:

        @action()
        def tactics() -> None:
            self.assertEqual(Make.current.stack, '/strategy/scheme/tactics')
            self.assertEqual(Make.current.step, 'tactics')

        @plan()
        def scheme() -> None:
            self.assertEqual(Make.current.stack, '/strategy/scheme')
            self.assertEqual(Make.current.step, 'scheme')
            tactics()

        @plan()
        def strategy() -> None:
            self.assertEqual(Make.current.stack, '/strategy')
            self.assertEqual(Make.current.step, 'strategy')
            scheme()

        strategy()

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

    def test_empty_action(self) -> None:
        @action()
        def empty() -> Action:
            return Action(input=[], output=[], run=[])

        with LogCapture() as log:
            empty()

        log.check(('dynamake', 'DEBUG', '/empty: input: None'),
                  ('dynamake', 'DEBUG', '/empty: input paths: None'),
                  ('dynamake', 'DEBUG', '/empty: output: None'),
                  ('dynamake', 'DEBUG', '/empty: output paths before: None'),
                  ('dynamake', 'DEBUG', '/empty: needs to execute because has no outputs'),
                  ('dynamake', 'DEBUG', '/empty: output paths after: None'))

    def test_true_action(self) -> None:
        @action()
        def empty() -> Action:
            return Action(input=[], output=[], run=['true'])

        with LogCapture() as log:
            empty()

        log.check(('dynamake', 'DEBUG', '/empty: input: None'),
                  ('dynamake', 'DEBUG', '/empty: input paths: None'),
                  ('dynamake', 'DEBUG', '/empty: output: None'),
                  ('dynamake', 'DEBUG', '/empty: output paths before: None'),
                  ('dynamake', 'DEBUG', '/empty: needs to execute because has no outputs'),
                  ('dynamake', 'INFO', "/empty: run: true"),
                  ('dynamake', 'DEBUG', '/empty: output paths after: None'))

    def test_forbidden_missing_input(self) -> None:
        @action()
        def missing() -> Action:
            return Action(input=['missing.txt'], output=[], run=[])

        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               r'Missing input\(s\): missing.txt .* step: /missing',
                               missing)

    def test_assumed_missing_input(self) -> None:
        @action()
        def missing() -> Action:
            return Action(input=['missing.txt'], output=['output.txt'], run=[],
                          missing_inputs=MissingInputs.assume_up_to_date)

        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               r'Missing input\(s\): missing.txt .* step: /missing',
                               missing)

    def test_optional_missing_input(self) -> None:
        @action()
        def missing() -> Action:
            return Action(input=['missing.txt'], output=[], run=[])

        Make.missing_inputs = MissingInputs.optional

        with LogCapture() as log:
            missing()

        log.check(('dynamake', 'DEBUG', '/missing: input: missing.txt'),
                  ('dynamake', 'DEBUG', '/missing: input paths: None'),
                  ('dynamake', 'DEBUG', '/missing: output: None'),
                  ('dynamake', 'DEBUG', '/missing: output paths before: None'),
                  ('dynamake', 'DEBUG', '/missing: needs to execute because has no outputs'),
                  ('dynamake', 'DEBUG', '/missing: output paths after: None'))

    def test_forbidden_missing_output(self) -> None:
        @action()
        def missing(prefix: str) -> Action:  # pylint: disable=unused-argument
            return Action(input=[], output=['{prefix}.txt'], run=[])

        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               r'Missing output\(s\): output.txt'
                               r' .* pattern: \{prefix\}.txt .* step: /missing',
                               missing, 'output')

    def test_partial_missing_output(self) -> None:
        @action()
        def missing() -> Action:
            return Action(input=[], output=['output.txt'], run=[],
                          missing_outputs=MissingOutputs.partial)

        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               r'Missing output\(s\): output.txt .* step: /missing',
                               missing)

    def test_optional_missing_output(self) -> None:
        @action()
        def missing() -> Action:
            return Action(input=[], output=['output.txt'], run=[],
                          missing_outputs=MissingOutputs.optional)

        with LogCapture() as log:
            missing()

        log.check(('dynamake', 'DEBUG', '/missing: input: None'),
                  ('dynamake', 'DEBUG', '/missing: input paths: None'),
                  ('dynamake', 'DEBUG', '/missing: output: output.txt'),
                  ('dynamake', 'DEBUG', '/missing: output paths before: None'),
                  ('dynamake', 'DEBUG', '/missing: minimal output mtime: None'),
                  ('dynamake', 'DEBUG',
                   '/missing: need to execute assuming next step(s) need all inputs'),
                  ('dynamake', 'DEBUG', '/missing: output paths after: None'))


class TestFiles(TestWithFiles):

    def test_capture(self) -> None:
        @plan()
        def captor(foo: str) -> Captured:  # pylint: disable=unused-argument
            return capture('{foo}.{*bar}')

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
            return glob('{foo}.*')

        self.assertEqual(globber('x'), [])

        write_file('x.a')

        self.assertEqual(globber('x'), ['x.a'])

    def test_assumed_missing_input(self) -> None:
        @action()
        def missing() -> Action:
            return Action(input=['missing.txt'], output=['output.txt'], run=[],
                          missing_inputs=MissingInputs.assume_up_to_date)

        write_file('output.txt')

        with LogCapture() as log:
            missing()

        log.check(('dynamake', 'DEBUG', '/missing: input: missing.txt'),
                  ('dynamake', 'DEBUG', '/missing: input paths: None'),
                  ('dynamake', 'DEBUG', '/missing: output: output.txt'),
                  ('dynamake', 'DEBUG', '/missing: output paths before: output.txt'),
                  ('dynamake', 'DEBUG', StringComparison(r'/missing: minimal output mtime: .*')),
                  ('dynamake', 'DEBUG', '/missing: maximal input mtime: None'),
                  ('dynamake', 'DEBUG', '/missing: no need to execute ignoring missing inputs'))

    def test_execute_for_missing_output(self) -> None:
        @action()
        def touch() -> Action:
            return Action(input=['input.txt'], output=['output.txt'], run=['touch', 'output.txt'])

        write_file('input.txt')

        with LogCapture() as log:
            touch()

        log.check(('dynamake', 'DEBUG', '/touch: input: input.txt'),
                  ('dynamake', 'DEBUG', '/touch: input paths: input.txt'),
                  ('dynamake', 'DEBUG', '/touch: output: output.txt'),
                  ('dynamake', 'DEBUG', '/touch: output paths before: None'),
                  ('dynamake', 'DEBUG', '/touch: minimal output mtime: None'),
                  ('dynamake', 'DEBUG',
                   '/touch: need to execute assuming next step(s) need all inputs'),
                  ('dynamake', 'INFO', '/touch: run: touch output.txt'),
                  ('dynamake', 'DEBUG', '/touch: output paths after: output.txt'))

    def test_shell_for_missing_output(self) -> None:
        @action()
        def echo() -> Action:
            return Action(input=['input.txt'], output=['output.txt'],
                          run=['echo', '>', 'output.txt'], shell=True)

        write_file('input.txt')

        with LogCapture() as log:
            echo()

        log.check(('dynamake', 'DEBUG', '/echo: input: input.txt'),
                  ('dynamake', 'DEBUG', '/echo: input paths: input.txt'),
                  ('dynamake', 'DEBUG', '/echo: output: output.txt'),
                  ('dynamake', 'DEBUG', '/echo: output paths before: None'),
                  ('dynamake', 'DEBUG', '/echo: minimal output mtime: None'),
                  ('dynamake', 'DEBUG',
                   '/echo: need to execute assuming next step(s) need all inputs'),
                  ('dynamake', 'INFO', '/echo: run: echo > output.txt'),
                  ('dynamake', 'DEBUG', '/echo: output paths after: output.txt'))

    def test_skip_for_missing_output(self) -> None:
        @action()
        def touch() -> Action:
            return Action(input=['input.txt'], output=['output.txt'], run=['touch', 'output.txt'])

        Make.missing_inputs = MissingInputs.assume_up_to_date

        with LogCapture() as log:
            touch()

        log.check(('dynamake', 'DEBUG', '/touch: input: input.txt'),
                  ('dynamake', 'DEBUG', '/touch: input paths: None'),
                  ('dynamake', 'DEBUG', '/touch: output: output.txt'),
                  ('dynamake', 'DEBUG', '/touch: output paths before: None'),
                  ('dynamake', 'DEBUG', '/touch: minimal output mtime: None'),
                  ('dynamake', 'DEBUG',
                   '/touch: no need to execute assuming next step(s) allow missing inputs'))

    def test_skip_for_old_input(self) -> None:
        @action()
        def touch() -> Action:
            return Action(input=['???.txt'], output=['output.txt'], run=['touch', 'output.txt'])

        write_file('foo.txt')
        write_file('bar.txt')
        sleep(1e-3)
        write_file('output.txt')

        with LogCapture() as log:
            touch()

        log.check(('dynamake', 'DEBUG', '/touch: input: ???.txt'),
                  ('dynamake', 'DEBUG', '/touch: input paths: bar.txt foo.txt'),
                  ('dynamake', 'DEBUG', '/touch: output: output.txt'),
                  ('dynamake', 'DEBUG', '/touch: output paths before: output.txt'),
                  ('dynamake', 'DEBUG', StringComparison('/touch: minimal output mtime: .*')),
                  ('dynamake', 'DEBUG', StringComparison('/touch: maximal input mtime:.*')),
                  ('dynamake', 'DEBUG', '/touch: no need to execute since outputs are newer'))

    def test_run_for_old_output(self) -> None:
        @action()
        def touch() -> Action:
            return Action(input=['input.txt'], output=['output.txt'], run=['touch', 'output.txt'])

        write_file('output.txt')
        sleep(1e-3)
        write_file('input.txt')

        with LogCapture() as log:
            touch()

        log.check(('dynamake', 'DEBUG', '/touch: input: input.txt'),
                  ('dynamake', 'DEBUG', '/touch: input paths: input.txt'),
                  ('dynamake', 'DEBUG', '/touch: output: output.txt'),
                  ('dynamake', 'DEBUG', '/touch: output paths before: output.txt'),
                  ('dynamake', 'DEBUG', StringComparison('/touch: minimal output mtime: .*')),
                  ('dynamake', 'DEBUG', StringComparison('/touch: maximal input mtime:.*')),
                  ('dynamake', 'DEBUG', '/touch: need to execute since inputs are newer'),
                  ('dynamake', 'DEBUG', '/touch: delete stale outputs: '),
                  ('dynamake', 'INFO', '/touch: run: touch output.txt'),
                  ('dynamake', 'DEBUG', '/touch: output paths after: output.txt'))

    def test_remove_before_run(self) -> None:
        @action()
        def fail() -> Action:
            return Action(input=['input.txt'], output=['output.txt'], run=['false'])

        write_file('output.txt')
        write_file('input.txt')

        with LogCapture() as log:
            self.assertRaisesRegex(RuntimeError,  # type: ignore
                                   r'/fail: .* command: false',
                                   fail)

        log.check(('dynamake', 'DEBUG', '/fail: input: input.txt'),
                  ('dynamake', 'DEBUG', '/fail: input paths: input.txt'),
                  ('dynamake', 'DEBUG', '/fail: output: output.txt'),
                  ('dynamake', 'DEBUG', '/fail: output paths before: output.txt'),
                  ('dynamake', 'DEBUG', StringComparison('/fail: minimal output mtime: .*')),
                  ('dynamake', 'DEBUG', StringComparison('/fail: maximal input mtime:.*')),
                  ('dynamake', 'DEBUG', '/fail: need to execute since inputs are newer'),
                  ('dynamake', 'DEBUG', '/fail: delete stale outputs: '),
                  ('dynamake', 'INFO', '/fail: run: false'),
                  ('dynamake', 'DEBUG', '/fail: failed with exit status: 1'))

        self.assertFalse(os.path.exists('output.txt'))

    def test_remove_after_fail(self) -> None:
        @action()
        def fail() -> Action:
            return Action(input=['input.txt'], output=['output.txt'],
                          run=[['touch', 'output.txt'], ['false']])

        write_file('output.txt')
        write_file('input.txt')

        with LogCapture() as log:
            self.assertRaisesRegex(RuntimeError,  # type: ignore
                                   r'/fail: .* command: false',
                                   fail)

        log.check(('dynamake', 'DEBUG', '/fail: input: input.txt'),
                  ('dynamake', 'DEBUG', '/fail: input paths: input.txt'),
                  ('dynamake', 'DEBUG', '/fail: output: output.txt'),
                  ('dynamake', 'DEBUG', '/fail: output paths before: output.txt'),
                  ('dynamake', 'DEBUG', StringComparison('/fail: minimal output mtime: .*')),
                  ('dynamake', 'DEBUG', StringComparison('/fail: maximal input mtime:.*')),
                  ('dynamake', 'DEBUG', '/fail: need to execute since inputs are newer'),
                  ('dynamake', 'DEBUG', '/fail: delete stale outputs: '),
                  ('dynamake', 'INFO', '/fail: run: touch output.txt'),
                  ('dynamake', 'INFO', '/fail: run: false'),
                  ('dynamake', 'DEBUG', '/fail: failed with exit status: 1'),
                  ('dynamake', 'DEBUG', '/fail: delete failed outputs: output.txt'))

        self.assertFalse(os.path.exists('output.txt'))

    def test_keep_output(self) -> None:
        @action()
        def fail() -> Action:
            return Action(input=['input.txt'], output=['output.txt'], run=['false'],
                          delete_stale_outputs=False, delete_failed_outputs=False)

        write_file('output.txt')
        write_file('input.txt')

        with LogCapture() as log:
            self.assertRaisesRegex(RuntimeError,  # type: ignore
                                   r'/fail: .* command: false',
                                   fail)

        log.check(('dynamake', 'DEBUG', '/fail: input: input.txt'),
                  ('dynamake', 'DEBUG', '/fail: input paths: input.txt'),
                  ('dynamake', 'DEBUG', '/fail: output: output.txt'),
                  ('dynamake', 'DEBUG', '/fail: output paths before: output.txt'),
                  ('dynamake', 'DEBUG', StringComparison('/fail: minimal output mtime: .*')),
                  ('dynamake', 'DEBUG', StringComparison('/fail: maximal input mtime:.*')),
                  ('dynamake', 'DEBUG', '/fail: need to execute since inputs are newer'),
                  ('dynamake', 'INFO', '/fail: run: false'),
                  ('dynamake', 'DEBUG', '/fail: failed with exit status: 1'))

        self.assertTrue(os.path.exists('output.txt'))

    def test_delete_dir(self) -> None:
        @action()
        def mkdir() -> Action:
            return Action(input=['input.txt'], output=['output.dir'], run=['mkdir', 'output.dir'])

        os.mkdir('output.dir')
        write_file('output.dir/output.txt')
        write_file('input.txt')

        with LogCapture() as log:
            mkdir()

        log.check(('dynamake', 'DEBUG', '/mkdir: input: input.txt'),
                  ('dynamake', 'DEBUG', '/mkdir: input paths: input.txt'),
                  ('dynamake', 'DEBUG', '/mkdir: output: output.dir'),
                  ('dynamake', 'DEBUG', '/mkdir: output paths before: output.dir'),
                  ('dynamake', 'DEBUG', StringComparison('/mkdir: minimal output mtime: .*')),
                  ('dynamake', 'DEBUG', StringComparison('/mkdir: maximal input mtime:.*')),
                  ('dynamake', 'DEBUG', '/mkdir: need to execute since inputs are newer'),
                  ('dynamake', 'DEBUG', '/mkdir: delete stale outputs: '),
                  ('dynamake', 'INFO', '/mkdir: run: mkdir output.dir'),
                  ('dynamake', 'DEBUG', '/mkdir: output paths after: output.dir'))

        self.assertFalse(os.path.exists('output.dir/output.txt'))

    def test_touch_dir(self) -> None:
        @action()
        def mkdir() -> Action:
            return Action(input=['input.txt'], output=['output.dir'],
                          run=['mkdir', '-p', 'output.dir'],
                          delete_stale_outputs=False,
                          touch_success_outputs=True)

        os.mkdir('output.dir')
        write_file('output.dir/output.txt')
        write_file('input.txt')

        with LogCapture() as log:
            mkdir()

        log.check(('dynamake', 'DEBUG', '/mkdir: input: input.txt'),
                  ('dynamake', 'DEBUG', '/mkdir: input paths: input.txt'),
                  ('dynamake', 'DEBUG', '/mkdir: output: output.dir'),
                  ('dynamake', 'DEBUG', '/mkdir: output paths before: output.dir'),
                  ('dynamake', 'DEBUG', StringComparison('/mkdir: minimal output mtime: .*')),
                  ('dynamake', 'DEBUG', StringComparison('/mkdir: maximal input mtime:.*')),
                  ('dynamake', 'DEBUG', '/mkdir: need to execute since inputs are newer'),
                  ('dynamake', 'INFO', '/mkdir: run: mkdir -p output.dir'),
                  ('dynamake', 'DEBUG', '/mkdir: output paths after: output.dir'),
                  ('dynamake', 'DEBUG', '/mkdir: touch outputs: output.dir'))

        self.assertTrue(os.path.exists('output.dir/output.txt'))
        self.assertTrue(os.stat('output.dir').st_mtime_ns > os.stat('input.txt').st_mtime_ns)

    def test_delete_empty_dir(self) -> None:
        @action()
        def fail() -> Action:
            return Action(input=['input.txt'], output=['output.dir/output.txt'], run=['false'],
                          delete_empty_directories=True)

        os.mkdir('output.dir')
        write_file('output.dir/output.txt')
        write_file('input.txt')

        with LogCapture() as log:
            self.assertRaisesRegex(RuntimeError,  # type: ignore
                                   r'/fail: .* command: false',
                                   fail)

        log.check(('dynamake', 'DEBUG', '/fail: input: input.txt'),
                  ('dynamake', 'DEBUG', '/fail: input paths: input.txt'),
                  ('dynamake', 'DEBUG', '/fail: output: output.dir/output.txt'),
                  ('dynamake', 'DEBUG', '/fail: output paths before: output.dir/output.txt'),
                  ('dynamake', 'DEBUG', StringComparison('/fail: minimal output mtime: .*')),
                  ('dynamake', 'DEBUG', StringComparison('/fail: maximal input mtime:.*')),
                  ('dynamake', 'DEBUG', '/fail: need to execute since inputs are newer'),
                  ('dynamake', 'DEBUG', '/fail: delete stale outputs: '),
                  ('dynamake', 'INFO', '/fail: run: false'),
                  ('dynamake', 'DEBUG', '/fail: failed with exit status: 1'))

        self.assertFalse(os.path.exists('output.dir'))
