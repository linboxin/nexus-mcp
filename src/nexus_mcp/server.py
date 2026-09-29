"""MCP server entry point (stdio). Protocol only; no Moodle logic lives here."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator

from urllib.parse import urlparse

from mcp.server.mcpserver import MCPServer

from . import __version__, runtime
from .tools import register_all

if TYPE_CHECKING:
    from .oauth import NexusOAuthProvider

INSTRUCTIONS = """\
Nexus is Union College's Moodle. Everything here is READ-ONLY and comes from the signed-in student's own account.

Prefer the high-level tools so you don't chain many calls:
- "What's due?" / "this week" -> upcoming_assignments(days=7)
- "What's overdue?" -> overdue_assignments()
- "Did I submit X?" -> submission_status(assignment_id) or assignment_details(assignment_id)
- "What's my grade in X?" -> course_grade(course_id); all courses -> current_grades()
- "What changed?" -> course_updates(since="24h")
- "Find the notes on X" -> search_course_materials(query), then get_material(id) to read it
- "Briefing" -> daily_briefing(); "my week" -> weekly_briefing(); "what next?" -> what_should_i_do_next()
- list_courses / get_course only when the student asks about a specific course; nexus_status for identity/timezone.

Status "external" means the assignment is handed in outside Nexus (cs-gitlab, GitHub, ...): say "check <place>", never "not started". "What changed" includes edits to the course page's own text (instructors' News lists) and, if Google is connected, to linked Google Docs/Slides.

All times are already in the student's academic timezone (see the `timezone` fields). Submission and grade data can be cached for a few seconds: if `freshness.cached` is true, say "as of <time>" instead of "right now".

Errors carry codes: NEXUS_AUTH_ERROR (tell the student to run `nexus-mcp login` in a terminal to sign in via Okta), NEXUS_PERMISSION_ERROR (their role can't see it), NEXUS_UNSUPPORTED (Nexus disabled that Moodle function), NEXUS_RESOURCE_NOT_FOUND, NEXUS_API_ERROR (Nexus down/unreachable). Never present an empty result as "nothing due" when a tool errored.
"""


@asynccontextmanager
async def _lifespan(_server: MCPServer) -> AsyncIterator[dict]:
    try:
        yield {}
    finally:
        await runtime.close()


def create_server(
    *, auth_token: str | None = None, public_url: str | None = None, oauth: "NexusOAuthProvider | None" = None
) -> MCPServer:
    """Build the server. With ``auth_token`` every HTTP request must carry
    ``Authorization: Bearer <auth_token>`` (the SDK answers 401 otherwise).
    With ``oauth`` the server is also an OAuth authorization server (see
    ``oauth.py``); the static token, if any, is then accepted by the provider."""
    extra: dict = {}
    if oauth is not None:
        from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions

        from .oauth import SCOPE

        base = oauth.public_url
        extra = {
            "auth_server_provider": oauth,
            "auth": AuthSettings(
                issuer_url=base,
                resource_server_url=base + "/mcp",
                client_registration_options=ClientRegistrationOptions(
                    enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]
                ),
                revocation_options=RevocationOptions(enabled=True),
                validate_token_resource=False,
            ),
        }
    elif auth_token:
        from mcp.server.auth.settings import AuthSettings

        from .http_auth import StaticTokenVerifier

        base = (public_url or "http://127.0.0.1:8765").rstrip("/")
        extra = {
            "token_verifier": StaticTokenVerifier(auth_token),
            "auth": AuthSettings(issuer_url=base, resource_server_url=base + "/mcp", validate_token_resource=False),
        }
    server = MCPServer(
        "nexus-mcp",
        title="Nexus (Union College Moodle)",
        version=__version__,
        instructions=INSTRUCTIONS,
        lifespan=_lifespan,
        log_level="WARNING",  # keep stderr quiet: httpx would otherwise log every request at INFO
        **extra,
    )
    register_all(server)
    if oauth is not None:
        from .oauth import CONNECT_PATH, connect_routes

        server.custom_route(CONNECT_PATH, methods=["GET", "POST"])(connect_routes(oauth))
    return server


def build_oauth_provider(public_url: str, *, static_tokens: list[str] | None = None) -> "NexusOAuthProvider":
    """OAuth provider for ``serve --oauth``: owner = whoever the stored Nexus token belongs to."""
    import asyncio

    from .auth import TokenStore, resolve_token
    from .cli import verify_token
    from .config import Settings
    from .errors import NexusError
    from .moodle.nexus import Nexus
    from .oauth import NexusOAuthProvider

    settings = Settings.from_env()

    async def identify(token: str):
        return await verify_token(settings, token)

    async def on_new_token(stored) -> None:
        if not settings.token:  # an explicit NEXUS_TOKEN would win on restart anyway
            TokenStore(settings.base_url, mode=settings.token_storage, config_dir=settings.config_dir).save(stored)
        await runtime.close()
        runtime.set_nexus(Nexus.from_settings(settings, stored.token))

    provider = NexusOAuthProvider(
        public_url=public_url,
        site_url=settings.base_url,
        service=settings.service,
        state_dir=settings.config_dir,
        identify=identify,
        on_new_token=on_new_token,
        static_tokens=static_tokens,
        url_scheme=settings.url_scheme,
    )
    if provider.owner_user_id is None:
        try:
            token, _ = resolve_token(settings)
            owner = asyncio.run(verify_token(settings, token))
        except NexusError:
            owner = None
        if owner is not None and owner.user_id is not None:
            provider.set_owner(owner.user_id)
    return provider


def transport_security(public_url: str | None, host: str, port: int):
    """DNS-rebinding protection that also admits the public hostname.

    The SDK only accepts Host headers matching the bind address by default, so
    requests arriving through a tunnel (Host: xyz.trycloudflare.com) were
    rejected with 421. Bearer auth still gates everything.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    hosts = {f"{host}:*", host, "127.0.0.1:*", "localhost:*"}
    origins = {f"http://{host}:{port}", f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    if public_url:
        parsed = urlparse(public_url)
        if parsed.hostname:
            hosts.update({parsed.hostname, f"{parsed.hostname}:*"})
            origins.add(f"{parsed.scheme}://{parsed.netloc}")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=sorted(hosts), allowed_origins=sorted(origins)
    )


def serve(
    transport: str = "stdio",
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    auth_token: str | None = None,
    public_url: str | None = None,
    stateless: bool = False,
    oauth: bool = False,
) -> None:
    """Run the server. ``stdio`` for local clients; ``http`` = streamable HTTP at ``/mcp``.

    Without ``auth_token`` the HTTP door is open: bind to localhost only. With it,
    requests need ``Authorization: Bearer <auth_token>`` (what remote agents send).
    ``stateless`` answers each request independently (plain JSON, no SSE), which
    some hosted clients and tunnels handle better.
    """
    if transport == "http":
        provider = None
        if oauth:
            if not public_url:
                raise ValueError("--oauth needs --public-url (the https:// address clients use)")
            provider = build_oauth_provider(public_url, static_tokens=[auth_token] if auth_token else None)
        server = create_server(auth_token=auth_token, public_url=public_url or f"http://{host}:{port}", oauth=provider)
        server.run(
            transport="streamable-http",
            host=host,
            port=port,
            stateless_http=stateless,
            json_response=stateless,
            transport_security=transport_security(public_url, host, port),
        )
    else:
        create_server().run(transport="stdio")


def main() -> None:
    serve("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
