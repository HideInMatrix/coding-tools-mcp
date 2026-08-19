from __future__ import annotations

import base64
import io
import mimetypes
import re
from typing import Any

from ...errors import ToolError
from ...patching import apply_patch as apply_patch_envelope
from ...workspace import matches_any
from .._shared import iso_mtime, truncate_text


class FilesystemHandlers:
    """Workspace-scoped filesystem and media tool handlers."""

    def read_file(self, args: dict[str, Any]) -> dict[str, Any]:
        resolved = self.workspace.existing(str(args["path"]))
        if resolved.absolute.is_dir():
            raise ToolError("IS_DIRECTORY", "path is a directory", "validation")
        if not resolved.absolute.is_file():
            raise ToolError("NOT_FILE", f"not a file: {resolved.display}", "filesystem")
        max_bytes = int(args.get("max_bytes", 131_072))
        start = int(args.get("start_line", 1))
        requested_end = args.get("end_line")
        requested_max_lines = args.get("max_lines")
        if requested_end is not None and requested_max_lines is not None:
            calculated_end = start + int(requested_max_lines) - 1
            if int(requested_end) != calculated_end:
                raise ToolError(
                    "INVALID_ARGUMENT",
                    "end_line and max_lines select different ranges",
                    "validation",
                )
        end = (
            int(requested_end)
            if requested_end is not None
            else start + int(requested_max_lines) - 1
            if requested_max_lines is not None
            else None
        )
        if end is not None and end < start:
            raise ToolError(
                "INVALID_RANGE",
                "end_line must be greater than or equal to start_line",
                "validation",
            )
        try:
            total_bytes = resolved.absolute.stat().st_size
            with resolved.absolute.open("rb") as raw_handle:
                if b"\x00" in raw_handle.read(4096):
                    raise ToolError(
                        "BINARY_FILE",
                        f"binary file read blocked for text tool: {resolved.display}",
                        "validation",
                    )
        except ToolError:
            raise
        except OSError as exc:
            raise ToolError(
                "READ_FAILED",
                f"cannot read file: {resolved.display}",
                "filesystem",
                True,
                {"error": str(exc)},
            ) from exc

        selected_parts: list[str] = []
        selected_bytes = 0
        total_lines = 0
        byte_limit_hit = False
        try:
            with resolved.absolute.open(
                "r",
                encoding="utf-8",
                errors="strict",
                newline="",
            ) as handle:
                for total_lines, line in enumerate(handle, start=1):
                    if total_lines < start:
                        continue
                    if end is not None and total_lines > end:
                        continue
                    if byte_limit_hit:
                        continue
                    encoded = line.encode("utf-8")
                    remaining = max_bytes - selected_bytes
                    if len(encoded) <= remaining:
                        selected_parts.append(line)
                        selected_bytes += len(encoded)
                        continue
                    if remaining > 0:
                        selected_parts.append(encoded[:remaining].decode("utf-8", "ignore"))
                        selected_bytes = max_bytes
                    byte_limit_hit = True
        except UnicodeDecodeError as exc:
            raise ToolError(
                "UNSUPPORTED_ENCODING",
                f"file is not valid UTF-8: {resolved.display}",
                "validation",
            ) from exc
        except OSError as exc:
            raise ToolError(
                "READ_FAILED",
                f"cannot read file: {resolved.display}",
                "filesystem",
                True,
                {"error": str(exc)},
            ) from exc

        content = "".join(selected_parts)
        actual_lines = content.count("\n") + (
            1 if content and not content.endswith("\n") else 0
        )
        selected_end = min(end, total_lines) if end is not None else total_lines
        actual_end = (
            min(selected_end, start + max(0, actual_lines - 1))
            if content
            else start - 1
        )
        range_has_more = selected_end < total_lines
        truncated = byte_limit_hit or range_has_more
        next_start_line = actual_end + 1 if truncated and actual_end < total_lines else None
        return {
            "path": resolved.display,
            "content": content,
            "encoding": "utf-8",
            "max_bytes": max_bytes,
            "start_line": start,
            "end_line": actual_end,
            "total_lines": total_lines,
            "total_bytes": total_bytes,
            "bytes_read": len(content.encode("utf-8")),
            "truncated": truncated,
            "truncated_by": (
                "bytes" if byte_limit_hit else "lines" if range_has_more else None
            ),
            "next_start_line": next_start_line,
            "warnings": ["content truncated"] if truncated else [],
        }

    def list_dir(self, args: dict[str, Any]) -> dict[str, Any]:
        resolved = self.workspace.existing(str(args.get("path", ".")))
        if not resolved.absolute.is_dir():
            raise ToolError(
                "NOT_A_DIRECTORY",
                f"not a directory: {resolved.display}",
                "filesystem",
            )
        recursive = bool(args.get("recursive", False))
        max_depth = int(args.get("max_depth", 1))
        max_entries = int(args.get("max_entries", 1_000))
        include_hidden = bool(args.get("include_hidden", False))
        include_ignored = bool(args.get("include_ignored", False))
        entries: list[dict[str, Any]] = []
        base_depth = len(resolved.absolute.parts)
        stack = [resolved.absolute]
        while stack and len(entries) < max_entries:
            directory = stack.pop()
            try:
                children = list(directory.iterdir())
            except OSError as exc:
                raise ToolError(
                    "LIST_FAILED",
                    f"cannot list directory: {directory}",
                    "filesystem",
                    True,
                    {"error": str(exc)},
                ) from exc
            for child in children:
                relative = child.relative_to(self.workspace.root)
                if not include_hidden and self.workspace.hidden(relative):
                    continue
                if not include_ignored and self.workspace.ignored(relative):
                    continue
                try:
                    stat = child.lstat()
                    kind = (
                        "symlink"
                        if child.is_symlink()
                        else "directory"
                        if child.is_dir()
                        else "file"
                    )
                    size = stat.st_size
                except OSError:
                    kind, size = "unknown", 0
                entries.append(
                    {
                        "name": child.name,
                        "path": relative.as_posix(),
                        "type": kind,
                        "size_bytes": size,
                        "modified": iso_mtime(child),
                    }
                )
                if len(entries) >= max_entries:
                    break
                depth = len(child.parts) - base_depth
                if recursive and kind == "directory" and depth < max_depth:
                    stack.append(child)
        sort = str(args.get("sort", "name"))
        if sort == "type":
            entries.sort(key=lambda item: (item["type"], item["name"]))
        elif sort == "modified":
            entries.sort(key=lambda item: item["modified"])
        else:
            entries.sort(key=lambda item: item["path"])
        truncated = bool(stack) or len(entries) >= max_entries
        return {
            "path": resolved.display,
            "entries": entries,
            "count": len(entries),
            "truncated": truncated,
            "warnings": ["entry limit reached"] if truncated else [],
        }

    def list_files(self, args: dict[str, Any]) -> dict[str, Any]:
        base = self.workspace.existing(str(args.get("path", ".")))
        patterns = list(args.get("patterns") or [])
        if args.get("glob"):
            patterns.append(str(args["glob"]))
        excludes = list(args.get("exclude_patterns") or [])
        max_results = int(args.get("max_results", 5_000))
        files: list[dict[str, Any]] = []
        truncated = False
        for absolute, relative in self.workspace.iter_files(
            base,
            include_hidden=bool(args.get("include_hidden", False)),
            include_ignored=bool(args.get("include_ignored", False)),
        ):
            display = relative.as_posix()
            if patterns and not matches_any(display, patterns):
                continue
            if excludes and matches_any(display, excludes):
                continue
            try:
                size = absolute.stat().st_size
            except OSError:
                size = 0
            files.append(
                {"path": display, "size_bytes": size, "modified": iso_mtime(absolute)}
            )
            if len(files) >= max_results:
                truncated = True
                break
        if args.get("sort", "path") == "modified":
            files.sort(key=lambda item: item["modified"], reverse=True)
        else:
            files.sort(key=lambda item: item["path"])
        return {"files": files, "count": len(files), "truncated": truncated}

    def search_text(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args["query"])
        flags = 0 if args.get("case_sensitive", False) else re.IGNORECASE
        try:
            expression = re.compile(
                query if args.get("regex", False) else re.escape(query), flags
            )
        except re.error as exc:
            raise ToolError(
                "INVALID_REGEX",
                f"invalid regular expression: {exc}",
                "validation",
            ) from exc
        base = self.workspace.existing(str(args.get("path", ".")))
        include_patterns = list(args.get("include_globs") or [])
        if args.get("glob"):
            include_patterns.append(str(args["glob"]))
        exclude_patterns = list(args.get("exclude_globs") or [])
        context = int(args.get("context_lines", 0))
        max_results = int(args.get("max_results", 1_000))
        preview_bytes = int(args.get("max_preview_bytes", 512))
        matches: list[dict[str, Any]] = []
        truncated = False
        for absolute, relative in self.workspace.iter_files(
            base, include_hidden=False, include_ignored=False
        ):
            display = relative.as_posix()
            if include_patterns and not matches_any(display, include_patterns):
                continue
            if exclude_patterns and matches_any(display, exclude_patterns):
                continue
            try:
                text = absolute.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            lines = text.splitlines()
            for index, line in enumerate(lines):
                found = expression.search(line)
                if not found:
                    continue
                preview, _ = truncate_text(line, preview_bytes)
                matches.append(
                    {
                        "path": display,
                        "line": index + 1,
                        "column": found.start() + 1,
                        "preview": preview,
                        "before": lines[max(0, index - context) : index],
                        "after": lines[index + 1 : index + 1 + context],
                    }
                )
                if len(matches) >= max_results:
                    truncated = True
                    break
            if truncated:
                break
        return {
            "query": query,
            "matches": matches,
            "total_matches": len(matches),
            "total_matches_exact": not truncated,
            "truncated": truncated,
        }

    def apply_patch(self, args: dict[str, Any]) -> dict[str, Any]:
        return dict(
            apply_patch_envelope(
                self.workspace,
                str(args["patch"]),
                dry_run=bool(args.get("dry_run", False)),
            )
        )

    def view_image(self, args: dict[str, Any]) -> dict[str, Any]:
        resolved = self.workspace.existing(str(args["path"]))
        if not resolved.absolute.is_file():
            raise ToolError("NOT_FILE", "image path must be a file", "validation")
        mime_type = (
            mimetypes.guess_type(resolved.absolute.name)[0] or "application/octet-stream"
        )
        if not mime_type.startswith("image/"):
            raise ToolError(
                "NOT_IMAGE", f"unsupported image type: {mime_type}", "validation"
            )
        try:
            data = resolved.absolute.read_bytes()
        except OSError as exc:
            raise ToolError(
                "READ_FAILED",
                "cannot read image",
                "filesystem",
                True,
                {"error": str(exc)},
            ) from exc
        max_bytes = int(args.get("max_bytes", 5_242_880))
        width = height = None
        resized = False
        warnings: list[str] = []
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                need_resize = bool(args.get("auto_resize", True)) and (
                    len(data) > max_bytes
                    or width > int(args.get("max_width", 2_000))
                    or height > int(args.get("max_height", 2_000))
                )
                if need_resize:
                    converted = image.convert(
                        "RGBA" if image.mode in {"RGBA", "LA"} else "RGB"
                    )
                    converted.thumbnail(
                        (
                            int(args.get("max_width", 2_000)),
                            int(args.get("max_height", 2_000)),
                        )
                    )
                    output = io.BytesIO()
                    fmt = (
                        "PNG"
                        if mime_type == "image/png"
                        else "WEBP"
                        if mime_type == "image/webp"
                        else "JPEG"
                    )
                    if fmt == "JPEG" and converted.mode != "RGB":
                        converted = converted.convert("RGB")
                    converted.save(output, format=fmt, quality=85, optimize=True)
                    data = output.getvalue()
                    width, height = converted.size
                    mime_type = {
                        "PNG": "image/png",
                        "WEBP": "image/webp",
                        "JPEG": "image/jpeg",
                    }[fmt]
                    resized = True
        except ImportError:
            warnings.append(
                "Pillow is not installed; dimensions/auto-resize are unavailable"
            )
        except Exception as exc:
            warnings.append(f"image metadata/resize failed: {exc}")
        if len(data) > max_bytes:
            raise ToolError(
                "IMAGE_TOO_LARGE",
                "image exceeds max_bytes after resize attempt",
                "validation",
                False,
                {"bytes": len(data), "max_bytes": max_bytes, "warnings": warnings},
            )
        return {
            "path": resolved.display,
            "mime_type": mime_type,
            "size_bytes": len(data),
            "width": width,
            "height": height,
            "resized": resized,
            "warnings": warnings,
            "_image": (mime_type, base64.b64encode(data).decode("ascii")),
        }
