"""Convert structured tool payloads into MCP Tool Results."""

from __future__ import annotations

from typing import Any


MODEL_TEXT_LIMIT = 2 * 1024 * 1024


def _bounded(text: str) -> str:
    raw = text.encode("utf-8", "replace")
    if len(raw) <= MODEL_TEXT_LIMIT:
        return text
    return raw[:MODEL_TEXT_LIMIT].decode("utf-8", "ignore") + "\n[model text truncated]"


def _render_error(payload: dict[str, Any]) -> str:
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    code = error.get("code", "TOOL_ERROR")
    message = error.get("message", "Tool call failed")
    retryable = error.get("retryable")
    lines = [f"{code}: {message}"]
    if isinstance(retryable, bool):
        lines.append(f"Retryable: {'yes' if retryable else 'no'}.")
        if not retryable:
            lines.append("Do not repeat this call unchanged.")
    return "\n".join(lines)


def _render(name: str, payload: dict[str, Any]) -> str:
    if payload.get("ok") is False:
        return _render_error(payload)
    if name == "read_file":
        content = payload.get("content")
        if isinstance(content, str):
            if payload.get("truncated"):
                return f"[Showing lines {payload.get('start_line')}-{payload.get('end_line')} of {payload.get('total_lines')}]\n{content}"
            return content
    if name in {"list_dir", "list_files"}:
        items = payload.get("entries") if isinstance(payload.get("entries"), list) else payload.get("files")
        if isinstance(items, list):
            return "\n".join(str(item.get("path") or item.get("name")) for item in items if isinstance(item, dict)) or "No entries found."
    if name == "search_text":
        matches = payload.get("matches")
        if isinstance(matches, list):
            return "\n".join(
                f"{item.get('path')}:{item.get('line')}:{item.get('column')}: {item.get('preview', '')}"
                for item in matches
                if isinstance(item, dict)
            ) or "No matches found."
    if name == "apply_patch":
        return f"{'Patch validated' if payload.get('dry_run') else 'Patch applied'}: {payload.get('summary', '')}".strip()
    if name in {"exec_command", "write_stdin", "kill_command"}:
        parts = [f"Status: {payload.get('status', 'unknown')}"]
        if payload.get("exit_code") is not None:
            parts[0] += f" | exit code {payload.get('exit_code')}"
        stdout = payload.get("stdout")
        stderr = payload.get("stderr")
        if isinstance(stdout, str) and stdout:
            parts.append(stdout)
        if isinstance(stderr, str) and stderr:
            parts.append(f"stderr:\n{stderr}")
        if payload.get("status") == "running" and payload.get("command_id"):
            parts.append(f"Command still running; poll command_id={payload['command_id']} with write_stdin.")
        return "\n".join(parts)
    if name == "read_output":
        return str(payload.get("data", ""))
    if name.startswith("git_"):
        for key in ("diff", "output", "text"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
        return str(payload.get("summary") or f"{name} completed.")
    if name == "server_info":
        return f"{payload.get('server')} {payload.get('version')}\nWorkspace: {payload.get('workspace')}"
    summary = payload.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    return f"{name} completed successfully."


def make_tool_result(
    tool_name: str,
    payload: dict[str, Any],
    *,
    image: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """Return MCP content plus structuredContent governed by outputSchema."""

    is_error = payload.get("ok") is False
    content: list[dict[str, Any]] = []
    if image is not None:
        mime_type, data = image
        content.append({"type": "image", "mimeType": mime_type, "data": data})
    text = _render(tool_name, payload)
    if text:
        content.append({"type": "text", "text": _bounded(text)})
    return {
        "content": content,
        "structuredContent": payload,
        "isError": is_error,
    }
