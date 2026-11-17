import importlib.util
import json
from pathlib import Path
from dataclasses import asdict
from copy import deepcopy
import io
import tarfile
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from utils.test_draft_publish import Client as DraftClient, MANIFEST


MODULE_PATH = Path(__file__).with_name("drain_splunkbase_publish_queue.py")
SPEC = importlib.util.spec_from_file_location("drain_splunkbase_publish_queue", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture(autouse=True)
def disable_verification_wait(monkeypatch):
    monkeypatch.setattr(MODULE, "VERIFICATION_POLL_TIMEOUT_SECONDS", 0)


def test_retry_after_supports_seconds_and_http_dates():
    now = MODULE.parse_datetime("2026-07-29T12:00:00Z")

    assert MODULE.format_datetime(MODULE.retry_after_datetime("300", now)) == (
        "2026-07-29T12:05:00Z"
    )
    assert (
        MODULE.format_datetime(MODULE.retry_after_datetime("Wed, 29 Jul 2026 12:07:00 GMT", now))
        == "2026-07-29T12:07:00Z"
    )


def test_newer_release_version_requires_strictly_newer_version():
    client = Mock()
    client.get_existing_releases.return_value = [
        {"release_name": "0.9.0"},
        {"release_name": "1.0.0"},
        {"release_name": "1.5.0"},
        {"release_name": "2.0.0"},
    ]

    assert MODULE.newer_release_version(client, "example-guid", "1.0.0") == "2.0.0"
    assert MODULE.newer_release_version(client, "example-guid", "2.0.0") is None


def test_worker_run_url_uses_current_actions_run(monkeypatch):
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.example.com/")
    monkeypatch.setenv("GITHUB_REPOSITORY", "splunk-soar-connectors/.github")
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")

    assert MODULE.worker_run_url() == (
        "https://github.example.com/splunk-soar-connectors/.github/actions/runs/12345"
    )


def test_worker_waits_for_budget_available_within_run_deadline(monkeypatch):
    queue = Mock()
    item = verifying_item()
    now = MODULE.parse_datetime("2026-07-29T12:00:00Z")
    retry_at = MODULE.parse_datetime("2026-07-29T12:03:00Z")
    queue.reserve_attempt.side_effect = [retry_at, None]
    sleep = Mock()
    monkeypatch.setattr(MODULE.time, "sleep", sleep)
    monkeypatch.setattr(MODULE, "utc_now", lambda: retry_at)

    current, remaining_retry = MODULE.wait_for_upload_budget(
        queue,
        item,
        now,
        MODULE.parse_datetime("2026-07-29T13:00:00Z"),
    )

    assert current == retry_at
    assert remaining_retry is None
    sleep.assert_called_once_with(180)


def test_worker_does_not_wait_past_run_deadline(monkeypatch):
    queue = Mock()
    item = verifying_item()
    now = MODULE.parse_datetime("2026-07-29T12:59:00Z")
    retry_at = MODULE.parse_datetime("2026-07-29T13:02:00Z")
    queue.reserve_attempt.return_value = retry_at
    sleep = Mock()
    monkeypatch.setattr(MODULE.time, "sleep", sleep)

    current, remaining_retry = MODULE.wait_for_upload_budget(
        queue,
        item,
        now,
        MODULE.parse_datetime("2026-07-29T13:00:00Z"),
    )

    assert current == now
    assert remaining_retry == retry_at
    sleep.assert_not_called()


def test_simulation_deduplicates_and_respects_hourly_budget(tmp_path, capsys):
    queue = [
        {
            "repository": f"splunk-soar-connectors/repo-{index:02}",
            "candidate_version": "1.0.0",
        }
        for index in range(21)
    ]
    queue.append(queue[0])
    queue_file = tmp_path / "queue.json"
    queue_file.write_text(json.dumps(queue))
    args = type(
        "Args",
        (),
        {
            "queue_file": str(queue_file),
            "now": "2026-07-29T12:00:00Z",
            "publisher_alias": "soar-connectors-default",
        },
    )

    assert MODULE.simulate(args) == 0

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 21
    assert lines[0].endswith("2026-07-29T12:00:00Z")
    assert lines[19].endswith("2026-07-29T12:57:00Z")
    assert lines[20].endswith("2026-07-29T13:00:00Z")


def verifying_item():
    return type(
        "Item",
        (),
        {
            "verification": {
                "status": "verifying",
                "package_id": "package-123",
                "request_id": "request-123",
            },
            "appid": "example-guid",
            "app_name": "Example",
            "candidate_version": "1.2.3",
            "issue_number": 1,
            "publisher_alias": "soar-connectors-default",
            "repository": "splunk-soar-connectors/example",
            "run_attempt": 2,
            "run_id": 123,
            "verification_rechecks": 0,
        },
    )()


def draft_queue_setup(tmp_path, monkeypatch):
    item = verifying_item()
    item.appid = "guid"
    item.verification = None
    item.attempts = []
    path = tmp_path / "example.tgz"
    with tarfile.open(path, "w:gz") as archive:
        for name, data in {
            "example.json": json.dumps({**MANIFEST, "logo": "logo.svg"}),
            "details.md": "Details",
            "release_notes/1.2.3.md": "* Fix",
        }.items():
            member = tarfile.TarInfo("example/" + name)
            content = data.encode()
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    queue = Mock()
    queue.reserve_attempt.return_value = None

    def verify(item, result, when, reason):
        item.verification = deepcopy(result)

    queue.verify.side_effect = verify
    queue.record_attempt.side_effect = lambda item, attempt: item.attempts.append(asdict(attempt))
    client = DraftClient()
    client.find_app = lambda guid: None
    client.public_app_by_guid = lambda guid: None
    client.get_apps = lambda filters: [{"id": 11, "support": "splunk"}]
    client.get_existing_releases = lambda guid: []
    client.ensure_app_editors = Mock()
    monkeypatch.setattr(MODULE, "DraftSplunkbase", lambda *args, **kwargs: client)
    monkeypatch.setenv("SPLUNKBASE_USER", "test-user")
    monkeypatch.setenv("SPLUNKBASE_PASSWORD", "test-password")
    args = SimpleNamespace(artifact=str(path), connector_workspace=str(tmp_path))
    return queue, item, args, client


@pytest.mark.parametrize("verification", [None, {"publication_flow": "draft_v2", "package_id": 22}])
def test_publish_item_always_selects_draft_publishing(monkeypatch, verification):
    item = verifying_item()
    item.verification = verification
    item.attempts = []
    queue = Mock()
    queue.get_item.return_value = item
    publish = Mock(return_value=0)
    monkeypatch.setattr(MODULE, "queue_from_environment", lambda: queue)
    monkeypatch.setattr(MODULE, "publish_draft_item", publish)
    args = SimpleNamespace(issue_number=1)
    assert MODULE.publish_item(args) == 0
    publish.assert_called_once_with(queue, item, args)


@pytest.mark.parametrize("verification", [None, {"package_id": 22}])
def test_old_upload_checkpoints_block_without_another_upload(monkeypatch, verification):
    item = verifying_item()
    item.verification = verification
    item.attempts = [{"outcome": "started"}]
    queue = Mock()
    queue.get_item.return_value = item
    publish = Mock()
    monkeypatch.setattr(MODULE, "queue_from_environment", lambda: queue)
    monkeypatch.setattr(MODULE, "publish_draft_item", publish)
    assert MODULE.publish_item(SimpleNamespace(issue_number=1)) == 1
    publish.assert_not_called()
    queue.reserve_attempt.assert_not_called()
    queue.block.assert_called_once()


def test_saved_draft_publication_finishes_without_another_upload(tmp_path, monkeypatch):
    queue, item, args, client = draft_queue_setup(tmp_path, monkeypatch)
    item.verification = {
        "publication_flow": "draft_v2",
        "splunkbase_app_id": 11,
        "package_id": 22,
        "release_id": 33,
        "publication_requested": True,
    }
    queue.get_item.return_value = item
    client.visible = True
    args.issue_number = 1
    monkeypatch.setattr(MODULE, "queue_from_environment", lambda: queue)
    assert MODULE.publish_item(args) == 0
    queue.complete.assert_called_once()
    queue.reserve_attempt.assert_not_called()
    assert client.calls == []


def test_draft_verification_rechecks_are_bounded_without_resubmitting(tmp_path, monkeypatch):
    queue, item, args, client = draft_queue_setup(tmp_path, monkeypatch)
    client.pending = True
    for _ in range(2):
        assert MODULE.publish_draft_item(queue, item, args) == 0
    assert MODULE.publish_draft_item(queue, item, args) == 1
    queue.block.assert_called_once()
    queue.complete.assert_not_called()
    assert item.verification_rechecks == 3
    assert client.calls == ["create", "s3", "register"]


@pytest.mark.parametrize("releases", [None, []])
@pytest.mark.parametrize("package_id", [None, 69438])
def test_diagnostics_only_read_queue_and_app_metadata(monkeypatch, capsys, releases, package_id):
    queue = Mock()
    item = verifying_item()
    item.verification = {"package_id": package_id, "splunkbase_app_id": 5758}
    queue.get_item.return_value = item
    app = {"id": 5758, "app_id": item.appid, "releases": releases}
    client = Mock(spec=["find_app", "get_app", "public_app_by_guid", "get_upload_status"])
    client.find_app.return_value = app
    client.public_app_by_guid.return_value = app
    client.get_app.return_value = app
    client.get_upload_status.return_value = {"result": "Pass", "details": {"id": 5758}}
    monkeypatch.setattr(MODULE, "queue_from_environment", lambda: queue)
    monkeypatch.setattr(MODULE, "DraftSplunkbase", lambda *args, **kwargs: client)
    monkeypatch.setenv("SPLUNKBASE_USER", "test-user")
    monkeypatch.setenv("SPLUNKBASE_PASSWORD", "test-password")

    assert MODULE.diagnose_item(SimpleNamespace(issue_number=916)) == (1 if releases is None else 0)

    assert queue.method_calls == [call.get_item(916)]
    expected_calls = [call.find_app(item.appid), call.public_app_by_guid(item.appid)]
    if package_id:
        expected_calls.append(call.get_upload_status(package_id))
        expected_calls.append(call.get_app(5758))
    assert client.method_calls == expected_calls
    output = capsys.readouterr()
    if package_id:
        assert '"lookup": "validation"' in output.out
        assert '"app_id_matches": true' in output.out
        assert '"app_guid_matches": false' in output.out
        assert '"release_type": "NoneType"' in output.out
    if releases is None:
        assert "Splunkbase app detail is missing releases" in output.out
        assert "release_for_version" in output.err
    assert "test-password" not in output.out + output.err
    assert "test-user" not in output.out + output.err


def test_draft_api_deferral_reports_underlying_exception(tmp_path, monkeypatch, capsys):
    queue, item, args, client = draft_queue_setup(tmp_path, monkeypatch)
    client.find_app = Mock(
        side_effect=MODULE.SplunkbaseAmbiguousUpload(
            "Splunkbase GET returned HTTP 503", status_code=503, request_id="request-123"
        )
    )
    assert MODULE.publish_draft_item(queue, item, args) == 0
    output = capsys.readouterr()
    assert '"error_type": "SplunkbaseAmbiguousUpload"' in output.out
    assert '"status_code": 503' in output.out
    assert '"request_id": "request-123"' in output.out
    assert "Splunkbase GET returned HTTP 503" in output.out
    queue.record_attempt.assert_not_called()
    queue.complete.assert_not_called()


def test_draft_queue_stays_open_until_the_release_is_public(tmp_path, monkeypatch):
    queue, item, args, client = draft_queue_setup(tmp_path, monkeypatch)
    assert MODULE.publish_draft_item(queue, item, args) == 0
    queue.complete.assert_not_called()
    assert item.verification["publication_requested"] is True
    assert item.verification["release_id"] == 33
    assert len(item.attempts) == 1
    client.visible = True
    assert MODULE.publish_draft_item(queue, item, args) == 0
    queue.complete.assert_called_once()
    queue.reserve_attempt.assert_called_once()
    assert (
        client.calls.count("s3")
        == client.calls.count("register")
        == client.calls.count("submit")
        == 1
    )


def test_draft_queue_rate_limit_retains_app_identity_for_safe_retry(tmp_path, monkeypatch):
    queue, item, args, client = draft_queue_setup(tmp_path, monkeypatch)
    original_upload = client.upload_package
    client.upload_package = Mock(
        side_effect=MODULE.SplunkbaseRateLimited("quota", retry_after="300")
    )
    assert MODULE.publish_draft_item(queue, item, args) == 0
    assert item.verification["splunkbase_app_id"] == 11
    assert item.verification["pending_operation"] == ""
    queue.complete.assert_not_called()
    client.upload_package = original_upload
    client.visible = True
    assert MODULE.publish_draft_item(queue, item, args) == 0
    assert client.calls.count("create") == 1
    queue.complete.assert_called_once()


def finalization_args():
    return type(
        "Args",
        (),
        {
            "artifact": "/tmp/example.tgz",
            "connector_workspace": "/tmp/example",
            "issue_number": 1,
        },
    )()


def publisher_metadata():
    return type(
        "Publisher",
        (),
        {
            "get_app_json": staticmethod(lambda artifact: {"logo": "example.svg"}),
            "get_release_notes": staticmethod(lambda version, workspace: "* Fixed publication."),
            "get_release_notes_from_tarball": staticmethod(lambda artifact, version: None),
            "get_previous_release_version": staticmethod(lambda releases, version: None),
        },
    )()


@pytest.mark.parametrize(
    (
        "existed_before",
        "expected_new_app",
        "expected_return_code",
        "previous_release_version",
    ),
    [(True, False, 0, "1.0.0"), (False, True, 2, None)],
)
def test_finalization_reconstructs_outputs_and_new_app_side_effects(
    monkeypatch,
    existed_before,
    expected_new_app,
    expected_return_code,
    previous_release_version,
):
    queue = Mock()
    client = Mock()
    client.get_apps.return_value = [{"id": "app-123", "support": "splunk"}]
    outputs = {}
    monkeypatch.setattr(MODULE, "publisher", publisher_metadata())
    monkeypatch.setattr(MODULE, "write_output", outputs.__setitem__)
    item = verifying_item()
    result = {
        "status": "verifying",
        "package_id": "package-123",
        "app_existed_before_upload": existed_before,
        "previous_release_version": previous_release_version,
    }

    assert (
        MODULE.finalize_publication(
            queue,
            client,
            item,
            finalization_args(),
            result,
            MODULE.parse_datetime("2026-07-29T12:00:00Z"),
        )
        == 0
    )

    if expected_new_app:
        client.ensure_app_editors.assert_called_once_with("app-123")
    else:
        client.ensure_app_editors.assert_not_called()
    assert outputs["new_app"] is expected_new_app
    assert outputs["publish_return_code"] == expected_return_code
    assert outputs["previous_release_version"] == (previous_release_version or "")
    assert outputs["support_tag"] == "splunk"
    assert outputs["splunk_base_url"] == "https://splunkbase.splunk.com/app/app-123"
    queue.complete.assert_called_once()
    queue.delete_asset.assert_called_once_with(item)


@pytest.mark.parametrize("failure_point", ["metadata", "editors"])
def test_finalization_failure_retains_artifact_and_requeues(monkeypatch, failure_point):
    queue = Mock()
    client = Mock()
    client.get_apps.return_value = [{"id": "app-123", "support": "splunk"}]
    if failure_point == "metadata":
        client.get_apps.side_effect = RuntimeError("metadata unavailable")
    else:
        client.ensure_app_editors.side_effect = RuntimeError("editor unavailable")
    monkeypatch.setattr(MODULE, "publisher", publisher_metadata())
    item = verifying_item()

    assert (
        MODULE.finalize_publication(
            queue,
            client,
            item,
            finalization_args(),
            {
                "status": "verifying",
                "app_existed_before_upload": False,
            },
            MODULE.parse_datetime("2026-07-29T12:00:00Z"),
        )
        == 0
    )

    queue.verify.assert_called_once()
    queue.complete.assert_not_called()
    queue.delete_asset.assert_not_called()
