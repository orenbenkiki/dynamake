"""
Test the application utilities.
"""

import argparse
import sys

from dynamake.application import ConfigArgs
from dynamake.application import config
from tests import TestWithFiles
from tests import write_file

# pylint: disable=missing-docstring,too-many-public-methods,no-self-use
# pylint: disable=blacklisted-name,too-few-public-methods


def main_function() -> int:
    parser = argparse.ArgumentParser(description='Test')
    parser.add_argument('--bad', action='store_true', help='Call bad')
    ConfigArgs.current = ConfigArgs({'bar': (1, int, 'The number of bars')})
    ConfigArgs.current.add_to_parser(parser)
    args = parser.parse_args()
    ConfigArgs.current.parse(args)
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


class TestArgs(TestWithFiles):

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

    def test_argument(self) -> None:
        write_file('config.yaml', '{bar: 1}')
        sys.argv = ['test', '--bar', '2', '--config', 'config.yaml']
        self.assertEqual(main_function(), 3)

    def test_non_mapping(self) -> None:
        write_file('config.yaml', '[]')
        sys.argv = ['test', '--config', 'config.yaml']
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               'file: config.yaml .* top-level mapping',
                               main_function)

    def test_invalid_argument_value(self) -> None:
        sys.argv = ['test', '--bar', 'x']
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               'value: x .* argument: bar',
                               main_function)

    def test_invalid_config_value(self) -> None:
        write_file('config.yaml', '{bar: x}')
        sys.argv = ['test', '--config', 'config.yaml']
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               'value: x .* argument: bar',
                               main_function)

    def test_unknown_argument(self) -> None:
        write_file('config.yaml', '{baz: x}')
        sys.argv = ['test', '--config', 'config.yaml']
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               'argument: baz .* file: config.yaml',
                               main_function)

    def test_bad_argument(self) -> None:
        sys.argv = ['test', '--bad']
        self.assertRaisesRegex(RuntimeError,  # type: ignore
                               'argument: baz .* function: tests.test_application.bad',
                               main_function)
