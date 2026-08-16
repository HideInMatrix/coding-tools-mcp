from __future__ import annotations

import http.client
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from coding_tools_mcp import __version__
from coding_tools_mcp.oauth import OAuthConfig, create_access_token, validate_access_token
from coding_tools_mcp.protocol import (
    META_CLIENT_CAPABILITIES,
    META_PROTOCOL_VERSION,
    dispatch,
)
from coding_tools_mcp.runtime import Runtime
from coding_tools_mcp.server import MCPHTTPServer


class CustomMCPServerContractTests(unittest.TestCase):
    def test_project_owned_version(self) -> None:
        self.assertEqual(__version__, "1.0.0")

    def test_every_exposed_tool_has_input_and_output_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                tools = runtime.list_tools()["tools"]
            finally:
                runtime.close()

        self.assertEqual(len(tools), 18)
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
        self.assertEqual(len(listed["result"]["tools"]), 18)

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


class RuntimeSafetyTests(unittest.TestCase):
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


class HTTPTransportTests(unittest.TestCase):
    def test_authenticated_mcp_tools_list_exposes_output_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = OAuthConfig(
                password="password",
                server_url="http://127.0.0.1",
                token_secret=b"z" * 32,
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
                self.assertEqual(len(tools), 18)
                self.assertTrue(all("outputSchema" in tool for tool in tools))
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
        token = create_access_token(config, "client-1")
        self.assertTrue(validate_access_token(config, token))
        self.assertFalse(validate_access_token(config, token + "tampered"))


if __name__ == "__main__":
    unittest.main()