"""
Test the make utilities.
"""

from dynamake.make import Make
from dynamake.make import step
from tests import TestWithFiles

# pylint: disable=missing-docstring,too-many-public-methods,no-self-use
# pylint: disable=blacklisted-name,too-few-public-methods


class TestMake(TestWithFiles):

    def test_call_function(self) -> None:
        called_function = False

        @step()
        def function() -> None:
            nonlocal called_function
            called_function = True

        Make.main(function)
        self.assertTrue(called_function)

    def test_call_static_method(self) -> None:

        class Klass:
            called_static_method = False

            @step()
            @staticmethod
            def static_method() -> None:
                Klass.called_static_method = True

        Make.main(Klass.static_method)
        self.assertTrue(Klass.called_static_method)
