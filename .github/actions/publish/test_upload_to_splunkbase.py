import importlib.util
import io
import json
from pathlib import Path
import tarfile

from packaging.version import parse
import pytest
import requests


MODULE_PATH = Path(__file__).with_name("upload_to_splunkbase.py")
SPEC = importlib.util.spec_from_file_location("upload_to_splunkbase", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

from utils.api.splunkbase import build_user_agent


def test_existing_version_is_only_successful_on_rerun():
    version = parse("2.3.4")

    assert not MODULE.is_successful_rerun_of_existing_version(version, version, run_attempt="1")
    assert MODULE.is_successful_rerun_of_existing_version(version, version, run_attempt="2")


def test_rerun_does_not_accept_an_older_candidate():
    assert not MODULE.is_successful_rerun_of_existing_version(
        parse("2.3.3"), parse("2.3.4"), run_attempt="2"
    )


def test_previous_release_version_uses_highest_version_below_candidate():
    releases = [
        {"release_name": "2.0.0"},
        {"release_name": "1.2.3"},
        {"release_name": "1.0.0"},
    ]

    assert MODULE.get_previous_release_version(releases, "2.0.0") == "1.2.3"


def test_previous_release_version_is_empty_for_first_release():
    assert MODULE.get_previous_release_version([], "1.0.0") is None


def test_github_outputs_include_previous_release_version(tmp_path, monkeypatch):
    output_path = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))

    MODULE._write_github_outputs(
        {
            "name": "Test App",
            "logo": "test.svg",
            "app_version": "2.0.0",
        },
        "test-app",
        "* Fixed behavior.",
        new_app=False,
        sb_appid="123",
        support_tag="splunk",
        previous_release_version="1.2.3",
    )

    assert "previous_release_version=1.2.3\n" in output_path.read_text()


def test_release_notes_can_be_read_from_queued_tarball(tmp_path):
    tarball = tmp_path / "connector.tgz"
    release_notes = b"**Unreleased**\n\n* Fixed upload handling.\n"
    info = tarfile.TarInfo("connector/release_notes/2.3.4.md")
    info.size = len(release_notes)
    with tarfile.open(tarball, "w:gz") as archive:
        archive.addfile(info, io.BytesIO(release_notes))

    assert MODULE.get_release_notes_from_tarball(tarball, "2.3.4") == ("\n* Fixed upload handling.")


def test_structured_rate_limit_result_is_written(tmp_path, monkeypatch):
    result_path = tmp_path / "publish-result.json"
    monkeypatch.setattr(MODULE, "PUBLISH_RESULT_PATH", str(result_path))
    error = MODULE.SplunkbaseRateLimited(
        "rate limited",
        status_code=429,
        retry_after="300",
        request_id="request-123",
    )

    code = MODULE._record_upload_error("rate_limited", error)

    assert code == 10
    assert json.loads(result_path.read_text()) == {
        "message": "rate limited",
        "request_id": "request-123",
        "retry_after": "300",
        "status": "rate_limited",
        "status_code": 429,
    }


def test_terminal_failure_persists_public_safe_diagnostic(tmp_path, monkeypatch):
    result_path = tmp_path / "publish-result.json"
    monkeypatch.setattr(MODULE, "PUBLISH_RESULT_PATH", str(result_path))

    reason = "Candidate version 1.0.0 must be greater than the latest released version 1.0.0."

    assert MODULE._fail(reason) == MODULE.RESULT_CODES["failed"]
    assert json.loads(result_path.read_text()) == {
        "failure_reason": reason,
        "status": "failed",
    }


@pytest.mark.parametrize(
    "validation_error",
    [
        requests.Timeout("validation timed out"),
        requests.ConnectionError("validation connection failed"),
        RuntimeError("validation GET exhausted 5xx retries"),
        ValueError("validation response was not JSON"),
    ],
)
def test_post_upload_get_failure_stays_in_verification(validation_error):
    client = type(
        "Client",
        (),
        {"check_upload_status": lambda self, package_id: (_ for _ in ()).throw(validation_error)},
    )()

    status, response = MODULE._check_post_upload_validation(client, "package-123")

    assert status == "verifying"
    assert response == {}


def test_continued_validation_stays_in_verification():
    response = {"message": "Package validation still in progress."}
    client = type(
        "Client",
        (),
        {
            "check_upload_status": lambda self, package_id: response,
            "_is_retryable_response": lambda self, value: True,
        },
    )()

    assert MODULE._check_post_upload_validation(client, "package-123") == (
        "verifying",
        response,
    )


def test_definitive_validation_rejection_is_classified():
    response = {"message": "Package failed validation.", "errors": ["invalid manifest"]}
    client = type(
        "Client",
        (),
        {
            "check_upload_status": lambda self, package_id: response,
            "_is_retryable_response": lambda self, value: False,
        },
    )()

    assert MODULE._check_post_upload_validation(client, "package-123") == (
        "validation_failed",
        response,
    )


@pytest.mark.parametrize("response", [{}, {"message": "unknown"}, ["malformed"]])
def test_inconclusive_validation_response_stays_in_verification(response):
    client = type(
        "Client",
        (),
        {
            "check_upload_status": lambda self, package_id: response,
            "_is_retryable_response": lambda self, value: False,
        },
    )()

    status, _ = MODULE._check_post_upload_validation(client, "package-123")

    assert status == "verifying"


def test_unexpected_failure_preserves_written_verification_result(tmp_path, monkeypatch):
    result_path = tmp_path / "publish-result.json"
    result = {
        "status": "verifying",
        "package_id": "package-123",
        "request_id": "request-123",
    }
    result_path.write_text(json.dumps(result))
    monkeypatch.setattr(MODULE, "PUBLISH_RESULT_PATH", str(result_path))
    monkeypatch.setattr(MODULE, "parse_args", lambda: object())
    monkeypatch.setattr(
        MODULE,
        "main",
        lambda args: (_ for _ in ()).throw(RuntimeError("post-upload failure")),
    )

    assert MODULE.cli() == MODULE.RESULT_CODES["verifying"]
    assert json.loads(result_path.read_text()) == result


def test_unexpected_pre_upload_failure_persists_safe_diagnostic(tmp_path, monkeypatch):
    result_path = tmp_path / "publish-result.json"
    monkeypatch.setattr(MODULE, "PUBLISH_RESULT_PATH", str(result_path))
    monkeypatch.setattr(MODULE, "parse_args", lambda: object())
    monkeypatch.setattr(
        MODULE,
        "main",
        lambda args: (_ for _ in ()).throw(RuntimeError("secret response text")),
    )

    assert MODULE.cli() == MODULE.RESULT_CODES["failed"]
    assert json.loads(result_path.read_text()) == {
        "failure_reason": "The publisher raised an unexpected error before completing.",
        "status": "failed",
    }


def test_accepted_upload_persists_package_metadata_before_failed_status_get(
    tmp_path,
    monkeypatch,
):
    result_path = tmp_path / "publish-result.json"

    class Client:
        last_upload_request_id = "request-123"
        status_checks = 0
        request_context = None

        def __init__(self, *args, request_context=None, **kwargs):
            type(self).request_context = request_context

        def get_existing_releases(self, appid):
            return []

        def get_apps(self, filters):
            return []

        def upload_app(self, *args):
            return "package-123"

        def check_upload_status(self, package_id):
            type(self).status_checks += 1
            raise requests.Timeout("validation timed out")

    monkeypatch.setattr(MODULE, "PUBLISH_RESULT_PATH", str(result_path))
    monkeypatch.setenv("UPLOAD_PATH", str(tmp_path / "connector.tgz"))
    monkeypatch.setenv("GITHUB_RUN_ID", "worker-run")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "9")
    monkeypatch.setenv("SOURCE_GITHUB_RUN_ID", "source-run")
    monkeypatch.setenv("SOURCE_GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setattr(
        MODULE,
        "get_app_json",
        lambda tarball: {
            "appid": "example-guid",
            "app_version": "1.2.3",
            "name": "Example",
            "publisher": "Splunk",
        },
    )
    monkeypatch.setattr(MODULE, "get_release_notes", lambda *args: "* Fixed validation.")
    monkeypatch.setattr(MODULE, "get_license_info", lambda app_json: ("license", "url"))
    monkeypatch.setattr(MODULE, "Splunkbase", Client)

    code = MODULE.main(type("Args", (), {"app_repo_name": "example"})())

    assert code == MODULE.RESULT_CODES["verifying"]
    assert Client.status_checks == 0
    assert Client.request_context == {
        "repo": "example",
        "version": "1.2.3",
        "run_id": "source-run",
        "run_attempt": "2",
    }
    user_agent = build_user_agent(**Client.request_context)
    assert "run/source-run" in user_agent
    assert "attempt/2" in user_agent
    assert "worker-run" not in user_agent
    assert json.loads(result_path.read_text()) == {
        "appid": "example-guid",
        "app_existed_before_upload": False,
        "app_name": "Example",
        "package_id": "package-123",
        "release_version": "1.2.3",
        "request_id": "request-123",
        "status": "verifying",
    }
