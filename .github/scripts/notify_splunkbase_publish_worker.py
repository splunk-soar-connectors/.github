# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = [
#   "requests>=2.32.3,<3.0.0",
# ]
# ///
"""Notify the internal connector channel about an incomplete queue worker run."""

from __future__ import annotations

import os

import requests


def build_message(environment: dict[str, str]) -> str:
    run_url = environment.get("WORKER_RUN_URL")
    run = f"<{run_url}|open worker run>" if run_url else "unavailable"
    conclusion = environment.get("WORKER_CONCLUSION") or "unknown"
    reason = environment.get("WORKER_REASON") or "No additional reason was recorded."
    return "\n".join(
        [
            ":warning: Splunkbase publish queue worker did not complete",
            f"Conclusion: {conclusion}",
            f"Worker run: {run}",
            f"Reason: {reason}",
        ]
    )


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
        raise RuntimeError(f"Slack rejected worker failure notification: {payload.get('error')}")


if __name__ == "__main__":
    main()
