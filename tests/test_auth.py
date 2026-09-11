import base64
import hashlib
import json
import os

import pytest

from nexus_mcp.auth import TokenStore, build_launch_url, parse_token_url, resolve_token
from nexus_mcp.auth.client import StoredToken
from nexus_mcp.config import Settings
from nexus_mcp.errors import NexusAuthError

SITE = "https://nexus.union.edu"
TOKEN = "0123456789abcdef0123456789abcdef"


def make_link(passport: str, *, site: str = SITE, private: str | None = "priv", scheme: str = "nexusmcp") -> str:
    site_id = hashlib.md5((site + passport).encode()).hexdigest()
    payload = f"{site_id}:::{TOKEN}" + (f":::{private}" if private else "")
    return f"{scheme}://token=" + base64.b64encode(payload.encode()).decode()


def test_build_launch_url_has_all_parameters():
    url = build_launch_url(SITE + "/", "abc123")
    assert url.startswith(SITE + "/admin/tool/mobile/launch.php?")
    assert "service=moodle_mobile_app" in url
    assert "passport=abc123" in url
    assert "urlscheme=nexusmcp" in url
    assert "confirmed=1" in url


def test_build_launch_url_rejects_bad_scheme():
    with pytest.raises(ValueError):
        build_launch_url(SITE, "p", url_scheme="http://localhost")


def test_parse_token_url_roundtrip_with_passport_check():
    bundle = parse_token_url(make_link("nonce"), site_url=SITE, passport="nonce")
    assert bundle.token == TOKEN
    assert bundle.private_token == "priv"
    assert bundle.scheme == "nexusmcp"


def test_parse_token_url_rejects_other_passport():
    with pytest.raises(NexusAuthError, match="mismatch"):
        parse_token_url(make_link("nonce"), site_url=SITE, passport="other")


def test_parse_token_url_accepts_bare_base64_and_url_encoding():
    link = make_link("n", private=None)
    blob = link.split("token=", 1)[1]
    assert parse_token_url(blob).token == TOKEN
    assert parse_token_url(link.replace("=", "%3D")).token == TOKEN
    assert parse_token_url(f"  '{link}' ").token == TOKEN


def test_parse_token_url_rejects_garbage():
    with pytest.raises(NexusAuthError):
        parse_token_url("nexusmcp://token=not-base64-at-all!!")
    with pytest.raises(NexusAuthError):
        parse_token_url(base64.b64encode(b"only-one-part").decode())


def test_token_store_file_backend(tmp_path):
    store = TokenStore(SITE, mode="file", config_dir=tmp_path)
    assert store.load() is None
    stored = StoredToken(token=TOKEN, site_url=SITE, user_id=1, fullname="Sam", username="sam", obtained_at=1.0)
    assert store.save(stored) == "file"
    assert oct(os.stat(store.file_path).st_mode & 0o777) == "0o600"
    loaded = store.load()
    assert loaded is not None and loaded.token == TOKEN and loaded.fullname == "Sam"
    assert json.loads(store.file_path.read_text())["nexus.union.edu"]["token"] == TOKEN
    store.clear()
    assert store.load() is None


def test_resolve_token_prefers_env_then_errors(tmp_path):
    assert resolve_token(Settings(token="envtoken")) == ("envtoken", "env")
    with pytest.raises(NexusAuthError, match="nexus-mcp login"):
        resolve_token(Settings(token=None, token_storage="file", config_dir=tmp_path))


def test_parse_token_url_accepts_forced_openlms_scheme():
    bundle = parse_token_url(make_link("nonce", scheme="ltgopenlmsapp"), site_url=SITE, passport="nonce")
    assert bundle.token == TOKEN and bundle.scheme == "ltgopenlmsapp"


def test_settings_handler_schemes_default_and_override():
    assert Settings.from_env({}, dotenv=False).handler_schemes == ("nexusmcp", "ltgopenlmsapp")
    assert Settings.from_env({"NEXUS_HANDLER_SCHEMES": "a, b"}, dotenv=False).handler_schemes == ("a", "b")


def test_callback_module_writes_link(tmp_path):
    from nexus_mcp.auth.callback import main

    out = tmp_path / "cb.txt"
    assert main(["--out", str(out), "ltgopenlmsapp://token=abc"]) == 0
    assert out.read_text() == "ltgopenlmsapp://token=abc"
    assert main(["--out", str(out)]) == 1
