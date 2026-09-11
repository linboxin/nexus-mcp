import httpx
import pytest

from nexus_mcp.errors import (
    NexusAPIError,
    NexusAuthError,
    NexusNotFoundError,
    NexusPermissionError,
    NexusUnsupportedError,
)
from nexus_mcp.moodle.client import flatten_params
from tests.conftest import SITE, make_nexus, site_info


def test_flatten_params_nested():
    out = flatten_params({"courseids": [1, 2], "options": {"userevents": True, "timestart": 5}, "skip": None, "empty": []})
    assert out == {"courseids[0]": "1", "courseids[1]": "2", "options[userevents]": "1", "options[timestart]": "5"}


async def test_invalid_token_is_auth_error(fake):
    nx = make_nexus(token="badtoken")
    with pytest.raises(NexusAuthError) as info:
        await nx.ensure_ready()
    assert info.value.code == "NEXUS_AUTH_ERROR"
    assert "nexus-mcp login" in info.value.message
    await nx.aclose()


async def test_expired_token_is_auth_error(fake):
    fake.error("core_webservice_get_site_info", "invalidtoken", "Invalid token - token expired")
    nx = make_nexus()
    with pytest.raises(NexusAuthError, match="expired"):
        await nx.ensure_ready()
    await nx.aclose()


async def test_permission_failure(fake, nexus):
    fake.error("core_enrol_get_users_courses", "nopermissions", "Sorry, but you do not currently have permissions")
    with pytest.raises(NexusPermissionError) as info:
        await nexus.courses.list_courses("all")
    assert info.value.code == "NEXUS_PERMISSION_ERROR"


async def test_function_missing_from_service_is_unsupported(fake):
    fake.on("core_webservice_get_site_info", site_info(functions=["core_webservice_get_site_info", "core_user_get_users_by_field"]))
    nx = make_nexus()
    await nx.ensure_ready()
    with pytest.raises(NexusUnsupportedError) as info:
        await nx.courses.list_courses("all")
    assert info.value.code == "NEXUS_UNSUPPORTED"
    assert "core_enrol_get_users_courses" in info.value.message
    await nx.aclose()


async def test_moodle_error_on_unknown_function_maps_to_unsupported_even_if_invalidrecord(fake):
    fake.on("core_webservice_get_site_info", site_info(functions=["core_webservice_get_site_info"]))
    nx = make_nexus()
    await nx.ensure_ready()
    fake.error("mod_quiz_get_quizzes_by_courses", "invalidrecord", "Can't find data record in database table external_functions.", exception="dml_missing_record_exception")
    with pytest.raises(NexusUnsupportedError):
        await nx.client.call("mod_quiz_get_quizzes_by_courses")
    await nx.aclose()


async def test_not_found(fake, nexus):
    fake.error("core_course_get_course_module", "invalidcoursemodule", "Course module not found", exception="dml_missing_record_exception")
    with pytest.raises(NexusNotFoundError):
        await nexus.client.call("core_course_get_course_module", cmid=1)


async def test_http_500_is_api_error(fake, nexus):
    fake.on("core_enrol_get_users_courses", httpx.Response(503, text="Service Unavailable"))
    with pytest.raises(NexusAPIError, match="503"):
        await nexus.courses.list_courses("all")


async def test_html_response_is_api_error(fake, nexus):
    fake.on("core_enrol_get_users_courses", httpx.Response(200, text="<html><body>Sign in</body></html>", headers={"content-type": "text/html"}))
    with pytest.raises(NexusAPIError, match="HTML"):
        await nexus.courses.list_courses("all")


async def test_missing_token_is_auth_error(fake):
    nx = make_nexus(token=None)  # type: ignore[arg-type]
    with pytest.raises(NexusAuthError):
        await nx.ensure_ready()
    await nx.aclose()


async def test_site_context_and_timezone(nexus):
    assert nexus.site.user_id == 7
    assert nexus.site.tz_name == "America/New_York"
    assert nexus.site.tz_source == "moodle-account"
    assert nexus.now().isoformat().startswith("2026-09-11T12:00")


async def test_timezone_falls_back_to_site_default_when_account_is_99(fake):
    fake.on("core_user_get_users_by_field", [{"id": 7, "timezone": "99"}])
    nx = make_nexus()
    await nx.ensure_ready()
    assert nx.site.tz_name == "America/New_York"
    assert nx.site.tz_source == "site-default"
    await nx.aclose()
