import importlib.util
from pathlib import Path
from unittest.mock import Mock


MODULE_PATH = Path(__file__).with_name("notify_splunkbase_publish_failure.py")
SPEC = importlib.util.spec_from_file_location("notify_splunkbase_publish_failure", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def environment(**overrides):
    values = {
        "SLACK_INTERNAL_TOKEN": "token",
        "SLACK_INTERNAL_CHANNEL": "C123",
        "QUEUE_ISSUE_URL": "https://github.com/splunk-soar-connectors/.github/issues/10",
        "WORKER_RUN_URL": ("https://github.com/splunk-soar-connectors/.github/actions/runs/12345"),
        "CONNECTOR_REPOSITORY": "splunk-soar-connectors/example",
        "CONNECTOR_VERSION": "1.2.3",
        "SPLUNKBASE_REQUEST_ID": "request-123",
        "SPLUNKBASE_PACKAGE_ID": "package-123",
        "FAILURE_REASON": "Splunkbase definitively rejected the package.",
    }
    values.update(overrides)
    return values


def test_message_contains_tracking_fields_and_codex_attribution():
    message = MODULE.build_message(environment())

    assert "splunk-soar-connectors/example v1.2.3" in message
    assert "request-123" in message
    assert "package-123" in message
    assert "/issues/10" in message
    assert (
        "Failed workflow: "
        "<https://github.com/splunk-soar-connectors/.github/actions/runs/12345"
        "|open failed worker run>"
    ) in message
    assert "Codex-authored" in message


def test_message_omits_splunkbase_ids_before_an_upload():
    message = MODULE.build_message(environment(SPLUNKBASE_REQUEST_ID="", SPLUNKBASE_PACKAGE_ID=""))

    assert "request ID" not in message
    assert "package ID" not in message
    assert "unavailable" not in message


def test_main_posts_one_internal_notification(monkeypatch):
    response = Mock()
    response.json.return_value = {"ok": True}
    post = Mock(return_value=response)
    monkeypatch.setattr(MODULE.requests, "post", post)
    for key, value in environment().items():
        monkeypatch.setenv(key, value)

    MODULE.main()

    post.assert_called_once()
    assert post.call_args.kwargs["json"]["channel"] == "C123"
    response.raise_for_status.assert_called_once()
