"""
Test the application utilities.
"""

import sys
from argparse import ArgumentParser
from threading import current_thread
from typing import Any
from typing import Callable
from typing import List
from typing import Optional
from typing import Tuple

from testfixtures import OutputCapture

from dynamake.application import Func
from dynamake.application import Param
from dynamake.application import Prog
from dynamake.application import config
from dynamake.application import main as da_main
from dynamake.application import override
from dynamake.application import parallel
from dynamake.patterns import str2int
from tests import TestWithFiles
from tests import TestWithReset
from tests import write_file

# pylint: disable=missing-docstring,too-many-public-methods,no-self-use
# pylint: disable=blacklisted-name,too-few-public-methods


class TestFunction(TestWithReset):

    def test_conflicting(self) -> None:
        @config()
        def repeated() -> None:  # pylint: disable=unused-variable
            pass

        def conflict() -> Any:
            @config()
            def repeated() -> None:  # pylint: disable=unused-variable
                pass

        self.assertRaisesRegex(RuntimeError,
                               'Conflicting .* function: repeated .* '
                               'both: .*.test_conflicting.<locals>.repeated '
                               'and: .*.conflict.<locals>.repeated',
                               conflict)

    def test_register_after_finalization(self) -> None:
        Func.finalize()
        Func.finalize()

        def post_finalize() -> Any:
            @config()
            def function() -> None:  # pylint: disable=unused-variable
                pass

        self.assertRaisesRegex(RuntimeError,
                               'function: .*.post_finalize.<locals>.function '
                               'after Func.finalize',
                               post_finalize)

    def test_collect_parameters(self) -> None:

        @config()
        def use_foo(*, foo: int = 0) -> int:
            return foo

        @config()
        def use_bar(*, bar: int = 0) -> int:
            return bar

        @config()
        def use_both() -> int:  # pylint: disable=unused-variable
            return use_foo() + use_bar()

        @config()
        def use_none(baz: int) -> int:  # pylint: disable=unused-variable
            use_foo = 1
            use_bar = 2
            return baz + use_foo + use_bar

        Func.finalize()

        foo = Func.by_name['use_foo']
        self.assertFalse(foo.has_positional_arguments)
        self.assertEqual(foo.direct_parameter_names, set(['foo']))
        self.assertEqual(foo.indirect_parameter_names, set(['foo']))

        bar = Func.by_name['use_bar']
        self.assertFalse(bar.has_positional_arguments)
        self.assertEqual(bar.direct_parameter_names, set(['bar']))
        self.assertEqual(bar.indirect_parameter_names, set(['bar']))

        both = Func.by_name['use_both']
        self.assertFalse(both.has_positional_arguments)
        self.assertEqual(both.direct_parameter_names, set())
        self.assertEqual(both.indirect_parameter_names, set(['foo', 'bar']))

        none = Func.by_name['use_none']
        self.assertTrue(none.has_positional_arguments)
        self.assertEqual(none.direct_parameter_names, set())
        self.assertEqual(none.indirect_parameter_names, set())

    def test_collect_recursive_parameters(self) -> None:

        @config()
        def use_foo(*, foo: int = 0) -> int:
            return foo + use_bar()

        @config()
        def use_bar(*, bar: int = 0) -> int:
            return bar + use_foo()

        @config()
        def use_both(baz: int) -> int:  # pylint: disable=unused-variable
            return baz + use_foo() + use_bar()

        Func.finalize()

        foo = Func.by_name['use_foo']
        self.assertFalse(foo.has_positional_arguments)
        self.assertEqual(foo.direct_parameter_names, set(['foo']))
        self.assertEqual(foo.indirect_parameter_names, set(['foo', 'bar']))

        bar = Func.by_name['use_bar']
        self.assertFalse(bar.has_positional_arguments)
        self.assertEqual(bar.direct_parameter_names, set(['bar']))
        self.assertEqual(bar.indirect_parameter_names, set(['foo', 'bar']))

        both = Func.by_name['use_both']
        self.assertTrue(both.has_positional_arguments)
        self.assertEqual(both.direct_parameter_names, set())
        self.assertEqual(both.indirect_parameter_names, set(['foo', 'bar']))


class TestParameters(TestWithReset):

    def test_missing_parameter(self) -> None:
        @config()
        def use_foo(*, foo: int) -> int:  # pylint: disable=unused-variable
            return foo

        self.assertRaisesRegex(RuntimeError,
                               'Missing .* parameter: foo .* '
                               'function: .*.test_missing_parameter.<locals>.use_foo',
                               Prog.current.verify)

    def test_conflicting_parameter(self) -> None:
        Param(name='foo', default=1, parser=int, description='The number of foos')

        self.assertRaisesRegex(RuntimeError,
                               'Multiple .* parameter: foo',
                               Param, name='foo', default=2, parser=str,
                               description='The size of a foo')

    def test_used_parameter(self) -> None:
        Param(name='foo', default=1, parser=int, description='The number of foos')
        self.assertRaisesRegex(RuntimeError,
                               'Unused parameter: foo',
                               Prog.current.verify)

    def test_unknown_parameter(self) -> None:
        parameters = Prog()

        self.assertRaisesRegex(RuntimeError,
                               'Unknown parameter: bar .* function: .*.test_missing_parameter',
                               parameters.get, 'bar', TestParameters.test_missing_parameter)

    def test_parallel(self) -> None:
        results = parallel(1, 2, _call_in_parallel, kwargs=lambda index: {'index': index})
        self.assertEqual(results, [('Fork-1.Thread-1', 0), ('Fork-1.Thread-1', 1)])

    def test_overrides(self) -> None:
        Prog.logger.setLevel('WARN')
        Param(name='bar', default=1, parser=int, description='The number of bars')

        @config()
        def foo(*, bar: int = 0) -> int:
            return bar

        self.assertEqual(foo(), 1)

        with override(bar=2):
            self.assertEqual(foo(), 2)

        self.assertEqual(foo(), 1)

        def nested() -> None:
            with override(baz=2):
                self.assertEqual(foo(), 2)

        self.assertRaisesRegex(RuntimeError,
                               'Unknown override parameter: baz',
                               nested)

    def test_parallel_overrides(self) -> None:
        Prog.logger.setLevel('WARN')
        Param(name='bar', default=1, parser=int, description='The number of bars')

        @config()
        def foo(*, bar: int = 0) -> int:
            return bar

        results = parallel(1, 2, foo, overrides=lambda index: {'bar': index})
        self.assertEqual(results, [0, 1])


def _call_in_parallel(index: int) -> Tuple[str, int]:
    return (current_thread().name, index)


def define_main_function() -> Callable:
    class Foo:
        @config()
        @staticmethod
        def add_foo(foo: int, *, bar: int = 0) -> int:
            return foo + bar

    @config()
    def add(foo: int, *, bar: int = 0) -> int:
        return foo + bar

    def main_function() -> int:
        Param(name='bar', default=1, parser=int, description='The number of bars')
        parser = ArgumentParser(description='Test')
        Prog.add_parameters_to_parser(parser, functions=['add', 'add_foo'])
        args = parser.parse_args()
        Prog.parse_args(args)
        return add(1) + Foo.add_foo(0, bar=0)

    return main_function


class TestSimpleMain(TestWithFiles):

    def test_defaults(self) -> None:
        sys.argv = ['test']
        self.assertEqual(define_main_function()(), 2)

    def test_empty_config(self) -> None:
        write_file('config.yaml', '')
        sys.argv = ['test', '--config', 'config.yaml']
        self.assertEqual(define_main_function()(), 2)

    def test_one_config(self) -> None:
        write_file('config.yaml', '{bar: 2}')
        sys.argv = ['test', '--config', 'config.yaml']
        self.assertEqual(define_main_function()(), 3)

    def test_two_configs(self) -> None:
        write_file('one.yaml', '{bar: 1}')
        write_file('two.yaml', '{bar: 2}')
        sys.argv = ['test', '--config', 'one.yaml', '--config', 'two.yaml']
        self.assertEqual(define_main_function()(), 3)

    def test_parameter(self) -> None:
        write_file('config.yaml', '{bar: 1}')
        sys.argv = ['test', '--bar', '2', '--config', 'config.yaml']
        self.assertEqual(define_main_function()(), 3)

    def test_top_non_mapping(self) -> None:
        write_file('config.yaml', '[]')
        sys.argv = ['test', '--config', 'config.yaml']
        self.assertRaisesRegex(RuntimeError,
                               'file: config.yaml .* top-level mapping',
                               define_main_function())

    def test_invalid_parameter_value(self) -> None:
        sys.argv = ['test', '--bar', 'x']
        self.assertRaisesRegex(RuntimeError,
                               'value: x .* parameter: bar',
                               define_main_function())

    def test_invalid_config_value(self) -> None:
        write_file('config.yaml', '{bar: x}')
        sys.argv = ['test', '--config', 'config.yaml']
        self.assertRaisesRegex(RuntimeError,
                               'value: x .* parameter: bar',
                               define_main_function())

    def test_conflicting_parameter(self) -> None:
        write_file('config.yaml', '{bar: 1, "bar?": 1}')
        sys.argv = ['test', '--config', 'config.yaml']
        self.assertRaisesRegex(RuntimeError,
                               '.* both: bar and: bar\\? .* file: config.yaml',
                               define_main_function())

    def test_allowed_known_parameter(self) -> None:
        write_file('config.yaml', '{"bar?": 1}')
        sys.argv = ['test', '--config', 'config.yaml']
        self.assertEqual(define_main_function()(), 2)

    def test_allowed_unknown_parameter(self) -> None:
        write_file('config.yaml', '{"baz?": x}')
        sys.argv = ['test', '--config', 'config.yaml']
        self.assertEqual(define_main_function()(), 2)

    def test_forbidden_unknown_parameter(self) -> None:
        write_file('config.yaml', '{baz: x}')
        sys.argv = ['test', '--config', 'config.yaml']
        self.assertRaisesRegex(RuntimeError,
                               'parameter: baz .* file: config.yaml',
                               define_main_function())


def define_main_commands(is_top: bool, extra: Optional[List[str]] = None) -> Callable:
    class Foo:  # pylint: disable=unused-variable
        @config(top=is_top)
        @staticmethod
        def add_foo(*, foo: int = 0, bar: int = 0) -> int:
            """
            Add with foo.
            """
            return 1 + foo + bar

    @config(top=is_top)
    def add(*, bar: int = 0, baz: int = 0) -> int:  # pylint: disable=unused-variable
        return bar + baz

    def main_function() -> int:
        parser = ArgumentParser(description='Test')
        Param(name='foo', default=1, parser=int, description='The number of foos')
        Param(name='bar', default=1, parser=int, description='The number of bars')
        Param(name='baz', default=1, parser=int, description='The number of bazes')
        functions: Optional[List[str]] = None
        if not is_top:
            functions = ['add', 'add_foo'] + (extra or [])
        Prog.add_commands_to_parser(parser, functions)
        args = parser.parse_args()
        Prog.parse_args(args)
        return Prog.call_with_args(args)

    return main_function


class TestCommandsMain(TestWithFiles):

    def test_add_defaults(self) -> None:
        sys.argv = ['test', 'add']
        self.assertEqual(define_main_commands(True)(), 2)

    def test_add_foo_defaults(self) -> None:
        sys.argv = ['test', 'add_foo']
        self.assertEqual(define_main_commands(False)(), 3)

    def test_unknown_command(self) -> None:
        sys.argv = ['test', 'bar']
        self.assertRaisesRegex(RuntimeError,
                               'Unknown .* function: bar',
                               define_main_commands(False, ['bar']))

    def test_unknown_function(self) -> None:
        sys.argv = ['test', 'add']

        @config()
        def unreachable() -> None:  # pylint: disable=unused-variable
            pass
        self.assertRaisesRegex(RuntimeError,
                               'function: .*.test_unknown_function.<locals>.unreachable '
                               '.* not reachable',
                               define_main_commands(True))

    def test_positional_command(self) -> None:
        @config()
        def bar(foo: int, *, baz: int = 0) -> int:  # pylint: disable=unused-variable
            return foo + baz

        sys.argv = ['test', 'bar']
        self.assertRaisesRegex(RuntimeError,
                               'function: .*.test_positional_command.<locals>.bar .* '
                               'positional arguments',
                               define_main_commands(False, ['bar']))


class TestUniversalMain(TestWithFiles):

    def test_defaults(self) -> None:
        Param(name='foo', parser=str2int(), default=1, description='The size of a foo.')

        @config(top=True)
        def top(*, foo: int = 0) -> None:  # pylint: disable=unused-variable
            print('foo', foo)

        sys.argv = ['test', 'top']
        with OutputCapture() as output:
            da_main(ArgumentParser(description='Test'))
        output.compare('foo 1')
