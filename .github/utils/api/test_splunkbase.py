from unittest.mock import Mock, patch

import pytest
import requests

from .splunkbase import (
    READ_REQUEST_NUM_RETRIES,
    Splunkbase,
    SplunkbaseAmbiguousUpload,
    SplunkbasePermissionDenied,
    SplunkbaseRateLimited,
    SplunkbaseResponseError,
    SplunkbaseValidationFailed,
    USER_AGENT,
    _post_request_with_files,
    _response_json,
    _retrying_session,
    _single_attempt_session,
    build_user_agent,
)


def test_retrying_session_only_retries_read_only_gets():
    session = _retrying_session()
    retry = session.get_adapter("https://").max_retries

    assert session.headers["User-Agent"] == USER_AGENT
    assert retry.total == READ_REQUEST_NUM_RETRIES
    assert retry.allowed_methods == frozenset(["GET"])
    assert {429, 500, 502, 503, 504} <= set(retry.status_forcelist)
    assert 403 not in retry.status_forcelist
    assert retry.respect_retry_after_header is True


def test_upload_session_has_no_automatic_retries():
    session = _single_attempt_session(user_agent="queue-test")
    retry = session.get_adapter("https://").max_retries

    assert session.headers["User-Agent"] == "queue-test"
    assert retry.total == 0
    assert retry.connect == 0
    assert retry.read == 0
    assert retry.status == 0


def _response(status_code, *, data=None, text="", headers=None):
    response = Mock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 400
    response.text = text
    response.headers = headers or {}
    response.json.return_value = data or {}
    return response


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (_response(401), SplunkbasePermissionDenied),
        (_response(403), SplunkbasePermissionDenied),
        (_response(429, headers={"Retry-After": "300"}), SplunkbaseRateLimited),
        (_response(503), SplunkbaseAmbiguousUpload),
        (_response(422, text="invalid package"), SplunkbaseValidationFailed),
    ],
)
def test_upload_failure_makes_exactly_one_post(response, error_type):
    session = Mock()
    session.post.return_value = response

    with patch(
        "utils.api.splunkbase._single_attempt_session",
        return_value=session,
    ):
        with pytest.raises(error_type):
            _post_request_with_files({}, "https://example.test/upload", {}, {"package": Mock()})

    session.post.assert_called_once()


def test_rate_limit_preserves_retry_after_and_request_id():
    session = Mock()
    session.post.return_value = _response(
        429,
        headers={"Retry-After": "300", "X-Request-ID": "request-123"},
    )

    with patch(
        "utils.api.splunkbase._single_attempt_session",
        return_value=session,
    ):
        with pytest.raises(SplunkbaseRateLimited) as error:
            _post_request_with_files({}, "https://example.test/upload", {}, {"package": Mock()})

    assert error.value.retry_after == "300"
    assert error.value.request_id == "request-123"


def test_successful_upload_response_preserves_request_id():
    session = Mock()
    session.post.return_value = _response(
        201,
        data={
            "message": "Release was successfully uploaded and is being validated.",
            "package_id": "package-123",
        },
        headers={"X-Request-ID": "request-123"},
    )

    with patch(
        "utils.api.splunkbase._single_attempt_session",
        return_value=session,
    ):
        result = _post_request_with_files(
            {},
            "https://example.test/upload",
            {},
            {"package": Mock()},
        )

    assert result["package_id"] == "package-123"
    assert result["_request_id"] == "request-123"


@pytest.mark.parametrize("transport_error", [requests.ConnectionError(), requests.Timeout()])
def test_transport_failure_makes_exactly_one_post(transport_error):
    session = Mock()
    session.post.side_effect = transport_error

    with patch(
        "utils.api.splunkbase._single_attempt_session",
        return_value=session,
    ):
        with pytest.raises(SplunkbaseAmbiguousUpload):
            _post_request_with_files({}, "https://example.test/upload", {}, {"package": Mock()})

    session.post.assert_called_once()


def test_user_agent_adds_only_non_secret_correlation():
    user_agent = build_user_agent(
        repo="crowdsec",
        version="1.2.3",
        run_id="123",
        run_attempt="2",
    )

    assert user_agent.startswith(USER_AGENT)
    assert "repo/crowdsec" in user_agent
    assert "version/1.2.3" in user_agent
    assert "run/123" in user_agent
    assert "attempt/2" in user_agent


def test_retryable_response_helper_can_be_called_on_an_instance():
    client = object.__new__(Splunkbase)

    assert client._is_retryable_response({"message": "Package validation still in progress."})


def test_editor_reconciliation_adds_only_missing_editors():
    client = object.__new__(Splunkbase)
    client.auth = {"Authorization": "Bearer token"}
    client._splunkbase_editor_url = "https://example.test/app/{sb_appid}/editors/"

    with (
        patch(
            "utils.api.splunkbase._get_request",
            return_value={"editors": [{"username": "nastor_splunk"}]},
        ),
        patch("utils.api.splunkbase._post_request", return_value={"ok": True}) as post,
    ):
        client.ensure_app_editors("app-123")

    post.assert_called_once_with(
        client.auth,
        "https://example.test/app/app-123/editors/",
        data={"username": "coh_splunk"},
    )


def test_response_json_rejects_missing_collection_fields():
    response = Mock(ok=True)
    response.json.return_value = {"detail": "temporarily unavailable"}

    with pytest.raises(SplunkbaseResponseError, match="results"):
        _response_json(response, required_keys={"results"})


def test_response_json_rejects_non_json_response():
    response = Mock(ok=True)
    response.json.side_effect = ValueError("not json")

    with pytest.raises(SplunkbaseResponseError, match="non-JSON"):
        _response_json(response)
