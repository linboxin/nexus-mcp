import os
import stat

import pytest

from nexus_mcp import cli
from nexus_mcp.http_auth import StaticTokenVerifier, find_tunnel_url, load_or_create_http_token, new_http_token
from nexus_mcp.server import create_server


async def test_static_verifier_accepts_only_the_secret():
    v = StaticTokenVerifier("nxs_" + "a" * 40)
    assert (await v.verify_token("nxs_" + "a" * 40)).client_id == "nexus-remote"
    assert await v.verify_token("nxs_" + "b" * 40) is None
    assert await v.verify_token("") is None
    with pytest.raises(ValueError):
        StaticTokenVerifier("short")


def test_token_file_is_private_and_stable(tmp_path):
    first = load_or_create_http_token(tmp_path)
    assert first.startswith("nxs_") and len(first) > 30
    assert stat.S_IMODE(os.stat(tmp_path / "http-token.txt").st_mode) == 0o600
    assert load_or_create_http_token(tmp_path) == first
    assert load_or_create_http_token(tmp_path, rotate=True) != first
    assert new_http_token() != new_http_token()


def test_find_tunnel_url():
    log = "2026-09-12 INF +---+\nINF |  https://tidy-lamp-42.trycloudflare.com  |\n"
    assert find_tunnel_url(log) == "https://tidy-lamp-42.trycloudflare.com"
    assert find_tunnel_url("no url here") is None


async def test_server_with_bearer_auth_builds_and_keeps_tools():
    server = create_server(auth_token="nxs_" + "x" * 40, public_url="https://example.trycloudflare.com")
    assert {t.name for t in await server.list_tools()} >= {"daily_briefing", "list_courses"}


def test_serve_refuses_public_bind_without_token(capsys, monkeypatch):
    monkeypatch.delenv("NEXUS_HTTP_TOKEN", raising=False)
    args = cli.build_parser().parse_args(["serve", "--transport", "http", "--host", "0.0.0.0"])
    assert args.func(args) == 2
    assert "Refusing" in capsys.readouterr().err


def test_transport_security_admits_tunnel_host():
    from nexus_mcp.server import transport_security

    ts = transport_security("https://tidy-lamp-42.trycloudflare.com", "127.0.0.1", 8765)
    assert ts.enable_dns_rebinding_protection
    assert "tidy-lamp-42.trycloudflare.com" in ts.allowed_hosts and "tidy-lamp-42.trycloudflare.com:*" in ts.allowed_hosts
    assert "127.0.0.1:*" in ts.allowed_hosts and "localhost:*" in ts.allowed_hosts
    assert "https://tidy-lamp-42.trycloudflare.com" in ts.allowed_origins
    local = transport_security(None, "127.0.0.1", 8765)
    assert not any("trycloudflare" in h for h in local.allowed_hosts)
