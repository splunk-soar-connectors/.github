import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest


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
        },
    )()


def interrupted_item():
    item = verifying_item()
    item.verification = None
    item.attempts = [
        {
            "started_at": "2026-07-29T12:00:00Z",
            "outcome": "started",
            "app_existed_before_upload": False,
        }
    ]
    return item


@pytest.mark.parametrize(
    "validation_error",
    [
        TimeoutError("validation timed out"),
        ConnectionError("validation connection failed"),
        RuntimeError("validation GET exhausted 5xx retries"),
        ValueError("validation response was not JSON"),
    ],
)
def test_verification_get_failures_do_not_post_or_reserve(monkeypatch, validation_error):
    queue = Mock()
    client = Mock()
    client.get_existing_releases.return_value = []
    client.get_upload_status.side_effect = validation_error
    run_publisher = Mock()
    monkeypatch.setattr(MODULE, "run_publisher", run_publisher)

    result = MODULE.reconcile_verification(
        queue,
        client,
        verifying_item(),
        object(),
        MODULE.parse_datetime("2026-07-29T12:00:00Z"),
    )

    assert result == 0
    queue.verify.assert_called_once()
    queue.reserve_attempt.assert_not_called()
    run_publisher.assert_not_called()


def test_verification_polls_every_ten_seconds_until_release_appears(monkeypatch):
    queue = Mock()
    client = Mock()
    client.get_existing_releases.side_effect = [
        [],
        [],
        [{"release_name": "1.2.3"}],
        [{"release_name": "1.2.3"}],
    ]
    client.get_upload_status.return_value = {"message": "Package validation still in progress."}
    client._is_retryable_response.return_value = True
    client.get_apps.return_value = [{"id": "app-123", "support": "splunk"}]
    clock = [0]

    def advance_clock(seconds):
        assert seconds == 10
        clock[0] += seconds

    monkeypatch.setattr(MODULE, "VERIFICATION_POLL_TIMEOUT_SECONDS", 300)
    monkeypatch.setattr(MODULE.time, "monotonic", lambda: clock[0])
    sleep = Mock(side_effect=advance_clock)
    monkeypatch.setattr(MODULE.time, "sleep", sleep)
    monkeypatch.setattr(MODULE, "load_publisher_module", publisher_metadata)

    result = MODULE.reconcile_verification(
        queue,
        client,
        verifying_item(),
        finalization_args(),
        MODULE.parse_datetime("2026-07-29T12:00:00Z"),
    )

    assert result == 0
    assert sleep.call_args_list == [((10,),), ((10,),)]
    assert client.get_upload_status.call_count == 2
    queue.complete.assert_called_once()
    queue.reserve_attempt.assert_not_called()


@pytest.mark.parametrize(
    ("entry_path", "existed_before", "expected_new_app"),
    [
        ("preexisting", True, False),
        ("recovered", True, False),
        ("recovered", False, True),
    ],
)
def test_recovery_paths_share_finalization_without_post_or_reserve(
    monkeypatch,
    entry_path,
    existed_before,
    expected_new_app,
):
    queue = Mock()
    client = Mock()
    client.get_existing_releases.return_value = [{"release_name": "1.2.3"}]
    client.get_apps.return_value = [{"id": "app-123", "support": "splunk"}]
    run_publisher = Mock()
    outputs = {}
    monkeypatch.setattr(MODULE, "load_publisher_module", publisher_metadata)
    monkeypatch.setattr(MODULE, "write_output", outputs.__setitem__)
    monkeypatch.setattr(MODULE, "run_publisher", run_publisher)
    item = verifying_item()

    if entry_path == "recovered":
        item.verification["app_existed_before_upload"] = existed_before
        result = MODULE.reconcile_verification(
            queue,
            client,
            item,
            finalization_args(),
            MODULE.parse_datetime("2026-07-29T12:00:00Z"),
        )
    else:
        item.verification = None
        queue.get_item.return_value = item
        monkeypatch.setattr(MODULE, "queue_from_environment", lambda: queue)
        monkeypatch.setattr(MODULE, "Splunkbase", lambda *args, **kwargs: client)
        monkeypatch.setenv("SPLUNKBASE_USER", "publisher")
        monkeypatch.setenv("SPLUNKBASE_PASSWORD", "password")
        result = MODULE.publish_item(finalization_args())

    assert result == 0
    client.get_upload_status.assert_not_called()
    if expected_new_app:
        client.ensure_app_editors.assert_called_once_with("app-123")
    else:
        client.ensure_app_editors.assert_not_called()
    assert outputs["new_app"] is expected_new_app
    assert outputs["support_tag"] == "splunk"
    assert outputs["splunk_base_url"] == "https://splunkbase.splunk.com/app/app-123"
    queue.complete.assert_called_once()
    queue.delete_asset.assert_called_once_with(item)
    queue.reserve_attempt.assert_not_called()
    run_publisher.assert_not_called()


def test_ambiguous_upload_is_finalized_when_version_appears_without_second_post(
    monkeypatch,
):
    queue = Mock()
    item = verifying_item()
    item.verification = None
    queue.get_item.return_value = item
    queue.reserve_attempt.return_value = None
    client = Mock()
    client.get_existing_releases.side_effect = [
        [],
        [],
        [{"release_name": "1.2.3"}],
    ]
    client.get_apps.side_effect = [
        [],
        [{"id": "app-123", "support": "splunk"}],
    ]
    run_publisher = Mock(
        return_value=(
            12,
            {
                "status": "ambiguous",
                "app_existed_before_upload": False,
                "request_id": "request-123",
            },
        )
    )
    outputs = {}
    monkeypatch.setattr(MODULE, "queue_from_environment", lambda: queue)
    monkeypatch.setattr(MODULE, "Splunkbase", lambda *args, **kwargs: client)
    monkeypatch.setattr(MODULE, "load_publisher_module", publisher_metadata)
    monkeypatch.setattr(MODULE, "run_publisher", run_publisher)
    monkeypatch.setattr(MODULE, "write_output", outputs.__setitem__)
    monkeypatch.setenv("SPLUNKBASE_USER", "publisher")
    monkeypatch.setenv("SPLUNKBASE_PASSWORD", "password")

    assert MODULE.publish_item(finalization_args()) == 0

    assert run_publisher.call_count == 1
    assert queue.reserve_attempt.call_count == 1
    assert outputs["new_app"] is True
    assert outputs["publish_return_code"] == 2
    assert outputs["support_tag"] == "splunk"
    assert outputs["splunk_base_url"] == "https://splunkbase.splunk.com/app/app-123"
    client.ensure_app_editors.assert_called_once_with("app-123")
    queue.complete.assert_called_once()
    queue.delete_asset.assert_called_once_with(item)


def test_hard_exit_after_counted_attempt_recovers_new_app_without_second_post(
    monkeypatch,
):
    queue = Mock()
    item = interrupted_item()
    queue.get_item.return_value = item
    client = Mock()
    client.get_existing_releases.side_effect = [
        [],
        [{"release_name": "1.2.3"}],
    ]
    client.get_apps.return_value = [{"id": "app-123", "support": "splunk"}]
    run_publisher = Mock()
    outputs = {}
    monkeypatch.setattr(MODULE, "queue_from_environment", lambda: queue)
    monkeypatch.setattr(MODULE, "Splunkbase", lambda *args, **kwargs: client)
    monkeypatch.setattr(MODULE, "load_publisher_module", publisher_metadata)
    monkeypatch.setattr(MODULE, "run_publisher", run_publisher)
    monkeypatch.setattr(MODULE, "write_output", outputs.__setitem__)
    monkeypatch.setenv("SPLUNKBASE_USER", "publisher")
    monkeypatch.setenv("SPLUNKBASE_PASSWORD", "password")

    assert MODULE.publish_item(finalization_args()) == 0
    item.verification = queue.verify.call_args.args[1]
    assert item.verification["app_existed_before_upload"] is False

    assert MODULE.publish_item(finalization_args()) == 0

    queue.reserve_attempt.assert_not_called()
    run_publisher.assert_not_called()
    client.ensure_app_editors.assert_called_once_with("app-123")
    assert outputs["new_app"] is True
    queue.complete.assert_called_once()


def test_app_existence_snapshot_is_persisted_before_publisher_runs(monkeypatch):
    queue = Mock()
    item = interrupted_item()
    item.attempts = []
    queue.get_item.return_value = item
    queue.reserve_attempt.return_value = None
    client = Mock()
    client.get_existing_releases.return_value = []
    client.get_apps.return_value = []
    events = []

    def record_attempt(recorded_item, attempt):
        events.append("recorded")
        recorded_item.attempts.append(
            {
                "started_at": attempt.started_at,
                "outcome": attempt.outcome,
                "app_existed_before_upload": attempt.app_existed_before_upload,
            }
        )

    def run_publisher(args, published_item):
        events.append("publisher")
        return 14, {"status": "verifying"}

    outputs = {}
    queue.record_attempt.side_effect = record_attempt
    monkeypatch.setattr(MODULE, "queue_from_environment", lambda: queue)
    monkeypatch.setattr(MODULE, "Splunkbase", lambda *args, **kwargs: client)
    monkeypatch.setattr(MODULE, "run_publisher", run_publisher)
    monkeypatch.setattr(MODULE, "write_output", outputs.__setitem__)
    monkeypatch.setenv("SPLUNKBASE_USER", "publisher")
    monkeypatch.setenv("SPLUNKBASE_PASSWORD", "password")

    assert MODULE.publish_item(finalization_args()) == 0

    assert events == ["recorded", "publisher"]
    assert item.attempts[-1]["app_existed_before_upload"] is False
    assert queue.verify.call_count == 2
    assert outputs["queue_status"] == "verifying"


def test_pre_upload_metadata_failure_blocks_without_reserving_or_posting(monkeypatch):
    queue = Mock()
    item = interrupted_item()
    item.attempts = []
    queue.get_item.return_value = item
    client = Mock()
    client.get_existing_releases.return_value = []
    client.get_apps.side_effect = RuntimeError("metadata unavailable")
    run_publisher = Mock()
    outputs = {}
    monkeypatch.setattr(MODULE, "queue_from_environment", lambda: queue)
    monkeypatch.setattr(MODULE, "Splunkbase", lambda *args, **kwargs: client)
    monkeypatch.setattr(MODULE, "run_publisher", run_publisher)
    monkeypatch.setattr(MODULE, "write_output", outputs.__setitem__)
    monkeypatch.setenv("SPLUNKBASE_USER", "publisher")
    monkeypatch.setenv("SPLUNKBASE_PASSWORD", "password")

    assert MODULE.publish_item(finalization_args()) == 1

    queue.block.assert_called_once()
    queue.reserve_attempt.assert_not_called()
    run_publisher.assert_not_called()
    assert outputs["queue_status"] == "blocked"
    assert outputs["failure_reason"].startswith("Splunkbase app metadata")


def test_continued_pending_validation_does_not_post_or_reserve(monkeypatch):
    queue = Mock()
    client = Mock()
    client.get_existing_releases.return_value = []
    client.get_upload_status.return_value = {"message": "Package validation still in progress."}
    client._is_retryable_response.return_value = True
    run_publisher = Mock()
    monkeypatch.setattr(MODULE, "run_publisher", run_publisher)

    result = MODULE.reconcile_verification(
        queue,
        client,
        verifying_item(),
        object(),
        MODULE.parse_datetime("2026-07-29T12:00:00Z"),
    )

    assert result == 0
    queue.verify.assert_called_once()
    queue.reserve_attempt.assert_not_called()
    run_publisher.assert_not_called()


def test_definitive_rejection_blocks_without_post_or_reserve(monkeypatch):
    queue = Mock()
    client = Mock()
    client.get_existing_releases.return_value = []
    client.get_upload_status.return_value = {"message": "Package failed validation."}
    client._is_retryable_response.return_value = False
    run_publisher = Mock()
    outputs = {}
    monkeypatch.setattr(MODULE, "run_publisher", run_publisher)
    monkeypatch.setattr(MODULE, "write_output", outputs.__setitem__)

    result = MODULE.reconcile_verification(
        queue,
        client,
        verifying_item(),
        object(),
        MODULE.parse_datetime("2026-07-29T12:00:00Z"),
    )

    assert result == 1
    queue.block.assert_called_once()
    queue.reserve_attempt.assert_not_called()
    run_publisher.assert_not_called()
    assert outputs["queue_status"] == "blocked"
    assert outputs["package_id"] == "package-123"
    assert outputs["failure_reason"].startswith("Splunkbase definitively rejected")


@pytest.mark.parametrize("response", [{}, {"message": "unknown"}, ["malformed"]])
def test_inconclusive_status_does_not_post_or_reserve(monkeypatch, response):
    queue = Mock()
    client = Mock()
    client.get_existing_releases.return_value = []
    client.get_upload_status.return_value = response
    client._is_retryable_response.return_value = False
    run_publisher = Mock()
    monkeypatch.setattr(MODULE, "run_publisher", run_publisher)

    result = MODULE.reconcile_verification(
        queue,
        client,
        verifying_item(),
        object(),
        MODULE.parse_datetime("2026-07-29T12:00:00Z"),
    )

    assert result == 0
    queue.verify.assert_called_once()
    queue.block.assert_not_called()
    queue.reserve_attempt.assert_not_called()
    run_publisher.assert_not_called()


def test_run_publisher_passes_queued_source_identity(tmp_path, monkeypatch):
    captured = {}
    result_path = tmp_path / "result.json"
    publisher_output = tmp_path / "publisher-output"
    connector_workspace = tmp_path / "connector"
    connector_workspace.mkdir()
    args = type(
        "Args",
        (),
        {
            "artifact": str(tmp_path / "example.tgz"),
            "result_path": str(result_path),
            "publisher_output": str(publisher_output),
            "connector_workspace": str(connector_workspace),
        },
    )()
    item = type(
        "Item",
        (),
        {
            "repository": "splunk-soar-connectors/example",
            "run_id": 12345,
            "run_attempt": 3,
        },
    )()

    def fake_run(command, cwd, env, check):
        captured.update(env)
        result_path.write_text('{"status": "verifying"}')
        return type("Completed", (), {"returncode": 14})()

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)
    monkeypatch.setenv("GITHUB_RUN_ID", "worker-run")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "9")

    return_code, result = MODULE.run_publisher(args, item)

    assert return_code == 14
    assert result == {"status": "verifying"}
    assert captured["SOURCE_GITHUB_RUN_ID"] == "12345"
    assert captured["SOURCE_GITHUB_RUN_ATTEMPT"] == "3"
    assert captured["GITHUB_RUN_ID"] == "worker-run"
    assert captured["GITHUB_RUN_ATTEMPT"] == "9"


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
    monkeypatch.setattr(MODULE, "load_publisher_module", publisher_metadata)
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
    monkeypatch.setattr(MODULE, "load_publisher_module", publisher_metadata)
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
