#!/usr/bin/env python3
"""Phase 0 diagnostic: read-only checks against Nexus' Moodle web services.

Runs without any credentials for the public probes, then (with a token) proves
authenticated access with the equivalent of ``list_courses()``.

    uv run scripts/test_moodle_api.py                       # uses the token stored by `nexus-mcp login`
    uv run scripts/test_moodle_api.py --login               # run the Okta SSO login flow first
    uv run scripts/test_moodle_api.py --token-url 'nexusmcp://token=...'
    uv run scripts/test_moodle_api.py --all                 # also probe assignments / calendar / grades / forums
    uv run scripts/test_moodle_api.py --public-only         # no token needed

Nothing here writes to Nexus. No passwords, cookies or secrets are requested.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nexus_mcp.auth import LoginSession, TokenStore, parse_token_url, resolve_token  # noqa: E402
from nexus_mcp.config import Settings  # noqa: E402
from nexus_mcp.errors import NexusAuthError, NexusError  # noqa: E402
from nexus_mcp.moodle.client import MoodleClient  # noqa: E402
from nexus_mcp.moodle.nexus import Nexus  # noqa: E402


def say(msg: str) -> None:
    print(msg, flush=True)


async def public_probes(settings: Settings) -> bool:
    say(f"== Public probes against {settings.base_url} (no token) ==")
    try:
        reach = await MoodleClient.check_reachable(settings.base_url)
        say(f"reachable: HTTP {reach['status']} -> {reach['final_url'][:90]}{'…' if len(reach['final_url']) > 90 else ''}")
    except NexusError as exc:
        say(f"NOT reachable: {exc.message}")
        return False
    try:
        cfg = await MoodleClient.fetch_public_config(settings.base_url)
    except NexusError as exc:
        say(f"public config failed: {exc.message}")
        return False
    keys = ["sitename", "enablewebservices", "enablemobilewebservice", "typeoflogin", "launchurl", "tool_mobile_disabledfeatures", "tool_mobile_androidappid", "maintenanceenabled"]
    for k in keys:
        say(f"  {k}: {cfg.get(k)!r}")
    for idp in cfg.get("identityproviders", []) or []:
        say(f"  identity provider: {idp.get('name')} -> {idp.get('url')}")
    return bool(cfg.get("enablewebservices")) and bool(cfg.get("enablemobilewebservice"))


async def authenticated_probes(settings: Settings, token: str, run_all: bool) -> int:
    nx = Nexus.from_settings(settings, token)
    try:
        say("\n== core_webservice_get_site_info ==")
        try:
            site = await nx.ensure_ready()
        except NexusError as exc:
            say(f"FAILED: {exc.code}: {exc.message}")
            return 1
        say(f"user: {site.fullname} ({site.username}, id {site.user_id}) | site: {site.site_name} | Moodle {site.release}")
        say(f"timezone: {site.tz_name} (source: {site.tz_source}) | {len(site.functions)} functions available to this token")

        say("\n== list_courses() ==")
        courses = await nx.courses.list_courses("all")
        say(json.dumps([{"id": c.id, "fullname": c.name} for c in courses], indent=2))
        say(f"{len(courses)} courses ({sum(1 for c in courses if c.classification == 'inprogress')} in progress)")
        if not run_all:
            return 0

        async def probe(label: str, coro):  # type: ignore[no-untyped-def]
            try:
                result = await coro
                say(f"{label}: OK -> {result}")
            except NexusError as exc:
                say(f"{label}: {exc.code}: {exc.message}")

        say("\n== extended read-only probes ==")

        async def assignments():  # type: ignore[no-untyped-def]
            rows, warnings = await nx.assignments.raw_assignments(None)
            return f"{len(rows)} assignments; warnings={warnings}"

        async def upcoming():  # type: ignore[no-untyped-def]
            items, _ = await nx.assignments.upcoming(days=14)
            return "; ".join(f"{a.course_short or a.course}: {a.name} due {a.due.short if a.due else '-'} [{a.status.status if a.status else a.status_note}]" for a in items[:5]) or "none in 14 days"

        async def calendar():  # type: ignore[no-untyped-def]
            events, _ = await nx.calendar.events(days=14)
            return f"{len(events)} events; first: " + "; ".join(f"{e.name} @ {e.start.short if e.start else '-'}" for e in events[:3])

        async def grades():  # type: ignore[no-untyped-def]
            data = await nx.grades.current_grades()
            return "; ".join(f"{c['course']}: {c['grade_display'] or c['access']}" for c in data["courses"][:6])

        async def forums():  # type: ignore[no-untyped-def]
            items, _ = await nx.notifications.announcements(days=30)
            return f"{len(items)} announcements in 30 days; latest: " + (items[0].subject if items else "-")

        async def contents():  # type: ignore[no-untyped-def]
            if not courses:
                return "no courses"
            cid = next((c.id for c in courses if c.classification == "inprogress"), courses[0].id)
            mats, _ = await nx.materials.all_materials(cid)
            return f"course {cid}: {len(mats)} materials"

        await probe("mod_assign_get_assignments", assignments())
        await probe("upcoming_assignments(14)", upcoming())
        await probe("calendar events(14)", calendar())
        await probe("current_grades", grades())
        await probe("announcements(30d)", forums())
        await probe("course materials", contents())
        return 0
    finally:
        await nx.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="Nexus base URL (default NEXUS_URL or https://nexus.union.edu)")
    parser.add_argument("--token", help="Use this web-service token (otherwise NEXUS_TOKEN or the stored token)")
    parser.add_argument("--token-url", help="Decode a nexusmcp://token=... link and use it (not stored unless --save)")
    parser.add_argument("--login", action="store_true", help="Run the browser SSO login flow first and store the token")
    parser.add_argument("--save", action="store_true", help="Store the token given via --token/--token-url")
    parser.add_argument("--all", action="store_true", help="Run the extended read-only probes")
    parser.add_argument("--public-only", action="store_true", help="Only run the unauthenticated probes")
    args = parser.parse_args()

    settings = Settings.from_env()
    if args.url:
        settings = Settings.from_env({**dict(__import__("os").environ), "NEXUS_URL": args.url})

    ws_ok = asyncio.run(public_probes(settings))
    if args.public_only:
        return 0 if ws_ok else 1

    token: str | None = args.token
    store = TokenStore(settings.base_url, mode=settings.token_storage, config_dir=settings.config_dir)
    try:
        if args.token_url:
            token = parse_token_url(args.token_url).token
        elif args.login:
            session = LoginSession(settings.base_url, service=settings.service, url_scheme=settings.url_scheme, config_dir=settings.config_dir, handler_schemes=settings.handler_schemes)
            session.prepare()
            say("\nOpening your browser for Union SSO. After signing in, allow 'Nexus MCP Login' to open, or paste the")
            say("'Click here to launch the app' link here.\n" + session.launch_url)
            session.open_browser()
            token = session.wait().token
            args.save = True
        if token is None:
            token, source = resolve_token(settings)
            say(f"\nusing stored token ({source})")
    except NexusAuthError as exc:
        say(f"\n{exc.code}: {exc.message}")
        say("Hint: run `nexus-mcp login` (or this script with --login) to obtain a token through Okta.")
        return 1

    rc = asyncio.run(authenticated_probes(settings, token, args.all))
    if rc == 0 and args.save and token:
        from nexus_mcp.cli import verify_token

        stored = asyncio.run(verify_token(settings, token))
        say(f"token stored in the {store.save(stored)}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
