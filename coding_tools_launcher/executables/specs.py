from __future__ import annotations

from .models import ExecutableSpec


_SPECS = {
    "frpc": ExecutableSpec(
        key="frpc",
        display_name="FRP Client",
        executable_name="frpc",
        bundled_product="frp",
        version_args=("--version",),
    ),
    "ngrok": ExecutableSpec(
        key="ngrok",
        display_name="ngrok",
        executable_name="ngrok",
        bundled_product="ngrok",
        version_args=("version",),
        version_markers=("ngrok",),
    ),
    "tailscale": ExecutableSpec(
        key="tailscale",
        display_name="Tailscale",
        executable_name="tailscale",
        bundled_product="tailscale",
        version_args=("version",),
        extra_known_paths=(
            "/Applications/Tailscale.app/Contents/MacOS/tailscale",
        ),
    ),
}


def executable_spec(key: str) -> ExecutableSpec:
    try:
        return _SPECS[key]
    except KeyError as exc:
        raise ValueError(f"未知客户端: {key}") from exc
