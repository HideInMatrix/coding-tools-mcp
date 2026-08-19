from __future__ import annotations

import re


SENSITIVE_ENV_RE = re.compile(
    r"(token|secret|credential|api[_-]?key|password|passwd|private)", re.I
)

SANDBOX_PROTECTED_ENV = {
    "HOME",
    "PATH",
    "TMPDIR",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "GOPROXY",
    "GOTOOLCHAIN",
    "PIP_NO_INDEX",
    "npm_config_offline",
    "YARN_ENABLE_NETWORK",
    "CARGO_NET_OFFLINE",
}

NETWORK_RE = re.compile(
    r"(https?://|\bcurl\b|\bwget\b|\bssh\b|\bscp\b|\bftp\b|\bnc\b|\bnetcat\b|socket\.|requests\.|urllib\.|httpx\b|aiohttp\b)",
    re.I,
)
NETWORK_COMMAND_RE = re.compile(
    r"(?:\b(?:npm|pnpm|yarn)\s+(?:install|i|ci|add|update|upgrade|publish|audit|outdated)\b|"
    r"\b(?:pip|pip3)\s+install\b|\bpython(?:3)?\s+-m\s+pip\s+install\b|"
    r"\bgo\s+(?:get|install)\b|\bgo\s+mod\s+download\b|"
    r"\bgit\s+(?:clone|fetch|pull|push|ls-remote)\b|"
    r"\bcargo\s+(?:fetch|install|update|publish|search)\b)",
    re.I,
)
GIT_METADATA_WRITE_RE = re.compile(
    r"\bgit\s+(?:add|commit|merge|rebase|cherry-pick|revert|checkout|switch|stash|tag|update-index|write-tree|reset|clean)\b",
    re.I,
)
WINDOWS_BATCH_META_RE = re.compile(r"[&|<>^()%!\r\n\"]")
SHELL_EXPANSION_RE = re.compile(r"(`|\$\(|\$\{|\$[A-Za-z_][A-Za-z0-9_]*)")
INLINE_SCRIPT_RE = re.compile(
    r"\b(python(?:3)?\s+-c|node\s+-e|ruby\s+-e|perl\s+-e|(?:ba|z|)sh\s+-c)\b",
    re.I,
)
DESTRUCTIVE_RE = re.compile(
    r"(^|\s)(sudo\b|su\b|mkfs\b|mount\b|umount\b|chmod\s+-R\b|chown\s+-R\b|rm\s+-[^\s]*r[^\s]*f\b|rm\s+-[^\s]*f[^\s]*r\b)",
    re.I,
)
REDIRECT_ESCAPE_RE = re.compile(r"(?:^|\s)(?:>|>>|<)\s*(/[^\s]+|\.\./[^\s]+)")


__all__ = [
    "DESTRUCTIVE_RE",
    "GIT_METADATA_WRITE_RE",
    "INLINE_SCRIPT_RE",
    "NETWORK_COMMAND_RE",
    "NETWORK_RE",
    "REDIRECT_ESCAPE_RE",
    "SANDBOX_PROTECTED_ENV",
    "SENSITIVE_ENV_RE",
    "SHELL_EXPANSION_RE",
    "WINDOWS_BATCH_META_RE",
]
