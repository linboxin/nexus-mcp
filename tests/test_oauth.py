"""OAuth sign-in: the full Claude.ai-style flow against the real Starlette app, Moodle faked."""

import base64
import hashlib
import os
import re
import stat
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from nexus_mcp.auth import StoredToken
from nexus_mcp.errors import NexusAuthError
from nexus_mcp.oauth import CONNECT_PATH, MAX_ATTEMPTS, STATE_FILENAME, NexusOAuthProvider
from nexus_mcp.server import create_server

PUBLIC = "https://nexus.example.com"
SITE = "https://nexus.test"
OWNER = 42
STATIC = "nxs_" + "s" * 40
OWNER_TOKEN, OTHER_TOKEN = "a" * 32, "b" * 32
USERS = {OWNER_TOKEN: (OWNER, "Owner Student"), OTHER_TOKEN: (7, "Someone Else")}


async def fake_identify(token: str) -> StoredToken:
    if token not in USERS:
        raise NexusAuthError("Invalid token")
    uid, name = USERS[token]
    return StoredToken(token=token, site_url=SITE, user_id=uid, fullname=name, username=name.lower())


def token_link(passport: str, token: str, *, site: str = SITE) -> str:
    site_id = hashlib.md5((site + passport).encode()).hexdigest()
    return "ltgopenlmsapp://token=" + base64.b64encode(f"{site_id}:::{token}".encode()).decode()


def pkce() -> tuple[str, str]:
    verifier = "v" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


@pytest.fixture
def received():
    return []


@pytest.fixture
def provider(tmp_path, received):
    return NexusOAuthProvider(
        public_url=PUBLIC,
        site_url=SITE,
        service="moodle_mobile_app",
        state_dir=tmp_path,
        identify=fake_identify,
        owner_user_id=OWNER,
        on_new_token=received.append,
        static_tokens=[STATIC],
    )


@asynccontextmanager
async def app_client(provider):
    app = create_server(oauth=provider).streamable_http_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC) as c:
            yield c


async def register(client) -> dict:
    r = await client.post(
        "/register",
        json={"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"], "client_name": "Claude",
              "token_endpoint_auth_method": "none"},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def start_authorize(client, reg) -> str:
    _, challenge = pkce()
    r = await client.get(
        "/authorize",
        params={"response_type": "code", "client_id": reg["client_id"], "redirect_uri": reg["redirect_uris"][0],
                "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz"},
    )
    assert r.status_code == 302, r.text
    loc = urlparse(r.headers["location"])
    assert loc.path == CONNECT_PATH
    return parse_qs(loc.query)["req"][0]


async def connect(client, provider, req: str, token: str = OWNER_TOKEN, passport: str | None = None):
    passport = passport or provider._state.pending[req]["passport"]
    return await client.post(CONNECT_PATH, data={"req": req, "link": token_link(passport, token)})


async def test_metadata_advertises_registration_and_pkce(provider):
    async with app_client(provider) as client:
        meta = (await client.get("/.well-known/oauth-authorization-server")).json()
        assert meta["registration_endpoint"].endswith("/register")
        assert "S256" in meta["code_challenge_methods_supported"]
        prm = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
        assert prm["resource"] == PUBLIC + "/mcp"


async def test_full_flow_issues_tokens_that_open_mcp(provider, received):
    async with app_client(provider) as client:
        reg = await register(client)
        req = await start_authorize(client, reg)

        page = await client.get(CONNECT_PATH, params={"req": req})
        assert page.status_code == 200 and "Claude" in page.text
        passport = provider._state.pending[req]["passport"]
        assert f"passport={passport}" in page.text and "launch.php" in page.text

        r = await connect(client, provider, req)
        assert r.status_code == 303
        redirect = urlparse(r.headers["location"])
        q = parse_qs(redirect.query)
        assert redirect.netloc == "claude.ai" and q["state"] == ["xyz"]
        assert received and received[0].user_id == OWNER  # fresh Nexus token handed to the server

        verifier, _ = pkce()
        tok = await client.post(
            "/token",
            data={"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": reg["redirect_uris"][0],
                  "client_id": reg["client_id"], "code_verifier": verifier},
        )
        assert tok.status_code == 200, tok.text
        body = tok.json()
        assert body["token_type"].lower() == "bearer" and body["refresh_token"]

        # the code is single use
        again = await client.post(
            "/token",
            data={"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": reg["redirect_uris"][0],
                  "client_id": reg["client_id"], "code_verifier": verifier},
        )
        assert again.status_code == 400

        assert (await mcp_init(client, body["access_token"])).status_code == 200
        assert (await mcp_init(client, "nope")).status_code == 401

        # refresh rotates both tokens
        ref = await client.post(
            "/token",
            data={"grant_type": "refresh_token", "refresh_token": body["refresh_token"], "client_id": reg["client_id"]},
        )
        assert ref.status_code == 200, ref.text
        new = ref.json()
        assert new["access_token"] != body["access_token"]
        assert (await mcp_init(client, body["access_token"])).status_code == 401
        assert (await mcp_init(client, new["access_token"])).status_code == 200
        reuse = await client.post(
            "/token",
            data={"grant_type": "refresh_token", "refresh_token": body["refresh_token"], "client_id": reg["client_id"]},
        )
        assert reuse.status_code == 400


async def mcp_init(client, token: str):
    return await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
                 "Host": "127.0.0.1:8765"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
    )


async def test_static_bearer_still_works(provider):
    async with app_client(provider) as client:
        assert (await mcp_init(client, STATIC)).status_code == 200


async def test_other_student_is_refused(provider, received):
    async with app_client(provider) as client:
        req = await start_authorize(client, await register(client))
        r = await connect(client, provider, req, token=OTHER_TOKEN)
        assert r.status_code == 400 and "Someone Else" in r.text and "belongs to a different student" in r.text
        assert not received


async def test_link_from_another_login_attempt_is_refused(provider):
    async with app_client(provider) as client:
        req = await start_authorize(client, await register(client))
        r = await connect(client, provider, req, passport="0" * 32)
        assert r.status_code == 400 and "login attempt" in r.text


async def test_garbage_and_attempt_limit(provider):
    async with app_client(provider) as client:
        req = await start_authorize(client, await register(client))
        for _ in range(MAX_ATTEMPTS):
            r = await client.post(CONNECT_PATH, data={"req": req, "link": "hello"})
            assert r.status_code == 400
        r = await connect(client, provider, req)
        assert r.status_code == 400 and "Too many attempts" in r.text


async def test_unknown_request_shows_expired_page(provider):
    async with app_client(provider) as client:
        r = await client.get(CONNECT_PATH, params={"req": "bogus"})
        assert r.status_code == 400 and "expired" in r.text.lower()


async def test_page_escapes_client_name(provider):
    async with app_client(provider) as client:
        r = await client.post(
            "/register",
            json={"redirect_uris": ["https://x.example/cb"], "client_name": "<script>alert(1)</script>",
                  "token_endpoint_auth_method": "none"},
        )
        req = await start_authorize(client, r.json())
        page = await client.get(CONNECT_PATH, params={"req": req})
        assert "<script>alert(1)" not in page.text and "&lt;script&gt;" in page.text


async def test_state_is_private_and_secrets_hashed(provider, tmp_path):
    async with app_client(provider) as client:
        reg = await register(client)
        req = await start_authorize(client, reg)
        code = parse_qs(urlparse((await connect(client, provider, req)).headers["location"]).query)["code"][0]
        path = tmp_path / STATE_FILENAME
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert code not in path.read_text()
        assert not re.search(r"nx[acr]_[A-Za-z0-9_-]{20,}", path.read_text())


async def test_no_owner_means_no_authorize(tmp_path):
    p = NexusOAuthProvider(public_url=PUBLIC, site_url=SITE, service="moodle_mobile_app", state_dir=tmp_path,
                           identify=fake_identify)
    app = create_server(oauth=p).streamable_http_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC) as c:
            reg = await register(c)
            _, challenge = pkce()
            r = await c.get(
                "/authorize",
                params={"response_type": "code", "client_id": reg["client_id"], "redirect_uri": reg["redirect_uris"][0],
                        "code_challenge": challenge, "code_challenge_method": "S256", "state": "s"},
            )
            assert r.status_code == 302 and "error=temporarily_unavailable" in r.headers["location"]


async def test_revoke_all_signs_everyone_out(provider):
    async with app_client(provider) as client:
        reg = await register(client)
        req = await start_authorize(client, reg)
        code = parse_qs(urlparse((await connect(client, provider, req)).headers["location"]).query)["code"][0]
        verifier, _ = pkce()
        tok = (await client.post(
            "/token",
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": reg["redirect_uris"][0],
                  "client_id": reg["client_id"], "code_verifier": verifier},
        )).json()
        assert await provider.revoke_all() == 1
        assert (await mcp_init(client, tok["access_token"])).status_code == 401
