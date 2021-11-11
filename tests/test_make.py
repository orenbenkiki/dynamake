"""
Test the make utilities.
"""

# pylint: disable=too-many-lines

import argparse
import asyncio
import os
import sys
from time import sleep
from typing import Callable
from typing import List
from typing import Optional
from typing import Tuple

from testfixtures import LogCapture  # type: ignore

from dynamake import Logger
from dynamake import Parameter
from dynamake import StepException
from dynamake import above
from dynamake import done
from dynamake import make
from dynamake import optional
from dynamake import output
from dynamake import outputs
from dynamake import phony
from dynamake import reading
from dynamake import require
from dynamake import reset
from dynamake import resource_parameters
from dynamake import shell
from dynamake import spawn
from dynamake import step
from dynamake import sync
from dynamake import writing
from tests import TestWithFiles
from tests import TestWithReset
from tests import write_file

# pylint: disable=missing-docstring,too-many-public-methods,no-self-use
# pylint: disable=blacklisted-name,too-few-public-methods


class TestMake(TestWithReset):
    def test_normal_function(self) -> None:
        def define() -> None:
            @step(output="all")
            def function() -> None:  # pylint: disable=unused-variable
                pass

        self.assertRaisesRegex(
            RuntimeError, "test_normal_function.<locals>.define.<locals>.function " "is not a coroutine", define
        )

    def test_no_output(self) -> None:
        def define() -> None:
            @step(output=None)
            async def function() -> None:  # pylint: disable=unused-variable
                pass

        self.assertRaisesRegex(
            RuntimeError, "test_no_output.<locals>.define.<locals>.function " "specifies no output", define
        )

    def test_conflicting_steps(self) -> None:
        @step(output="none")
        async def function() -> None:  # pylint: disable=unused-variable
            pass

        def _register() -> None:
            @step(output="none")
            async def function() -> None:  # pylint: disable=unused-variable
                pass

        self.assertRaisesRegex(
            RuntimeError,
            "Conflicting .* step: function .* "
            "both: .*.test_conflicting_steps.<locals>.function "
            "and: .*._register.<locals>.function",
            _register,
        )

    def test_bad_resources(self) -> None:
        self.assertRaisesRegex(RuntimeError, "Unknown resource parameter: foo", resource_parameters, foo=1)

        self.assertRaisesRegex(
            RuntimeError,
            ".* amount: 1000000 .* resource: jobs .* greater .* amount:",
            resource_parameters,
            jobs=1000000,
        )


class TestMain(TestWithFiles):
    def check(
        self, register: Callable, *, error: Optional[str] = None, log: Optional[List[Tuple[str, str, str]]] = None
    ) -> None:
        reset(is_test=True)
        register()

        sys.argv += ["--log_level", "DEBUG"]

        with LogCapture() as captured_log:
            if error is None:
                make(argparse.ArgumentParser())
            else:
                self.assertRaisesRegex(BaseException, error, make, argparse.ArgumentParser())

        if log is not None:
            captured_log.check(*log)

    def test_no_op(self) -> None:
        def _register() -> None:
            @step(output=phony("all"))
            async def no_op() -> None:  # pylint: disable=unused-variable
                pass

        sys.argv += ["--jobs", "0"]

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - no_op"),
                ("dynamake", "TRACE", "#1 - no_op - Call"),
                (
                    "dynamake",
                    "WHY",
                    "#1 - no_op - Must run actions because missing the persistent actions: "
                    ".dynamake/no_op.actions.yaml",
                ),
                ("dynamake", "DEBUG", "#1 - no_op - Synced"),
                ("dynamake", "DEBUG", "#1 - no_op - Write the persistent actions: .dynamake/no_op.actions.yaml"),
                ("dynamake", "TRACE", "#1 - no_op - Complete"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Complete"),
            ],
        )

    def test_multiple_producers(self) -> None:
        def _register() -> None:
            @step(output=phony("all"))
            async def foo() -> None:  # pylint: disable=unused-variable
                pass

            @step(output=phony("all"))
            async def bar() -> None:  # pylint: disable=unused-variable
                pass

        self.assertRaisesRegex(
            StepException, "output: all .* step: bar .* step: foo .* priority: 0", self.check, _register
        )

    def test_multiple_producers_priorities(self) -> None:
        def _register() -> None:
            @step(output=phony("all"))
            async def bar() -> None:  # pylint: disable=unused-variable
                pass

            @step(output=phony("all"), priority=above("bar"))
            async def foo() -> None:  # pylint: disable=unused-variable
                pass

        sys.argv += ["--jobs", "0"]

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - candidate producer: foo priority: 1"),
                ("dynamake", "DEBUG", "#0 - make - candidate producer: bar priority: 0"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - foo"),
                ("dynamake", "TRACE", "#1 - foo - Call"),
                (
                    "dynamake",
                    "WHY",
                    "#1 - foo - Must run actions because missing the persistent actions: .dynamake/foo.actions.yaml",
                ),
                ("dynamake", "DEBUG", "#1 - foo - Synced"),
                ("dynamake", "DEBUG", "#1 - foo - Write the persistent actions: .dynamake/foo.actions.yaml"),
                ("dynamake", "TRACE", "#1 - foo - Complete"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Complete"),
            ],
        )

    def test_generate_many(self) -> None:
        def _register() -> None:
            foo = Parameter(name="foo", default=1, parser=int, description="foo")

            @step(output=phony("all"))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require("foo.1.1")

            @step(output="foo.{*major}.{*_minor}")
            async def make_foos(major: str) -> None:  # pylint: disable=unused-variable
                Logger.info(f"OUTPUTS: {outputs()}")
                Logger.info(f"OUTPUT: {output()}")
                for minor in range(0, foo.value):
                    await done(asyncio.sleep(2))
                    await shell(f"touch foo.{major}.{minor}")

        sys.argv += ["--jobs", "0", "--foo", "2"]

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                (
                    "dynamake",
                    "WHY",
                    "#1 - make_all - Must run actions because missing the persistent actions: "
                    ".dynamake/make_all.actions.yaml",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo.1.1"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo.1.1 will be produced by "
                    "the spawned: #1.1 - make_foos/major=1",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foos/major=1 - Call"),
                (
                    "dynamake",
                    "WHY",
                    "#1.1 - make_foos/major=1 - Must run actions "
                    "because missing the persistent actions: .dynamake/make_foos/major=1.actions.yaml",
                ),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foos/major=1 - Nonexistent required output(s): foo.{*major}.{*_minor}",
                ),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - OUTPUTS: ['foo.1.{*_minor}']"),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - OUTPUT: foo.1.{*_minor}"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Synced"),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - Run: touch foo.1.0"),
                ("dynamake", "TRACE", "#1.1 - make_foos/major=1 - Success: touch foo.1.0"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Synced"),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - Run: touch foo.1.1"),
                ("dynamake", "TRACE", "#1.1 - make_foos/major=1 - Success: touch foo.1.1"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Synced"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foos/major=1 - Has the output: foo.{*major}.{*_minor} -> foo.1.0 time: 1",
                ),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foos/major=1 - Has the output: foo.{*major}.{*_minor} -> foo.1.1 time: 2",
                ),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foos/major=1 - Write "
                    "the persistent actions: .dynamake/make_foos/major=1.actions.yaml",
                ),
                ("dynamake", "TRACE", "#1.1 - make_foos/major=1 - Done"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo.1.1"),
                ("dynamake", "DEBUG", "#1 - make_all - Write the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "TRACE", "#1 - make_all - Complete"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

        # Do not rebuild without reason.

        sys.argv += ["--log_skipped_actions", "true"]

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Read the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo.1.1"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo.1.1 will be produced by "
                    "the spawned: #1.1 - make_foos/major=1",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foos/major=1 - Call"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foos/major=1 - Read "
                    "the persistent actions: .dynamake/make_foos/major=1.actions.yaml",
                ),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Existing output: foo.{*major}.{*_minor} -> foo.1.0"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Existing output: foo.{*major}.{*_minor} -> foo.1.1"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Oldest output: foo.1.0 time: 1"),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - OUTPUTS: ['foo.1.{*_minor}']"),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - OUTPUT: foo.1.{*_minor}"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - No inputs"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foos/major=1 - Can skip actions "
                    "because all the outputs exist and there are no newer inputs",
                ),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - Skip: touch foo.1.0"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - No inputs"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foos/major=1 - Can skip actions "
                    "because all the outputs exist and there are no newer inputs",
                ),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - Skip: touch foo.1.1"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - No inputs"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foos/major=1 - Has the output: foo.{*major}.{*_minor} -> foo.1.0 time: 1",
                ),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foos/major=1 - Has the output: foo.{*major}.{*_minor} -> foo.1.1 time: 2",
                ),
                ("dynamake", "TRACE", "#1.1 - make_foos/major=1 - Skipped"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo.1.1"),
                ("dynamake", "DEBUG", "#1 - make_all - Write the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "TRACE", "#1 - make_all - Complete"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Skipped"),
            ],
        )

        # Rebuild when some outputs are missing.

        os.remove("foo.1.0")

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Read the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo.1.1"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo.1.1 will be produced by "
                    "the spawned: #1.1 - make_foos/major=1",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foos/major=1 - Call"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foos/major=1 - Read "
                    "the persistent actions: .dynamake/make_foos/major=1.actions.yaml",
                ),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Existing output: foo.{*major}.{*_minor} -> foo.1.1"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Missing the old built output: foo.1.0"),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - OUTPUTS: ['foo.1.{*_minor}']"),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - OUTPUT: foo.1.{*_minor}"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Synced"),
                (
                    "dynamake",
                    "WHY",
                    "#1.1 - make_foos/major=1 - Must run actions to create the missing output(s): foo.1.0",
                ),
                ("dynamake", "FILE", "#1.1 - make_foos/major=1 - Remove the stale output: foo.1.1"),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - Run: touch foo.1.0"),
                ("dynamake", "TRACE", "#1.1 - make_foos/major=1 - Success: touch foo.1.0"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Synced"),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - Run: touch foo.1.1"),
                ("dynamake", "TRACE", "#1.1 - make_foos/major=1 - Success: touch foo.1.1"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Synced"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foos/major=1 - Has the output: foo.{*major}.{*_minor} -> foo.1.0 time: 3",
                ),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foos/major=1 - Has the output: foo.{*major}.{*_minor} -> foo.1.1 time: 4",
                ),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foos/major=1 - Write "
                    "the persistent actions: .dynamake/make_foos/major=1.actions.yaml",
                ),
                ("dynamake", "TRACE", "#1.1 - make_foos/major=1 - Done"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo.1.1"),
                ("dynamake", "DEBUG", "#1 - make_all - Write the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "TRACE", "#1 - make_all - Complete"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

        os.remove("foo.1.0")

        # But do not rebuild if not using persistent state.

        sys.argv += ["--rebuild_changed_actions", "false"]

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo.1.1"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo.1.1 will be produced by "
                    "the spawned: #1.1 - make_foos/major=1",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foos/major=1 - Call"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Existing output: foo.{*major}.{*_minor} -> foo.1.1"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Oldest output: foo.1.1 time: 4"),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - OUTPUTS: ['foo.1.{*_minor}']"),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - OUTPUT: foo.1.{*_minor}"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - No inputs"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foos/major=1 - Can skip actions "
                    "because all the outputs exist and there are no newer inputs",
                ),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - Skip: touch foo.1.0"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - No inputs"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foos/major=1 - Can skip actions "
                    "because all the outputs exist and there are no newer inputs",
                ),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - Skip: touch foo.1.1"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - No inputs"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foos/major=1 - Has the output: foo.{*major}.{*_minor} -> foo.1.1 time: 4",
                ),
                ("dynamake", "TRACE", "#1.1 - make_foos/major=1 - Skipped"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo.1.1"),
                ("dynamake", "TRACE", "#1 - make_all - Complete"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Skipped"),
            ],
        )

        # This can cause a build to fail:

        os.remove("foo.1.1")
        sleep(2)
        write_file("foo.1.0", "!\n")

        self.check(
            _register,
            error="make_all - Failed to build the required target.s.",
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo.1.1"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo.1.1 will be produced by "
                    "the spawned: #1.1 - make_foos/major=1",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foos/major=1 - Call"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Existing output: foo.{*major}.{*_minor} -> foo.1.0"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Oldest output: foo.1.0 time: 5"),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - OUTPUTS: ['foo.1.{*_minor}']"),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - OUTPUT: foo.1.{*_minor}"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - No inputs"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foos/major=1 - Can skip actions "
                    "because all the outputs exist and there are no newer inputs",
                ),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - Skip: touch foo.1.0"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - No inputs"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foos/major=1 - Can skip actions "
                    "because all the outputs exist and there are no newer inputs",
                ),
                ("dynamake", "INFO", "#1.1 - make_foos/major=1 - Skip: touch foo.1.1"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foos/major=1 - No inputs"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foos/major=1 - Has the output: foo.{*major}.{*_minor} -> foo.1.0 time: 5",
                ),
                ("dynamake", "TRACE", "#1.1 - make_foos/major=1 - Skipped"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "ERROR", "#1 - make_all - The required: foo.1.1 has failed to build"),
                ("dynamake", "TRACE", "#1 - make_all - Fail"),
                ("dynamake", "ERROR", "#0 - make - Fail"),
            ],
        )

    def test_copy(self) -> None:
        def _register() -> None:
            @step(output="bar")
            async def copy_foo_to_bar() -> None:  # pylint: disable=unused-variable
                require("foo")
                await done(asyncio.sleep(2))
                await spawn("cp", "foo", "bar")

        sys.argv += ["--jobs", "0"]
        sys.argv += ["--rebuild_changed_actions", "false", "bar"]

        sleep(2)
        write_file("foo", "!\n")

        # Build due to missing output.

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: bar"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: bar"),
                (
                    "dynamake",
                    "DEBUG",
                    "#0 - make - The required: bar will be produced by the spawned: #1 - copy_foo_to_bar",
                ),
                ("dynamake", "TRACE", "#1 - copy_foo_to_bar - Call"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Nonexistent required output(s): bar"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Build the required: foo"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - The required: foo is a source file"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Synced"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Has the required: foo"),
                ("dynamake", "WHY", "#1 - copy_foo_to_bar - Must run actions to create the missing output(s): bar"),
                ("dynamake", "INFO", "#1 - copy_foo_to_bar - Run: cp foo bar"),
                ("dynamake", "TRACE", "#1 - copy_foo_to_bar - Success: cp foo bar"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Synced"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Has the output: bar time: 1"),
                ("dynamake", "TRACE", "#1 - copy_foo_to_bar - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: bar"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

        self.expect_file("bar", "!\n")

        # Skip existing up-to-date output.

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: bar"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: bar"),
                (
                    "dynamake",
                    "DEBUG",
                    "#0 - make - The required: bar will be produced by the spawned: #1 - copy_foo_to_bar",
                ),
                ("dynamake", "TRACE", "#1 - copy_foo_to_bar - Call"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Existing output: bar"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Oldest output: bar time: 1"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Build the required: foo"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - The required: foo is a source file"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Synced"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Newest input: foo time: 0"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - copy_foo_to_bar - Can skip actions "
                    "because all the outputs exist and are newer than all the inputs",
                ),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Skip: cp foo bar"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Synced"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Newest input: foo time: 0"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Has the output: bar time: 1"),
                ("dynamake", "TRACE", "#1 - copy_foo_to_bar - Skipped"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: bar"),
                ("dynamake", "TRACE", "#0 - make - Skipped"),
            ],
        )

        self.expect_file("bar", "!\n")

        sleep(2)
        write_file("foo", "?\n")

        # Rebuild out-of-date output.

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: bar"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: bar"),
                (
                    "dynamake",
                    "DEBUG",
                    "#0 - make - The required: bar will be produced by the spawned: #1 - copy_foo_to_bar",
                ),
                ("dynamake", "TRACE", "#1 - copy_foo_to_bar - Call"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Existing output: bar"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Oldest output: bar time: 1"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Build the required: foo"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - The required: foo is a source file"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Synced"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Newest input: foo time: 2"),
                (
                    "dynamake",
                    "WHY",
                    "#1 - copy_foo_to_bar - Must run actions "
                    "because the output: bar is not newer than the input: foo",
                ),
                ("dynamake", "FILE", "#1 - copy_foo_to_bar - Remove the stale output: bar"),
                ("dynamake", "INFO", "#1 - copy_foo_to_bar - Run: cp foo bar"),
                ("dynamake", "TRACE", "#1 - copy_foo_to_bar - Success: cp foo bar"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Synced"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - copy_foo_to_bar - Has the output: bar time: 3"),
                ("dynamake", "TRACE", "#1 - copy_foo_to_bar - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: bar"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

        self.expect_file("bar", "?\n")

    def test_require_active(self) -> None:
        sys.argv += ["--jobs", "0"]
        sys.argv += ["--rebuild_changed_actions", "false"]

        def _register() -> None:
            @step(output=phony("all"))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require("foo")
                await done(asyncio.sleep(2))
                require("bar")

            @step(output="foo")
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell("sleep 2; touch foo")

            @step(output=phony("bar"))
            async def make_bar() -> None:  # pylint: disable=unused-variable
                require("foo")
                await shell("touch bar")

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo will be produced by the spawned: #1.1 - make_foo",
                ),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Nonexistent required output(s): foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "WHY", "#1.1 - make_foo - Must run actions to create the missing output(s): foo"),
                ("dynamake", "INFO", "#1.1 - make_foo - Run: sleep 2; touch foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: bar"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: bar will be produced by the spawned: #1.2 - make_bar",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "TRACE", "#1.2 - make_bar - Call"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.2 - make_bar - The required: foo will be produced by the spawned: #1.2.1 - make_foo",
                ),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Sync"),
                ("dynamake", "DEBUG", "#1.2.1 - make_foo - Paused by waiting for: #1.1 - make_foo"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Success: sleep 2; touch foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Has the output: foo time: 1"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Done"),
                ("dynamake", "DEBUG", "#1.2.1 - make_foo - Resumed by completion of: #1.1 - make_foo"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Synced"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Has the required: foo"),
                ("dynamake", "WHY", "#1.2 - make_bar - Must run actions to satisfy the phony output: bar"),
                ("dynamake", "INFO", "#1.2 - make_bar - Run: touch bar"),
                ("dynamake", "TRACE", "#1.2 - make_bar - Success: touch bar"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Synced"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Has the required: foo"),
                ("dynamake", "TRACE", "#1.2 - make_bar - Done"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: bar"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "TRACE", "#1 - make_all - Complete"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

    def test_missing_output(self) -> None:
        def _register() -> None:
            @step(output="all")
            async def no_op() -> None:  # pylint: disable=unused-variable
                pass

        sys.argv += ["--jobs", "0"]
        sys.argv += ["--rebuild_changed_actions", "false"]

        self.check(
            _register,
            error="Aborting due to previous error",
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - no_op"),
                ("dynamake", "TRACE", "#1 - no_op - Call"),
                ("dynamake", "DEBUG", "#1 - no_op - Nonexistent required output(s): all"),
                ("dynamake", "DEBUG", "#1 - no_op - Synced"),
                ("dynamake", "ERROR", "#1 - no_op - Missing the output(s): all"),
                ("dynamake", "TRACE", "#1 - no_op - Fail"),
                ("dynamake", "ERROR", "#0 - make - Fail"),
            ],
        )

    def test_remove_empty_directories(self) -> None:
        def _register() -> None:
            @step(output=["foo/bar", "foo/baz"])
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell("mkdir -p foo")
                await shell("touch foo/bar")

        os.makedirs("foo")
        sleep(2)
        write_file("foo/baz", "z")

        sys.argv += ["--jobs", "0"]
        sys.argv += ["--rebuild_changed_actions", "false"]
        sys.argv += ["--remove_empty_directories", "true", "foo/bar"]

        self.check(
            _register,
            error="make_foo - Missing some output.s.",
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: foo/bar"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: foo/bar"),
                (
                    "dynamake",
                    "DEBUG",
                    "#0 - make - The required: foo/bar will be produced by the spawned: #1 - make_foo",
                ),
                ("dynamake", "TRACE", "#1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1 - make_foo - Nonexistent required output(s): foo/bar"),
                ("dynamake", "DEBUG", "#1 - make_foo - Existing output: foo/baz"),
                ("dynamake", "DEBUG", "#1 - make_foo - Synced"),
                ("dynamake", "WHY", "#1 - make_foo - Must run actions to create the missing output(s): foo/bar"),
                ("dynamake", "FILE", "#1 - make_foo - Remove the stale output: foo/baz"),
                ("dynamake", "FILE", "#1 - make_foo - Remove the empty directory: foo"),
                ("dynamake", "INFO", "#1 - make_foo - Run: mkdir -p foo"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1 - make_foo - Success: mkdir -p foo"),
                ("dynamake", "DEBUG", "#1 - make_foo - Synced"),
                ("dynamake", "INFO", "#1 - make_foo - Run: touch foo/bar"),
                ("dynamake", "TRACE", "#1 - make_foo - Success: touch foo/bar"),
                ("dynamake", "DEBUG", "#1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1 - make_foo - Has the output: foo/bar time: 1"),
                ("dynamake", "ERROR", "#1 - make_foo - Missing the output(s): foo/baz"),
                ("dynamake", "FILE", "#1 - make_foo - Remove the failed output: foo/bar"),
                ("dynamake", "FILE", "#1 - make_foo - Remove the empty directory: foo"),
                ("dynamake", "TRACE", "#1 - make_foo - Fail"),
                ("dynamake", "ERROR", "#0 - make - Fail"),
            ],
        )

    def test_remove_stale_outputs(self) -> None:
        def _register() -> None:
            @step(output=["foo", phony("all")])
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell("echo @ > foo")

        write_file("foo", "!\n")

        sys.argv += ["--jobs", "0"]
        sys.argv += ["--rebuild_changed_actions", "false"]

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_foo"),
                ("dynamake", "TRACE", "#1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1 - make_foo - Existing output: foo"),
                ("dynamake", "DEBUG", "#1 - make_foo - Synced"),
                ("dynamake", "WHY", "#1 - make_foo - Must run actions to satisfy the phony output: all"),
                ("dynamake", "FILE", "#1 - make_foo - Remove the stale output: foo"),
                ("dynamake", "INFO", "#1 - make_foo - Run: echo @ > foo"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1 - make_foo - Success: echo @ > foo"),
                ("dynamake", "DEBUG", "#1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1 - make_foo - Has the output: foo time: 1"),
                ("dynamake", "TRACE", "#1 - make_foo - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

        sys.argv += ["--remove_stale_outputs", "false"]

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                (
                    "dynamake",
                    "DEBUG",
                    "#0 - make - The required: all will be produced by the spawned: #1 - make_foo",
                ),
                ("dynamake", "TRACE", "#1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1 - make_foo - Existing output: foo"),
                ("dynamake", "DEBUG", "#1 - make_foo - Synced"),
                ("dynamake", "WHY", "#1 - make_foo - Must run actions to satisfy the phony output: all"),
                ("dynamake", "INFO", "#1 - make_foo - Run: echo @ > foo"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1 - make_foo - Success: echo @ > foo"),
                ("dynamake", "DEBUG", "#1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1 - make_foo - Has the output: foo time: 2"),
                ("dynamake", "TRACE", "#1 - make_foo - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

    def test_phony_dependencies(self) -> None:
        def _register() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                require("foo")
                await shell("touch all")

            @step(output=phony("foo"))
            async def make_foo() -> None:  # pylint: disable=unused-variable
                require("bar")
                await shell("true")

        write_file("bar", "0\n")
        sys.argv += ["--jobs", "0"]
        sys.argv += ["--rebuild_changed_actions", "false"]

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Nonexistent required output(s): all"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo will be produced by the spawned: #1.1 - make_foo",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Build the required: bar"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - The required: bar is a source file"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Has the required: bar"),
                ("dynamake", "WHY", "#1.1 - make_foo - Must run actions to satisfy the phony output: foo"),
                ("dynamake", "INFO", "#1.1 - make_foo - Run: true"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Success: true"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Has the required: bar"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Done"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "WHY", "#1 - make_all - Must run actions to create the missing output(s): all"),
                ("dynamake", "INFO", "#1 - make_all - Run: touch all"),
                ("dynamake", "TRACE", "#1 - make_all - Success: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 1"),
                ("dynamake", "TRACE", "#1 - make_all - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Existing output: all"),
                ("dynamake", "DEBUG", "#1 - make_all - Oldest output: all time: 1"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo will be produced by the spawned: #1.1 - make_foo",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Build the required: bar"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - The required: bar is a source file"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Has the required: bar"),
                ("dynamake", "WHY", "#1.1 - make_foo - Must run actions to satisfy the phony output: foo"),
                ("dynamake", "INFO", "#1.1 - make_foo - Run: true"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Success: true"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Has the required: bar"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Done"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Newest input: foo time: 0"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - Can skip actions because all the outputs exist "
                    "and are newer than all the inputs",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Skip: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Newest input: foo time: 0"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 1"),
                ("dynamake", "TRACE", "#1 - make_all - Skipped"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

        sleep(2)
        write_file("bar", "0\n")

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Existing output: all"),
                ("dynamake", "DEBUG", "#1 - make_all - Oldest output: all time: 1"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo will be produced by the spawned: #1.1 - make_foo",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Build the required: bar"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - The required: bar is a source file"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Has the required: bar"),
                ("dynamake", "WHY", "#1.1 - make_foo - Must run actions to satisfy the phony output: foo"),
                ("dynamake", "INFO", "#1.1 - make_foo - Run: true"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Success: true"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Has the required: bar"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Done"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Newest input: foo time: 2"),
                (
                    "dynamake",
                    "WHY",
                    "#1 - make_all - Must run actions because the output: all is not newer than the input: foo",
                ),
                ("dynamake", "FILE", "#1 - make_all - Remove the stale output: all"),
                ("dynamake", "INFO", "#1 - make_all - Run: touch all"),
                ("dynamake", "TRACE", "#1 - make_all - Success: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 3"),
                ("dynamake", "TRACE", "#1 - make_all - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

    def test_failed_action(self) -> None:
        def _register() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell("false")

        sys.argv += ["--jobs", "0"]
        sys.argv += ["--rebuild_changed_actions", "false"]

        self.check(
            _register,
            error="make_all - Failure: false",
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Nonexistent required output(s): all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "WHY", "#1 - make_all - Must run actions to create the missing output(s): all"),
                ("dynamake", "INFO", "#1 - make_all - Run: false"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "ERROR", "#1 - make_all - Failure: false"),
                ("dynamake", "TRACE", "#1 - make_all - Fail"),
                ("dynamake", "ERROR", "#0 - make - Fail"),
            ],
        )

    def test_stop_on_failure(self) -> None:
        def _register() -> None:
            @step(output=phony("all"))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require("foo")
                await done(asyncio.sleep(2))
                require("bar")
                await shell("true")

            @step(output="foo")
            async def make_foo() -> None:  # pylint: disable=unused-variable
                require("baz")
                await shell("touch foo")

            @step(output="bar")
            async def make_bar() -> None:  # pylint: disable=unused-variable
                require("baz")
                await shell("touch bar")

            @step(output="baz")
            async def make_baz() -> None:  # pylint: disable=unused-variable
                await shell("touch baz")
                await shell("false")

        sys.argv += ["--jobs", "0"]
        sys.argv += ["--rebuild_changed_actions", "false"]

        self.check(
            _register,
            error="Aborting due to previous error",
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo will be produced by the spawned: #1.1 - make_foo",
                ),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Nonexistent required output(s): foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Build the required: baz"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foo - The required: baz will be produced by the spawned: #1.1.1 - make_baz",
                ),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Sync"),
                ("dynamake", "TRACE", "#1.1.1 - make_baz - Call"),
                ("dynamake", "DEBUG", "#1.1.1 - make_baz - Nonexistent required output(s): baz"),
                ("dynamake", "DEBUG", "#1.1.1 - make_baz - Synced"),
                ("dynamake", "WHY", "#1.1.1 - make_baz - Must run actions to create the missing output(s): baz"),
                ("dynamake", "INFO", "#1.1.1 - make_baz - Run: touch baz"),
                ("dynamake", "TRACE", "#1.1.1 - make_baz - Success: touch baz"),
                ("dynamake", "DEBUG", "#1.1.1 - make_baz - Synced"),
                ("dynamake", "INFO", "#1.1.1 - make_baz - Run: false"),
                ("dynamake", "ERROR", "#1.1.1 - make_baz - Failure: false"),
                ("dynamake", "FILE", "#1.1.1 - make_baz - Remove the failed output: baz"),
                ("dynamake", "TRACE", "#1.1.1 - make_baz - Fail"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Fail"),
                ("dynamake", "TRACE", "#1 - make_all - Fail"),
                ("dynamake", "ERROR", "#0 - make - Fail"),
            ],
        )

    def test_continue_on_failure(self) -> None:
        def _register() -> None:
            @step(output=phony("all"))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require("foo")
                await done(asyncio.sleep(2))
                require("bar")
                await shell("true")

            @step(output="foo")
            async def make_foo() -> None:  # pylint: disable=unused-variable
                require("baz")
                await shell("touch foo")

            @step(output="bar")
            async def make_bar() -> None:  # pylint: disable=unused-variable
                require("baz")
                await shell("touch bar")

            @step(output="baz")
            async def make_baz() -> None:  # pylint: disable=unused-variable
                await shell("touch baz")
                await shell("false")

        sys.argv += ["--jobs", "0"]
        sys.argv += ["--rebuild_changed_actions", "false"]
        sys.argv += ["--failure_aborts_build", "false"]

        self.check(
            _register,
            error="Failed to build the required target.s.",
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo will be produced by the spawned: #1.1 - make_foo",
                ),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Nonexistent required output(s): foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Build the required: baz"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foo - The required: baz will be produced by the spawned: #1.1.1 - make_baz",
                ),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Sync"),
                ("dynamake", "TRACE", "#1.1.1 - make_baz - Call"),
                ("dynamake", "DEBUG", "#1.1.1 - make_baz - Nonexistent required output(s): baz"),
                ("dynamake", "DEBUG", "#1.1.1 - make_baz - Synced"),
                ("dynamake", "WHY", "#1.1.1 - make_baz - Must run actions to create the missing output(s): baz"),
                ("dynamake", "INFO", "#1.1.1 - make_baz - Run: touch baz"),
                ("dynamake", "TRACE", "#1.1.1 - make_baz - Success: touch baz"),
                ("dynamake", "DEBUG", "#1.1.1 - make_baz - Synced"),
                ("dynamake", "INFO", "#1.1.1 - make_baz - Run: false"),
                ("dynamake", "ERROR", "#1.1.1 - make_baz - Failure: false"),
                ("dynamake", "DEBUG", "#1.1.1 - make_baz - Synced"),
                ("dynamake", "DEBUG", "#1.1.1 - make_baz - Has the output: baz time: 1"),
                ("dynamake", "FILE", "#1.1.1 - make_baz - Remove the failed output: baz"),
                ("dynamake", "TRACE", "#1.1.1 - make_baz - Fail"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - The required: baz has failed to build"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Can't run: touch foo"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Fail"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: bar"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: bar will be produced by the spawned: #1.2 - make_bar",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "TRACE", "#1.2 - make_bar - Call"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Nonexistent required output(s): bar"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Build the required: baz"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Synced"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - The required: baz has failed to build"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Can't run: touch bar"),
                ("dynamake", "TRACE", "#1.2 - make_bar - Fail"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - The required: bar has failed to build"),
                ("dynamake", "DEBUG", "#1 - make_all - The required: foo has failed to build"),
                ("dynamake", "DEBUG", "#1 - make_all - Can't run: true"),
                ("dynamake", "TRACE", "#1 - make_all - Fail"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - The required: all has failed to build"),
                ("dynamake", "ERROR", "#0 - make - Fail"),
            ],
        )

    def test_remove_failed_outputs(self) -> None:
        def _register() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                require("foo")
                await shell("echo @ > all; false")

        write_file("foo", "!\n")

        sys.argv += ["--jobs", "0"]
        sys.argv += ["--rebuild_changed_actions", "false"]

        self.check(
            _register,
            error="make_all - Failure: echo @ > all; false",
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Nonexistent required output(s): all"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - The required: foo is a source file"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "WHY", "#1 - make_all - Must run actions to create the missing output(s): all"),
                ("dynamake", "INFO", "#1 - make_all - Run: echo @ > all; false"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "ERROR", "#1 - make_all - Failure: echo @ > all; false"),
                ("dynamake", "FILE", "#1 - make_all - Remove the failed output: all"),
                ("dynamake", "TRACE", "#1 - make_all - Fail"),
                ("dynamake", "ERROR", "#0 - make - Fail"),
            ],
        )

        sys.argv += ["--remove_failed_outputs", "false"]

        sleep(2)
        write_file("all", "?\n")
        sleep(2)
        write_file("foo", "!\n")

        self.check(
            _register,
            error="make_all - Failure: echo @ > all; false",
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Existing output: all"),
                ("dynamake", "DEBUG", "#1 - make_all - Oldest output: all time: 1"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - The required: foo is a source file"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Newest input: foo time: 2"),
                (
                    "dynamake",
                    "WHY",
                    "#1 - make_all - Must run actions because the output: all is not newer than the input: foo",
                ),
                ("dynamake", "FILE", "#1 - make_all - Remove the stale output: all"),
                ("dynamake", "INFO", "#1 - make_all - Run: echo @ > all; false"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "ERROR", "#1 - make_all - Failure: echo @ > all; false"),
                ("dynamake", "TRACE", "#1 - make_all - Fail"),
                ("dynamake", "ERROR", "#0 - make - Fail"),
            ],
        )

        self.expect_file("all", "@\n")

    def test_touch_success_outputs(self) -> None:
        def _register() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell("touch all")

        sys.argv += ["--jobs", "0"]
        sys.argv += ["--rebuild_changed_actions", "false"]
        sys.argv += ["--touch_success_outputs", "true"]

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Nonexistent required output(s): all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "WHY", "#1 - make_all - Must run actions to create the missing output(s): all"),
                ("dynamake", "INFO", "#1 - make_all - Run: touch all"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1 - make_all - Success: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "FILE", "#1 - make_all - Touch the output: all"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 1"),
                ("dynamake", "TRACE", "#1 - make_all - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

    def test_built_dependencies(self) -> None:
        def _register() -> None:
            @step(output=phony("all"))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require("foo")
                await sync()
                require("bar")

            @step(output="foo")
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell("touch foo")

            @step(output="bar")
            async def make_bar() -> None:  # pylint: disable=unused-variable
                require("foo")
                await shell("touch bar")

        sys.argv += ["--jobs", "0"]
        sys.argv += ["--rebuild_changed_actions", "false"]

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo will be produced by the spawned: #1.1 - make_foo",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Nonexistent required output(s): foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "WHY", "#1.1 - make_foo - Must run actions to create the missing output(s): foo"),
                ("dynamake", "INFO", "#1.1 - make_foo - Run: touch foo"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Success: touch foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Has the output: foo time: 1"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Done"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: bar"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: bar will be produced by the spawned: #1.2 - make_bar",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "TRACE", "#1.2 - make_bar - Call"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Nonexistent required output(s): bar"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Build the required: foo"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - The required: foo was built"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Synced"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Has the required: foo"),
                ("dynamake", "WHY", "#1.2 - make_bar - Must run actions to create the missing output(s): bar"),
                ("dynamake", "INFO", "#1.2 - make_bar - Run: touch bar"),
                ("dynamake", "TRACE", "#1.2 - make_bar - Success: touch bar"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Synced"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Has the required: foo"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Has the output: bar time: 2"),
                ("dynamake", "TRACE", "#1.2 - make_bar - Done"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: bar"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "TRACE", "#1 - make_all - Complete"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

    def test_missing_input(self) -> None:
        def _register() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                require("foo")

            @step(output="foo")
            async def make_foo() -> None:  # pylint: disable=unused-variable
                require("bar")

        sys.argv += ["--jobs", "0"]
        sys.argv += ["--rebuild_changed_actions", "false"]

        self.check(
            _register,
            error="Aborting due to previous error",
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Nonexistent required output(s): all"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo will be produced by the spawned: #1.1 - make_foo",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Nonexistent required output(s): foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Build the required: bar"),
                ("dynamake", "ERROR", "#1.1 - make_foo - Don't know how to make the target: bar"),
                ("dynamake", "ERROR", "#1.1 - make_foo - invoked to produce the target: foo"),
                ("dynamake", "ERROR", "#1.1 - make_foo - required by the step: #1 - make_all"),
                ("dynamake", "ERROR", "#1.1 - make_foo - invoked to produce the target: all"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Fail"),
                ("dynamake", "TRACE", "#1 - make_all - Fail"),
                ("dynamake", "ERROR", "#0 - make - Fail"),
            ],
        )

        sys.argv += ["foo"]

        self.check(
            _register,
            error="Aborting due to previous error",
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: foo"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#0 - make - The required: foo will be produced by the spawned: #1 - make_foo",
                ),
                ("dynamake", "TRACE", "#1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1 - make_foo - Nonexistent required output(s): foo"),
                ("dynamake", "DEBUG", "#1 - make_foo - Build the required: bar"),
                ("dynamake", "ERROR", "#1 - make_foo - Don't know how to make the target: bar"),
                ("dynamake", "ERROR", "#1 - make_foo - invoked to produce the target: foo"),
                ("dynamake", "TRACE", "#1 - make_foo - Fail"),
                ("dynamake", "ERROR", "#0 - make - Fail"),
            ],
        )

    def test_require_optional(self) -> None:
        sys.argv += ["--jobs", "0"]
        sys.argv += ["--rebuild_changed_actions", "false"]

        def _register() -> None:
            @step(output=phony("all"))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require(optional("foo"))
                require(optional("baz"))

            @step(output=[optional("foo"), "bar"])
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell("touch bar")

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo will be produced by the spawned: #1.1 - make_foo",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: baz"),
                ("dynamake", "DEBUG", "#1 - make_all - The optional required: baz does not exist and can't be built"),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Nonexistent required output(s): bar"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Nonexistent optional output(s): foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "WHY", "#1.1 - make_foo - Must run actions to create the missing output(s): bar"),
                ("dynamake", "INFO", "#1.1 - make_foo - Run: touch bar"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Success: touch bar"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Has the output: bar time: 1"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Did not make the optional output(s): foo"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Done"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "TRACE", "#1 - make_all - Complete"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo will be produced by the spawned: #1.1 - make_foo",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: baz"),
                ("dynamake", "DEBUG", "#1 - make_all - The optional required: baz does not exist and can't be built"),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Existing output: bar"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Nonexistent optional output(s): foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Oldest output: bar time: 1"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - No inputs"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foo - Can skip actions because all the outputs exist and there are no newer inputs",
                ),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Skip: touch bar"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - No inputs"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Has the output: bar time: 1"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Did not make the optional output(s): foo"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Skipped"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "TRACE", "#1 - make_all - Complete"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Skipped"),
            ],
        )

    def test_optional_output(self) -> None:

        sys.argv += ["--jobs", "0"]
        sys.argv += ["--rebuild_changed_actions", "false"]

        def _register_without() -> None:
            @step(output=phony("all"))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require("foo")

            @step(output="foo")
            async def make_foo() -> None:  # pylint: disable=unused-variable
                pass

        self.check(
            _register_without,
            error="Aborting due to previous error",
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo will be produced by the spawned: #1.1 - make_foo",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Nonexistent required output(s): foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "ERROR", "#1.1 - make_foo - Missing the output(s): foo"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Fail"),
                ("dynamake", "TRACE", "#1 - make_all - Fail"),
                ("dynamake", "ERROR", "#0 - make - Fail"),
            ],
        )

        def _register_with_input() -> None:
            @step(output=phony("all"))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require(optional("foo"))

            @step(output="foo")
            async def make_foo() -> None:  # pylint: disable=unused-variable
                pass

        self.check(
            _register_without,
            error="Aborting due to previous error",
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo will be produced by the spawned: #1.1 - make_foo",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Nonexistent required output(s): foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "ERROR", "#1.1 - make_foo - Missing the output(s): foo"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Fail"),
                ("dynamake", "TRACE", "#1 - make_all - Fail"),
                ("dynamake", "ERROR", "#0 - make - Fail"),
            ],
        )

        def _register_with_output() -> None:
            @step(output=phony("all"))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require("foo")

            @step(output=optional("foo"))
            async def make_foo() -> None:  # pylint: disable=unused-variable
                pass

        self.check(
            _register_with_output,
            error="Failed to build",
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo will be produced by the spawned: #1.1 - make_foo",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Nonexistent optional output(s): foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Did not make the optional output(s): foo"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Complete"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "ERROR", "#1 - make_all - The required: foo has failed to build"),
                ("dynamake", "TRACE", "#1 - make_all - Fail"),
                ("dynamake", "ERROR", "#0 - make - Fail"),
            ],
        )

        def _register_with_both() -> None:
            @step(output=phony("all"))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require(optional("foo"))

            @step(output=optional("foo"))
            async def make_foo() -> None:  # pylint: disable=unused-variable
                pass

        self.check(
            _register_with_both,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo will be produced by the spawned: #1.1 - make_foo",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Nonexistent optional output(s): foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Did not make the optional output(s): foo"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Complete"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "TRACE", "#1 - make_all - Complete"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Complete"),
            ],
        )

    def test_remove_persistent_data(self) -> None:
        def _register() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell("false")

        os.mkdir(".dynamake")
        write_file(".dynamake/make_all.actions.yaml", "*invalid")

        sys.argv += ["--jobs", "0"]

        self.check(
            _register,
            error="make_all - Failure: false",
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                (
                    "dynamake",
                    "WARNING",
                    "#1 - make_all - Must run actions because read "
                    "the invalid persistent actions: .dynamake/make_all.actions.yaml",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Nonexistent required output(s): all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "INFO", "#1 - make_all - Run: false"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "ERROR", "#1 - make_all - Failure: false"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - Remove the persistent actions: .dynamake/make_all.actions.yaml",
                ),
                ("dynamake", "TRACE", "#1 - make_all - Fail"),
                ("dynamake", "ERROR", "#0 - make - Fail"),
            ],
        )

        self.check(
            _register,
            error="make_all - Failure: false",
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                (
                    "dynamake",
                    "WHY",
                    "#1 - make_all - Must run actions because missing "
                    "the persistent actions: .dynamake/make_all.actions.yaml",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Nonexistent required output(s): all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "INFO", "#1 - make_all - Run: false"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "ERROR", "#1 - make_all - Failure: false"),
                ("dynamake", "TRACE", "#1 - make_all - Fail"),
                ("dynamake", "ERROR", "#0 - make - Fail"),
            ],
        )

    def test_remove_parameterized_persistent_data(self) -> None:
        def _register() -> None:
            @step(output="{*name}")
            async def make_all(**kwargs: str) -> None:  # pylint: disable=unused-variable,unused-argument
                await shell("false")

        os.makedirs(".dynamake/make_all")
        write_file(".dynamake/make_all/name=all.actions.yaml", "*invalid")

        sys.argv += ["--jobs", "0"]

        self.check(
            _register,
            error="make_all/name=all - Failure: false",
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                (
                    "dynamake",
                    "DEBUG",
                    "#0 - make - The required: all will be produced by the spawned: #1 - make_all/name=all",
                ),
                ("dynamake", "TRACE", "#1 - make_all/name=all - Call"),
                (
                    "dynamake",
                    "WARNING",
                    "#1 - make_all/name=all - Must run actions because read "
                    "the invalid persistent actions: .dynamake/make_all/name=all.actions.yaml",
                ),
                ("dynamake", "DEBUG", "#1 - make_all/name=all - Nonexistent required output(s): {*name}"),
                ("dynamake", "DEBUG", "#1 - make_all/name=all - Synced"),
                ("dynamake", "INFO", "#1 - make_all/name=all - Run: false"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "ERROR", "#1 - make_all/name=all - Failure: false"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all/name=all - Remove "
                    "the persistent actions: .dynamake/make_all/name=all.actions.yaml",
                ),
                ("dynamake", "TRACE", "#1 - make_all/name=all - Fail"),
                ("dynamake", "ERROR", "#0 - make - Fail"),
            ],
        )

        self.check(
            _register,
            error="make_all/name=all - Failure: false",
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                (
                    "dynamake",
                    "DEBUG",
                    "#0 - make - The required: all will be produced by the spawned: #1 - make_all/name=all",
                ),
                ("dynamake", "TRACE", "#1 - make_all/name=all - Call"),
                (
                    "dynamake",
                    "WHY",
                    "#1 - make_all/name=all - Must run actions because missing "
                    "the persistent actions: .dynamake/make_all/name=all.actions.yaml",
                ),
                ("dynamake", "DEBUG", "#1 - make_all/name=all - Nonexistent required output(s): {*name}"),
                ("dynamake", "DEBUG", "#1 - make_all/name=all - Synced"),
                ("dynamake", "INFO", "#1 - make_all/name=all - Run: false"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "ERROR", "#1 - make_all/name=all - Failure: false"),
                ("dynamake", "TRACE", "#1 - make_all/name=all - Fail"),
                ("dynamake", "ERROR", "#0 - make - Fail"),
            ],
        )

    def test_add_final_action(self) -> None:
        def _register_without() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell("touch all")

        sys.argv += ["--jobs", "0"]

        self.check(
            _register_without,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                (
                    "dynamake",
                    "WHY",
                    "#1 - make_all - Must run actions because missing "
                    "the persistent actions: .dynamake/make_all.actions.yaml",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Nonexistent required output(s): all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "INFO", "#1 - make_all - Run: touch all"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1 - make_all - Success: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 1"),
                ("dynamake", "DEBUG", "#1 - make_all - Write the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "TRACE", "#1 - make_all - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

        def _register_with() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell("touch all")
                await shell("true")

        self.check(
            _register_with,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Read the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "DEBUG", "#1 - make_all - Existing output: all"),
                ("dynamake", "DEBUG", "#1 - make_all - Oldest output: all time: 1"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - No inputs"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - Can skip actions because all the outputs exist and there are no newer inputs",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Skip: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - No inputs"),
                ("dynamake", "WHY", "#1 - make_all - Must run actions because changed to add action(s)"),
                ("dynamake", "DEBUG", "#1 - make_all - Must restart step to run skipped action(s)"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "FILE", "#1 - make_all - Remove the stale output: all"),
                ("dynamake", "INFO", "#1 - make_all - Run: touch all"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1 - make_all - Success: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "INFO", "#1 - make_all - Run: true"),
                ("dynamake", "TRACE", "#1 - make_all - Success: true"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 2"),
                ("dynamake", "DEBUG", "#1 - make_all - Write the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "TRACE", "#1 - make_all - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

    def test_abandon_early_action(self) -> None:
        def _register_with() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell("true")
                await shell("touch all")

        sys.argv += ["--jobs", "0"]

        self.check(
            _register_with,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                (
                    "dynamake",
                    "WHY",
                    "#1 - make_all - Must run actions because missing "
                    "the persistent actions: .dynamake/make_all.actions.yaml",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Nonexistent required output(s): all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "INFO", "#1 - make_all - Run: true"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1 - make_all - Success: true"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "INFO", "#1 - make_all - Run: touch all"),
                ("dynamake", "TRACE", "#1 - make_all - Success: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 1"),
                ("dynamake", "DEBUG", "#1 - make_all - Write the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "TRACE", "#1 - make_all - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

        def _register_without() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell("touch all")

        self.check(
            _register_without,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Read the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "DEBUG", "#1 - make_all - Existing output: all"),
                ("dynamake", "DEBUG", "#1 - make_all - Oldest output: all time: 1"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - No inputs"),
                (
                    "dynamake",
                    "WHY",
                    "#1 - make_all - Must run actions because changed the command: true into the command: touch all",
                ),
                ("dynamake", "FILE", "#1 - make_all - Remove the stale output: all"),
                ("dynamake", "INFO", "#1 - make_all - Run: touch all"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1 - make_all - Success: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 2"),
                ("dynamake", "DEBUG", "#1 - make_all - Write the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "TRACE", "#1 - make_all - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

    def test_abandon_output(self) -> None:
        def _register_with() -> None:
            @step(output=["all", "foo"])
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell("touch all; sleep 1; touch foo")

        sys.argv += ["--jobs", "0"]

        self.check(
            _register_with,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                (
                    "dynamake",
                    "WHY",
                    "#1 - make_all - Must run actions because missing the persistent actions: "
                    ".dynamake/make_all.actions.yaml",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Nonexistent required output(s): all"),
                ("dynamake", "DEBUG", "#1 - make_all - Nonexistent required output(s): foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "INFO", "#1 - make_all - Run: touch all; sleep 1; touch foo"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1 - make_all - Success: touch all; sleep 1; touch foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 1"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: foo time: 2"),
                ("dynamake", "DEBUG", "#1 - make_all - Write the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "TRACE", "#1 - make_all - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

        os.remove("all")

        self.check(
            _register_with,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Read the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "DEBUG", "#1 - make_all - Nonexistent required output(s): all"),
                ("dynamake", "DEBUG", "#1 - make_all - Existing output: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "WHY", "#1 - make_all - Must run actions to create the missing output(s): all"),
                ("dynamake", "FILE", "#1 - make_all - Remove the stale output: foo"),
                ("dynamake", "INFO", "#1 - make_all - Run: touch all; sleep 1; touch foo"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1 - make_all - Success: touch all; sleep 1; touch foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 3"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: foo time: 4"),
                ("dynamake", "DEBUG", "#1 - make_all - Write the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "TRACE", "#1 - make_all - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

        def _register_without() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell("touch all")

        self.check(
            _register_without,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Read the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "DEBUG", "#1 - make_all - Existing output: all"),
                ("dynamake", "DEBUG", "#1 - make_all - Changed to abandon the output: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "WHY", "#1 - make_all - Must run actions because changed to abandon the output: foo"),
                ("dynamake", "FILE", "#1 - make_all - Remove the stale output: all"),
                ("dynamake", "INFO", "#1 - make_all - Run: touch all"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1 - make_all - Success: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 5"),
                ("dynamake", "DEBUG", "#1 - make_all - Write the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "TRACE", "#1 - make_all - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

    def test_abandon_final_action(self) -> None:
        def _register_with() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell("touch all")
                await shell("true")

        sys.argv += ["--jobs", "0"]

        self.check(
            _register_with,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                (
                    "dynamake",
                    "WHY",
                    "#1 - make_all - Must run actions because missing "
                    "the persistent actions: .dynamake/make_all.actions.yaml",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Nonexistent required output(s): all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "INFO", "#1 - make_all - Run: touch all"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1 - make_all - Success: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "INFO", "#1 - make_all - Run: true"),
                ("dynamake", "TRACE", "#1 - make_all - Success: true"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 1"),
                ("dynamake", "DEBUG", "#1 - make_all - Write the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "TRACE", "#1 - make_all - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

        def _register_without() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell("touch all")

        self.check(
            _register_without,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Read the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "DEBUG", "#1 - make_all - Existing output: all"),
                ("dynamake", "DEBUG", "#1 - make_all - Oldest output: all time: 1"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - No inputs"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - Can skip actions because all the outputs exist and there are no newer inputs",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Skip: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - No inputs"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 1"),
                (
                    "dynamake",
                    "WARNING",
                    "#1 - make_all - Skipped some action(s) even though changed to remove some final action(s)",
                ),
                ("dynamake", "TRACE", "#1 - make_all - Skipped"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Skipped"),
            ],
        )

    def test_add_required(self) -> None:
        def _register_without() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell("touch all")

        write_file("foo", "!\n")

        sys.argv += ["--jobs", "0"]

        self.check(
            _register_without,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                (
                    "dynamake",
                    "WHY",
                    "#1 - make_all - Must run actions because missing "
                    "the persistent actions: .dynamake/make_all.actions.yaml",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Nonexistent required output(s): all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "INFO", "#1 - make_all - Run: touch all"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1 - make_all - Success: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 1"),
                ("dynamake", "DEBUG", "#1 - make_all - Write the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "TRACE", "#1 - make_all - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

        def _register_with() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                require("foo")
                await shell("touch all")

        self.check(
            _register_with,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Read the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "DEBUG", "#1 - make_all - Existing output: all"),
                ("dynamake", "DEBUG", "#1 - make_all - Oldest output: all time: 1"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - The required: foo is a source file"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Newest input: foo time: 0"),
                ("dynamake", "WHY", "#1 - make_all - Must run actions because changed to require: foo"),
                ("dynamake", "FILE", "#1 - make_all - Remove the stale output: all"),
                ("dynamake", "INFO", "#1 - make_all - Run: touch all"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1 - make_all - Success: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 2"),
                ("dynamake", "DEBUG", "#1 - make_all - Write the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "TRACE", "#1 - make_all - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

    def test_remove_required(self) -> None:
        def _register_with() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                require("foo")
                await shell("touch all")

        write_file("foo", "!\n")

        sys.argv += ["--jobs", "0"]

        self.check(
            _register_with,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                (
                    "dynamake",
                    "WHY",
                    "#1 - make_all - Must run actions because missing "
                    "the persistent actions: .dynamake/make_all.actions.yaml",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Nonexistent required output(s): all"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - The required: foo is a source file"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "INFO", "#1 - make_all - Run: touch all"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1 - make_all - Success: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 1"),
                ("dynamake", "DEBUG", "#1 - make_all - Write the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "TRACE", "#1 - make_all - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

        def _register_without() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell("touch all")

        self.check(
            _register_without,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Read the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "DEBUG", "#1 - make_all - Existing output: all"),
                ("dynamake", "DEBUG", "#1 - make_all - Oldest output: all time: 1"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - No inputs"),
                ("dynamake", "WHY", "#1 - make_all - Must run actions because changed to not require: foo"),
                ("dynamake", "FILE", "#1 - make_all - Remove the stale output: all"),
                ("dynamake", "INFO", "#1 - make_all - Run: touch all"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1 - make_all - Success: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 2"),
                ("dynamake", "DEBUG", "#1 - make_all - Write the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "TRACE", "#1 - make_all - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

    def test_touch_required(self) -> None:
        def _register() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                require("foo")
                await done(asyncio.sleep(2))
                await shell("touch all")

        write_file("foo", "0\n")

        sys.argv += ["--jobs", "0"]

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                (
                    "dynamake",
                    "WHY",
                    "#1 - make_all - Must run actions because missing "
                    "the persistent actions: .dynamake/make_all.actions.yaml",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Nonexistent required output(s): all"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - The required: foo is a source file"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "INFO", "#1 - make_all - Run: touch all"),
                ("dynamake", "TRACE", "#1 - make_all - Success: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 1"),
                ("dynamake", "DEBUG", "#1 - make_all - Write the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "TRACE", "#1 - make_all - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

        sleep(2)
        write_file("foo", "1\n")

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Read the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "DEBUG", "#1 - make_all - Existing output: all"),
                ("dynamake", "DEBUG", "#1 - make_all - Oldest output: all time: 1"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - The required: foo is a source file"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Newest input: foo time: 2"),
                (
                    "dynamake",
                    "WHY",
                    "#1 - make_all - Must run actions because the modification time of "
                    "the required: foo has changed from: 0 into: 2",
                ),
                ("dynamake", "FILE", "#1 - make_all - Remove the stale output: all"),
                ("dynamake", "INFO", "#1 - make_all - Run: touch all"),
                ("dynamake", "TRACE", "#1 - make_all - Success: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 3"),
                ("dynamake", "DEBUG", "#1 - make_all - Write the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "TRACE", "#1 - make_all - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

    def test_change_required_producer(self) -> None:
        def _register_without() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                require("foo")
                await shell("touch all")

        write_file("foo", "!\n")

        sys.argv += ["--jobs", "0"]

        self.check(
            _register_without,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                (
                    "dynamake",
                    "WHY",
                    "#1 - make_all - Must run actions because missing "
                    "the persistent actions: .dynamake/make_all.actions.yaml",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Nonexistent required output(s): all"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - The required: foo is a source file"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "INFO", "#1 - make_all - Run: touch all"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1 - make_all - Success: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 1"),
                ("dynamake", "DEBUG", "#1 - make_all - Write the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "TRACE", "#1 - make_all - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

        def _register_with() -> None:
            @step(output="all")
            async def make_all() -> None:  # pylint: disable=unused-variable
                require("foo")
                await shell("touch all")

            @step(output="foo")
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell("touch foo")

        self.check(
            _register_with,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Read the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "DEBUG", "#1 - make_all - Existing output: all"),
                ("dynamake", "DEBUG", "#1 - make_all - Oldest output: all time: 1"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo will be produced by the spawned: #1.1 - make_foo",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Call"),
                (
                    "dynamake",
                    "WHY",
                    "#1.1 - make_foo - Must run actions because missing "
                    "the persistent actions: .dynamake/make_foo.actions.yaml",
                ),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Existing output: foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "FILE", "#1.1 - make_foo - Remove the stale output: foo"),
                ("dynamake", "INFO", "#1.1 - make_foo - Run: touch foo"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Success: touch foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Has the output: foo time: 2"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1.1 - make_foo - Write the persistent actions: .dynamake/make_foo.actions.yaml",
                ),
                ("dynamake", "TRACE", "#1.1 - make_foo - Done"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Newest input: foo time: 2"),
                (
                    "dynamake",
                    "WHY",
                    "#1 - make_all - Must run actions because the producer of "
                    "the required: foo has changed from: source file into: make_foo",
                ),
                ("dynamake", "FILE", "#1 - make_all - Remove the stale output: all"),
                ("dynamake", "INFO", "#1 - make_all - Run: touch all"),
                ("dynamake", "TRACE", "#1 - make_all - Success: touch all"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the output: all time: 3"),
                ("dynamake", "DEBUG", "#1 - make_all - Write the persistent actions: .dynamake/make_all.actions.yaml"),
                ("dynamake", "TRACE", "#1 - make_all - Done"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

    def test_resources(self) -> None:
        def _register() -> None:
            @step(output=phony("all"))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require("foo")
                require("bar")

            @step(output="foo")
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell("sleep 1; touch foo")

            @step(output="bar")
            async def make_bar() -> None:  # pylint: disable=unused-variable
                await shell("sleep 2; touch bar")

        sys.argv += ["--jobs", "1", "--rebuild_changed_actions", "false"]

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Available resources: jobs=1"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo will be produced by the spawned: #1.1 - make_foo",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: bar"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: bar will be produced by the spawned: #1.2 - make_bar",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Nonexistent required output(s): foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "WHY", "#1.1 - make_foo - Must run actions to create the missing output(s): foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Grab resources: jobs=1"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Available resources: jobs=0"),
                ("dynamake", "INFO", "#1.1 - make_foo - Run: sleep 1; touch foo"),
                ("dynamake", "TRACE", "#1.2 - make_bar - Call"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Nonexistent required output(s): bar"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Synced"),
                ("dynamake", "WHY", "#1.2 - make_bar - Must run actions to create the missing output(s): bar"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Available resources: jobs=0"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Paused by waiting for resources: jobs=1"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Success: sleep 1; touch foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Free resources: jobs=1"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Available resources: jobs=1"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Has the output: foo time: 1"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Done"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Grab resources: jobs=1"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Available resources: jobs=0"),
                ("dynamake", "INFO", "#1.2 - make_bar - Run: sleep 2; touch bar"),
                ("dynamake", "TRACE", "#1.2 - make_bar - Success: sleep 2; touch bar"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Free resources: jobs=1"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Available resources: jobs=1"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Synced"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Has the output: bar time: 2"),
                ("dynamake", "TRACE", "#1.2 - make_bar - Done"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: bar"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "TRACE", "#1 - make_all - Complete"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

    def test_custom_resources(self) -> None:
        def _register() -> None:
            Parameter(name="foo", default=2, parser=int, description="foo")

            resource_parameters(foo=1)

            @step(output=phony("all"))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require("foo")
                await done(asyncio.sleep(2))
                require("bar")

            @step(output="foo")
            async def make_foo() -> None:  # pylint: disable=unused-variable
                await shell("sleep 4; touch foo", foo=2)

            @step(output="bar")
            async def make_bar() -> None:  # pylint: disable=unused-variable
                await shell("sleep 2; touch bar", jobs=0)

        write_file("DynaMake.yaml", "jobs: 8\n")

        sys.argv += ["--rebuild_changed_actions", "false"]

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Available resources: foo=2,jobs=8"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo will be produced by the spawned: #1.1 - make_foo",
                ),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Nonexistent required output(s): foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "WHY", "#1.1 - make_foo - Must run actions to create the missing output(s): foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Grab resources: foo=2,jobs=1"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Available resources: foo=0,jobs=7"),
                ("dynamake", "INFO", "#1.1 - make_foo - Run: sleep 4; touch foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: bar"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: bar will be produced by the spawned: #1.2 - make_bar",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "TRACE", "#1.2 - make_bar - Call"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Nonexistent required output(s): bar"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Synced"),
                ("dynamake", "WHY", "#1.2 - make_bar - Must run actions to create the missing output(s): bar"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Available resources: foo=0,jobs=7"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Paused by waiting for resources: foo=1"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Success: sleep 4; touch foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Free resources: foo=2,jobs=1"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Available resources: foo=2,jobs=8"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Has the output: foo time: 1"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Done"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Grab resources: foo=1"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Available resources: foo=1,jobs=8"),
                ("dynamake", "INFO", "#1.2 - make_bar - Run: sleep 2; touch bar"),
                ("dynamake", "TRACE", "#1.2 - make_bar - Success: sleep 2; touch bar"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Free resources: foo=1"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Available resources: foo=2,jobs=8"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Synced"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Has the output: bar time: 2"),
                ("dynamake", "TRACE", "#1.2 - make_bar - Done"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: bar"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "TRACE", "#1 - make_all - Complete"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )

    def test_unknown_resources(self) -> None:
        def _register() -> None:
            @step(output=phony("all"))
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell("true", foo=2)

        sys.argv += ["--jobs", "0"]
        sys.argv += ["--rebuild_changed_actions", "false"]

        self.check(
            _register,
            error="unknown resource: foo",
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "WHY", "#1 - make_all - Must run actions to satisfy the phony output: all"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
            ],
        )

    def test_request_too_much_resources(self) -> None:
        def _register() -> None:
            @step(output=phony("all"))
            async def make_all() -> None:  # pylint: disable=unused-variable
                await shell("true", jobs=1000000)

        sys.argv += ["--jobs", "8"]
        sys.argv += ["--rebuild_changed_actions", "false"]

        self.check(
            _register,
            error="resource: jobs amount: 1000000 .* greater .* amount:",
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Available resources: jobs=8"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                ("dynamake", "DEBUG", "#0 - make - The required: all will be produced by the spawned: #1 - make_all"),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "WHY", "#1 - make_all - Must run actions to satisfy the phony output: all"),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
            ],
        )

    def test_async_rwlock(self) -> None:
        def _register() -> None:
            @step(output=phony("all"))
            async def make_all() -> None:  # pylint: disable=unused-variable
                require("foo")
                await done(asyncio.sleep(2))
                require("bar")
                require("baz")

            @step(output="foo")
            async def make_foo() -> None:  # pylint: disable=unused-variable
                async with reading("db"):
                    await shell("sleep 4; touch foo")

            @step(output="bar")
            async def make_bar() -> None:  # pylint: disable=unused-variable
                async with reading("db"):
                    await shell("touch bar")

            @step(output="baz")
            async def make_baz() -> None:  # pylint: disable=unused-variable
                async with writing("db"):
                    await shell("sleep 2; touch baz", jobs=0)

        sys.argv += ["--jobs", "0", "--rebuild_changed_actions", "false"]

        self.check(
            _register,
            log=[
                ("dynamake", "TRACE", "#0 - make - Targets: all"),
                ("dynamake", "DEBUG", "#0 - make - Build the required: all"),
                (
                    "dynamake",
                    "DEBUG",
                    "#0 - make - The required: all will be produced by the spawned: #1 - make_all",
                ),
                ("dynamake", "TRACE", "#1 - make_all - Call"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: foo"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: foo will be produced by the spawned: #1.1 - make_foo",
                ),
                ("dynamake", "DEBUG", "#0 - make - Sync"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Call"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Nonexistent required output(s): foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "WHY", "#1.1 - make_foo - Must run actions to create the missing output(s): foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Want read lock of: db"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Got read lock of: db"),
                ("dynamake", "INFO", "#1.1 - make_foo - Run: sleep 4; touch foo"),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: bar"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: bar will be produced by the spawned: #1.2 - make_bar",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Build the required: baz"),
                (
                    "dynamake",
                    "DEBUG",
                    "#1 - make_all - The required: baz will be produced by the spawned: #1.3 - make_baz",
                ),
                ("dynamake", "DEBUG", "#1 - make_all - Sync"),
                ("dynamake", "TRACE", "#1.2 - make_bar - Call"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Nonexistent required output(s): bar"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Synced"),
                ("dynamake", "WHY", "#1.2 - make_bar - Must run actions to create the missing output(s): bar"),
                ("dynamake", "TRACE", "#1.3 - make_baz - Call"),
                ("dynamake", "DEBUG", "#1.3 - make_baz - Nonexistent required output(s): baz"),
                ("dynamake", "DEBUG", "#1.3 - make_baz - Synced"),
                ("dynamake", "WHY", "#1.3 - make_baz - Must run actions to create the missing output(s): baz"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Want read lock of: db"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - step: #1.1 - make_foo is reading data: db"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Got read lock of: db"),
                ("dynamake", "INFO", "#1.2 - make_bar - Run: touch bar"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Released read lock of: db"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - step: #1.1 - make_foo is reading data: db"),
                ("dynamake", "TRACE", "#1.2 - make_bar - Success: touch bar"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Synced"),
                ("dynamake", "DEBUG", "#1.2 - make_bar - Has the output: bar time: 1"),
                ("dynamake", "TRACE", "#1.2 - make_bar - Done"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Released read lock of: db"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Success: sleep 4; touch foo"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Synced"),
                ("dynamake", "DEBUG", "#1.1 - make_foo - Has the output: foo time: 2"),
                ("dynamake", "TRACE", "#1.1 - make_foo - Done"),
                ("dynamake", "DEBUG", "#1.3 - make_baz - Want write lock of: db"),
                ("dynamake", "DEBUG", "#1.3 - make_baz - Got write lock of: db"),
                ("dynamake", "INFO", "#1.3 - make_baz - Run: sleep 2; touch baz"),
                ("dynamake", "DEBUG", "#1.3 - make_baz - Released write lock of: db"),
                ("dynamake", "TRACE", "#1.3 - make_baz - Success: sleep 2; touch baz"),
                ("dynamake", "DEBUG", "#1.3 - make_baz - Synced"),
                ("dynamake", "DEBUG", "#1.3 - make_baz - Has the output: baz time: 3"),
                ("dynamake", "TRACE", "#1.3 - make_baz - Done"),
                ("dynamake", "DEBUG", "#1 - make_all - Synced"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: bar"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: baz"),
                ("dynamake", "DEBUG", "#1 - make_all - Has the required: foo"),
                ("dynamake", "TRACE", "#1 - make_all - Complete"),
                ("dynamake", "DEBUG", "#0 - make - Synced"),
                ("dynamake", "DEBUG", "#0 - make - Has the required: all"),
                ("dynamake", "TRACE", "#0 - make - Done"),
            ],
        )
