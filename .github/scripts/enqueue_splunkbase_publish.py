"""Create or update one durable Splunkbase queue item."""

import json
import os
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO_ROOT))

from utils.publish_queue import GitHubClient, GitHubIssuePublishQueue, PublishQueueItem


def main() -> int:
    token = os.environ["GITHUB_TOKEN"]
    repository = os.environ["QUEUE_REPOSITORY"]
    payload = json.loads(os.environ["QUEUE_ITEM_JSON"])
    queue = GitHubIssuePublishQueue(GitHubClient(token), repository)
    item = queue.enqueue(PublishQueueItem.from_dict(payload))
    print(f"Queued {item.repository} v{item.candidate_version} as issue #{item.issue_number}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
