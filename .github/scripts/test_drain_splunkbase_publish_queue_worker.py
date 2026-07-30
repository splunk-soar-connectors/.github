import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock


MODULE_PATH = Path(__file__).with_name("drain_splunkbase_publish_queue_worker.py")
SPEC = importlib.util.spec_from_file_location(
    "drain_splunkbase_publish_queue_worker",
    MODULE_PATH,
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def queue_with_items(count):
    queue = Mock()
    items = [
        SimpleNamespace(
            attempts=[],
            candidate_version="1.0.0",
            issue_number=index + 1,
            repository=f"splunk-soar-connectors/example-{index + 1}",
        )
        for index in range(count)
    ]
    queue.oldest_eligible.side_effect = [*items, None]
    return queue


def test_drain_stops_after_twenty_started_upload_attempts(monkeypatch):
    queue = queue_with_items(MODULE.MAX_UPLOAD_ATTEMPTS + 1)
    process_item = Mock(
        return_value=MODULE.ItemOutcome(
            queue_status="published",
            attempts_started=1,
            failed=False,
        )
    )
    monkeypatch.setattr(MODULE.DRAIN, "queue_from_environment", lambda: queue)
    monkeypatch.setattr(MODULE, "process_item", process_item)
    monkeypatch.setattr(MODULE.time, "monotonic", lambda: 0)

    assert MODULE.drain_queue(SimpleNamespace()) == 0

    assert process_item.call_count == MODULE.MAX_UPLOAD_ATTEMPTS


def test_pre_upload_failures_do_not_consume_attempt_limit(monkeypatch):
    queue = queue_with_items(MODULE.MAX_UPLOAD_ATTEMPTS + 1)
    process_item = Mock(
        return_value=MODULE.ItemOutcome(
            queue_status="blocked",
            attempts_started=0,
            failed=True,
        )
    )
    monkeypatch.setattr(MODULE.DRAIN, "queue_from_environment", lambda: queue)
    monkeypatch.setattr(MODULE, "process_item", process_item)
    monkeypatch.setattr(MODULE.time, "monotonic", lambda: 0)

    assert MODULE.drain_queue(SimpleNamespace()) == 1

    assert process_item.call_count == MODULE.MAX_UPLOAD_ATTEMPTS + 1


def test_drain_stops_selecting_after_one_hour(monkeypatch):
    queue = queue_with_items(2)
    process_item = Mock(
        return_value=MODULE.ItemOutcome(
            queue_status="blocked",
            attempts_started=0,
            failed=True,
        )
    )
    clock = iter([0, 0, MODULE.MAX_RUN_SECONDS])
    monkeypatch.setattr(MODULE.DRAIN, "queue_from_environment", lambda: queue)
    monkeypatch.setattr(MODULE, "process_item", process_item)
    monkeypatch.setattr(MODULE.time, "monotonic", lambda: next(clock))

    assert MODULE.drain_queue(SimpleNamespace()) == 1

    process_item.assert_called_once()


def test_rate_limit_stops_the_current_drain(monkeypatch):
    queue = queue_with_items(2)
    process_item = Mock(
        return_value=MODULE.ItemOutcome(
            queue_status="rate_limited",
            attempts_started=1,
            failed=False,
        )
    )
    monkeypatch.setattr(MODULE.DRAIN, "queue_from_environment", lambda: queue)
    monkeypatch.setattr(MODULE, "process_item", process_item)
    monkeypatch.setattr(MODULE.time, "monotonic", lambda: 0)

    assert MODULE.drain_queue(SimpleNamespace()) == 0

    process_item.assert_called_once()


def test_process_item_counts_attempt_and_sends_blocked_notification(tmp_path, monkeypatch):
    item = SimpleNamespace(
        app_name="Example",
        appid="example-guid",
        artifact_name="app-tar",
        asset_name="example.tgz",
        attempts=[],
        candidate_version="1.0.0",
        commit_sha="abc123",
        issue_number=1,
        publisher_alias="soar-connectors-default",
        release_tag="splunkbase-publish-queue",
        repository="splunk-soar-connectors/example",
        run_attempt=1,
        run_id=123,
    )
    refreshed = SimpleNamespace(attempts=[{"outcome": "started"}])
    queue = Mock()
    queue.get_item.return_value = refreshed
    blocked_notification = Mock()

    def publish_item(_args):
        Path(os.environ["GITHUB_OUTPUT"]).write_text(
            "queue_status=blocked\nfailure_reason=terminal failure\n"
        )
        return 1

    monkeypatch.setattr(MODULE, "checkout_connector", Mock())
    monkeypatch.setattr(MODULE.DRAIN, "publish_item", publish_item)
    monkeypatch.setattr(MODULE, "send_blocked_notification", blocked_notification)

    outcome = MODULE.process_item(queue, item, MODULE.DRAIN.utc_now())

    assert outcome == MODULE.ItemOutcome(
        queue_status="blocked",
        attempts_started=1,
        failed=True,
    )
    blocked_notification.assert_called_once()


def test_process_item_preserves_success_metrics_and_notification(monkeypatch):
    item = SimpleNamespace(
        app_name="Example",
        appid="example-guid",
        artifact_name="app-tar",
        asset_name="example.tgz",
        attempts=[],
        candidate_version="1.0.0",
        commit_sha="abc123",
        issue_number=1,
        publisher_alias="soar-connectors-default",
        release_tag="splunkbase-publish-queue",
        repository="splunk-soar-connectors/example",
        run_attempt=1,
        run_id=123,
    )
    queue = Mock()
    queue.get_item.return_value = SimpleNamespace(attempts=[{"outcome": "published"}])
    metrics = Mock()
    notification = Mock()

    def publish_item(_args):
        Path(os.environ["GITHUB_OUTPUT"]).write_text(
            "queue_status=published\npublish_return_code=0\n"
        )
        return 0

    monkeypatch.setattr(MODULE, "checkout_connector", Mock())
    monkeypatch.setattr(MODULE.DRAIN, "publish_item", publish_item)
    monkeypatch.setattr(MODULE, "send_release_metrics", metrics)
    monkeypatch.setattr(MODULE, "send_release_notification", notification)

    outcome = MODULE.process_item(queue, item, MODULE.DRAIN.utc_now())

    assert outcome == MODULE.ItemOutcome(
        queue_status="published",
        attempts_started=1,
        failed=False,
    )
    metrics.assert_called_once()
    notification.assert_called_once()
