from datetime import datetime, timedelta, timezone
import json
from unittest.mock import Mock, patch

import pytest
import requests

from .splunkbase import (
    SplunkbaseAmbiguousUpload,
    SplunkbasePermissionDenied,
    SplunkbaseRateLimited,
    SplunkbaseValidationFailed,
)
from .splunkbase_draft import DraftSplunkbase


def response(code=200, data=None):
    result = requests.Response()
    result.status_code = code
    result._content = json.dumps(data or {}).encode() if code != 204 else b""
    return result


@pytest.fixture
def client():
    value = object.__new__(DraftSplunkbase)
    value.api_url = "https://stage.splunkbase.splunk.com/api/v2"
    value.user_agent = "test-publisher"
    value.auth = {"Authorization": "Bearer test-token"}
    value.expires_at = None
    return value


def test_staging_login_posts_form_credentials_and_caches_expiry():
    session = Mock(headers={})
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    session.post.return_value = response(data={"data": {"token": "test-token", "expires": expiry}})
    with patch("utils.api.splunkbase_draft._single_attempt_session", return_value=session):
        value = DraftSplunkbase("test-user", "test-password", environment="STAGE")
    assert value.api_url == "https://stage.splunkbase.splunk.com/api/v2"
    assert value.expires_at == datetime.fromisoformat(expiry)
    assert value.auth == {"Authorization": "Bearer test-token"}
    session.post.assert_called_once_with(
        "https://api.staging.splunk.com/2.0/rest/login/splunk",
        data={"username": "test-user", "password": "test-password"},
        timeout=(10, 60),
        allow_redirects=False,
    )


def test_auth_refresh_is_bounded_and_persistent_denial_has_safe_message(client):
    session = Mock(headers={})
    session.request.return_value = response(403, {"detail": "secret server text"})
    client._get_bearer_auth = Mock(return_value={"Authorization": "Bearer refreshed"})
    with patch("utils.api.splunkbase_draft._single_attempt_session", return_value=session):
        with pytest.raises(SplunkbasePermissionDenied) as error:
            client.submit_draft(11)
    assert session.request.call_count == 2
    client._get_bearer_auth.assert_called_once()
    assert "secret" not in str(error.value)


def test_expiring_token_refreshes_before_request(client):
    client.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    client._get_bearer_auth = Mock(return_value={"Authorization": "Bearer refreshed"})
    session = Mock(headers={})
    session.request.return_value = response()
    with patch("utils.api.splunkbase_draft._retrying_session", return_value=session):
        client.get_app(11)
    client._get_bearer_auth.assert_called_once()
    assert session.request.call_args.kwargs["headers"] == {"Authorization": "Bearer refreshed"}


@pytest.mark.parametrize(
    ("code", "error"),
    [
        (429, SplunkbaseRateLimited),
        (409, SplunkbaseValidationFailed),
        (503, SplunkbaseAmbiguousUpload),
        (302, SplunkbaseAmbiguousUpload),
    ],
)
def test_mutations_do_not_retry_quota_conflict_server_error_or_redirect(client, code, error):
    session = Mock(headers={})
    session.request.return_value = response(code)
    session.request.return_value.headers["Retry-After"] = "300"
    with patch("utils.api.splunkbase_draft._single_attempt_session", return_value=session):
        with pytest.raises(error):
            client.register_package(11, "example.tgz", "media/uploaded/key.tgz")
    session.request.assert_called_once()
    assert session.request.call_args.kwargs["allow_redirects"] is False


def test_submit_accepts_empty_success_response(client):
    session = Mock(headers={})
    result = response()
    result._content = b""
    session.request.return_value = result
    with patch("utils.api.splunkbase_draft._single_attempt_session", return_value=session):
        assert client.submit_draft(11) == {}


def test_s3_upload_uses_only_presigned_fields_and_registers_filename(tmp_path, client):
    path = tmp_path / "example.tgz"
    path.write_bytes(b"package")
    fields = {
        "key": "media/uploaded/key.tgz",
        "policy": "test-policy",
        "signature": "test-signature",
    }
    client.request = Mock(
        side_effect=[
            {"url": "https://bucket.s3.us-west-2.amazonaws.com/", "fields": fields},
            {"id": 22},
        ]
    )
    session = requests.Session()
    session.post = Mock(return_value=response(204))
    checkpoint = Mock()
    with patch("utils.api.splunkbase_draft._single_attempt_session", return_value=session):
        assert client.upload_package(11, path, checkpoint) == 22
    assert session.trust_env is False
    assert "Authorization" not in session.headers
    assert session.post.call_args.kwargs["data"] == fields
    assert list(session.post.call_args.kwargs["files"]) == ["file"]
    assert session.post.call_args.kwargs["allow_redirects"] is False
    checkpoint.assert_called_once_with(fields["key"])
    assert client.request.call_args_list[-1].kwargs["json"] == {
        "filename": "example.tgz",
        "s3_key": fields["key"],
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://bucket.s3.amazonaws.com",
        "https://evil.test",
        "https://s3.amazonaws.com.evil.test",
        "https://user:password@bucket.s3.amazonaws.com",
        "https://bucket.s3.amazonaws.com:8443",
    ],
)
def test_invalid_s3_destination_is_rejected_before_upload(client, tmp_path, url):
    client.request = Mock(return_value={"url": url, "fields": {"key": "key"}})
    with patch("utils.api.splunkbase_draft._single_attempt_session") as session:
        with pytest.raises(SplunkbaseValidationFailed, match="invalid S3"):
            client.upload_package(11, tmp_path / "package.tgz", Mock())
    session.assert_not_called()


def test_expired_s3_grant_never_registers_package(client, tmp_path):
    path = tmp_path / "package.tgz"
    path.write_bytes(b"package")
    client.request = Mock(
        return_value={"url": "https://bucket.s3.amazonaws.com", "fields": {"key": "key"}}
    )
    session = Mock(headers={})
    session.post.return_value = response(403, {"detail": "expired secret policy"})
    with patch("utils.api.splunkbase_draft._single_attempt_session", return_value=session):
        with pytest.raises(SplunkbaseValidationFailed, match="S3 rejected") as error:
            client.upload_package(11, path, Mock())
    client.request.assert_called_once()
    assert "secret" not in str(error.value)


def test_public_visibility_check_sends_no_bearer_credentials(client):
    session = Mock(headers={})
    session.request.return_value = response(data={"results": [{"id": 11}]})
    with patch("utils.api.splunkbase_draft._retrying_session", return_value=session):
        assert client.public_app(11)["id"] == 11
    assert session.request.call_args.kwargs["headers"] == {}


def test_editor_lookup_reuses_a_private_listing(client):
    client.get_app = Mock(return_value={"id": 11, "app_id": "guid", "draft": True})
    assert client.find_app("guid")["id"] == 11


def test_editor_lookup_only_treats_an_explicit_404_as_absent(client):
    client.get_app = Mock(side_effect=SplunkbaseValidationFailed("not found", status_code=404))
    assert client.find_app("guid") is None
    client.get_app = Mock(side_effect=SplunkbasePermissionDenied("denied", status_code=403))
    with pytest.raises(SplunkbasePermissionDenied):
        client.find_app("guid")


def test_draft_creation_uses_the_documented_json_content_type(client):
    client.request = Mock(return_value={"id": 11})
    assert client.create_draft({"name": "Example", "product": "soar"}) == 11
    client.request.assert_called_once_with(
        "POST", "apps/", json={"draft": "true", "name": "Example", "product": "soar"}
    )
