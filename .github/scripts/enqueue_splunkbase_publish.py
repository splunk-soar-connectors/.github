# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = [
#   "requests>=2.32.3,<3.0.0",
# ]
# ///
"""Create or update one durable Splunkbase queue item."""

import json
import os
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO_ROOT))

from utils.publish_queue import GitHubClient, GitHubIssuePublishQueue, PublishQueueItem


def unwrap_queue_item(payload: dict) -> dict:
    """Accept the bounded nested payload and legacy flat payloads."""

    queue_item = payload.get("queue_item", payload)
    if not isinstance(queue_item, dict):
        raise ValueError("Repository dispatch queue_item must be an object")
    return queue_item


def main() -> int:
    token = os.environ["GITHUB_TOKEN"]
    repository = os.environ["QUEUE_REPOSITORY"]
    payload = unwrap_queue_item(json.loads(os.environ["QUEUE_ITEM_JSON"]))
    queue = GitHubIssuePublishQueue(GitHubClient(token), repository)
    item = queue.enqueue(PublishQueueItem.from_dict(payload))
    print(f"Queued {item.repository} v{item.candidate_version} as issue #{item.issue_number}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
