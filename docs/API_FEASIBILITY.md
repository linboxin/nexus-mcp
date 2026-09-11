# Nexus (nexus.union.edu) — Moodle API feasibility

Investigated **2026-09-11**, first with unauthenticated read-only HTTP probes
(no credentials, cookies or tokens involved), then confirmed the same day with a
student token obtained through the SSO login flow (`nexus-mcp test-connection`).

## TL;DR

| Question | Answer |
|---|---|
| Is Nexus Moodle? | Yes. Moodle **4.5.13 (Build 20260810)**, hosted by **Open LMS** (Blackboard's Moodle SaaS). Release confirmed after login. |
| Are Moodle Web Services enabled? | **Yes** — `enablewebservices: 1`. The REST endpoint is live. |
| Can a student account use them? | **Yes** — the mobile web service (`moodle_mobile_app`) is enabled (`enablemobilewebservice: 1`) and it is the service Moodle issues tokens for to *every* user who signs in through the mobile-app login flow. |
| How does login work? | Okta **SAML 2.0** via the `auth_saml2` plugin. The site forces login (the root URL 303s straight to `union.okta.com`). |
| Can a token be obtained legitimately? | **Yes** — Nexus is configured for mobile-app login *via a browser window* (`typeoflogin: 2`). That is exactly the flow the official Moodle/Open LMS app uses with SSO sites: the browser goes through Okta, then Moodle hands the app a token. No password ever touches this project. |
| Username/password token endpoint? | `login/token.php` responds, but SAML (Okta) accounts cannot authenticate with it, and we never ask for passwords anyway. Not used. |
| OAuth/OIDC? | Not used for Nexus login (`auth_oidc`/`auth_oauth2` issuer flow not exposed; the SSO is SAML). Irrelevant for us: the SAML browser flow feeds the same token mechanism. |
| Anything disabled by Union? | `tool_mobile_disabledfeatures` is empty: no mobile feature is switched off. Whether individual *functions* are trimmed from the mobile service is only knowable after login (see §4). |

**Verdict: feasible.** Build against the official mobile-service function set;
verify the exact list with `core_webservice_get_site_info().functions` at runtime.

## 1. Probes and observations

| Probe | Result |
|---|---|
| `GET https://nexus.union.edu/` | 303 → `https://union.okta.com/app/unioncollege_nexus_2/.../sso/saml?SAMLRequest=…&RelayState=https://nexus.union.edu/`. Cookies set: `MoodleSession`, `MDL_SSP_SessID` (SimpleSAMLphp, i.e. `auth_saml2`). Server: Apache. |
| `POST /lib/ajax/service-nologin.php?info=tool_mobile_get_public_config` | 200. `sitename: "Union's Learning Management System"`, `enablewebservices: 1`, `enablemobilewebservice: 1`, `typeoflogin: 2`, `launchurl: https://nexus.union.edu/admin/tool/mobile/launch.php`, `identityproviders: [{name: "Login via Okta", url: "/auth/saml2/login.php?wants&idp=d6c1f799e0854767d05379bdb8ffac22&passive=off"}]`, `tool_mobile_disabledfeatures: ""`, `tool_mobile_androidappid: com.openlms.openlmsmobile`, `tool_mobile_iosappid: 1553337282`, `tool_mobile_setuplink: https://www.openlms.net/open-lms-mobile-app/`, `country: US`, `lang: en_us`, `maintenanceenabled: 0`. |
| `GET /webservice/rest/server.php?wsfunction=core_webservice_get_site_info&moodlewsrestformat=json` (no token) | `{"exception":"core\\exception\\moodle_exception","errorcode":"invalidtoken"}` — the REST protocol is enabled (a disabled protocol answers differently). |
| `GET /login/token.php?username=probe&password=probe&service=moodle_mobile_app` | `{"errorcode":"invalidlogin"}` — endpoint alive, but useless for Okta accounts. |
| `GET /login/index.php?saml=off` | Renders Moodle's own login page (theme `boost`) with a "Login via Okta" button. `M.cfg.usertimezone = "America/New_York"`, `M.cfg.apibase = https://nexus.union.edu/r.php/api` (the core router introduced in Moodle 4.5). |
| `GET /admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=…&urlscheme=moodlemobile` | 303 → Okta with `RelayState` pointing back at `launch.php` with the same parameters — the SSO round-trip preserves the launch request. |
| `GET /lib/upgrade.txt`, `/admin/environment.xml` | 404 (version files blocked, as usual on hosted Moodle). |

## 2. Moodle version

Version files are blocked, so the release was fingerprinted from which plugin
`version.php` files exist (HTTP 200 = present, 404 = absent):

| Present (200) | Meaning |
|---|---|
| `mod/subsection`, `ai/provider/openai`, `ai/placement/courseassist`, `lib/editor/tiny/plugins/aiplacement` | Introduced in Moodle **4.5** |
| `admin/tool/mfa`, `report/themeusage`, `communication/provider/matrix`, `lib/editor/tiny` | 4.1–4.3 features (consistent) |
| `lib/editor/atto`, `mod/chat`, `mod/survey` | Still present → **older than 5.0** (removed from core in 5.0) |
| `lib/editor/tinymce` → 404 | Legacy TinyMCE gone → ≥ 4.1 |
| `M.cfg.apibase = /r.php/api` | Core router → ≥ 4.5 |

Conclusion: **Moodle 4.5.x (LTS)**. Union-visible extras: `auth/saml2` (Okta),
`mod/turnitintooltwo`, `mod/hvp`, `mod/h5pactivity`, `mod/lti`, `mod/bigbluebuttonbn`,
`filter/mathjaxloader`. Not present: `theme/snap`, `local/mr` (so not the Open LMS
"Snap" flavour), `mod/zoom`, `local/kaltura`, `mod/kalvidres`, `plagiarism/turnitin`.

Confirmed after login: `core_webservice_get_site_info().release` =
**`4.5.13 (Build: 20260810)`**.

## 3. Authentication mechanisms

1. **Website login** — Okta SAML (`auth_saml2`). Forced for everyone; a manual
   Moodle form exists at `?saml=off` for non-SSO accounts only.
2. **Web-service tokens** — Moodle issues per-user tokens for *enabled external
   services*. Students do not have `moodle/webservice:createtoken` (manager-only
   by default), so they cannot mint arbitrary tokens on the Security keys page.
   They **do** have `moodle/webservice:createmobiletoken` (granted to the
   authenticated-user archetype), which is what the mobile-app launch flow uses.
3. **Mobile-app SSO launch flow** (`admin/tool/mobile/launch.php`) — the
   sanctioned way to give an SSO user a token:
   - Client opens `launch.php?service=moodle_mobile_app&passport=<nonce>&urlscheme=<scheme>&confirmed=1`.
   - Moodle stores the request in a 15-minute cookie, runs `require_login()`
     → Okta → back to `launch.php`.
   - Moodle calls `generate_token_for_current_user(moodle_mobile_app)` and
     returns `<scheme>://token=base64(md5(wwwroot+passport):::token[:::privatetoken])`.
     With `confirmed=1` the token link is rendered on an HTML page ("Click here
     to launch the app") instead of a bare redirect, so it can be copied.
   - `urlscheme` must match `^[a-zA-Z][a-zA-Z0-9-+.]*$` (no `http://…` capture
     trick). **Union forces the scheme:** `tool_mobile | forcedurlscheme` is set
     to `ltgopenlmsapp` (the Open LMS app), so the link Nexus renders is always
     `ltgopenlmsapp://token=…` whatever `urlscheme` we request (verified
     2026-09-11 after login). The macOS handler therefore claims both
     `nexusmcp://` and `ltgopenlmsapp://`; the parser accepts any scheme.
   This is implemented in `nexus_mcp/auth/client.py` and used by `nexus-mcp login`.
   See [AUTHENTICATION.md](AUTHENTICATION.md).
4. **OAuth 2 / OIDC** — `auth/oauth2` (core) exists but Nexus' identity provider
   is SAML; `launch.php`'s `oauthsso` parameter is therefore not applicable.
5. **Revocation** — the student can reset/delete the mobile token on
   `/user/managetoken.php` (Preferences → Security keys). Admins can also set a
   token validity period; if Union does, tokens expire and `nexus-mcp login`
   must be repeated (the server reports `NEXUS_AUTH_ERROR`).

## 4. Web-service functions available to a student token

The `moodle_mobile_app` service is a fixed set declared by Moodle core and its
plugins (`db/services.php`, `'services' => [MOODLE_OFFICIAL_MOBILE_SERVICE]`).
Parsing the Moodle 4.5 sources gives **284** such functions. Role capabilities
then gate what a *student* sees inside each call. The functions this project
relies on (all `type = read`):

| Feature | Function | In mobile service (4.5 source) | Capability notes |
|---|---|---|---|
| Identity, function list, release | `core_webservice_get_site_info` | ✓ | none |
| Timezone | `core_user_get_users_by_field` | ✓ | own record always readable |
| Courses | `core_enrol_get_users_courses` | ✓ | own enrolments |
| Course details (teachers, summary) | `core_course_get_courses_by_field` | ✓ | enrolled courses |
| Sections, modules, files | `core_course_get_contents` | ✓ | enrolled; hidden items filtered by Moodle |
| Module lookup by cmid | `core_course_get_course_module` | ✓ | |
| Assignments (with per-student override dates) | `mod_assign_get_assignments` | ✓ | `mod/assign:view` |
| Submission status, feedback, extension | `mod_assign_get_submission_status` | ✓ | `mod/assign:view` (own) |
| Calendar (all event kinds) | `core_calendar_get_calendar_events` | ✓ | |
| Timeline / action events (with URLs, overdue flag) | `core_calendar_get_action_events_by_timesort` | ✓ | |
| Course totals across courses | `gradereport_overview_get_course_grades` | ✓ | course `showgrades` setting |
| Per-course grade items + feedback | `gradereport_user_get_grade_items` | ✓ | `gradereport/user:view` (students have it unless the teacher hides grades) |
| Announcements | `mod_forum_get_forums_by_courses`, `mod_forum_get_forum_discussions` | ✓ | `mod/forum:viewdiscussion` |
| What changed since | `core_course_get_updates_since` | ✓ | |
| Notifications | `message_popup_get_popup_notifications` | ✓ | own |
| Page content | `mod_page_get_pages_by_courses` | ✓ | `mod/page:view` |
| Quizzes (deadlines via calendar; details later) | `mod_quiz_get_quizzes_by_courses` | ✓ | `mod/quiz:view` |
| Files (PDF, docs) | `webservice/pluginfile.php?token=…` | n/a | file access follows module visibility |

Union can remove functions from the service or restrict it; the authoritative
list for *this* token is `core_webservice_get_site_info().functions`, which the
server loads at start-up and `test-connection` prints as a capability matrix.

**Confirmed with a student token (2026-09-11):** the service exposes **453**
functions (Open LMS adds its own on top of Moodle's 284), and every function in
the table above is present. All capability rows in `test-connection` are ✓:
Courses, Course details, Assignments, Submission status, Calendar, Grades,
Materials, Announcements, Course updates, Notifications, Page content. The
student's Moodle account timezone is `99` (server default), so the site default
`America/New_York` is used.

Not available through web services (any Moodle): the **grade history report**
(`gradereport_history` has no external functions), the "submitted at" instant
separate from a submission's last-modified time, and global search unless the
admin enables it (`core_search_get_results` needs global search on).

## 5. Feature support matrix

| Desired feature | Status | How |
|---|---|---|
| `list_courses`, `get_course` | ✓ supported | courses + course fields + contents |
| `upcoming_assignments`, `overdue_assignments`, `assignment_details`, `submission_status` | ✓ supported | assignments + submission status (extensions honoured) |
| `upcoming_events` | ✓ supported | calendar + action events merged |
| `current_grades`, `course_grade` | ✓ supported, per-course permission-dependent | overview + grade items; hidden gradebooks reported, not silently empty |
| `grade_history` | ⚠ partial by design | no history WS; returns current grades with graded dates and says so |
| `search_course_materials` | ✓ supported (local text search) | course contents index |
| `get_material` | ✓ pages, books, links, labels, text/HTML files; PDF text with optional `pypdf`; other binaries metadata only | contents + pluginfile |
| `recent_announcements` | ✓ supported | news forums |
| `course_updates` | ✓ supported | updates-since + announcements + notifications |
| Intelligence tools | ✓ composed from the above | no extra API |
| Write operations (submit, post, message) | ✗ deliberately not implemented | read-only project |

## 6. Union-specific limitations and unknowns

- **Login is interactive by nature.** Okta (and MFA) must be completed in the
  student's browser. This is by design; there is no headless login.
- **Token lifetime** is set by Union's admins (`validuntil`). Unknown until
  the first login; `test-connection` will show `NEXUS_AUTH_ERROR` when expired.
- **Term dates.** Confirmed: academic courses carry start/end dates, so past
  terms classify as `past` (13 of 21 enrolments). The 8 "in progress" courses
  include non-academic ones (Academic Integrity Training, DEI, Strategic Plan,
  Recordings, Career Accelerator, Middle States Accreditation) that never end;
  they add a little noise to briefings but are genuinely open enrolments.
- **Hidden modules.** `mod_assign_get_assignments` returns one "No access
  rights in module context" warning per assignment the student cannot see
  (21 on this account); the service collapses them into one counted line.
- **Third-party activities** (Turnitin, H5P, LTI/BigBlueButton) are visible as
  course modules but have no student-readable content functions.
- **Hidden gradebooks.** Instructors can hide grades per course; those courses
  are reported with `access: hidden`.
- **Global search** availability is unknown (probably off); material search is
  therefore a local index over course contents.
- **Rate limits.** Open LMS hosts many schools; the client limits itself to 6
  concurrent requests and caches conservatively.

## 7. Prior art survey (don't rebuild the wheel)

| Project | Language / license | Auth | Fit for Nexus |
|---|---|---|---|
| [loyaniu/moodle-mcp](https://github.com/loyaniu/moodle-mcp) (PyPI `moodle-mcp` 0.2.1) | Python, **no license** | token pasted from `/user/managetoken.php` | Closest in scope (30 tools incl. `daily_briefing`, overdue, study load). Blocked by: no SSO token flow (Union students can't create tokens on that page), no license to reuse code, no NEXUS-style error taxonomy/cache labelling, `requests` (sync). Used as a *reference* for function choices only. |
| [Jawadh-Salih/moodle-mcp-server](https://github.com/Jawadh-Salih/moodle-mcp-server) | Go, MIT | username/password → `login/token.php`, or pasted token | Student-oriented but password-based; SAML accounts can't use it. Includes `submit_assignment` (write). |
| [theredbluepill/moodle-mcp-server](https://github.com/theredbluepill/moodle-mcp-server) | Python, Apache-2.0 | pre-made token | Admin/teacher CRUD (create course, enrol users). Wrong audience. |
| [scatjay/isu-moodle-mcp](https://github.com/scatjay/isu-moodle-mcp) | Python | pre-made token | Teacher-oriented (rosters, grade matrices). |
| [peancor](https://github.com/peancor/moodle-mcp-server), [jctovar/mcp-moodle](https://github.com/jctovar/mcp-moodle), [Hefi002](https://github.com/Hefi002/tfg-mcp-moodle-server) | TS/Python | token | Teacher/admin or generic CRUD. |
| [C0D3D3V/Moodle-DL](https://github.com/C0D3D3V/Moodle-DL) | Python, GPL-3.0 | **SSO launch flow** (user pastes the `moodlemobile://token=` link) | Not an MCP server, but proves the SSO token flow works for institutions with SAML; its `extract_token` matches the Moodle source. Technique reused (implemented independently from `launch.php`, GPL code not copied). |

Decision: build this project (spec-specific tools, SSO flow, error taxonomy,
labelled caching, timezone handling, diagnostics) on top of well-maintained
building blocks rather than forking an unlicensed/password-based server:
`mcp` (official Python SDK 2.x), `httpx`, `pydantic`, `keyring`, `platformdirs`.

## 8. Phase 0 checklist

- [x] Determine Moodle version — 4.5.x (fingerprint); exact release printed after login
- [x] Determine API availability — WS + mobile service enabled, REST live
- [x] Determine authentication method — Okta SAML + Moodle mobile launch flow
- [x] Authenticate legitimately — `nexus-mcp login` (Okta in the browser; token link uses the forced `ltgopenlmsapp://` scheme)
- [x] `list_courses()` — 21 courses returned (`nexus-mcp list-courses --all`, `scripts/test_moodle_api.py --all`)
