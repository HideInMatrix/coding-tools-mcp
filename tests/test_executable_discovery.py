from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_workbench.executables.models import (
    ExecutableCandidate,
    ExecutableSpec,
)
from agent_workbench.executables.resolver import ExecutableResolver
from agent_workbench.executables.verification import verify_executable


TEST_SPEC = ExecutableSpec(
    key="python-test",
    display_name="Python Test",
    executable_name="python-test",
    bundled_product="python-test",
    version_args=("--version",),
    version_markers=("python",),
)


def candidate(path: str, source: str) -> ExecutableCandidate:
    return ExecutableCandidate(
        path=Path(path),
        source=source,
        version="1.2.3",
    )


class ExecutableResolverTests(unittest.TestCase):
    def test_manual_path_has_highest_priority(self) -> None:
        resolver = ExecutableResolver()
        with (
            patch("agent_workbench.executables.resolver.Path.is_file", return_value=True),
            patch(
                "agent_workbench.executables.resolver.verify_executable",
                return_value=candidate("/manual/tool", "manual"),
            ) as verify,
            patch("agent_workbench.executables.resolver.bundled_candidate") as bundled,
        ):
            result = resolver.resolve(TEST_SPEC, configured="/manual/tool")
        self.assertEqual(result.source, "manual")
        bundled.assert_not_called()
        self.assertEqual(verify.call_args.kwargs["source"], "manual")

    def test_bundled_client_precedes_system_detection(self) -> None:
        resolver = ExecutableResolver()
        with (
            patch(
                "agent_workbench.executables.resolver.bundled_candidate",
                return_value=Path("/bundle/tool"),
            ),
            patch(
                "agent_workbench.executables.resolver.verify_executable",
                return_value=candidate("/bundle/tool", "bundled"),
            ) as verify,
            patch("agent_workbench.executables.resolver.standard_candidates") as standard,
            patch("agent_workbench.executables.resolver.path_candidate") as path_lookup,
        ):
            result = resolver.resolve(TEST_SPEC)
        self.assertEqual(result.source, "bundled")
        self.assertEqual(verify.call_args.kwargs["source"], "bundled")
        standard.assert_not_called()
        path_lookup.assert_not_called()

    def test_standard_location_precedes_path(self) -> None:
        resolver = ExecutableResolver()
        with (
            patch(
                "agent_workbench.executables.resolver.bundled_candidate",
                return_value=None,
            ),
            patch(
                "agent_workbench.executables.resolver.standard_candidates",
                return_value=[Path("/standard/tool")],
            ),
            patch(
                "agent_workbench.executables.resolver.verify_executable",
                return_value=candidate("/standard/tool", "standard"),
            ),
            patch("agent_workbench.executables.resolver.path_candidate") as path_lookup,
        ):
            result = resolver.resolve(TEST_SPEC)
        self.assertEqual(result.source, "standard")
        path_lookup.assert_not_called()

    def test_auto_only_ignores_saved_manual_override(self) -> None:
        resolver = ExecutableResolver()
        with (
            patch(
                "agent_workbench.executables.resolver.bundled_candidate",
                return_value=Path("/bundle/tool"),
            ),
            patch(
                "agent_workbench.executables.resolver.verify_executable",
                return_value=candidate("/bundle/tool", "bundled"),
            ) as verify,
        ):
            result = resolver.resolve(
                TEST_SPEC,
                configured="/old/manual/tool",
                auto_only=True,
            )
        self.assertEqual(result.source, "bundled")
        self.assertEqual(verify.call_args.args[1], Path("/bundle/tool"))


class ExecutableVerificationTests(unittest.TestCase):
    def test_version_probe_uses_argument_array_without_shell(self) -> None:
        spec = ExecutableSpec(
            key="python",
            display_name="Python",
            executable_name="python",
            bundled_product="python",
            version_args=("--version",),
            version_markers=("python",),
        )
        result = verify_executable(spec, Path(sys.executable), source="manual")
        self.assertTrue(result.verified)
        self.assertIn(".", result.version)

    def test_nonzero_version_probe_is_rejected(self) -> None:
        spec = ExecutableSpec(
            key="broken",
            display_name="Broken Client",
            executable_name="broken",
            bundled_product="broken",
            version_args=("--version",),
        )
        with (
            patch(
                "agent_workbench.executables.verification._safe_real_path",
                return_value=Path("/broken/client"),
            ),
            patch(
                "agent_workbench.executables.verification._run_probe",
                return_value=(127, "missing target"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "退出码: 127"):
                verify_executable(spec, Path("/broken/client"), source="path")


if __name__ == "__main__":
    unittest.main()
