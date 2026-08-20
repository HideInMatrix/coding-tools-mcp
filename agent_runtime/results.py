"""Convert structured tool payloads into MCP Tool Results."""

from __future__ import annotations

import json
from typing import Any


MODEL_TEXT_LIMIT = (2 * 1_048_576) + 65_536


def _bounded(text: str) -> str:
    raw = text.encode("utf-8", "replace")
    if len(raw) <= MODEL_TEXT_LIMIT:
        return text
    return raw[:MODEL_TEXT_LIMIT].decode("utf-8", "ignore") + "\n[model text truncated]"


def _render_error(payload: dict[str, Any]) -> str:
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    code = error.get("code", "TOOL_ERROR")
    message = error.get("message", "Tool call failed")
    lines = [f"{code}: {message}"]
    facts: list[str] = []
    category = error.get("category")
    if isinstance(category, str) and category:
        facts.append(f"Category: {category}.")
    retryable = error.get("retryable")
    if isinstance(retryable, bool):
        facts.append(f"Retryable: {'yes' if retryable else 'no'}.")
        if not retryable:
            facts.append("Do not repeat this call unchanged.")
    if facts:
        lines.append(" ".join(facts))
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    retry_hint = details.get("retry_hint")
    if isinstance(retry_hint, str) and retry_hint:
        lines.append(f"Retry: {retry_hint}")
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, list):
        for item in diagnostics:
            if not isinstance(item, dict):
                continue
            suggestion = item.get("suggested_fix") or item.get("suggested_next_command")
            if isinstance(suggestion, str) and suggestion:
                lines.append(f"Suggested action: {suggestion}")
    return "\n".join(lines)


def _tool_call(name: str, arguments: dict[str, Any]) -> str:
    rendered = ", ".join(
        f"{key}={json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"
        for key, value in arguments.items()
    )
    return f"{name}({rendered})"


def _next_action(payload: dict[str, Any]) -> str:
    action = payload.get("next_action")
    if not isinstance(action, dict):
        return ""
    tool = action.get("tool")
    arguments = action.get("arguments")
    if not isinstance(tool, str) or not isinstance(arguments, dict):
        return ""
    return _tool_call(tool, arguments)


def _render_exec(payload: dict[str, Any]) -> str:
    header = [f"Status: {payload.get('status', 'unknown')}"]
    if payload.get("exit_code") is not None:
        header.append(f"exit code {payload.get('exit_code')}")
    if payload.get("signal"):
        header.append(f"signal {payload.get('signal')}")
    if payload.get("timed_out"):
        header.append("timed out")
    elapsed = payload.get("elapsed_ms")
    if isinstance(elapsed, (int, float)):
        header.append(f"{int(elapsed)} ms")
    parts = [" | ".join(header)]
    stdout = payload.get("stdout")
    stderr = payload.get("stderr")
    preview = payload.get("preview")
    if isinstance(stdout, str) and stdout:
        parts.append(stdout)
    if isinstance(stderr, str) and stderr:
        parts.append(f"stderr:\n{stderr}")
    if len(parts) == 1 and isinstance(preview, str) and preview:
        parts.append(preview)
    if len(parts) == 1 and isinstance(payload.get("summary"), str):
        parts.append(str(payload["summary"]))
    if payload.get("status") == "running" and payload.get("command_id"):
        parts.append(
            f'Command still running; poll with write_stdin(command_id="{payload["command_id"]}", chars="", yield_time_ms=10000).'
        )
    if payload.get("truncated"):
        refs = payload.get("output_refs") if isinstance(payload.get("output_refs"), dict) else {}
        continuation_added = False
        for stream in ("stdout", "stderr"):
            ref = refs.get(stream)
            if not isinstance(ref, str) or not ref:
                continue
            omitted = payload.get(f"{stream}_omitted_bytes")
            if payload.get(f"{stream}_truncated") or (isinstance(omitted, int) and omitted > 0):
                parts.append(
                    f"{stream} output truncated; continue with {_tool_call('read_output', {'output_ref': ref, 'offset': 0})}."
                )
                continuation_added = True
        if not continuation_added:
            parts.append("Output truncated; use read_output with the returned output_ref to read more.")
    return "\n".join(parts)


def _render(name: str, payload: dict[str, Any]) -> str:
    if payload.get("ok") is False:
        return _render_error(payload)
    if name == "read_file":
        content = payload.get("content")
        if isinstance(content, str):
            if payload.get("truncated"):
                continuation = _next_action(payload)
                if not continuation and payload.get("next_start_line"):
                    continuation = _tool_call(
                        "read_file",
                        {
                            "path": payload.get("path", ""),
                            "start_line": payload.get("next_start_line"),
                        },
                    )
                hint = (
                    f"; continue with {continuation}"
                    if continuation
                    else "; content truncated; raise max_bytes or request a narrower range"
                )
                return (
                    f"[Showing lines {payload.get('start_line')}-{payload.get('end_line')} "
                    f"of {payload.get('total_lines')}{hint}]\n{content}"
                )
            return content
    if name in {"list_dir", "list_files"}:
        items = payload.get("entries") if isinstance(payload.get("entries"), list) else payload.get("files")
        if isinstance(items, list):
            lines = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                path = item.get("path") or item.get("name")
                kind = item.get("type")
                lines.append(f"{path}{' [' + str(kind) + ']' if kind else ''}")
            if payload.get("truncated"):
                lines.append("… results truncated; narrow the path/patterns or raise the entry limit.")
            return "\n".join(lines) or "No entries found."
    if name == "search_text":
        matches = payload.get("matches")
        if isinstance(matches, list):
            lines: list[str] = []
            for item in matches:
                if not isinstance(item, dict):
                    continue
                column = item.get("column")
                location = f"{item.get('path')}:{item.get('line')}"
                if column:
                    location += f":{column}"
                lines.append(f"{location}: {item.get('preview', '')}")
                before = item.get("before")
                after = item.get("after")
                if isinstance(before, list):
                    lines.extend(f"  {value}" for value in before)
                if isinstance(after, list):
                    lines.extend(f"  {value}" for value in after)
            if payload.get("truncated"):
                lines.append("… results truncated; narrow the query/path or raise max_results.")
            return "\n".join(lines) or "No matches found."
    if name == "apply_patch":
        prefix = "Patch validated" if payload.get("dry_run") else "Patch applied"
        files = payload.get("affected_files")
        count = len(files) if isinstance(files, list) else 0
        summary = str(payload.get("summary") or "").strip()
        text = (
            f"{prefix} to {count} file{'s' if count != 1 else ''} "
            f"(+{payload.get('additions', 0)} -{payload.get('removals', 0)})."
        )
        return text + (f"\n{summary}" if summary else "")
    if name in {"exec_process", "exec_command", "write_stdin", "kill_command"}:
        if name == "kill_command":
            signal_sent = payload.get("signal_sent")
            suffix = f" (signal {signal_sent})" if signal_sent else ""
            return f"Command {payload.get('command_id', '')}: {payload.get('status', 'completed')}{suffix}."
        return _render_exec(payload)
    if name == "read_output":
        content = str(payload.get("content", payload.get("data", "")))
        if payload.get("next_offset") is not None:
            continuation = _next_action(payload)
            if not continuation:
                continuation = _tool_call(
                    "read_output",
                    {
                        "output_ref": payload.get("stream_output_ref") or payload.get("output_ref") or "",
                        "offset": payload.get("next_offset"),
                    },
                )
            return f"{content}\n[more: {continuation}]"
        return content
    if name == "git_status":
        if not payload.get("is_repo", True):
            return "Not a Git repository."
        entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
        lines = [f"## {payload.get('branch') or 'detached'}"]
        for entry in entries:
            if isinstance(entry, dict):
                lines.append(
                    f"{entry.get('index_status', ' ')}{entry.get('worktree_status', ' ')} {entry.get('path', '')}"
                )
        if not entries:
            lines.append("Working tree clean.")
        if payload.get("truncated"):
            lines.append("… status entries truncated; narrow path or raise max_entries.")
        return "\n".join(lines)
    if name == "git_diff":
        text = payload.get("diff") if isinstance(payload.get("diff"), str) else "No diff."
        return text + ("\n… diff truncated; raise max_bytes or diff specific paths." if payload.get("truncated") else "")
    if name == "git_show":
        text = payload.get("content") if isinstance(payload.get("content"), str) else payload.get("output")
        rendered = text if isinstance(text, str) and text else "No output."
        return rendered + ("\n… output truncated; raise max_bytes or narrow paths." if payload.get("truncated") else "")
    if name == "git_log":
        commits = payload.get("commits") if isinstance(payload.get("commits"), list) else []
        if not commits:
            return "No commits found."
        text = "\n".join(
            f"{item.get('short_hash', '')} {item.get('subject', '')}"
            for item in commits
            if isinstance(item, dict)
        )
        if payload.get("truncated"):
            continuation = _next_action(payload)
            text += "\n… more commits available"
            text += f"; continue with {continuation}." if continuation else "; raise max_count or use skip."
        return text
    if name == "git_blame":
        lines_data = payload.get("lines") if isinstance(payload.get("lines"), list) else payload.get("entries")
        if not isinstance(lines_data, list) or not lines_data:
            return "No blame lines found."
        text = "\n".join(
            f"{item.get('line', '')} {item.get('commit', '')} {item.get('content', '')}"
            for item in lines_data
            if isinstance(item, dict)
        )
        if payload.get("truncated"):
            continuation = _next_action(payload)
            text += "\n… blame lines truncated"
            text += f"; continue with {continuation}." if continuation else "; raise max_lines or advance start_line."
        return text
    if name == "server_info":
        return f"{payload.get('server')} {payload.get('version')}\nWorkspace: {payload.get('workspace')}"
    if name == "check_exec_environment":
        warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
        suffix = "\n" + "\n".join(str(item) for item in warnings) if warnings else ""
        return f"Execution environment checked.{suffix}"
    if name == "discover_toolchains":
        toolchains = payload.get("toolchains") if isinstance(payload.get("toolchains"), dict) else {}
        selected: list[str] = []
        for kind, value in toolchains.items():
            if not isinstance(value, dict) or not isinstance(value.get("selected"), dict):
                continue
            current = value["selected"]
            selected.append(f"{kind}: {current.get('version', '?')} ({current.get('source', '?')})")
        return "Toolchains discovered." + ("\n" + "\n".join(selected) if selected else "")
    if name == "request_permissions":
        return f"Permission request: {payload.get('status', 'completed')}."
    if name == "view_image":
        dimensions = ""
        if payload.get("width") and payload.get("height"):
            dimensions = f", {payload['width']}×{payload['height']}"
        return f"Image: {payload.get('path', '')} ({payload.get('mime_type', 'unknown')}{dimensions})"
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
