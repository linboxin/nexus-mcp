import json
from pathlib import Path

import pytest

from nexus_mcp import clients
from nexus_mcp.clients import Client, ClientConfigError, install, render_snippet, server_command

CMD, ARGS = "/opt/homebrew/bin/uvx", ["union-nexus-mcp", "serve"]


def mk(tmp_path: Path, fmt: str, name: str = "cfg.json") -> Client:
    return Client("x", "X Client", fmt, tmp_path / name, (tmp_path,))


def test_json_merge_keeps_other_servers_and_backs_up(tmp_path):
    client = mk(tmp_path, "mcpServers")
    client.path.write_text(json.dumps({"mcpServers": {"other": {"command": "npx", "args": ["x"]}}, "theme": "dark"}))
    msg = install(client, command=CMD, args=ARGS)
    data = json.loads(client.path.read_text())
    assert data["mcpServers"]["nexus"] == {"command": CMD, "args": ARGS}
    assert data["mcpServers"]["other"]["command"] == "npx" and data["theme"] == "dark"
    assert "backup" in msg and list(tmp_path.glob("cfg.json.bak-*"))
    msg = install(client, command=CMD, args=ARGS, remove=True)
    assert "removed" in msg and "nexus" not in json.loads(client.path.read_text())["mcpServers"]
    assert "nothing to remove" in install(client, command=CMD, args=ARGS, remove=True)


def test_json_created_when_missing(tmp_path):
    client = mk(tmp_path / "deep" / "er", "mcpServers")
    install(client, command=CMD, args=ARGS)
    assert json.loads(client.path.read_text()) == {"mcpServers": {"nexus": {"command": CMD, "args": ARGS}}}


def test_vscode_servers_format(tmp_path):
    client = mk(tmp_path, "servers", "mcp.json")
    install(client, command=CMD, args=ARGS)
    assert json.loads(client.path.read_text())["servers"]["nexus"] == {"type": "stdio", "command": CMD, "args": ARGS}


def test_invalid_json_is_not_clobbered(tmp_path):
    client = mk(tmp_path, "mcpServers")
    client.path.write_text("{ // comment\n}")
    with pytest.raises(ClientConfigError, match="add this yourself"):
        install(client, command=CMD, args=ARGS)
    assert client.path.read_text().startswith("{ //")


def test_toml_replace_and_remove(tmp_path):
    client = mk(tmp_path, "toml", "config.toml")
    client.path.write_text('model = "o3"\n\n[mcp_servers.nexus]\ncommand = "old"\nargs = ["a", "[b]"]\n\n[mcp_servers.other]\ncommand = "keep"\n')
    install(client, command=CMD, args=ARGS)
    text = client.path.read_text()
    assert text.count("[mcp_servers.nexus]") == 1 and '"old"' not in text
    assert '[mcp_servers.other]\ncommand = "keep"' in text and text.startswith('model = "o3"')
    assert f'command = "{CMD}"' in text
    install(client, command=CMD, args=ARGS, remove=True)
    text = client.path.read_text()
    assert "[mcp_servers.nexus]" not in text and "[mcp_servers.other]" in text


def test_claude_code_falls_back_to_file_without_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(clients.shutil, "which", lambda name: None)
    client = Client("claude-code", "Claude Code", "claude-cli", tmp_path / ".claude.json", (tmp_path,))
    client.path.write_text(json.dumps({"numStartups": 3, "mcpServers": {"acp": {"command": "acp"}}}))
    install(client, command=CMD, args=ARGS)
    data = json.loads(client.path.read_text())
    assert data["numStartups"] == 3 and set(data["mcpServers"]) == {"acp", "nexus"}


def test_dry_run_and_snippets(tmp_path):
    client = mk(tmp_path, "mcpServers")
    out = install(client, command=CMD, args=ARGS, dry_run=True)
    assert "would write to" in out and '"nexus"' in out and not client.path.exists()
    assert render_snippet(mk(tmp_path, "claude-cli"), CMD, ARGS).startswith("claude mcp add -s user nexus -- ")
    assert render_snippet(mk(tmp_path, "toml"), CMD, ARGS).startswith("[mcp_servers.nexus]")


def test_server_command_from_checkout_uses_uv_run():
    command, args = server_command()
    repo = Path(clients.__file__).resolve().parents[2]
    assert command.endswith("uv") and args == ["run", "--directory", str(repo), "nexus-mcp", "serve"]


def test_all_clients_have_unique_keys():
    keys = [c.key for c in clients.all_clients()]
    assert len(keys) == len(set(keys)) == 7
    assert clients.get_client("cursor").name == "Cursor"
    with pytest.raises(ValueError):
        clients.get_client("nope")
