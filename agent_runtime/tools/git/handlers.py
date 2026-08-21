from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from ...errors import ToolError
from .._shared import parse_diff_files, parse_git_branch_line, truncate_text


class GitHandlers:
    """Read-only Git tool handlers scoped to the configured workspace."""

    def _git(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        max_bytes: int = 262_144,
        check: bool = True,
    ) -> tuple[str, str, int, bool]:
        try:
            result = subprocess.run(
                ["git", *argv],
                cwd=str(cwd or self.workspace.root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                env=self._command_env({}),
            )
        except FileNotFoundError as exc:
            raise ToolError(
                "GIT_NOT_FOUND", "git executable is not available", "environment"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ToolError(
                "GIT_TIMEOUT", "git command timed out", "process", True
            ) from exc
        stdout, out_cut = truncate_text(result.stdout, max_bytes)
        stderr, err_cut = truncate_text(result.stderr, max_bytes)
        if check and result.returncode != 0:
            raise ToolError(
                "GIT_FAILED",
                stderr.strip() or "git command failed",
                "git",
                False,
                {"exit_code": result.returncode},
            )
        return stdout, stderr, result.returncode, out_cut or err_cut

    def _git_repo(self, path: str = ".") -> tuple[Path, bool]:
        cwd = self.workspace.existing(path).absolute
        if cwd.is_file():
            cwd = cwd.parent
        _, _, code, _ = self._git(
            ["rev-parse", "--is-inside-work-tree"],
            cwd=cwd,
            check=False,
            max_bytes=4_096,
        )
        return cwd, code == 0

    def git_status(self, args: dict[str, Any]) -> dict[str, Any]:
        cwd, is_repo = self._git_repo(str(args.get("path", ".")))
        if not is_repo:
            return {
                "is_repo": False,
                "branch": "",
                "head": "",
                "upstream": "",
                "ahead": 0,
                "behind": 0,
                "entries": [],
                "clean": True,
                "truncated": False,
                "warnings": [],
            }
        untracked = "all" if args.get("include_untracked", True) else "no"
        output, _, _, truncated = self._git(
            ["status", "--porcelain=v1", "-b", f"--untracked-files={untracked}"],
            cwd=cwd,
            max_bytes=1_048_576,
        )
        max_entries = int(args.get("max_entries", 1_000))
        entries: list[dict[str, Any]] = []
        branch = ""
        upstream = ""
        ahead = 0
        behind = 0
        status_lines = 0
        for line in output.splitlines():
            if line.startswith("## "):
                branch, upstream, ahead, behind = parse_git_branch_line(line[3:])
                continue
            if len(line) < 3:
                continue
            status_lines += 1
            if len(entries) >= max_entries:
                continue
            path_text = line[3:]
            original_path = None
            if " -> " in path_text:
                original_path, path_text = path_text.split(" -> ", 1)
            entries.append(
                {
                    "status": line[:2],
                    "path": path_text,
                    "original_path": original_path,
                    "index_status": line[0],
                    "worktree_status": line[1],
                }
            )
        head, _, _, _ = self._git(
            ["rev-parse", "HEAD"], cwd=cwd, max_bytes=4_096, check=False
        )
        return {
            "is_repo": True,
            "branch": branch,
            "head": head.strip(),
            "upstream": upstream,
            "ahead": ahead,
            "behind": behind,
            "entries": entries,
            "clean": status_lines == 0,
            "truncated": truncated or status_lines > len(entries),
            "warnings": ["entry limit reached"] if status_lines > len(entries) else [],
        }

    def _git_paths(self, args: dict[str, Any]) -> list[str]:
        values: list[str] = []
        if args.get("path"):
            values.append(str(args["path"]))
        values.extend(str(item) for item in args.get("paths") or [])
        return [self.workspace.writable(value).display for value in values]

    def git_diff(self, args: dict[str, Any]) -> dict[str, Any]:
        context = int(args.get("context_lines", 3))
        max_bytes = int(args.get("max_bytes", 262_144))
        paths = self._git_paths(args)
        parts: list[str] = []
        truncated = False
        if args.get("unstaged", True):
            argv = ["diff", f"--unified={context}"]
            if paths:
                argv += ["--", *paths]
            text, _, _, cut = self._git(argv, max_bytes=max_bytes)
            parts.append(text)
            truncated |= cut
        if args.get("staged", False):
            argv = ["diff", "--cached", f"--unified={context}"]
            if paths:
                argv += ["--", *paths]
            text, _, _, cut = self._git(argv, max_bytes=max_bytes)
            parts.append(text)
            truncated |= cut
        diff, extra_cut = truncate_text("".join(parts), max_bytes)
        is_truncated = truncated or extra_cut
        return {
            "diff": diff,
            "files": parse_diff_files(diff),
            "truncated": is_truncated,
            "exit_code": 0,
            "warnings": ["diff truncated"] if is_truncated else [],
        }

    def git_log(self, args: dict[str, Any]) -> dict[str, Any]:
        cwd, is_repo = self._git_repo(str(args.get("path", ".")))
        if not is_repo:
            return {
                "is_repo": False,
                "commits": [],
                "count": 0,
                "truncated": False,
                "warnings": [],
            }
        ref = str(args.get("ref", "HEAD"))
        if not ref or ref.startswith("-") or any(char in ref for char in "\x00\r\n"):
            raise ToolError("INVALID_ARGUMENT", "invalid git revision", "validation")
        max_count = int(args.get("max_count", 20))
        skip = int(args.get("skip", 0))
        fmt = "%H%x1f%h%x1f%an%x1f%ae%x1f%aI%x1f%s%x1e"
        output, _, _, output_truncated = self._git(
            [
                "log",
                ref,
                f"--max-count={max_count + 1}",
                f"--skip={skip}",
                f"--format={fmt}",
            ],
            cwd=cwd,
        )
        commits = []
        for record in output.strip("\x1e\n").split("\x1e"):
            if not record.strip():
                continue
            fields = record.strip().split("\x1f", 5)
            if len(fields) == 6:
                commits.append(
                    {
                        "hash": fields[0],
                        "short_hash": fields[1],
                        "author_name": fields[2],
                        "author_email": fields[3],
                        "author_date": fields[4],
                        "date": fields[4],
                        "subject": fields[5],
                    }
                )
        has_more = len(commits) > max_count
        commits = commits[:max_count]
        result: dict[str, Any] = {
            "is_repo": True,
            "ref": ref,
            "path": str(args.get("path", ".")),
            "max_count": max_count,
            "skip": skip,
            "commits": commits,
            "count": len(commits),
            "truncated": output_truncated or has_more,
            "warnings": ["commit limit reached"] if has_more else [],
        }
        if has_more:
            result["next_action"] = {
                "tool": "git_inspect",
                "arguments": {
                    "action": "log",
                    "path": str(args.get("path", ".")),
                    "ref": ref,
                    "max_count": max_count,
                    "skip": skip + max_count,
                },
            }
        return result

    def git_show(self, args: dict[str, Any]) -> dict[str, Any]:
        _, is_repo = self._git_repo(".")
        if not is_repo:
            return {
                "is_repo": False,
                "content": "",
                "output": "",
                "files": [],
                "truncated": False,
                "warnings": [],
            }
        max_bytes = int(args.get("max_bytes", 262_144))
        rev = str(args.get("rev", "HEAD"))
        if not rev or rev.startswith("-") or any(char in rev for char in "\x00\r\n"):
            raise ToolError("INVALID_ARGUMENT", "invalid git revision", "validation")
        argv = ["show", rev, f"--unified={int(args.get('context_lines', 3))}"]
        if not args.get("include_diff", True):
            argv.append("--no-patch")
        paths = self._git_paths(args)
        if paths:
            argv += ["--", *paths]
        output, stderr, code, truncated = self._git(
            argv, max_bytes=max_bytes, check=False
        )
        if code != 0:
            raise ToolError(
                "GIT_FAILED",
                stderr.strip() or "git show failed",
                "git",
                False,
                {"exit_code": code},
            )
        return {
            "is_repo": True,
            "content": output,
            "output": output,
            "rev": rev,
            "files": parse_diff_files(output),
            "truncated": truncated,
            "exit_code": code,
            "warnings": ["output truncated"] if truncated else [],
        }

    def git_blame(self, args: dict[str, Any]) -> dict[str, Any]:
        resolved = self.workspace.existing(str(args["path"]))
        if not resolved.absolute.is_file():
            raise ToolError("NOT_FILE", "git_blame path must be a file", "validation")
        _, is_repo = self._git_repo(".")
        if not is_repo:
            return {
                "is_repo": False,
                "path": resolved.display,
                "lines": [],
                "entries": [],
                "truncated": False,
                "warnings": [],
            }
        start = int(args.get("start_line", 1))
        end = args.get("end_line")
        max_lines = int(args.get("max_lines", 200))
        requested_end = int(end) if end is not None else start + max_lines - 1
        if requested_end < start:
            raise ToolError(
                "INVALID_ARGUMENT", "end_line must be >= start_line", "validation"
            )
        final_line = min(requested_end, start + max_lines - 1)
        argv = ["blame", "--line-porcelain", "-L", f"{start},{final_line}"]
        if args.get("rev"):
            argv.append(str(args["rev"]))
        argv += ["--", resolved.display]
        output, stderr, code, truncated = self._git(
            argv, max_bytes=1_048_576, check=False
        )
        if code != 0:
            raise ToolError(
                "GIT_FAILED",
                stderr.strip() or "git blame failed",
                "git",
                False,
                {"exit_code": code},
            )
        entries: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for line in output.splitlines():
            header = re.match(
                r"^([0-9a-f^]{40})\s+(\d+)\s+(\d+)(?:\s+(\d+))?$", line
            )
            if header:
                current = {
                    "commit": header.group(1),
                    "original_line": int(header.group(2)),
                    "line": int(header.group(3)),
                }
                entries.append(current)
            elif current is not None and line.startswith("author "):
                current["author"] = line[7:]
            elif current is not None and line.startswith("author-mail "):
                mail = line[12:].strip("<>")
                current["author_mail"] = mail
                current["author_email"] = mail
            elif current is not None and line.startswith("summary "):
                current["summary"] = line[8:]
            elif current is not None and line.startswith("\t"):
                current["content"] = line[1:]
        if len(entries) > max_lines:
            entries = entries[:max_lines]
            truncated = True
        truncated = truncated or requested_end > final_line
        result: dict[str, Any] = {
            "is_repo": True,
            "path": resolved.display,
            "rev": str(args["rev"]) if args.get("rev") else None,
            "start_line": start,
            "end_line": final_line,
            "max_lines": max_lines,
            "lines": entries,
            "entries": entries,
            "count": len(entries),
            "truncated": truncated,
            "warnings": ["line limit reached"] if truncated else [],
        }
        if requested_end > final_line:
            next_args: dict[str, Any] = {
                "path": str(args["path"]),
                "start_line": final_line + 1,
                "end_line": requested_end,
                "max_lines": max_lines,
            }
            if args.get("rev"):
                next_args["rev"] = str(args["rev"])
            result["next_action"] = {
                "tool": "git_inspect",
                "arguments": {"action": "blame", **next_args},
            }
        return result

    def git_inspect(self, args: dict[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "").strip()
        payload = {key: value for key, value in args.items() if key != "action"}
        handlers = {
            "status": self.git_status,
            "diff": self.git_diff,
            "log": self.git_log,
            "show": self.git_show,
            "blame": self.git_blame,
        }
        handler = handlers.get(action)
        if handler is None:
            raise ToolError("INVALID_ARGUMENT", f"unsupported git_inspect action: {action}", "validation")
        if action == "blame" and not str(payload.get("path") or "").strip():
            raise ToolError("INVALID_ARGUMENT", "action=blame requires path", "validation")
        return handler(payload)
