# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = [
#   "requests>=2.32.3,<3.0.0",
# ]
# ///
"""Notify the internal connector channel about a terminal queue failure."""

from __future__ import annotations

import os

import requests


def build_message(environment: dict[str, str]) -> str:
    lines = [
        ":warning: Splunkbase connector publication blocked",
        (f"Repository: {environment['CONNECTOR_REPOSITORY']} v{environment['CONNECTOR_VERSION']}"),
        f"Queue item: {environment['QUEUE_ISSUE_URL']}",
        f"Failed workflow: <{environment['WORKER_RUN_URL']}|open failed worker run>",
    ]
    if request_id := environment.get("SPLUNKBASE_REQUEST_ID"):
        lines.append(f"Splunkbase request ID: {request_id}")
    if package_id := environment.get("SPLUNKBASE_PACKAGE_ID"):
        lines.append(f"Splunkbase package ID: {package_id}")
    lines.extend(
        [
            f"Reason: {environment['FAILURE_REASON']}",
            "Automated by the Codex-authored Splunkbase publish queue.",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    response = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {os.environ['SLACK_INTERNAL_TOKEN']}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={
            "channel": os.environ["SLACK_INTERNAL_CHANNEL"],
            "text": build_message(os.environ),
            "unfurl_links": False,
            "unfurl_media": False,
        },
        timeout=(10, 30),
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(
            f"Slack rejected publication failure notification: {payload.get('error')}"
        )


if __name__ == "__main__":
    main()
