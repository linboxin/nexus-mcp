"""Register the server with AI clients (``nexus-mcp install`` / ``nexus-mcp setup``).

Every MCP client launches a stdio server the same way; only the config file
and its shape differ. GUI apps (Claude Desktop, Cursor, ...) start with a
minimal PATH, so the launcher is always written with an absolute path.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SERVER_KEY = "nexus"
PACKAGE = "union-nexus-mcp"


class ClientConfigError(Exception):
    """A client's config file could not be updated safely; the message carries the snippet."""


def _appdata() -> Path:
    return Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))


def _support(app: str) -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app
    if sys.platform.startswith("win"):
        return _appdata() / app
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / app


@dataclass(frozen=True)
class Client:
    key: str
    name: str
    fmt: str  # mcpServers | servers | toml | claude-cli
    path: Path
    detect: tuple[Path, ...]
    hint: str = "Restart it to load the server."

    @property
    def installed(self) -> bool:
        return any(p.exists() for p in self.detect)


def all_clients() -> list[Client]:
    home = Path.home()
    vscode_user = _support("Code") / "User"
    return [
        Client("claude-desktop", "Claude Desktop", "mcpServers", _support("Claude") / "claude_desktop_config.json", (_support("Claude"),), "Quit and reopen Claude Desktop; the tools appear under the connector menu."),
        Client("claude-code", "Claude Code", "claude-cli", home / ".claude.json", (home / ".claude.json", home / ".claude"), "Start a new Claude Code session; check with /mcp."),
        Client("cursor", "Cursor", "mcpServers", home / ".cursor" / "mcp.json", (home / ".cursor",), "Cursor picks it up from Settings → MCP; toggle it on if needed."),
        Client("windsurf", "Windsurf", "mcpServers", home / ".codeium" / "windsurf" / "mcp_config.json", (home / ".codeium" / "windsurf",), "Refresh MCP servers in Windsurf's Cascade panel."),
        Client("vscode", "VS Code (Copilot)", "servers", vscode_user / "mcp.json", (vscode_user,), "Open the Copilot chat, Agent mode → tools; start the server if prompted."),
        Client("gemini", "Gemini CLI", "mcpServers", home / ".gemini" / "settings.json", (home / ".gemini",), "Start a new gemini session; check with /mcp."),
        Client("codex", "Codex CLI", "toml", home / ".codex" / "config.toml", (home / ".codex",), "Start a new codex session."),
    ]


def get_client(key: str) -> Client:
    for client in all_clients():
        if client.key == key:
            return client
    raise ValueError(f"unknown client '{key}'; choose from {', '.join(c.key for c in all_clients())}")


def detect_clients() -> list[Client]:
    return [c for c in all_clients() if c.installed]


def server_command() -> tuple[str, list[str]]:
    """How a client should launch the server.

    * running from a source checkout  -> ``uv run --directory <repo> nexus-mcp serve``
    * installed from PyPI / as a tool -> ``uvx union-nexus-mcp serve``
    * last resort                     -> this interpreter, ``-m nexus_mcp.cli serve``
    Absolute paths are used so GUI apps with a minimal PATH can find them.
    """
    repo = Path(__file__).resolve().parents[2]
    pyproject = repo / "pyproject.toml"
    if pyproject.exists() and re.search(r'^name\s*=\s*"' + re.escape(PACKAGE) + '"', pyproject.read_text(errors="ignore"), re.M):
        uv = shutil.which("uv") or os.environ.get("UV") or "uv"
        return uv, ["run", "--directory", str(repo), "nexus-mcp", "serve"]
    uvx = shutil.which("uvx")
    if uvx:
        return uvx, [PACKAGE, "serve"]
    uv = shutil.which("uv") or os.environ.get("UV")
    if uv:
        return uv, ["tool", "run", PACKAGE, "serve"]
    script = shutil.which("nexus-mcp")
    if script:
        return script, ["serve"]
    return sys.executable, ["-m", "nexus_mcp.cli", "serve"]


def server_entry(command: str, args: list[str], fmt: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"command": command, "args": list(args)}
    if fmt == "servers":
        entry = {"type": "stdio", **entry}
    return entry


def render_snippet(client: Client, command: str, args: list[str]) -> str:
    if client.fmt == "toml":
        return f"[mcp_servers.{SERVER_KEY}]\ncommand = {json.dumps(command)}\nargs = {json.dumps(list(args))}\n"
    if client.fmt == "claude-cli":
        return "claude mcp add -s user " + SERVER_KEY + " -- " + " ".join(shlex.quote(x) for x in [command, *args])
    root = "servers" if client.fmt == "servers" else "mcpServers"
    return json.dumps({root: {SERVER_KEY: server_entry(command, args, client.fmt)}}, indent=2)


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_name(path.name + time.strftime(".bak-%Y%m%d-%H%M%S"))
    shutil.copy2(path, backup)
    return backup


def _write_json(client: Client, command: str, args: list[str], remove: bool, *, path: Path | None = None) -> str:
    path = path or client.path
    data: dict[str, Any] = {}
    if path.exists() and path.read_text("utf-8").strip():
        try:
            data = json.loads(path.read_text("utf-8"))
        except ValueError as exc:
            raise ClientConfigError(
                f"{path} is not plain JSON ({exc}); add this yourself:\n{render_snippet(client, command, args)}"
            ) from exc
    if not isinstance(data, dict):
        raise ClientConfigError(f"{path} does not contain a JSON object; add this yourself:\n{render_snippet(client, command, args)}")
    root = "servers" if client.fmt == "servers" else "mcpServers"
    servers = data.setdefault(root, {})
    if not isinstance(servers, dict):
        raise ClientConfigError(f"{path}: '{root}' is not an object; add this yourself:\n{render_snippet(client, command, args)}")
    if remove:
        if SERVER_KEY not in servers:
            return f"{client.name}: nothing to remove in {path}"
        servers.pop(SERVER_KEY)
        action = "removed from"
    else:
        servers[SERVER_KEY] = server_entry(command, args, client.fmt)
        action = "written to"
    backup = _backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    return f"{client.name}: server '{SERVER_KEY}' {action} {path}" + (f" (backup {backup.name})" if backup else "")


_TOML_BLOCK = re.compile(r"(?m)^\[mcp_servers\." + re.escape(SERVER_KEY) + r"\][ \t]*\n(?:(?!^\[[^\n]*\][ \t]*$).*\n?)*")


def _write_toml(client: Client, command: str, args: list[str], remove: bool) -> str:
    path = client.path
    text = path.read_text("utf-8") if path.exists() else ""
    had = bool(_TOML_BLOCK.search(text))
    text = _TOML_BLOCK.sub("", text).rstrip()
    if remove:
        if not had:
            return f"{client.name}: nothing to remove in {path}"
        action = "removed from"
    else:
        text = (text + "\n\n" if text else "") + render_snippet(client, command, args)
        action = "written to"
    backup = _backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", "utf-8")
    return f"{client.name}: server '{SERVER_KEY}' {action} {path}" + (f" (backup {backup.name})" if backup else "")


def _claude_code(client: Client, command: str, args: list[str], remove: bool) -> str:
    claude = shutil.which("claude")
    if claude:
        subprocess.run([claude, "mcp", "remove", "-s", "user", SERVER_KEY], capture_output=True, text=True)
        if remove:
            return f"{client.name}: server '{SERVER_KEY}' removed (claude mcp remove)"
        result = subprocess.run([claude, "mcp", "add", "-s", "user", SERVER_KEY, "--", command, *args], capture_output=True, text=True)
        if result.returncode == 0:
            return f"{client.name}: registered with `claude mcp add -s user {SERVER_KEY}`"
    # No CLI (or it failed): user-scope servers live at the top level of ~/.claude.json.
    return _write_json(Client(client.key, client.name, "mcpServers", client.path, client.detect, client.hint), command, args, remove)


def install(client: Client, *, command: str | None = None, args: list[str] | None = None, remove: bool = False, dry_run: bool = False) -> str:
    if command is None or args is None:
        command, args = server_command()
    if dry_run:
        verb = "would remove from" if remove else "would write to"
        return f"{client.name}: {verb} {client.path}\n{render_snippet(client, command, args)}"
    if client.fmt == "toml":
        return _write_toml(client, command, args, remove)
    if client.fmt == "claude-cli":
        return _claude_code(client, command, args, remove)
    return _write_json(client, command, args, remove)
