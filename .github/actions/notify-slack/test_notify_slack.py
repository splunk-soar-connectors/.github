import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("notify_slack.py")
SPEC = importlib.util.spec_from_file_location("notify_slack", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_updated_release_message_shows_version_transition_on_app_link():
    message = MODULE._build_message(
        app_name="Test App",
        support_tag="splunk",
        splunk_base_url="https://splunkbase.splunk.com/app/123",
        release_version="2.0.0",
        previous_release_version="1.2.3",
    )

    assert (
        ":splunk-arrow: <https://splunkbase.splunk.com/app/123|*Test App*> `v1.2.3 -> v2.0.0`"
    ) in message


def test_release_message_without_previous_version_shows_only_new_version_on_app_link():
    message = MODULE._build_message(
        app_name="Test App",
        support_tag="developer",
        splunk_base_url="https://splunkbase.splunk.com/app/123",
        release_version="1.0.0",
    )

    assert (":splunk-arrow: <https://splunkbase.splunk.com/app/123|*Test App*> `v1.0.0`") in message


def test_pending_review_message_includes_release_version():
    message = MODULE._build_message(
        app_name="Test App",
        support_tag="developer",
        splunk_base_url="https://splunkbase.splunk.com/app/123",
        release_version="1.0.0",
        new_app=True,
        template_name=MODULE.PENDING_REVIEW_TEMPLATE,
    )

    assert ":splunk-arrow: *Test App* v1.0.0" in message


@pytest.mark.parametrize("support_tag", ["splunk", "developer", "not_supported"])
def test_all_support_types_notify_internal_and_community_channels(
    monkeypatch,
    support_tag,
):
    environment = {
        "APP_NAME": "Test App",
        "APP_LOGO": "test.svg",
        "REPO_NAME": "test-app",
        "RELEASE_VERSION": "2.0.0",
        "PREVIOUS_RELEASE_VERSION": "1.2.3",
        "RELEASE_NOTES": "[]",
        "NEW_APP": "false",
        "SUPPORT_TAG": support_tag,
        "SPLUNK_BASE_URL": "https://splunkbase.splunk.com/app/123",
        "SLACK_INTERNAL_TOKEN": "internal-token",
        "SLACK_COMMUNITY_TOKEN": "community-token",
        "SLACK_INTERNAL_CHANNEL": "internal-channel",
        "SLACK_COMMUNITY_CHANNEL": "community-channel",
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    notifications = []
    monkeypatch.setattr(MODULE, "WebClient", lambda token: token)
    monkeypatch.setattr(
        MODULE,
        "_notify_slack_channel",
        lambda client, channel, release_data: notifications.append(
            (client, channel, release_data["support_tag"])
        ),
    )

    MODULE.main()

    assert notifications == [
        ("internal-token", "internal-channel", support_tag),
        ("community-token", "community-channel", support_tag),
    ]
