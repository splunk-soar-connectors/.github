import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest


MODULE_PATH = Path(__file__).with_name("drain_splunkbase_publish_queue.py")
SPEC = importlib.util.spec_from_file_location("drain_splunkbase_publish_queue", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_retry_after_supports_seconds_and_http_dates():
    now = MODULE.parse_datetime("2026-07-29T12:00:00Z")

    assert MODULE.format_datetime(MODULE.retry_after_datetime("300", now)) == (
        "2026-07-29T12:05:00Z"
    )
    assert (
        MODULE.format_datetime(MODULE.retry_after_datetime("Wed, 29 Jul 2026 12:07:00 GMT", now))
        == "2026-07-29T12:07:00Z"
    )


def test_simulation_deduplicates_and_respects_hourly_budget(tmp_path, capsys):
    queue = [
        {
            "repository": f"splunk-soar-connectors/repo-{index:02}",
            "candidate_version": "1.0.0",
        }
        for index in range(13)
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
    assert len(lines) == 13
    assert lines[0].endswith("2026-07-29T12:00:00Z")
    assert lines[11].endswith("2026-07-29T12:55:00Z")
    assert lines[12].endswith("2026-07-29T13:00:00Z")


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
            "candidate_version": "1.2.3",
        },
    )()


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
    client.check_upload_status.side_effect = validation_error
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


def test_eventual_version_appearance_completes_without_post_or_reserve(monkeypatch):
    queue = Mock()
    client = Mock()
    client.get_existing_releases.return_value = [{"release_name": "1.2.3"}]
    complete_existing = Mock(return_value=0)
    run_publisher = Mock()
    monkeypatch.setattr(MODULE, "complete_existing", complete_existing)
    monkeypatch.setattr(MODULE, "run_publisher", run_publisher)

    result = MODULE.reconcile_verification(
        queue,
        client,
        verifying_item(),
        object(),
        MODULE.parse_datetime("2026-07-29T12:00:00Z"),
    )

    assert result == 0
    complete_existing.assert_called_once()
    client.check_upload_status.assert_not_called()
    queue.reserve_attempt.assert_not_called()
    run_publisher.assert_not_called()


def test_continued_pending_validation_does_not_post_or_reserve(monkeypatch):
    queue = Mock()
    client = Mock()
    client.get_existing_releases.return_value = []
    client.check_upload_status.return_value = {"message": "Package validation still in progress."}
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
    client.check_upload_status.return_value = {"message": "Package failed validation."}
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

    assert result == 1
    queue.block.assert_called_once()
    queue.reserve_attempt.assert_not_called()
    run_publisher.assert_not_called()


@pytest.mark.parametrize("response", [{}, {"message": "unknown"}, ["malformed"]])
def test_inconclusive_status_does_not_post_or_reserve(monkeypatch, response):
    queue = Mock()
    client = Mock()
    client.get_existing_releases.return_value = []
    client.check_upload_status.return_value = response
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
