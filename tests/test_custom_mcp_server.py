from __future__ import annotations

import base64
import hashlib
import http.client
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_unexpected_tool_exception_is_returned_as_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                with self.assertLogs("coding_tools_mcp.runtime", level="ERROR"):
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

    def test_unexpected_dispatch_exception_returns_json_rpc_internal_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                with self.assertLogs("coding_tools_mcp.protocol", level="ERROR"):
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
                self.assertEqual(invalid_payload["error"], "invalid_token")
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
                self.assertEqual(len(tools), 18)
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
            runtime = Runtime(Path(temporary), oauth_config=config)
            server = MCPHTTPServer(("127.0.0.1", 0), runtime)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                token = create_access_token(config, "dispatch-test")
                connection = http.client.HTTPConnection(host, port, timeout=5)
                with patch(
                    "coding_tools_mcp.server.dispatch",
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
        token = create_access_token(config, "client-1")
        self.assertTrue(validate_access_token(config, token))
        self.assertFalse(validate_access_token(other, token))

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