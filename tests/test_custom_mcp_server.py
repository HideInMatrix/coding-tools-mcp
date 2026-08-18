from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from mcp_tools_server import __compatibility_baseline__, __version__
from mcp_tools_server.oauth import (
    OAUTH_TOKEN_TTL_SECONDS,
    OAuthConfig,
    client_from_metadata_document,
    create_access_token,
    valid_pkce_challenge,
    validate_access_token,
)
from mcp_tools_server.protocol import (
    META_CLIENT_CAPABILITIES,
    META_PROTOCOL_VERSION,
    dispatch,
)
from mcp_tools_server.runtime import Runtime
from mcp_tools_server.sandbox.backend import WindowsRestrictedTokenBackend
from mcp_tools_server.server import MCPHTTPServer
from mcp_tools_server.server import _normalize_public_server_url
from mcp_tools_server.server import _resolve_oauth_client
from mcp_tools_server.toolchains import ToolchainResolver


class CustomMCPServerContractTests(unittest.TestCase):
    def test_project_owned_version(self) -> None:
        self.assertEqual(__version__, "0.1.0")
        self.assertEqual(__compatibility_baseline__, "0.3.0")

    def test_oauth_defaults_match_030_compatibility_baseline(self) -> None:
        self.assertEqual(OAUTH_TOKEN_TTL_SECONDS, 24 * 60 * 60)
        self.assertTrue(valid_pkce_challenge("A" * 43))
        self.assertFalse(valid_pkce_challenge("A" * 44))
        self.assertFalse(valid_pkce_challenge("~" * 43))

        config = OAuthConfig(
            password="password",
            server_url="https://mcp.example.com",
            token_secret=b"x" * 32,
        )
        with self.assertRaises(ValueError):
            config.registry.register({"redirect_uris": ["myapp://callback"]})

    def test_dcr_accepts_and_echoes_application_type(self) -> None:
        config = OAuthConfig(
            password="password",
            server_url="https://mcp.example.com",
            token_secret=b"x" * 32,
        )
        registered = config.registry.register(
            {
                "redirect_uris": ["http://127.0.0.1/callback"],
                "token_endpoint_auth_method": "none",
                "application_type": "native",
            }
        )
        self.assertEqual(registered["application_type"], "native")
        client = config.registry.get(registered["client_id"])
        self.assertIsNotNone(client)
        assert client is not None
        self.assertEqual(client.application_type, "native")

    def test_cimd_document_requires_exact_https_client_id(self) -> None:
        client_id = "https://client.example.com/oauth/metadata.json"
        client = client_from_metadata_document(
            client_id,
            {
                "client_id": client_id,
                "client_name": "Example Client",
                "redirect_uris": ["https://client.example.com/oauth/callback"],
                "token_endpoint_auth_method": "none",
            },
        )
        self.assertEqual(client.client_id, client_id)
        with self.assertRaises(ValueError):
            client_from_metadata_document(
                client_id,
                {
                    "client_id": "https://other.example.com/client.json",
                    "client_name": "Wrong Client",
                    "redirect_uris": ["https://client.example.com/oauth/callback"],
                },
            )

    def test_cimd_client_is_resolved_on_demand_and_cached(self) -> None:
        client_id = "https://client.example.com/oauth/metadata.json"
        metadata = {
            "client_id": client_id,
            "client_name": "Claude",
            "redirect_uris": ["https://claude.example.com/oauth/callback"],
            "token_endpoint_auth_method": "none",
        }
        config = OAuthConfig(
            password="password",
            server_url="https://mcp.example.com",
            token_secret=b"x" * 32,
        )
        with patch(
            "mcp_tools_server.server._fetch_cimd_document",
            return_value=(metadata, 300),
        ) as fetch:
            first = _resolve_oauth_client(config, client_id)
            second = _resolve_oauth_client(config, client_id)
        self.assertIsNotNone(first)
        self.assertIs(first, second)
        fetch.assert_called_once_with(client_id)

    def test_public_server_url_accepts_base_or_full_mcp_url(self) -> None:
        self.assertEqual(
            _normalize_public_server_url("https://mcp.example.com"),
            "https://mcp.example.com",
        )
        self.assertEqual(
            _normalize_public_server_url("https://mcp.example.com/mcp"),
            "https://mcp.example.com",
        )
        self.assertEqual(
            _normalize_public_server_url("https://mcp.example.com/mcp/"),
            "https://mcp.example.com",
        )

    def test_every_exposed_tool_has_input_and_output_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                tools = runtime.list_tools()["tools"]
            finally:
                runtime.close()

        self.assertEqual(len(tools), 20)
        for tool in tools:
            with self.subTest(tool=tool["name"]):
                self.assertIsInstance(tool.get("inputSchema"), dict)
                output_schema = tool.get("outputSchema")
                self.assertIsInstance(output_schema, dict)
                self.assertEqual(output_schema.get("type"), "object")
                self.assertIn("ok", output_schema.get("properties", {}))
                self.assertIn("ok", output_schema.get("required", []))
                self.assertIsInstance(tool.get("annotations"), dict)

    def test_tool_call_returns_structured_content_and_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                result = runtime.call_tool("server_info", {})
            finally:
                runtime.close()

        self.assertFalse(result["isError"])
        self.assertTrue(result["structuredContent"]["ok"])
        self.assertIsInstance(result["content"], list)

    def test_unexpected_tool_exception_is_returned_as_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                with self.assertLogs("mcp_tools_server.runtime", level="ERROR"):
                    with patch.object(
                        Runtime,
                        "server_info",
                        side_effect=ExceptionGroup("reader failure", [RuntimeError("boom")]),
                    ):
                        result = runtime.call_tool("server_info", {})
            finally:
                runtime.close()

        self.assertTrue(result["isError"])
        error = result["structuredContent"]["error"]
        self.assertEqual(error["code"], "INTERNAL_TOOL_ERROR")
        self.assertEqual(error["details"]["exception_type"], "ExceptionGroup")

    def test_legacy_initialize_and_tools_list(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                initialized = dispatch(
                    runtime,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-11-25",
                            "capabilities": {},
                            "clientInfo": {"name": "unit-test", "version": "1"},
                        },
                    },
                )
                listed = dispatch(
                    runtime,
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                )
            finally:
                runtime.close()

        self.assertEqual(initialized["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(len(listed["result"]["tools"]), 20)

    def test_legacy_null_params_are_treated_as_empty_object(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                response = dispatch(
                    runtime,
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": None},
                )
            finally:
                runtime.close()

        self.assertEqual(len(response["result"]["tools"]), 20)

    def test_invalid_json_rpc_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                response = dispatch(
                    runtime,
                    {"jsonrpc": "2.0", "id": True, "method": "ping", "params": {}},
                )
            finally:
                runtime.close()

        self.assertEqual(response["error"]["code"], -32600)

    def test_initialize_requires_non_null_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                response = dispatch(
                    runtime,
                    {"jsonrpc": "2.0", "id": None, "method": "initialize", "params": {}},
                )
            finally:
                runtime.close()

        self.assertEqual(response["error"]["code"], -32600)

    def test_modern_client_capabilities_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                response = dispatch(
                    runtime,
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/list",
                        "params": {"_meta": {META_PROTOCOL_VERSION: "2026-07-28"}},
                    },
                )
            finally:
                runtime.close()

        self.assertEqual(response["error"]["code"], -32602)

    def test_tool_call_accepts_null_arguments_as_empty_object(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                response = dispatch(
                    runtime,
                    {
                        "jsonrpc": "2.0",
                        "id": 5,
                        "method": "tools/call",
                        "params": {"name": "server_info", "arguments": None},
                    },
                )
            finally:
                runtime.close()

        self.assertFalse(response["result"]["isError"])

    def test_modern_tools_list_marks_complete_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                response = dispatch(
                    runtime,
                    {
                        "jsonrpc": "2.0",
                        "id": "modern",
                        "method": "tools/list",
                        "params": {
                            "_meta": {
                                META_PROTOCOL_VERSION: "2026-07-28",
                                META_CLIENT_CAPABILITIES: {},
                            }
                        },
                    },
                )
            finally:
                runtime.close()

        result = response["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["cacheScope"], "private")
        self.assertEqual(result["ttlMs"], 0)

    def test_unexpected_dispatch_exception_returns_json_rpc_internal_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                with self.assertLogs("mcp_tools_server.protocol", level="ERROR"):
                    with patch.object(
                        Runtime,
                        "list_tools",
                        side_effect=ExceptionGroup("list failure", [RuntimeError("boom")]),
                    ):
                        response = dispatch(
                            runtime,
                            {"jsonrpc": "2.0", "id": 99, "method": "tools/list", "params": {}},
                        )
            finally:
                runtime.close()

        self.assertEqual(response["error"]["code"], -32603)
        self.assertEqual(response["error"]["data"]["exception_type"], "ExceptionGroup")


class RuntimeSafetyTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX fixture uses executable shell scripts")
    def test_toolchain_discovery_finds_nvm_without_sourcing_shell_rc(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            workspace = base / "workspace"
            home = base / "home"
            workspace.mkdir()
            home.mkdir()
            (workspace / ".nvmrc").write_text("25.7.0\n", encoding="utf-8")
            marker = base / "shell-rc-was-executed"
            (home / ".zshrc").write_text(f"touch {marker}\n", encoding="utf-8")
            bin_dir = home / ".nvm" / "versions" / "node" / "v25.7.0" / "bin"
            bin_dir.mkdir(parents=True)
            node = bin_dir / "node"
            npm = bin_dir / "npm"
            node.write_text("#!/bin/sh\necho v25.7.0\n", encoding="utf-8")
            npm.write_text("#!/bin/sh\necho 11.5.0\n", encoding="utf-8")
            node.chmod(0o755)
            npm.chmod(0o755)

            resolver = ToolchainResolver(workspace, home=home)
            result = resolver.discover(["node"])

        selected = result["toolchains"]["node"]["selected"]
        self.assertEqual(selected["version"], "25.7.0")
        self.assertEqual(selected["source"], "nvm")
        self.assertEqual(selected["executables"]["npm"], str(npm.resolve()))
        self.assertFalse(marker.exists())

    def test_safe_exec_path_does_not_globally_trust_workspace_bin_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            local_bin = workspace / "node_modules" / ".bin"
            local_bin.mkdir(parents=True)
            resolver = ToolchainResolver(workspace)

            safe_path = resolver.safe_path_entries()

        self.assertNotIn(str(local_bin.resolve()), safe_path)

    @unittest.skipIf(os.name == "nt", "POSIX fixture uses executable shell scripts")
    def test_exec_process_uses_validated_toolchain_path_without_login_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            workspace = base / "workspace"
            home = base / "home"
            workspace.mkdir()
            home.mkdir()
            (workspace / ".nvmrc").write_text("25.7.0\n", encoding="utf-8")
            bin_dir = home / ".nvm" / "versions" / "node" / "v25.7.0" / "bin"
            bin_dir.mkdir(parents=True)
            node = bin_dir / "node"
            npm = bin_dir / "npm"
            node.write_text("#!/bin/sh\necho v25.7.0\n", encoding="utf-8")
            npm.write_text("#!/bin/sh\necho npm-ok \"$@\"\n", encoding="utf-8")
            node.chmod(0o755)
            npm.chmod(0o755)

            with patch.dict(
                os.environ,
                {"HOME": str(home), "PATH": "/usr/bin:/bin"},
                clear=False,
            ):
                runtime = Runtime(workspace, permission_mode="safe")
                try:
                    result = runtime.call_tool(
                        "exec_process",
                        {
                            "program": "npm",
                            "args": ["run", "build"],
                            "yield_time_ms": 2_000,
                        },
                    )
                    environment = runtime.call_tool(
                        "check_exec_environment",
                        {},
                    )["structuredContent"]
                finally:
                    runtime.close()

        payload = result["structuredContent"]
        self.assertFalse(result["isError"])
        self.assertEqual(payload["exit_code"], 0)
        self.assertIn("npm-ok run build", payload["stdout"])
        self.assertFalse(payload["shell"])
        self.assertIn(str(bin_dir.resolve()), environment["effective_path"])
        self.assertIn("process.execute", environment["sandbox"]["capabilities"])

    def test_exec_process_blocks_known_network_package_commands_in_safe_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary), permission_mode="safe")
            try:
                result = runtime.call_tool(
                    "exec_process",
                    {"program": "npm", "args": ["install"]},
                )
            finally:
                runtime.close()

        self.assertTrue(result["isError"])
        error = result["structuredContent"]["error"]
        self.assertEqual(error["code"], "PERMISSION_REQUIRED")
        self.assertEqual(error["details"]["permission"], "network")

    def test_safe_mode_rejects_overriding_sandbox_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary), permission_mode="safe")
            try:
                result = runtime.call_tool(
                    "exec_command",
                    {
                        "cmd": "printf hello",
                        "env": {"HOME": str(Path.home()), "PATH": "/tmp"},
                    },
                )
            finally:
                runtime.close()

        self.assertTrue(result["isError"])
        error = result["structuredContent"]["error"]
        self.assertEqual(error["details"]["permission"], "sandbox_env_override")
        self.assertEqual(set(error["details"]["variables"]), {"HOME", "PATH"})

    def test_sandbox_environment_protection_is_case_insensitive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary), permission_mode="safe")
            try:
                result = runtime.call_tool(
                    "exec_command",
                    {
                        "cmd": "printf hello",
                        "env": {"path": "/tmp/attacker-bin"},
                    },
                )
            finally:
                runtime.close()

        self.assertTrue(result["isError"])
        error = result["structuredContent"]["error"]
        self.assertEqual(error["details"]["permission"], "sandbox_env_override")
        self.assertEqual(error["details"]["variables"], ["path"])

    @unittest.skipUnless(os.name == "nt", "Windows environment layout test")
    def test_windows_sandbox_redirects_user_profile_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary), permission_mode="safe")
            try:
                environment = runtime._command_env({})
                sandbox_home = str(runtime.commands.home_dir)
            finally:
                runtime.close()

        self.assertEqual(environment["USERPROFILE"], sandbox_home)
        self.assertTrue(environment["APPDATA"].startswith(sandbox_home))
        self.assertTrue(environment["LOCALAPPDATA"].startswith(sandbox_home))

    def test_safe_mode_blocks_plain_environment_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary), permission_mode="safe")
            try:
                result = runtime.call_tool(
                    "exec_command",
                    {"cmd": "printf $HOME"},
                )
            finally:
                runtime.close()

        self.assertTrue(result["isError"])
        error = result["structuredContent"]["error"]
        self.assertEqual(error["details"]["permission"], "shell_expansion")

    def test_non_dangerous_mode_blocks_home_path_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary), permission_mode="trusted")
            try:
                result = runtime.call_tool(
                    "exec_command",
                    {"cmd": "cat ~root/.profile"},
                )
            finally:
                runtime.close()

        self.assertTrue(result["isError"])
        error = result["structuredContent"]["error"]
        self.assertEqual(error["details"]["permission"], "shell_expansion")

    def test_os_sandbox_can_be_explicitly_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(
                os.environ,
                {"CODING_TOOLS_MCP_OS_SANDBOX": "off"},
                clear=False,
            ):
                runtime = Runtime(Path(temporary), permission_mode="safe")
                try:
                    environment = runtime.call_tool(
                        "check_exec_environment",
                        {},
                    )["structuredContent"]
                finally:
                    runtime.close()

        self.assertFalse(environment["sandbox"]["os_kernel_sandbox"])
        self.assertEqual(environment["sandbox"]["backend"], "application-policy")

    def test_windows_restricted_backend_reports_partial_isolation(self) -> None:
        with (
            patch.object(
                WindowsRestrictedTokenBackend,
                "_restricted_token_available",
                return_value=True,
            ),
            patch.object(
                WindowsRestrictedTokenBackend,
                "_experimental_appcontainer_available",
                return_value=True,
            ),
        ):
            backend = WindowsRestrictedTokenBackend(enabled=True)

        self.assertTrue(backend.state.enabled)
        self.assertTrue(backend.state.process_isolation)
        self.assertFalse(backend.state.filesystem_isolation)
        self.assertFalse(backend.state.network_isolation)
        self.assertTrue(backend.state.experimental_appcontainer_available)
        wrapped = backend.wrap(["cmd.exe", "/c", "echo ok"], cwd=Path.cwd())
        self.assertIn("--", wrapped)
        self.assertEqual(wrapped[-4:], ["cmd.exe", "/c", "echo ok"])

    @unittest.skipUnless(os.name == "nt", "Windows restricted-token integration test")
    def test_windows_restricted_launcher_runs_a_child_process(self) -> None:
        from mcp_tools_server.sandbox.windows_launcher import _launch_restricted

        comspec = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
        exit_code = _launch_restricted(
            [comspec, "/d", "/s", "/c", "exit 0"]
        )
        self.assertEqual(exit_code, 0)

    def test_file_search_and_command_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "hello.txt").write_text("alpha\nbeta\n", encoding="utf-8")
            runtime = Runtime(root)
            try:
                read_result = runtime.call_tool("read_file", {"path": "hello.txt"})
                search_result = runtime.call_tool("search_text", {"query": "beta"})
                command_result = runtime.call_tool(
                    "exec_command",
                    {"cmd": "printf hello", "yield_time_ms": 2_000},
                )
            finally:
                runtime.close()

        self.assertEqual(read_result["structuredContent"]["content"], "alpha\nbeta\n")
        self.assertEqual(search_result["structuredContent"]["matches"][0]["line"], 2)
        self.assertFalse(command_result["isError"])
        self.assertEqual(command_result["structuredContent"]["exit_code"], 0)
        self.assertEqual(command_result["structuredContent"]["stdout"], "hello")

    def test_read_file_rejects_binary_and_conflicting_line_range(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "binary.dat").write_bytes(b"abc\x00def")
            (root / "lines.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            runtime = Runtime(root)
            try:
                binary = runtime.call_tool("read_file", {"path": "binary.dat"})
                conflict = runtime.call_tool(
                    "read_file",
                    {
                        "path": "lines.txt",
                        "start_line": 1,
                        "end_line": 3,
                        "max_lines": 1,
                    },
                )
            finally:
                runtime.close()

        self.assertTrue(binary["isError"])
        self.assertEqual(binary["structuredContent"]["error"]["code"], "BINARY_FILE")
        self.assertTrue(conflict["isError"])
        self.assertEqual(
            conflict["structuredContent"]["error"]["code"],
            "INVALID_ARGUMENT",
        )

    def test_non_tty_initial_stdin_is_closed_after_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                result = runtime.call_tool(
                    "exec_command",
                    {
                        "cmd": "cat",
                        "stdin": "hello\n",
                        "yield_time_ms": 2_000,
                    },
                )
            finally:
                runtime.close()

        payload = result["structuredContent"]
        self.assertFalse(result["isError"])
        self.assertEqual(payload["status"], "exited")
        self.assertEqual(payload["stdout"], "hello\n")

    def test_command_timeout_watchdog_works_without_continuous_polling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                started = runtime.call_tool(
                    "exec_command",
                    {
                        "cmd": "sleep 2",
                        "timeout_ms": 100,
                        "yield_time_ms": 0,
                    },
                )
                command_id = started["structuredContent"]["command_id"]
                time.sleep(0.25)
                polled = runtime.call_tool(
                    "write_stdin",
                    {
                        "command_id": command_id,
                        "chars": "",
                        "yield_time_ms": 100,
                    },
                )
            finally:
                runtime.close()

        self.assertEqual(polled["structuredContent"]["status"], "timeout")
        self.assertTrue(polled["structuredContent"]["timed_out"])

    @unittest.skipIf(os.name == "nt", "POSIX PTY test")
    def test_tty_command_accepts_follow_up_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                started = runtime.call_tool(
                    "exec_command",
                    {
                        "cmd": "printf 'ready\\n'; read value; echo got",
                        "tty": True,
                        "yield_time_ms": 100,
                    },
                )
                command_id = started["structuredContent"]["command_id"]
                completed = runtime.call_tool(
                    "write_stdin",
                    {
                        "command_id": command_id,
                        "chars": "hello\n",
                        "yield_time_ms": 2_000,
                    },
                )
            finally:
                runtime.close()

        self.assertFalse(completed["isError"])
        self.assertEqual(completed["structuredContent"]["status"], "exited")
        self.assertIn("got", completed["structuredContent"]["stdout"])

    def test_command_verbosity_summary_and_preview_are_honored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                summary = runtime.call_tool(
                    "exec_command",
                    {"cmd": "printf hello", "verbosity": "summary"},
                )
                preview = runtime.call_tool(
                    "exec_command",
                    {"cmd": "printf hello", "verbosity": "preview"},
                )
            finally:
                runtime.close()

        self.assertNotIn("stdout", summary["structuredContent"])
        self.assertIn("summary", summary["structuredContent"])
        self.assertIn("hello", preview["structuredContent"]["preview"])

    def test_read_output_rejects_stream_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                started = runtime.call_tool(
                    "exec_command",
                    {"cmd": "sleep 1", "yield_time_ms": 0},
                )
                command_id = started["structuredContent"]["command_id"]
                result = runtime.call_tool(
                    "read_output",
                    {
                        "output_ref": f"command:{command_id}:stdout",
                        "stream": "stderr",
                    },
                )
            finally:
                runtime.close()

        self.assertTrue(result["isError"])
        self.assertEqual(
            result["structuredContent"]["error"]["code"],
            "INVALID_ARGUMENT",
        )

    def test_git_status_reports_workspace_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "tracked.txt").write_text("first\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "initial"],
                cwd=root,
                check=True,
            )
            (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
            runtime = Runtime(root)
            try:
                result = runtime.call_tool("git_status", {})
            finally:
                runtime.close()

        payload = result["structuredContent"]
        self.assertTrue(payload["is_repo"])
        self.assertFalse(payload["clean"])
        self.assertEqual(payload["entries"][0]["path"], "tracked.txt")
        self.assertEqual(payload["entries"][0]["index_status"], " ")
        self.assertEqual(payload["entries"][0]["worktree_status"], "M")
        self.assertIn("upstream", payload)
        self.assertIn("ahead", payload)
        self.assertIn("behind", payload)

    def test_git_read_tools_keep_030_compatible_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            target = root / "tracked.txt"
            target.write_text("first\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-qm",
                    "initial",
                ],
                cwd=root,
                check=True,
            )
            target.write_text("changed\n", encoding="utf-8")
            runtime = Runtime(root)
            try:
                diff = runtime.call_tool("git_diff", {})["structuredContent"]
                log = runtime.call_tool("git_log", {"max_count": 1})["structuredContent"]
                show = runtime.call_tool("git_show", {"rev": "HEAD"})["structuredContent"]
                blame = runtime.call_tool(
                    "git_blame",
                    {"path": "tracked.txt", "start_line": 1, "max_lines": 1},
                )["structuredContent"]
            finally:
                runtime.close()

        self.assertEqual(diff["files"][0]["path"], "tracked.txt")
        self.assertIn("author_date", log["commits"][0])
        self.assertEqual(show["content"], show["output"])
        self.assertTrue(show["is_repo"])
        self.assertEqual(blame["lines"], blame["entries"])
        self.assertTrue(blame["is_repo"])

    def test_path_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                result = runtime.call_tool("read_file", {"path": "../../etc/passwd"})
            finally:
                runtime.close()

        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"]["code"], "PATH_OUTSIDE_WORKSPACE")

    def test_safe_mode_blocks_network_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary), permission_mode="safe")
            try:
                result = runtime.call_tool("exec_command", {"cmd": "curl https://example.com"})
            finally:
                runtime.close()

        self.assertTrue(result["isError"])
        error = result["structuredContent"]["error"]
        self.assertEqual(error["code"], "PERMISSION_REQUIRED")
        self.assertEqual(error["details"]["permission"], "network")

    def test_request_permissions_does_not_report_fake_grant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary), permission_mode="trusted")
            try:
                result = runtime.call_tool(
                    "request_permissions",
                    {
                        "tool_name": "exec_command",
                        "permission": "network",
                        "reason": "compatibility test",
                        "arguments": {"cmd": "curl https://example.com"},
                    },
                )
            finally:
                runtime.close()

        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["status"], "unsupported")
        self.assertEqual(
            result["structuredContent"]["error"]["code"],
            "ELICITATION_UNSUPPORTED",
        )

    def test_patch_handles_multiple_hunks_in_one_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "demo.txt"
            target.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
            runtime = Runtime(root)
            try:
                result = runtime.call_tool(
                    "apply_patch",
                    {
                        "patch": """*** Begin Patch
*** Update File: demo.txt
@@
-one
+ONE
@@
-four
+FOUR
*** End Patch"""
                    },
                )
                final_text = target.read_text(encoding="utf-8")
            finally:
                runtime.close()

        self.assertFalse(result["isError"])
        self.assertEqual(final_text, "ONE\ntwo\nthree\nFOUR\n")

    def test_patch_move_preserves_mode_and_reports_030_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "script.sh"
            source.write_text("#!/bin/sh\necho old\n", encoding="utf-8")
            source.chmod(0o755)
            runtime = Runtime(root)
            try:
                result = runtime.call_tool(
                    "apply_patch",
                    {
                        "patch": """*** Begin Patch
*** Update File: script.sh
*** Move to: bin/script.sh
@@
-echo old
+echo new
*** End Patch"""
                    },
                )
            finally:
                runtime.close()

            destination = root / "bin" / "script.sh"
            self.assertFalse(source.exists())
            self.assertTrue(destination.exists())
            self.assertEqual(destination.read_text(encoding="utf-8"), "#!/bin/sh\necho new\n")
            self.assertEqual(destination.stat().st_mode & 0o777, 0o755)

        payload = result["structuredContent"]
        self.assertFalse(result["isError"])
        self.assertEqual(payload["affected_files"], [
            {"operation": "move", "path": "bin/script.sh", "old_path": "script.sh"}
        ])

    def test_patch_rejects_ambiguous_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "repeat.txt").write_text("same\nother\nsame\n", encoding="utf-8")
            runtime = Runtime(root)
            try:
                result = runtime.call_tool(
                    "apply_patch",
                    {
                        "patch": """*** Begin Patch
*** Update File: repeat.txt
@@
-same
+changed
*** End Patch"""
                    },
                )
            finally:
                runtime.close()

        self.assertTrue(result["isError"])
        self.assertEqual(
            result["structuredContent"]["error"]["code"],
            "PATCH_CONTEXT_AMBIGUOUS",
        )


class HTTPTransportTests(unittest.TestCase):
    def test_protected_resource_metadata_separates_issuer_and_mcp_resource(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = OAuthConfig(
                password="password",
                server_url="https://mcp.example.com",
                token_secret=b"p" * 32,
            )
            runtime = Runtime(Path(temporary), oauth_config=config)
            server = MCPHTTPServer(("127.0.0.1", 0), runtime)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                connection = http.client.HTTPConnection(host, port, timeout=5)
                connection.request(
                    "POST",
                    "/mcp",
                    body=json.dumps(
                        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
                    ),
                    headers={"Content-Type": "application/json"},
                )
                unauthorized = connection.getresponse()
                unauthorized.read()
                self.assertEqual(unauthorized.status, 401)
                self.assertIn(
                    'realm="coding-tools-mcp"',
                    unauthorized.getheader("WWW-Authenticate", ""),
                )
                self.assertIn(
                    'resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource"',
                    unauthorized.getheader("WWW-Authenticate", ""),
                )

                for metadata_path in (
                    "/.well-known/oauth-protected-resource",
                    "/.well-known/oauth-protected-resource/mcp",
                ):
                    connection.request("GET", metadata_path)
                    metadata_response = connection.getresponse()
                    metadata = json.loads(metadata_response.read())
                    self.assertEqual(metadata_response.status, 200)
                    self.assertEqual(metadata["resource"], "https://mcp.example.com/mcp")
                    self.assertEqual(
                        metadata["authorization_servers"],
                        ["https://mcp.example.com"],
                    )
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                runtime.close()
                thread.join(timeout=2)

    def test_oauth_metadata_advertises_cimd_and_refresh_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = OAuthConfig(
                password="password",
                server_url="https://mcp.example.com",
                token_secret=b"c" * 32,
            )
            runtime = Runtime(Path(temporary), oauth_config=config)
            server = MCPHTTPServer(("127.0.0.1", 0), runtime)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                connection = http.client.HTTPConnection(host, port, timeout=5)
                connection.request("GET", "/.well-known/oauth-authorization-server")
                response = connection.getresponse()
                metadata = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(metadata["issuer"], "https://mcp.example.com")
                self.assertTrue(metadata["client_id_metadata_document_supported"])
                self.assertEqual(
                    metadata["registration_endpoint"],
                    "https://mcp.example.com/oauth/register",
                )
                self.assertIn("offline_access", metadata["scopes_supported"])
                self.assertEqual(
                    metadata["protected_resources"],
                    ["https://mcp.example.com/mcp"],
                )
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                runtime.close()
                thread.join(timeout=2)

    def test_authorization_response_includes_rfc9207_issuer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = OAuthConfig(
                password="password",
                server_url="https://mcp.example.com",
                token_secret=b"i" * 32,
            )
            config.registry.add_preregistered(
                "issuer-test",
                ("https://chat.example.com/oauth/callback",),
                client_secret=None,
            )
            verifier = "v" * 43
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode("ascii")).digest()
            ).decode("ascii").rstrip("=")
            runtime = Runtime(Path(temporary), oauth_config=config)
            server = MCPHTTPServer(("127.0.0.1", 0), runtime)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                connection = http.client.HTTPConnection(host, port, timeout=5)
                body = urllib.parse.urlencode(
                    {
                        "client_id": "issuer-test",
                        "redirect_uri": "https://chat.example.com/oauth/callback",
                        "response_type": "code",
                        "code_challenge_method": "S256",
                        "code_challenge": challenge,
                        "resource": "https://mcp.example.com/mcp",
                        "state": "state-1",
                        "password": "password",
                    }
                )
                connection.request(
                    "POST",
                    "/oauth/authorize",
                    body=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 302)
                location = urllib.parse.urlsplit(response.getheader("Location", ""))
                query = urllib.parse.parse_qs(location.query)
                self.assertEqual(query["iss"], ["https://mcp.example.com"])
                self.assertEqual(query["state"], ["state-1"])
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                runtime.close()
                thread.join(timeout=2)

    def test_http_rejects_batch_and_scalar_json_as_invalid_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            server = MCPHTTPServer(("127.0.0.1", 0), runtime)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                connection = http.client.HTTPConnection(host, port, timeout=5)
                for body in ("[]", '"not-an-object"'):
                    connection.request(
                        "POST",
                        "/mcp",
                        body=body,
                        headers={"Content-Type": "application/json"},
                    )
                    response = connection.getresponse()
                    payload = json.loads(response.read())
                    self.assertEqual(response.status, 400)
                    self.assertEqual(payload["error"]["code"], -32600)
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                runtime.close()
                thread.join(timeout=2)

    def test_authenticated_mcp_tools_list_exposes_output_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = OAuthConfig(
                password="password",
                server_url="http://127.0.0.1",
                token_secret=b"z" * 32,
            )
            config.registry.add_preregistered(
                "http-test",
                ("http://127.0.0.1/callback",),
                client_secret=None,
            )
            runtime = Runtime(Path(temporary), oauth_config=config)
            server = MCPHTTPServer(("127.0.0.1", 0), runtime)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                connection = http.client.HTTPConnection(host, port, timeout=5)
                connection.request(
                    "POST",
                    "/mcp",
                    body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}),
                    headers={"Content-Type": "application/json"},
                )
                unauthorized = connection.getresponse()
                unauthorized.read()
                self.assertEqual(unauthorized.status, 401)
                self.assertNotIn("invalid_token", unauthorized.getheader("WWW-Authenticate", ""))

                connection.request(
                    "POST",
                    "/mcp",
                    body=json.dumps({"jsonrpc": "2.0", "id": 11, "method": "tools/list", "params": {}}),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer expired-or-invalid-token",
                    },
                )
                invalid = connection.getresponse()
                invalid_payload = json.loads(invalid.read())
                self.assertEqual(invalid.status, 401)
                self.assertEqual(invalid_payload["error"]["code"], -32000)
                self.assertEqual(
                    invalid_payload["error"]["data"]["reason"],
                    "invalid_token",
                )
                self.assertIn("error=\"invalid_token\"", invalid.getheader("WWW-Authenticate", ""))

                token = create_access_token(config, "http-test")
                connection.request(
                    "POST",
                    "/mcp",
                    body=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {token}",
                    },
                )
                response = connection.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, 200)
                tools = payload["result"]["tools"]
                self.assertEqual(len(tools), 20)
                self.assertTrue(all("outputSchema" in tool for tool in tools))
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                runtime.close()
                thread.join(timeout=2)

    def test_dispatch_exception_returns_json_rpc_internal_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = OAuthConfig(
                password="password",
                server_url="http://127.0.0.1",
                token_secret=b"d" * 32,
            )
            config.registry.add_preregistered(
                "dispatch-test",
                ("http://127.0.0.1/callback",),
                client_secret=None,
            )
            runtime = Runtime(Path(temporary), oauth_config=config)
            server = MCPHTTPServer(("127.0.0.1", 0), runtime)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                token = create_access_token(config, "dispatch-test")
                connection = http.client.HTTPConnection(host, port, timeout=5)
                with patch(
                    "mcp_tools_server.server.dispatch",
                    side_effect=ExceptionGroup("transport failure", [RuntimeError("boom")]),
                ):
                    connection.request(
                        "POST",
                        "/mcp",
                        body=json.dumps(
                            {"jsonrpc": "2.0", "id": 99, "method": "tools/list", "params": {}}
                        ),
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {token}",
                        },
                    )
                    response = connection.getresponse()
                    payload = json.loads(response.read())
                self.assertEqual(response.status, 500)
                self.assertEqual(payload["error"]["code"], -32603)
                self.assertEqual(payload["error"]["data"]["exception_type"], "ExceptionGroup")
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                runtime.close()
                thread.join(timeout=2)


    def test_modern_http_requests_require_and_accept_mirror_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = OAuthConfig(
                password="password",
                server_url="http://127.0.0.1",
                token_secret=b"m" * 32,
            )
            config.registry.add_preregistered(
                "modern-test",
                ("http://127.0.0.1/callback",),
                client_secret=None,
            )
            runtime = Runtime(Path(temporary), oauth_config=config)
            server = MCPHTTPServer(("127.0.0.1", 0), runtime)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                token = create_access_token(config, "modern-test")
                body = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 20,
                        "method": "tools/list",
                        "params": {
                            "_meta": {
                                META_PROTOCOL_VERSION: "2026-07-28",
                                META_CLIENT_CAPABILITIES: {},
                            }
                        },
                    }
                )
                connection = http.client.HTTPConnection(host, port, timeout=5)
                connection.request(
                    "POST",
                    "/mcp",
                    body=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {token}",
                    },
                )
                missing = connection.getresponse()
                missing_payload = json.loads(missing.read())
                self.assertEqual(missing.status, 400)
                self.assertEqual(missing_payload["error"]["code"], -32020)

                connection.request(
                    "POST",
                    "/mcp",
                    body=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {token}",
                        "MCP-Protocol-Version": "2026-07-28",
                        "Mcp-Method": "tools/list",
                    },
                )
                response = connection.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(len(payload["result"]["tools"]), 20)
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                runtime.close()
                thread.join(timeout=2)


class OAuthTokenTests(unittest.TestCase):
    def test_signed_access_token_round_trip(self) -> None:
        config = OAuthConfig(
            password="password",
            server_url="https://mcp.example.com",
            token_secret=b"x" * 32,
        )
        config.registry.add_preregistered(
            "client-1",
            ("http://127.0.0.1/callback",),
            client_secret=None,
        )
        token = create_access_token(config, "client-1")
        self.assertTrue(validate_access_token(config, token))
        self.assertFalse(validate_access_token(config, token + "tampered"))
        encoded = token.split(".", 2)[1]
        payload = json.loads(
            base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        )
        self.assertEqual(payload["iss"], "https://mcp.example.com")
        self.assertEqual(payload["aud"], "https://mcp.example.com/mcp")

    def test_revoked_dynamic_client_invalidates_existing_access_token(self) -> None:
        config = OAuthConfig(
            password="password",
            server_url="https://mcp.example.com",
            token_secret=b"x" * 32,
        )
        registered = config.registry.register(
            {
                "redirect_uris": ["https://chat.example.com/oauth/callback"],
                "token_endpoint_auth_method": "none",
            }
        )
        client_id = registered["client_id"]
        token = create_access_token(config, client_id)
        self.assertTrue(validate_access_token(config, token))
        self.assertTrue(config.registry.remove(client_id))
        self.assertFalse(validate_access_token(config, token))

    def test_access_token_is_bound_to_server_resource(self) -> None:
        config = OAuthConfig(
            password="password",
            server_url="https://mcp.example.com",
            token_secret=b"x" * 32,
        )
        other = OAuthConfig(
            password="password",
            server_url="https://other.example.com",
            token_secret=b"x" * 32,
        )
        for item in (config, other):
            item.registry.add_preregistered(
                "client-1",
                ("http://127.0.0.1/callback",),
                client_secret=None,
            )
        token = create_access_token(config, "client-1")
        self.assertTrue(validate_access_token(config, token))
        self.assertFalse(validate_access_token(other, token))

    def test_pre_split_access_token_remains_valid_during_compatibility_window(self) -> None:
        config = OAuthConfig(
            password="password",
            server_url="https://mcp.example.com",
            token_secret=b"x" * 32,
        )
        config.registry.add_preregistered(
            "client-1",
            ("http://127.0.0.1/callback",),
            client_secret=None,
        )
        payload = {
            "sub": "client-1",
            "client_id": "client-1",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "scope": "mcp",
            "iss": "https://mcp.example.com",
            "aud": "https://mcp.example.com",
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).decode("ascii").rstrip("=")
        signature = base64.urlsafe_b64encode(
            hmac.new(b"x" * 32, encoded.encode("ascii"), hashlib.sha256).digest()
        ).decode("ascii").rstrip("=")
        self.assertTrue(validate_access_token(config, f"ctm1.{encoded}.{signature}"))

    def test_oauth_resource_accepts_base_and_mcp_endpoint_alias(self) -> None:
        config = OAuthConfig(
            password="password",
            server_url="https://mcp.example.com",
            token_secret=b"x" * 32,
        )
        self.assertEqual(config.issuer, "https://mcp.example.com")
        self.assertEqual(config.resource, "https://mcp.example.com/mcp")
        self.assertEqual(
            config.normalize_resource("https://mcp.example.com"),
            "https://mcp.example.com/mcp",
        )

    def test_refresh_token_is_single_use_and_rotated(self) -> None:
        config = OAuthConfig(
            password="password",
            server_url="https://mcp.example.com",
            token_secret=b"x" * 32,
        )
        token = config.issue_refresh_token("client-1")
        self.assertIsNone(
            config.consume_refresh_token(
                token,
                client_id="wrong-client",
                resource=config.resource,
            )
        )
        grant = config.consume_refresh_token(
            token,
            client_id="client-1",
            resource=config.resource,
        )
        self.assertIsNotNone(grant)
        self.assertIsNone(
            config.consume_refresh_token(
                token,
                client_id="client-1",
                resource=config.resource,
            )
        )

    def test_refresh_token_survives_process_restart_with_same_token_secret(self) -> None:
        first = OAuthConfig(
            password="password",
            server_url="https://mcp.example.com",
            token_secret=b"r" * 32,
        )
        token = first.issue_refresh_token("client-1", first.resource)
        second = OAuthConfig(
            password="password",
            server_url="https://mcp.example.com",
            token_secret=b"r" * 32,
        )
        grant = second.consume_refresh_token(
            token,
            client_id="client-1",
            resource=second.resource,
        )
        self.assertIsNotNone(grant)
        assert grant is not None
        self.assertEqual(grant.resource, "https://mcp.example.com/mcp")


class OAuthRefreshHTTPTests(unittest.TestCase):
    def test_authorization_code_issues_refresh_token_and_refresh_rotates_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = OAuthConfig(
                password="password",
                server_url="http://127.0.0.1",
                token_secret=b"r" * 32,
            )
            config.registry.add_preregistered(
                "refresh-client",
                ("http://127.0.0.1/callback",),
                client_secret=None,
            )
            verifier = "a" * 43
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode("ascii")).digest()
            ).decode("ascii").rstrip("=")
            code = config.issue_code(
                "refresh-client",
                "http://127.0.0.1/callback",
                challenge,
                config.resource,
            )
            runtime = Runtime(Path(temporary), oauth_config=config)
            server = MCPHTTPServer(("127.0.0.1", 0), runtime)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                connection = http.client.HTTPConnection(host, port, timeout=5)
                body = (
                    "grant_type=authorization_code"
                    f"&code={code}"
                    "&client_id=refresh-client"
                    "&redirect_uri=http%3A%2F%2F127.0.0.1%2Fcallback"
                    f"&code_verifier={verifier}"
                    "&resource=http%3A%2F%2F127.0.0.1%2Fmcp"
                )
                connection.request(
                    "POST",
                    "/oauth/token",
                    body=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response = connection.getresponse()
                first = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertIn("refresh_token", first)

                refresh_body = (
                    "grant_type=refresh_token"
                    f"&refresh_token={first['refresh_token']}"
                    "&client_id=refresh-client"
                    "&resource=http%3A%2F%2F127.0.0.1%2Fmcp"
                )
                connection.request(
                    "POST",
                    "/oauth/token",
                    body=refresh_body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response = connection.getresponse()
                second = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertNotEqual(first["refresh_token"], second["refresh_token"])
                self.assertTrue(validate_access_token(config, second["access_token"]))

                connection.request(
                    "POST",
                    "/oauth/token",
                    body=refresh_body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response = connection.getresponse()
                replay = json.loads(response.read())
                self.assertEqual(response.status, 400)
                self.assertEqual(replay["error"], "invalid_grant")
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                runtime.close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()