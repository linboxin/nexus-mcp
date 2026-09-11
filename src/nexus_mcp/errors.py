"""Error taxonomy for Nexus MCP.

Every failure surfaced to an MCP client carries one of the stable codes below so
the AI (and the student) can tell an expired login apart from a missing
permission or a Moodle function Union has not enabled:

    NEXUS_AUTH_ERROR          token missing / invalid / expired
    NEXUS_PERMISSION_ERROR    Moodle refused because of the student's role
    NEXUS_API_ERROR           Nexus unreachable, HTML instead of JSON, 5xx, ...
    NEXUS_RESOURCE_NOT_FOUND  course / assignment / module does not exist
    NEXUS_UNSUPPORTED         web-service function not enabled on Nexus
"""

from __future__ import annotations

from typing import Any, Iterable


class NexusError(Exception):
    """Base class. ``code`` is one of the NEXUS_* identifiers."""

    code: str = "NEXUS_API_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.code}: {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, "details": self.details}


class NexusAuthError(NexusError):
    code = "NEXUS_AUTH_ERROR"


class NexusPermissionError(NexusError):
    code = "NEXUS_PERMISSION_ERROR"


class NexusAPIError(NexusError):
    code = "NEXUS_API_ERROR"


class NexusNotFoundError(NexusError):
    code = "NEXUS_RESOURCE_NOT_FOUND"


class NexusUnsupportedError(NexusError):
    code = "NEXUS_UNSUPPORTED"


# Moodle ``errorcode`` values, grouped by what they mean for a student client.
_AUTH_CODES = {"invalidtoken", "invalidlogin", "tokengenerationfailed", "usernotconfirmed", "usersuspended"}
_PERMISSION_CODES = {
    "nopermissions",
    "requireloginerror",
    "cannotviewreport",
    "notenrolledprofile",
    "usernotincourse",
    "usernotenrolled",
    "notingroup",
    "nopermissiontoviewgrades",
    "cannotviewprofile",
    "noviewdiscussionspermission",
    "cannotviewsubmissions",
    "notenrolled",
    "coursehidden",
    "courseisnotvisible",
    "noaccess",
    "accessdenied",
}
_NOT_FOUND_CODES = {
    "invalidrecord",
    "invalidcoursemodule",
    "invalidcoursemoduleid",
    "invalidcourseid",
    "invalidcmid",
    "invalidassignment",
    "invalidforumid",
    "invaliddiscussionid",
    "invaliduserid",
    "invalidcourse",
    "coursemisconf",
    "cannotfindcourse",
    "modulemisconf",
}
_UNSUPPORTED_CODES = {"enablewsdescription", "servicenotavailable", "webservicesdisabled", "functionnotfound"}


def map_moodle_error(
    payload: dict[str, Any],
    function: str,
    known_functions: Iterable[str] | None = None,
) -> NexusError:
    """Translate a Moodle REST error payload into the NexusError taxonomy.

    ``known_functions`` is the function list from ``core_webservice_get_site_info``
    (the authoritative list of what this token's service allows). When we have
    it, a failure on a function that is not in the list is reported as
    NEXUS_UNSUPPORTED regardless of the raw Moodle error code, because Moodle's
    wording for that case ("Access control exception", "invalidrecord") is
    misleading.
    """
    errorcode = str(payload.get("errorcode") or "")
    exception = str(payload.get("exception") or "")
    message = str(payload.get("message") or payload.get("error") or "Unknown Moodle error")
    details: dict[str, Any] = {"function": function, "errorcode": errorcode, "exception": exception}
    if payload.get("debuginfo"):
        details["debuginfo"] = str(payload["debuginfo"])[:500]
    known = set(known_functions) if known_functions is not None else None

    if errorcode in _AUTH_CODES:
        return NexusAuthError(
            f"Nexus rejected the stored token ({message}). Run `nexus-mcp login` to sign in again "
            "through Union's Okta SSO.",
            details=details,
        )
    if known is not None and function not in known:
        return NexusUnsupportedError(
            f"The Moodle function '{function}' is not enabled for this account's web service on Nexus "
            f"({message}).",
            details=details,
        )
    if errorcode == "accessexception" or exception == "webservice_access_exception":
        if known is None:
            return NexusUnsupportedError(
                f"Nexus refused access to '{function}'; it may not be enabled in Union's mobile web "
                f"service ({message}).",
                details=details,
            )
        return NexusPermissionError(f"Nexus refused access to '{function}': {message}", details=details)
    if errorcode in _PERMISSION_CODES or exception == "required_capability_exception":
        return NexusPermissionError(
            f"Your Nexus account does not have permission for '{function}': {message}", details=details
        )
    if errorcode in _NOT_FOUND_CODES or exception == "dml_missing_record_exception":
        return NexusNotFoundError(f"Nexus could not find that resource ({message}).", details=details)
    if errorcode in _UNSUPPORTED_CODES:
        return NexusUnsupportedError(f"Nexus does not support this operation ({message}).", details=details)
    return NexusAPIError(f"Nexus API error in '{function}': {message}", details=details)
