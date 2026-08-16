from __future__ import annotations

import shutil
from pathlib import Path

from .discovery import bundled_candidate, path_candidate, standard_candidates
from .models import ExecutableCandidate, ExecutableSpec
from .specs import executable_spec
from .verification import verify_executable


class ExecutableResolver:
    """Resolve provider clients without recursive filesystem scanning.

    Priority is intentionally deterministic:

    1. Explicit user-selected path.
    2. Application-bundled executable.
    3. Known standard install locations.
    4. The current process PATH.
    """

    def resolve(
        self,
        spec: ExecutableSpec,
        *,
        configured: str = "",
        auto_only: bool = False,
    ) -> ExecutableCandidate:
        raw = configured.strip()
        if raw and not auto_only:
            explicit = Path(raw).expanduser()
            if explicit.is_file():
                return verify_executable(spec, explicit, source="manual")
            from_path = shutil.which(raw)
            if from_path:
                return verify_executable(spec, Path(from_path), source="manual")
            raise RuntimeError(f"找不到用户指定的 {spec.display_name}: {raw}")

        bundled = bundled_candidate(spec)
        if bundled is not None:
            return verify_executable(spec, bundled, source="bundled")

        errors: list[str] = []
        for candidate in standard_candidates(spec):
            try:
                return verify_executable(spec, candidate, source="standard")
            except RuntimeError as exc:
                errors.append(str(exc))

        from_path = path_candidate(spec)
        if from_path is not None:
            try:
                return verify_executable(spec, from_path, source="path")
            except RuntimeError as exc:
                errors.append(str(exc))

        details = f" 最近的验证错误: {errors[-1]}" if errors else ""
        raise RuntimeError(
            f"未检测到可用的 {spec.display_name}。"
            "请安装客户端，或使用“选择…”手动指定可执行文件。"
            + details
        )


def resolve_executable(
    key: str,
    *,
    configured: str = "",
    auto_only: bool = False,
) -> ExecutableCandidate:
    return ExecutableResolver().resolve(
        executable_spec(key),
        configured=configured,
        auto_only=auto_only,
    )
