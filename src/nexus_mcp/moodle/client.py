"""Thin async HTTP client for Moodle's REST web-service protocol.

Only two Moodle endpoints are used:

* ``/webservice/rest/server.php`` (token-authenticated function calls)
* ``/lib/ajax/service-nologin.php`` (``tool_mobile_get_public_config`` only,
  which Moodle exposes without a login and which tells us whether web services
  are enabled and how login works)

plus ``/webservice/pluginfile.php`` for file downloads.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Iterable, Mapping

import httpx

from .. import __version__
from ..config import normalize_site_url
from ..errors import NexusAPIError, NexusAuthError, NexusNotFoundError, map_moodle_error

WS_PATH = "/webservice/rest/server.php"
PUBLIC_AJAX_PATH = "/lib/ajax/service-nologin.php"
USER_AGENT = f"nexus-mcp/{__version__} (student read-only MCP client)"


def flatten_params(params: Mapping[str, Any]) -> dict[str, str]:
    """Encode nested params the way Moodle's REST server expects.

    ``{"courseids": [1, 2], "options": {"userevents": True}}`` becomes
    ``courseids[0]=1&courseids[1]=2&options[userevents]=1``.
    Empty lists produce nothing (Moodle then applies the function default).
    """
    out: dict[str, str] = {}

    def walk(prefix: str, value: Any) -> None:
        if value is None:
            return
        if isinstance(value, bool):
            out[prefix] = "1" if value else "0"
        elif isinstance(value, (int, float, str)):
            out[prefix] = str(value)
        elif isinstance(value, Mapping):
            for key, item in value.items():
                walk(f"{prefix}[{key}]", item)
        elif isinstance(value, (list, tuple, set)):
            for index, item in enumerate(value):
                walk(f"{prefix}[{index}]", item)
        else:
            out[prefix] = str(value)

    for key, value in params.items():
        walk(key, value)
    return out


class MoodleClient:
    def __init__(
        self,
        base_url: str,
        token: str | None,
        *,
        timeout: float = 30.0,
        max_concurrency: int = 6,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = normalize_site_url(base_url)
        self._token = token
        self._http = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            transport=transport,
            follow_redirects=False,
        )
        self._sem = asyncio.Semaphore(max(1, max_concurrency))
        self._site_info: dict[str, Any] | None = None
        self._known_functions: frozenset[str] | None = None

    # -- metadata ----------------------------------------------------------
    @property
    def wsurl(self) -> str:
        return self.base_url + WS_PATH

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    @property
    def known_functions(self) -> frozenset[str] | None:
        return self._known_functions

    def set_known_functions(self, functions: Iterable[str]) -> None:
        self._known_functions = frozenset(functions)

    def has_function(self, name: str) -> bool:
        return self._known_functions is not None and name in self._known_functions

    # -- calls -------------------------------------------------------------
    async def call(self, function: str, **params: Any) -> Any:
        if not self._token:
            raise NexusAuthError(
                "No Nexus token is configured. Run `nexus-mcp login` to sign in through Union SSO."
            )
        data = {
            "wstoken": self._token,
            "wsfunction": function,
            "moodlewsrestformat": "json",
            **flatten_params(params),
        }
        async with self._sem:
            try:
                response = await self._http.post(self.wsurl, data=data)
            except httpx.TimeoutException as exc:
                raise NexusAPIError(
                    f"Timed out calling {function} on {self.base_url}.", details={"function": function}
                ) from exc
            except httpx.HTTPError as exc:
                raise NexusAPIError(
                    f"Network error calling {function} on {self.base_url}: {exc}", details={"function": function}
                ) from exc
        return self._parse(response, function)

    def _parse(self, response: httpx.Response, function: str) -> Any:
        if response.status_code >= 500:
            raise NexusAPIError(
                f"Nexus returned HTTP {response.status_code} for {function}; the site may be down or in maintenance.",
                details={"function": function, "status": response.status_code},
            )
        content_type = response.headers.get("content-type", "")
        try:
            payload = response.json()
        except ValueError as exc:
            if "text/html" in content_type or response.status_code in (301, 302, 303):
                raise NexusAPIError(
                    f"Nexus answered {function} with an HTML page instead of JSON (maintenance mode or an SSO "
                    "redirect). Check https://nexus.union.edu in a browser.",
                    details={"function": function, "status": response.status_code},
                ) from exc
            raise NexusAPIError(
                f"Nexus returned a non-JSON response for {function} (HTTP {response.status_code}).",
                details={"function": function, "status": response.status_code},
            ) from exc
        if isinstance(payload, dict) and ("exception" in payload or "errorcode" in payload):
            raise map_moodle_error(payload, function, self._known_functions)
        return payload

    async def get_site_info(self, *, refresh: bool = False) -> dict[str, Any]:
        if self._site_info is None or refresh:
            info = await self.call("core_webservice_get_site_info")
            if not isinstance(info, dict) or "userid" not in info:
                raise NexusAPIError("Unexpected core_webservice_get_site_info response from Nexus.")
            self._site_info = info
            self.set_known_functions(f.get("name", "") for f in info.get("functions", []))
        return self._site_info

    async def download(self, url: str, *, max_bytes: int = 8_000_000) -> tuple[bytes, str, bool]:
        """Download a Moodle file URL with the token. Returns (bytes, content-type, truncated)."""
        if not self._token:
            raise NexusAuthError("No Nexus token is configured.")
        if "/pluginfile.php" in url and "/webservice/pluginfile.php" not in url:
            url = url.replace("/pluginfile.php", "/webservice/pluginfile.php", 1)
        params = {"token": self._token} if "pluginfile.php" in url else {}
        async with self._sem:
            try:
                response = await self._http.get(url, params=params, follow_redirects=True)
            except httpx.HTTPError as exc:
                raise NexusAPIError(f"Could not download {url}: {exc}") from exc
        if response.status_code == 404:
            raise NexusNotFoundError("The file no longer exists on Nexus.", details={"url": url})
        if response.status_code >= 400:
            raise NexusAPIError(f"Nexus returned HTTP {response.status_code} for the file.", details={"url": url})
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict) and "errorcode" in payload:
                raise map_moodle_error(payload, "pluginfile", self._known_functions)
        body = response.content
        truncated = len(body) > max_bytes
        return body[:max_bytes], content_type, truncated

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "MoodleClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # -- unauthenticated probes -------------------------------------------
    @staticmethod
    async def fetch_public_config(
        base_url: str, *, timeout: float = 20.0, transport: httpx.AsyncBaseTransport | None = None
    ) -> dict[str, Any]:
        """``tool_mobile_get_public_config`` without a token (Moodle allows this)."""
        base = normalize_site_url(base_url)
        body = json.dumps([{"index": 0, "methodname": "tool_mobile_get_public_config", "args": {}}])
        async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}, transport=transport) as http:
            try:
                response = await http.post(
                    base + PUBLIC_AJAX_PATH,
                    params={"info": "tool_mobile_get_public_config"},
                    content=body,
                    headers={"Content-Type": "application/json"},
                )
            except httpx.HTTPError as exc:
                raise NexusAPIError(f"Could not reach {base}: {exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise NexusAPIError("Nexus did not answer the public-config probe with JSON.") from exc
        if not isinstance(payload, list) or not payload or payload[0].get("error"):
            raise NexusAPIError(f"Nexus public-config probe failed: {payload!r}"[:300])
        return payload[0].get("data") or {}

    @staticmethod
    async def check_reachable(
        base_url: str, *, timeout: float = 20.0, transport: httpx.AsyncBaseTransport | None = None
    ) -> dict[str, Any]:
        base = normalize_site_url(base_url)
        async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True, transport=transport) as http:
            try:
                response = await http.get(base + "/")
            except httpx.HTTPError as exc:
                raise NexusAPIError(f"Could not reach {base}: {exc}") from exc
        final = str(response.url)
        return {
            "status": response.status_code,
            "final_url": final,
            "sso_redirect": urlparse_host(final) != urlparse_host(base),
        }


def urlparse_host(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).netloc.lower()
