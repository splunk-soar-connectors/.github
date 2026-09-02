import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


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


def published_item():
    return SimpleNamespace(
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


def publish_succeeded(_args):
    Path(os.environ["GITHUB_OUTPUT"]).write_text("queue_status=published\npublish_return_code=0\n")
    return 0


def test_worker_logging_uses_only_time_level_and_message(monkeypatch):
    basic_config = Mock()
    monkeypatch.setattr(MODULE.logging, "basicConfig", basic_config)

    MODULE.configure_logging()

    basic_config.assert_called_once_with(
        level=MODULE.logging.INFO,
        format="{asctime} - {levelname} - {message}",
        datefmt="%Y-%m-%d %H:%M:%S",
        style="{",
        force=True,
    )


def test_connector_checkout_suppresses_routine_git_output(tmp_path, monkeypatch):
    run_checked = Mock()
    destination = tmp_path / "connector"
    monkeypatch.setattr(MODULE, "run_checked", run_checked)

    MODULE.checkout_connector(
        "splunk-soar-connectors/example",
        "abc123",
        destination,
    )

    commands = [call.args[0] for call in run_checked.call_args_list]
    assert commands[0] == [
        "git",
        "init",
        "--quiet",
        "--initial-branch=main",
        str(destination),
    ]
    assert "--quiet" in commands[2]
    assert "--quiet" in commands[3]


def test_traditional_release_metrics_use_tracked_manifests(tmp_path, monkeypatch):
    connector = tmp_path / "connector"
    connector.mkdir()
    current_manifest = connector / "example.json"
    current_manifest.write_text('{"name": "Example", "actions": []}')
    old_data = '{"name": "Example", "actions": [{"action": "old"}]}'
    previous = SimpleNamespace(returncode=0, stdout=old_data)
    run_checked = Mock()
    monkeypatch.setattr(MODULE.subprocess, "run", Mock(return_value=previous))
    monkeypatch.setattr(MODULE, "run_checked", run_checked)

    MODULE.send_release_metrics(
        {"publish_return_code": "0"},
        connector,
        tmp_path / "unused.tgz",
        tmp_path,
    )

    assert (tmp_path / "old-app.json").read_text() == old_data
    assert run_checked.call_args.args[0][2:4] == [
        str(current_manifest),
        str(tmp_path / "old-app.json"),
    ]


def test_existing_sdk_release_metrics_use_package_and_previous_tag(tmp_path, monkeypatch):
    connector = tmp_path / "connector"
    connector.mkdir()
    (connector / "uv.lock").write_text("")
    artifact = tmp_path / "queued.tgz"
    current_data = {"name": "Example", "actions": [{"action": "current"}]}
    old_data = {"name": "Example", "actions": [{"action": "old"}]}
    get_app_json = Mock(return_value=current_data)
    monkeypatch.setattr(
        MODULE.DRAIN,
        "load_publisher_module",
        lambda: SimpleNamespace(get_app_json=get_app_json),
    )

    commands = []

    def run_checked(command, **_kwargs):
        commands.append(command)
        if "worktree" in command:
            previous_checkout = Path(command[-2])
            previous_checkout.mkdir()
            (previous_checkout / "uv.lock").write_text("")
        if "manifests" in command:
            Path(command[-2]).write_text(json.dumps(old_data))

    monkeypatch.setattr(MODULE, "run_checked", run_checked)

    MODULE.send_release_metrics(
        {"publish_return_code": "0", "previous_release_version": "1.2.2"},
        connector,
        artifact,
        tmp_path,
    )

    get_app_json.assert_called_once_with(artifact)
    assert json.loads((tmp_path / "current-app.json").read_text()) == current_data
    assert json.loads((tmp_path / "old-app.json").read_text()) == old_data
    assert commands[0][-1] == "refs/tags/1.2.2:refs/tags/1.2.2"
    assert commands[1][-1] == "refs/tags/1.2.2"
    assert commands[2][0:4] == ["uv", "run", "--project", str(tmp_path / "previous-release")]
    assert commands[3][2:4] == [
        str(tmp_path / "current-app.json"),
        str(tmp_path / "old-app.json"),
    ]


def test_new_sdk_release_metrics_use_empty_previous_manifest(tmp_path, monkeypatch):
    connector = tmp_path / "connector"
    connector.mkdir()
    (connector / "uv.lock").write_text("")
    artifact = tmp_path / "queued.tgz"
    current_data = {"name": "Example", "actions": [{"action": "current"}]}
    get_app_json = Mock(return_value=current_data)
    monkeypatch.setattr(
        MODULE.DRAIN,
        "load_publisher_module",
        lambda: SimpleNamespace(get_app_json=get_app_json),
    )
    run_checked = Mock()
    monkeypatch.setattr(MODULE, "run_checked", run_checked)

    MODULE.send_release_metrics(
        {"publish_return_code": "2", "previous_release_version": ""},
        connector,
        artifact,
        tmp_path,
    )

    assert json.loads((tmp_path / "current-app.json").read_text()) == current_data
    assert json.loads((tmp_path / "old-app.json").read_text()) == {}
    run_checked.assert_called_once()


def test_sdk_release_metrics_require_one_uv_lock(tmp_path):
    connector = tmp_path / "connector"
    connector.mkdir()

    with pytest.raises(RuntimeError, match="Expected one SDK uv.lock, found 0"):
        MODULE.send_release_metrics(
            {"publish_return_code": "0"},
            connector,
            tmp_path / "queued.tgz",
            tmp_path,
        )


def test_sdk_release_metrics_reject_ambiguous_package_manifests(tmp_path, monkeypatch):
    connector = tmp_path / "connector"
    connector.mkdir()
    (connector / "uv.lock").write_text("")
    get_app_json = Mock(
        side_effect=ValueError("No or multiple JSON files found in top level of app repo")
    )
    monkeypatch.setattr(
        MODULE.DRAIN,
        "load_publisher_module",
        lambda: SimpleNamespace(get_app_json=get_app_json),
    )

    with pytest.raises(ValueError, match="multiple JSON files"):
        MODULE.send_release_metrics(
            {"publish_return_code": "0"},
            connector,
            tmp_path / "queued.tgz",
            tmp_path,
        )


def test_existing_sdk_release_metrics_require_previous_tag(tmp_path, monkeypatch):
    connector = tmp_path / "connector"
    connector.mkdir()
    (connector / "uv.lock").write_text("")
    monkeypatch.setattr(
        MODULE.DRAIN,
        "load_publisher_module",
        lambda: SimpleNamespace(get_app_json=Mock(return_value={})),
    )
    monkeypatch.setattr(
        MODULE,
        "run_checked",
        Mock(side_effect=subprocess.CalledProcessError(1, ["git", "fetch"])),
    )

    with pytest.raises(RuntimeError, match="previous release tag 1.2.2"):
        MODULE.send_release_metrics(
            {"publish_return_code": "0", "previous_release_version": "1.2.2"},
            connector,
            tmp_path / "queued.tgz",
            tmp_path,
        )


def test_existing_sdk_release_metrics_require_previous_lock(tmp_path, monkeypatch):
    connector = tmp_path / "connector"
    connector.mkdir()
    (connector / "uv.lock").write_text("")
    monkeypatch.setattr(
        MODULE.DRAIN,
        "load_publisher_module",
        lambda: SimpleNamespace(get_app_json=Mock(return_value={})),
    )

    def run_checked(command, **_kwargs):
        if "worktree" in command:
            Path(command[-2]).mkdir()

    monkeypatch.setattr(MODULE, "run_checked", run_checked)

    with pytest.raises(RuntimeError, match="Expected one SDK uv.lock, found 0"):
        MODULE.send_release_metrics(
            {"publish_return_code": "0", "previous_release_version": "1.2.2"},
            connector,
            tmp_path / "queued.tgz",
            tmp_path,
        )


def test_worker_log_separates_repository_results(monkeypatch, capsys):
    queue = queue_with_items(1)
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

    output = capsys.readouterr().out
    assert (
        f"{MODULE.LOG_SEPARATOR}\n"
        "Queue issue #1 | splunk-soar-connectors/example-1 v1.0.0\n"
        f"{MODULE.LOG_SEPARATOR}"
    ) in output
    assert (
        f"{MODULE.LOG_SEPARATOR}\nFinished splunk-soar-connectors/example-1 v1.0.0: blocked"
    ) in output


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
    item = published_item()
    queue = Mock()
    queue.get_item.return_value = SimpleNamespace(attempts=[{"outcome": "published"}])
    metrics = Mock()
    notification = Mock()

    monkeypatch.setattr(MODULE, "checkout_connector", Mock())
    monkeypatch.setattr(MODULE.DRAIN, "publish_item", publish_succeeded)
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


def test_metrics_failure_does_not_suppress_release_notification(monkeypatch, capsys):
    item = published_item()
    queue = Mock()
    queue.get_item.return_value = SimpleNamespace(attempts=[{"outcome": "published"}])
    metrics = Mock(side_effect=RuntimeError("metrics unavailable"))
    notification = Mock()
    monkeypatch.setattr(MODULE, "checkout_connector", Mock())
    monkeypatch.setattr(MODULE.DRAIN, "publish_item", publish_succeeded)
    monkeypatch.setattr(MODULE, "send_release_metrics", metrics)
    monkeypatch.setattr(MODULE, "send_release_notification", notification)

    outcome = MODULE.process_item(queue, item, MODULE.DRAIN.utc_now())

    assert outcome == MODULE.ItemOutcome(
        queue_status="published",
        attempts_started=1,
        failed=True,
    )
    notification.assert_called_once()
    assert "Release metrics failed" in capsys.readouterr().err


def test_notification_failure_does_not_suppress_release_metrics(monkeypatch, capsys):
    item = published_item()
    queue = Mock()
    queue.get_item.return_value = SimpleNamespace(attempts=[{"outcome": "published"}])
    metrics = Mock()
    notification = Mock(side_effect=RuntimeError("Slack unavailable"))
    monkeypatch.setattr(MODULE, "checkout_connector", Mock())
    monkeypatch.setattr(MODULE.DRAIN, "publish_item", publish_succeeded)
    monkeypatch.setattr(MODULE, "send_release_metrics", metrics)
    monkeypatch.setattr(MODULE, "send_release_notification", notification)

    outcome = MODULE.process_item(queue, item, MODULE.DRAIN.utc_now())

    assert outcome == MODULE.ItemOutcome(
        queue_status="published",
        attempts_started=1,
        failed=True,
    )
    metrics.assert_called_once()
    assert "Slack notification failed" in capsys.readouterr().err
