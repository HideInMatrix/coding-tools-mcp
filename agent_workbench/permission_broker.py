from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from agent_runtime.local_permission_broker import (
    BROKER_DIR_ENV,
    BROKER_SECRET_ENV,
    BROKER_SERVER_ID_ENV,
    BROKER_VERSION,
    WORKFLOW_APPROVAL_KIND,
    atomic_json_write,
    sign_payload,
    verify_payload,
)


class DesktopPermissionBroker:
    def __init__(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="micromatrix-workbench-permissions-"))
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        self.secret = secrets.token_bytes(32)

    def child_environment(self, server_id: str) -> dict[str, str]:
        return {
            BROKER_DIR_ENV: str(self.directory),
            BROKER_SECRET_ENV: self.secret.hex(),
            BROKER_SERVER_ID_ENV: server_id,
        }

    def pending(self) -> list[dict[str, Any]]:
        now = int(time.time())
        result: list[dict[str, Any]] = []
        for path in sorted(self.directory.glob("*.request.json")):
            response_path = path.with_name(
                path.name.removesuffix(".request.json") + ".response.json"
            )
            if response_path.exists():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict) or not verify_payload(self.secret, raw):
                continue
            if raw.get("kind") == WORKFLOW_APPROVAL_KIND:
                continue
            if raw.get("version") != BROKER_VERSION:
                continue
            expires_at = int(raw.get("expires_at", 0))
            if expires_at <= now:
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            result.append(
                {
                    "request_id": str(raw.get("request_id") or ""),
                    "server_id": str(raw.get("server_id") or ""),
                    "tool_name": str(raw.get("tool_name") or ""),
                    "permission": str(raw.get("permission") or ""),
                    "reason": str(raw.get("reason") or ""),
                    "arguments": raw.get("arguments") if isinstance(raw.get("arguments"), (dict, list)) else {},
                    "created_at": int(raw.get("created_at", 0)),
                    "expires_at": expires_at,
                }
            )
        return result

    def pending_workflow_approvals(self) -> list[dict[str, Any]]:
        now = int(time.time())
        result: list[dict[str, Any]] = []
        suffix = ".workflow-approval.request.json"
        for path in sorted(self.directory.glob(f"*{suffix}")):
            response_path = path.with_name(
                path.name.removesuffix(suffix)
                + ".workflow-approval.response.json"
            )
            if response_path.exists():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict) or not verify_payload(self.secret, raw):
                continue
            if raw.get("version") != BROKER_VERSION:
                continue
            if raw.get("kind") != WORKFLOW_APPROVAL_KIND:
                continue
            expires_at = int(raw.get("expires_at", 0))
            if expires_at <= now:
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            result.append(
                {
                    "request_id": str(raw.get("request_id") or ""),
                    "server_id": str(raw.get("server_id") or ""),
                    "run_id": str(raw.get("run_id") or ""),
                    "node_id": str(raw.get("node_id") or ""),
                    "approval_id": str(raw.get("approval_id") or ""),
                    "title": str(raw.get("title") or ""),
                    "description": str(raw.get("description") or ""),
                    "created_at": int(raw.get("created_at", 0)),
                    "expires_at": expires_at,
                }
            )
        return result

    def respond(self, request_id: str, decision: str | bool) -> bool:
        if not request_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in request_id):
            return False
        if isinstance(decision, bool):
            normalized_decision = "once" if decision else "deny"
        else:
            normalized_decision = str(decision or "").strip().lower()
        if normalized_decision not in {"deny", "once", "session"}:
            return False
        request_path = self.directory / f"{request_id}.request.json"
        response_path = self.directory / f"{request_id}.response.json"
        try:
            raw = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(raw, dict) or not verify_payload(self.secret, raw):
            return False
        if int(raw.get("expires_at", 0)) <= int(time.time()):
            return False
        payload: dict[str, Any] = {
            "version": BROKER_VERSION,
            "request_id": request_id,
            "approved": normalized_decision != "deny",
            "scope": "session" if normalized_decision == "session" else "once",
            "responded_at": int(time.time()),
        }
        payload["signature"] = sign_payload(self.secret, payload)
        try:
            atomic_json_write(response_path, payload)
        except OSError:
            return False
        return True

    def respond_workflow_approval(self, request_id: str, approved: bool) -> bool:
        if not request_id or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for character in request_id
        ):
            return False
        request_path = self.directory / f"{request_id}.workflow-approval.request.json"
        response_path = self.directory / f"{request_id}.workflow-approval.response.json"
        try:
            raw = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(raw, dict) or not verify_payload(self.secret, raw):
            return False
        if raw.get("version") != BROKER_VERSION or raw.get("kind") != WORKFLOW_APPROVAL_KIND:
            return False
        if int(raw.get("expires_at", 0)) <= int(time.time()):
            return False
        payload: dict[str, Any] = {
            "version": BROKER_VERSION,
            "kind": WORKFLOW_APPROVAL_KIND,
            "request_id": request_id,
            "server_id": str(raw.get("server_id") or ""),
            "run_id": str(raw.get("run_id") or ""),
            "node_id": str(raw.get("node_id") or ""),
            "approval_id": str(raw.get("approval_id") or ""),
            "approved": bool(approved),
            "responded_at": int(time.time()),
        }
        payload["signature"] = sign_payload(self.secret, payload)
        try:
            atomic_json_write(response_path, payload)
        except OSError:
            return False
        return True

    def clear_server(self, server_id: str) -> None:
        for path in self.directory.glob("*.request.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict) or not verify_payload(self.secret, raw):
                continue
            if raw.get("kind") == WORKFLOW_APPROVAL_KIND:
                continue
            if str(raw.get("server_id") or "") != server_id:
                continue
            request_id = str(raw.get("request_id") or "")
            for target in (
                path,
                self.directory / f"{request_id}.response.json",
            ):
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass

        suffix = ".workflow-approval.request.json"
        for path in self.directory.glob(f"*{suffix}"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict) or not verify_payload(self.secret, raw):
                continue
            if raw.get("kind") != WORKFLOW_APPROVAL_KIND:
                continue
            if str(raw.get("server_id") or "") != server_id:
                continue
            request_id = str(raw.get("request_id") or "")
            for target in (
                path,
                self.directory / f"{request_id}.workflow-approval.response.json",
            ):
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass

    def cleanup(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)

