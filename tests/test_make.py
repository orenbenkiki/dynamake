"""
Test the make utilities.
"""

from typing import Any
from typing import Dict
from typing import List
from unittest import TestCase

from dynamake.make import Make
from dynamake.make import action
from dynamake.make import expand
from dynamake.make import foreach
from dynamake.make import glob
from dynamake.make import plan
from tests import TestWithFiles
from tests import write_file

# pylint: disable=missing-docstring,too-many-public-methods,no-self-use
# pylint: disable=blacklisted-name,too-few-public-methods


class TestMake(TestCase):

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
        def expander(foo: str, *, bar: str) -> str:  # pylint: disable=unused-argument
            return expand('{foo}/{bar}')

        self.assertEqual(expander('a', bar='b'), 'a/b')

    def test_foreach_in_step(self) -> None:

        def collect(*args: str, **kwargs: Dict[str, Any]) -> str:
            return '%s %s' % (args, kwargs)

        @plan()
        def expander(foo: str, bar: int, *, baz: str, vaz: int) -> List[str]:  # pylint: disable=unused-argument
            return foreach([{'wild': 2}], collect, '{foo}', bar,
                           baz='{baz}', vaz=vaz, wild='{wild}')

        self.assertEqual(expander('a', 0, baz='b', vaz=1),
                         ["('a', 0) {'baz': 'b', 'vaz': 1, 'wild': '2'}"])


class TestGlob(TestWithFiles):

    def test_glob(self) -> None:
        @plan()
        def globber(foo: str) -> List[Dict[str, Any]]:  # pylint: disable=unused-argument
            return glob('{foo}.{*bar}')

        self.assertEqual(globber('x'), [])

        write_file('x.a', '')

        self.assertEqual(globber('x'), [{'bar': 'a'}])
