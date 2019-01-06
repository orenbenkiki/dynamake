"""
Test the application utilities.
"""

import argparse
import sys

from dynamake.application import ApplicationParameters
from dynamake.application import config
from tests import TestWithFiles
from tests import write_file

# pylint: disable=missing-docstring,too-many-public-methods,no-self-use
# pylint: disable=blacklisted-name,too-few-public-methods


def main_function() -> int:
    parser = argparse.ArgumentParser(description='Test')
    parser.add_argument('--bad', action='store_true', help='Call bad')
    ApplicationParameters.current = ApplicationParameters({'bar': (1, int, 'The number of bars')})
    ApplicationParameters.current.add_to_parser(parser)
    args = parser.parse_args()
    ApplicationParameters.current.parse_args(args)
    if args.bad:
        bad()
    return add(1) + Foo.add(0, bar=0)


class Foo:
    @config
    @staticmethod
    def add(foo: int, *, bar: int = 0) -> int:
        return foo + bar


@config
def add(foo: int, *, bar: int = 0) -> int:
    return foo + bar


@config
def bad(*, baz: int = 0) -> int:
    return baz


class TestApplication(TestWithFiles):

    def test_defaults(self) -> None:
        sys.argv = ['test']
        self.assertEqual(main_function(), 2)

    def test_empty_config(self) -> None:
        write_file('config.yaml', '')
        sys.argv = ['test', '--config', 'config.yaml']
        self.assertEqual(main_function(), 2)

    def test_one_config(self) -> None:
        write_file('config.yaml', '{bar: 2}')
        sys.argv = ['test', '--config', 'config.yaml']
        self.assertEqual(main_function(), 3)

    def test_two_configs(self) -> None:
        write_file('one.yaml', '{bar: 1}')
        write_file('two.yaml', '{bar: 2}')
        sys.argv = ['test', '--config', 'one.yaml', '--config', 'two.yaml']
        self.assertEqual(main_function(), 3)

    def test_parameter(self) -> None:
        write_file('config.yaml', '{bar: 1}')
        sys.argv = ['test', '--bar', '2', '--config', 'config.yaml']
        self.assertEqual(main_function(), 3)

    def test_top_non_mapping(self) -> None:
        write_file('config.yaml', '[]')
        sys.argv = ['test', '--config', 'config.yaml']
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               'file: config.yaml .* top-level mapping',
                               main_function)

    def test_invalid_parameter_value(self) -> None:
        sys.argv = ['test', '--bar', 'x']
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               'value: x .* parameter: bar',
                               main_function)

    def test_invalid_config_value(self) -> None:
        write_file('config.yaml', '{bar: x}')
        sys.argv = ['test', '--config', 'config.yaml']
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               'value: x .* parameter: bar',
                               main_function)

    def test_conflicting_parameter(self) -> None:
        write_file('config.yaml', '{bar: 1, "bar?": 1}')
        sys.argv = ['test', '--config', 'config.yaml']
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               '.* both: bar and: bar\\? .* file: config.yaml',
                               main_function)

    def test_allowed_known_parameter(self) -> None:
        write_file('config.yaml', '{"bar?": 1}')
        sys.argv = ['test', '--config', 'config.yaml']
        self.assertEqual(main_function(), 2)

    def test_allowed_unknown_parameter(self) -> None:
        write_file('config.yaml', '{"baz?": x}')
        sys.argv = ['test', '--config', 'config.yaml']
        self.assertEqual(main_function(), 2)

    def test_forbidden_unknown_parameter(self) -> None:
        write_file('config.yaml', '{baz: x}')
        sys.argv = ['test', '--config', 'config.yaml']
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               'parameter: baz .* file: config.yaml',
                               main_function)

    def test_bad_parameter(self) -> None:
        sys.argv = ['test', '--bad']
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               'parameter: baz .* function: tests.test_application.bad',
                               main_function)
