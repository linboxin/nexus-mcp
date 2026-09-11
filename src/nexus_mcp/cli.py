"""``nexus-mcp`` command line: login, logout, test-connection, whoami, list-courses, serve."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from . import __version__
from .auth import LoginSession, StoredToken, TokenStore, parse_token_url, resolve_token
from .clients import ClientConfigError, all_clients, detect_clients, get_client, install, server_command
from .config import Settings
from .errors import NexusAuthError, NexusError
from .moodle.client import MoodleClient
from .moodle.nexus import Nexus

OK, FAIL, WARN = "✓", "✗", "!"


def _err(*parts: Any) -> None:
    print(*parts, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# login / logout
# --------------------------------------------------------------------------- #


async def verify_token(settings: Settings, token: str) -> StoredToken:
    async with MoodleClient(settings.base_url, token, timeout=settings.http_timeout) as client:
        info = await client.get_site_info()
    site = str(info.get("siteurl") or "").rstrip("/")
    if site and site.lower() != settings.base_url.lower():
        raise NexusAuthError(f"The token belongs to {site}, not {settings.base_url}.")
    return StoredToken(
        token=token,
        site_url=settings.base_url,
        user_id=int(info["userid"]),
        fullname=str(info.get("fullname", "")),
        username=str(info.get("username", "")),
        obtained_at=time.time(),
        source="sso",
    )


def run_login(
    settings: Settings,
    store: TokenStore,
    *,
    no_browser: bool = False,
    no_handler: bool = False,
    token_url: str | None = None,
    passport: str | None = None,
    timeout: float = 600.0,
) -> StoredToken:
    """Interactive SSO login; returns the stored token or raises NexusError."""
    if token_url:
        bundle = parse_token_url(token_url, site_url=settings.base_url if passport else None, passport=passport)
    else:
        session = LoginSession(
            settings.base_url,
            service=settings.service,
            url_scheme=settings.url_scheme,
            config_dir=settings.config_dir,
            handler_schemes=settings.handler_schemes,
        )
        session.prepare(register_handler=not no_handler)
        _err(f"Nexus MCP login for {settings.base_url}")
        _err("")
        _err("1. Sign in through Union's Okta page that opens in your browser (we never see your password).")
        _err("2. Nexus then shows a green 'Your registration has been confirmed' banner. That is NOT the end:")
        _err("   the token is in the blue link below it, 'Click here if the app does not open automatically.'")
        if session.handler_app:
            _err("3. Click that link and allow 'Open Nexus MCP Login' when the browser asks; this terminal")
            _err("   receives the token automatically.")
            _err("   If no prompt appears: right-click the link → Copy Link Address → paste it here → Enter.")
        else:
            _err("3. Right-click that link → Copy Link Address → paste it here → Enter.")
        _err("")
        _err("Login URL (opens automatically; copy it into a browser if not):")
        _err("  " + session.launch_url)
        _err("")
        if not no_browser:
            session.open_browser()
        _err(f"Waiting up to {int(timeout)}s for the login to complete…")
        bundle = session.wait(timeout=float(timeout))
    stored = asyncio.run(verify_token(settings, bundle.token))
    backend = store.save(stored)
    _err(f"{OK} Signed in to Nexus as {stored.fullname} ({stored.username}); token stored in the {backend}.")
    _err("  Revoke it any time on https://nexus.union.edu/user/managetoken.php (Preferences → Security keys).")
    return stored


def cmd_login(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    store = TokenStore(settings.base_url, mode=args.storage or settings.token_storage, config_dir=settings.config_dir)
    try:
        run_login(settings, store, no_browser=args.no_browser, no_handler=args.no_handler, token_url=args.token_url, passport=args.passport, timeout=args.timeout)
    except NexusError as exc:
        _err(f"{FAIL} {exc.code}: {exc.message}")
        return 1
    except KeyboardInterrupt:
        _err("\nLogin cancelled.")
        return 130
    return 0


def cmd_logout(_args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    store = TokenStore(settings.base_url, mode=settings.token_storage, config_dir=settings.config_dir)
    store.clear()
    _err(f"{OK} Removed the stored Nexus token for {settings.base_url}.")
    _err("  To invalidate it on the server too, reset the 'Moodle mobile web service' key on")
    _err("  https://nexus.union.edu/user/managetoken.php")
    return 0


# --------------------------------------------------------------------------- #
# test-connection
# --------------------------------------------------------------------------- #

CAPABILITIES: list[tuple[str, str, list[str]]] = [
    ("Courses", "all", ["core_enrol_get_users_courses"]),
    ("Course details", "all", ["core_course_get_contents"]),
    ("Assignments", "all", ["mod_assign_get_assignments"]),
    ("Submission status", "all", ["mod_assign_get_submission_status"]),
    ("Calendar", "any", ["core_calendar_get_calendar_events", "core_calendar_get_action_events_by_timesort"]),
    ("Grades", "any", ["gradereport_user_get_grade_items", "gradereport_overview_get_course_grades"]),
    ("Materials", "all", ["core_course_get_contents", "core_course_get_course_module"]),
    ("Announcements", "all", ["mod_forum_get_forums_by_courses", "mod_forum_get_forum_discussions"]),
    ("Course updates", "all", ["core_course_get_updates_since"]),
    ("Notifications", "all", ["message_popup_get_popup_notifications"]),
    ("Page content", "all", ["mod_page_get_pages_by_courses"]),
]


async def _test_connection() -> int:
    settings = Settings.from_env()
    failures = 0

    def ok(label: str, detail: str = "") -> None:
        _err(f"{OK} {label}" + (f"  ({detail})" if detail else ""))

    def fail(label: str, detail: str = "") -> None:
        nonlocal failures
        failures += 1
        _err(f"{FAIL} {label}" + (f"  ({detail})" if detail else ""))

    # 1. reachable
    try:
        reach = await MoodleClient.check_reachable(settings.base_url, timeout=settings.http_timeout)
        ok("Nexus URL reachable", f"{settings.base_url}; HTTP {reach['status']}" + ("; redirects to SSO" if reach["sso_redirect"] else ""))
    except NexusError as exc:
        fail("Nexus URL reachable", exc.message)
        return 1

    # 2. web services enabled (public probe, no token)
    try:
        public = await MoodleClient.fetch_public_config(settings.base_url, timeout=settings.http_timeout)
        ws = bool(public.get("enablewebservices"))
        mobile = bool(public.get("enablemobilewebservice"))
        login_type = {1: "app", 2: "browser (SSO)", 3: "embedded browser"}.get(int(public.get("typeoflogin") or 0), "?")
        idps = ", ".join(p.get("name", "") for p in public.get("identityproviders", []) or [])
        if ws and mobile:
            ok("Moodle Web Services available", f"mobile service enabled; login via {login_type}; SSO: {idps or 'none'}")
        else:
            fail("Moodle Web Services available", f"enablewebservices={ws} enablemobilewebservice={mobile}")
    except NexusError as exc:
        fail("Moodle Web Services available", exc.message)

    # 3. auth
    try:
        token, source = resolve_token(settings)
    except NexusAuthError as exc:
        fail("Authentication successful", exc.message)
        return 1
    nx = Nexus.from_settings(settings, token)
    try:
        try:
            site = await nx.ensure_ready()
        except NexusError as exc:
            fail("Authentication successful", f"token from {source}: {exc.message}")
            return 1
        ok("Authentication successful", f"token from {source}")
        ok("Student identity available", f"{site.fullname} ({site.username}, id {site.user_id}); Moodle {site.release}; timezone {site.tz_name} [{site.tz_source}]")

        # 4. live API checks
        if nx.has("core_enrol_get_users_courses"):
            try:
                courses = await nx.courses.list_courses("all")
                current = [c for c in courses if c.classification == "inprogress"]
                ok("Course API available", f"{len(courses)} enrolled, {len(current)} in progress")
            except NexusError as exc:
                fail("Course API available", exc.message)
        else:
            fail("Course API available", "core_enrol_get_users_courses not in this token's service")
        if nx.has("mod_assign_get_assignments"):
            try:
                rows, _ = await nx.assignments.raw_assignments(None)
                ok("Assignment API available", f"{len(rows)} assignments visible")
            except NexusError as exc:
                fail("Assignment API available", exc.message)
        else:
            fail("Assignment API available", "mod_assign_get_assignments not in this token's service")
        if nx.has("core_calendar_get_calendar_events") or nx.has("core_calendar_get_action_events_by_timesort"):
            try:
                events, _ = await nx.calendar.events(days=14)
                ok("Calendar API available", f"{len(events)} events in the next 14 days")
            except NexusError as exc:
                fail("Calendar API available", exc.message)
        else:
            fail("Calendar API available", "no calendar functions in this token's service")
        if nx.has("gradereport_overview_get_course_grades"):
            try:
                await nx.grades.raw_overview()
                ok("Grade API available", "gradereport_overview_get_course_grades")
            except NexusError as exc:
                fail("Grade API available", exc.message)

        _err("")
        _err("Available capabilities (from this token's function list):")
        _err("")
        for label, mode, fns in CAPABILITIES:
            present = [f for f in fns if nx.has(f)]
            good = (len(present) == len(fns)) if mode == "all" else bool(present)
            missing = [f for f in fns if f not in present]
            mark = OK if good else FAIL
            _err(f"{mark} {label}" + (f"  (missing: {', '.join(missing)})" if missing and not good else ""))
        _err("")
        _err(f"{len(site.functions)} web-service functions are enabled for this token.")
    finally:
        await nx.aclose()
    return 1 if failures else 0


def cmd_test_connection(_args: argparse.Namespace) -> int:
    return asyncio.run(_test_connection())


# --------------------------------------------------------------------------- #
# whoami / list-courses / serve
# --------------------------------------------------------------------------- #


async def _with_nexus(fn):  # type: ignore[no-untyped-def]
    settings = Settings.from_env()
    token, _ = resolve_token(settings)
    nx = Nexus.from_settings(settings, token)
    try:
        await nx.ensure_ready()
        return await fn(nx)
    finally:
        await nx.aclose()


def cmd_whoami(_args: argparse.Namespace) -> int:
    async def run(nx: Nexus) -> int:
        print(json.dumps(nx.describe(), indent=2))
        return 0

    try:
        return asyncio.run(_with_nexus(run))
    except NexusError as exc:
        _err(f"{FAIL} {exc.code}: {exc.message}")
        return 1


def cmd_list_courses(args: argparse.Namespace) -> int:
    async def run(nx: Nexus) -> int:
        courses = await nx.courses.list_courses("all" if args.all else "inprogress")
        if args.json:
            print(json.dumps([{"id": c.id, "fullname": c.name, "shortname": c.short_name, "teacher": c.teacher, "classification": c.classification, "term": c.term} for c in courses], indent=2))
        else:
            for c in courses:
                print(f"{c.id:>7}  {c.name}  [{c.short_name}]  {c.teacher or ''}  ({c.classification}{', ' + c.term if c.term else ''})")
            if not courses:
                _err("No courses returned. Try --all.")
        return 0

    try:
        return asyncio.run(_with_nexus(run))
    except NexusError as exc:
        _err(f"{FAIL} {exc.code}: {exc.message}")
        return 1


def _selected_clients(keys: list[str] | None) -> list:
    if not keys or "detected" in keys:
        found = detect_clients()
        if not found:
            raise ValueError("no supported MCP client was detected; pass --client explicitly (see `nexus-mcp clients`)")
        return found
    if "all" in keys:
        return all_clients()
    return [get_client(k) for k in keys]


def cmd_clients(_args: argparse.Namespace) -> int:
    command, args = server_command()
    _err("Launcher that will be written: " + " ".join([command, *args]))
    _err("")
    for c in all_clients():
        mark = OK if c.installed else " "
        _err(f"{mark} {c.key:15} {c.name:20} {c.path}")
    _err("")
    _err("✓ = detected on this machine. `nexus-mcp install --client <key>` writes the config; --client detected does all found.")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    try:
        targets = _selected_clients(args.client)
    except ValueError as exc:
        _err(f"{FAIL} {exc}")
        return 1
    rc = 0
    for client in targets:
        try:
            _err(install(client, remove=args.remove, dry_run=args.dry_run))
            if not args.dry_run and not args.remove:
                _err(f"  {client.hint}")
        except ClientConfigError as exc:
            rc = 1
            _err(f"{FAIL} {exc}")
    return rc


def cmd_setup(args: argparse.Namespace) -> int:
    """login (if needed) → write client config → test-connection."""
    settings = Settings.from_env()
    store = TokenStore(settings.base_url, mode=settings.token_storage, config_dir=settings.config_dir)
    try:
        targets = _selected_clients(args.client)
    except ValueError as exc:
        _err(f"{FAIL} {exc}")
        return 1
    _err(f"Nexus MCP setup → {', '.join(c.name for c in targets)}")
    _err("")
    if not args.skip_login:
        signed_in = False
        try:
            token, source = resolve_token(settings)
            stored = asyncio.run(verify_token(settings, token))
            _err(f"{OK} Already signed in as {stored.fullname} (token from {source}).")
            signed_in = True
        except NexusError:
            pass
        if not signed_in:
            try:
                run_login(settings, store, no_browser=args.no_browser, no_handler=args.no_handler, timeout=args.timeout)
            except NexusError as exc:
                _err(f"{FAIL} {exc.code}: {exc.message}")
                return 1
            except KeyboardInterrupt:
                _err("\nSetup cancelled.")
                return 130
        _err("")
    rc = 0
    for client in targets:
        try:
            _err(f"{OK} " + install(client))
            _err(f"  {client.hint}")
        except ClientConfigError as exc:
            rc = 1
            _err(f"{FAIL} {exc}")
    if not args.skip_test:
        _err("")
        rc = max(rc, asyncio.run(_test_connection()))
    return rc


def cmd_serve(_args: argparse.Namespace) -> int:
    from .server import main as serve_main

    serve_main()
    return 0


# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nexus-mcp", description="Read-only MCP server for Union College's Nexus (Moodle).")
    parser.add_argument("--version", action="version", version=f"nexus-mcp {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_login = sub.add_parser("login", help="Sign in through Union SSO and store the Nexus token")
    p_login.add_argument("--no-browser", action="store_true", help="Don't open the browser automatically")
    p_login.add_argument("--no-handler", action="store_true", help="Don't register the nexusmcp:// URL handler (macOS)")
    p_login.add_argument("--token-url", help="Paste a nexusmcp://token=... link obtained manually (non-interactive)")
    p_login.add_argument("--passport", help="Passport used for the manual login URL (enables the site/passport check)")
    p_login.add_argument("--timeout", type=float, default=600, help="Seconds to wait for the browser login (default 600)")
    p_login.add_argument("--storage", choices=["auto", "keyring", "file"], help="Where to store the token (default: NEXUS_TOKEN_STORAGE or auto)")
    p_login.set_defaults(func=cmd_login)

    sub.add_parser("logout", help="Delete the stored token").set_defaults(func=cmd_logout)
    sub.add_parser("test-connection", help="Check reachability, web services, authentication and capabilities").set_defaults(func=cmd_test_connection)
    sub.add_parser("whoami", help="Print the signed-in identity and timezone").set_defaults(func=cmd_whoami)
    p_lc = sub.add_parser("list-courses", help="List courses (the Phase 0 proof of concept)")
    p_lc.add_argument("--all", action="store_true", help="Include past/future/hidden courses")
    p_lc.add_argument("--json", action="store_true", help="Print JSON")
    p_lc.set_defaults(func=cmd_list_courses)
    client_keys = [c.key for c in all_clients()] + ["detected", "all"]
    p_setup = sub.add_parser("setup", help="One-shot onboarding: login if needed, register with your AI client(s), test")
    p_setup.add_argument("--client", action="append", choices=client_keys, help="Client to configure (repeatable; default: all detected)")
    p_setup.add_argument("--no-browser", action="store_true", help="Don't open the browser automatically")
    p_setup.add_argument("--no-handler", action="store_true", help="Don't register the URL-scheme handler")
    p_setup.add_argument("--skip-login", action="store_true", help="Only write client config and test")
    p_setup.add_argument("--skip-test", action="store_true", help="Don't run test-connection at the end")
    p_setup.add_argument("--timeout", type=float, default=600, help="Seconds to wait for the browser login")
    p_setup.set_defaults(func=cmd_setup)
    p_install = sub.add_parser("install", help="Write the server entry into an AI client's config (Claude Desktop, Cursor, ...)")
    p_install.add_argument("--client", action="append", choices=client_keys, help="Client key (repeatable); 'detected' or 'all'")
    p_install.add_argument("--dry-run", action="store_true", help="Print the snippet instead of writing")
    p_install.add_argument("--remove", action="store_true", help="Remove the server entry instead")
    p_install.set_defaults(func=cmd_install)
    sub.add_parser("clients", help="List supported MCP clients and which are detected").set_defaults(func=cmd_clients)
    sub.add_parser("serve", help="Run the MCP server on stdio (default)").set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        return cmd_serve(args)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
