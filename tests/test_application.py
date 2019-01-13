"""
Test the application utilities.
"""

import argparse
import sys
from typing import Any
from typing import Callable
from typing import List
from typing import Optional

from dynamake.application import AppParams
from dynamake.application import ConfigurableFunction
from dynamake.application import Param
from dynamake.application import config
from tests import TestWithFiles
from tests import TestWithReset
from tests import write_file

# pylint: disable=missing-docstring,too-many-public-methods,no-self-use
# pylint: disable=blacklisted-name,too-few-public-methods


class TestFunction(TestWithReset):

    def test_conflicting(self) -> None:
        @config
        def repeated() -> None:  # pylint: disable=unused-variable
            pass

        def conflict() -> Any:
            @config
            def repeated() -> None:  # pylint: disable=unused-variable
                pass

        self.assertRaisesRegex(RuntimeError,
                               'Conflicting .* function: repeated .* '
                               'both: .*.test_conflicting.<locals>.repeated '
                               'and: .*.conflict.<locals>.repeated',
                               conflict)

    def test_register_after_finalization(self) -> None:
        ConfigurableFunction.finalize()
        ConfigurableFunction.finalize()

        def post_finalize() -> Any:
            @config
            def function() -> None:  # pylint: disable=unused-variable
                pass

        self.assertRaisesRegex(RuntimeError,
                               'function: .*.post_finalize.<locals>.function '
                               'after ConfigurableFunction.finalize',
                               post_finalize)

    def test_collect_parameters(self) -> None:

        @config
        def use_foo(*, foo: int = 0) -> int:
            return foo

        @config
        def use_bar(*, bar: int = 0) -> int:
            return bar

        @config
        def use_both() -> int:  # pylint: disable=unused-variable
            return use_foo() + use_bar()

        @config
        def use_none(baz: int) -> int:  # pylint: disable=unused-variable
            use_foo = 1
            use_bar = 2
            return baz + use_foo + use_bar

        ConfigurableFunction.finalize()

        foo = ConfigurableFunction.by_name['use_foo']
        self.assertFalse(foo.has_positional_arguments)
        self.assertEqual(foo.direct_parameter_names, set(['foo']))
        self.assertEqual(foo.indirect_parameter_names, set(['foo']))

        bar = ConfigurableFunction.by_name['use_bar']
        self.assertFalse(bar.has_positional_arguments)
        self.assertEqual(bar.direct_parameter_names, set(['bar']))
        self.assertEqual(bar.indirect_parameter_names, set(['bar']))

        both = ConfigurableFunction.by_name['use_both']
        self.assertFalse(both.has_positional_arguments)
        self.assertEqual(both.direct_parameter_names, set())
        self.assertEqual(both.indirect_parameter_names, set(['foo', 'bar']))

        none = ConfigurableFunction.by_name['use_none']
        self.assertTrue(none.has_positional_arguments)
        self.assertEqual(none.direct_parameter_names, set())
        self.assertEqual(none.indirect_parameter_names, set())

    def test_collect_recursive_parameters(self) -> None:

        @config
        def use_foo(*, foo: int = 0) -> int:
            return foo + use_bar()

        @config
        def use_bar(*, bar: int = 0) -> int:
            return bar + use_foo()

        @config
        def use_both(baz: int) -> int:  # pylint: disable=unused-variable
            return baz + use_foo() + use_bar()

        ConfigurableFunction.finalize()

        foo = ConfigurableFunction.by_name['use_foo']
        self.assertFalse(foo.has_positional_arguments)
        self.assertEqual(foo.direct_parameter_names, set(['foo']))
        self.assertEqual(foo.indirect_parameter_names, set(['foo', 'bar']))

        bar = ConfigurableFunction.by_name['use_bar']
        self.assertFalse(bar.has_positional_arguments)
        self.assertEqual(bar.direct_parameter_names, set(['bar']))
        self.assertEqual(bar.indirect_parameter_names, set(['foo', 'bar']))

        both = ConfigurableFunction.by_name['use_both']
        self.assertTrue(both.has_positional_arguments)
        self.assertEqual(both.direct_parameter_names, set())
        self.assertEqual(both.indirect_parameter_names, set(['foo', 'bar']))


class TestParameters(TestWithReset):

    def test_missing_parameter(self) -> None:
        @config
        def use_foo(*, foo: int) -> int:  # pylint: disable=unused-variable
            return foo

        self.assertRaisesRegex(RuntimeError,
                               'Missing .* parameter: foo .* '
                               'function: .*.test_missing_parameter.<locals>.use_foo',
                               AppParams)

    def test_used_parameter(self) -> None:
        self.assertRaisesRegex(RuntimeError,
                               'Unused parameter: foo',
                               AppParams, foo=Param(1, int, 'The number of foos'))

    def test_unknown_parameter(self) -> None:
        parameters = AppParams()

        self.assertRaisesRegex(RuntimeError,
                               'Unknown parameter: bar .* function: .*.test_missing_parameter',
                               parameters.get, 'bar', TestParameters.test_missing_parameter)


def define_main_function() -> Callable:
    class Foo:
        @config
        @staticmethod
        def add_foo(foo: int, *, bar: int = 0) -> int:
            return foo + bar

    @config
    def add(foo: int, *, bar: int = 0) -> int:
        return foo + bar

    def main_function() -> int:
        parser = argparse.ArgumentParser(description='Test')
        AppParams.current = AppParams(bar=Param(1, int, 'The number of bars'))
        AppParams.current.add_to_parser(parser)
        args = parser.parse_args()
        AppParams.current.parse_args(args)
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


def define_main_commands(extra: Optional[List[str]] = None) -> Callable:
    class Foo:  # pylint: disable=unused-variable
        @config
        @staticmethod
        def add_foo(*, foo: int = 0, bar: int = 0) -> int:
            """
            Add with foo.
            """
            return 1 + foo + bar

    @config
    def add(*, bar: int = 0, baz: int = 0) -> int:  # pylint: disable=unused-variable
        return bar + baz

    def main_function() -> int:
        parser = argparse.ArgumentParser(description='Test')
        AppParams.current = AppParams(foo=Param(1, int, 'The number of foos'),
                                      bar=Param(1, int, 'The number of bars'),
                                      baz=Param(1, int, 'The number of bazes'))
        AppParams.current.add_to_parser(parser, ['add', 'add_foo'] + (extra or []))
        args = parser.parse_args()
        AppParams.current.parse_args(args)
        return AppParams.call_with_args(args)

    return main_function


class TestCommandsMain(TestWithFiles):

    def test_add_defaults(self) -> None:
        sys.argv = ['test', 'add']
        self.assertEqual(define_main_commands()(), 2)

    def test_add_foo_defaults(self) -> None:
        sys.argv = ['test', 'add_foo']
        self.assertEqual(define_main_commands()(), 3)

    def test_unknown_command(self) -> None:
        sys.argv = ['test', 'bar']
        self.assertRaisesRegex(RuntimeError,
                               'Unknown .* function: bar',
                               define_main_commands(['bar']))

    def test_positional_command(self) -> None:
        @config
        def bar(foo: int, *, baz: int = 0) -> int:  # pylint: disable=unused-variable
            return foo + baz

        sys.argv = ['test', 'bar']
        self.assertRaisesRegex(RuntimeError,
                               'function: .*.test_positional_command.<locals>.bar .* '
                               'positional arguments',
                               define_main_commands(['bar']))
