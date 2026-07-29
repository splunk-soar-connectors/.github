from datetime import datetime, timedelta, timezone
import json

from .publish_queue import (
    BODY_END,
    BODY_START,
    LABELS,
    MAX_ATTEMPTS_PER_HOUR,
    MIN_ATTEMPT_INTERVAL,
    GitHubIssuePublishQueue,
    PublishQueueItem,
    _decode_body,
    _encode_body,
    format_datetime,
)


def make_item(**overrides):
    now = "2026-07-29T12:00:00Z"
    values = {
        "publisher_alias": "soar-connectors-default",
        "repository": "splunk-soar-connectors/example",
        "run_id": 123,
        "run_attempt": 1,
        "commit_sha": "abc123",
        "artifact_name": "app-tar",
        "asset_name": "example-1.2.3.tgz",
        "release_tag": "splunkbase-publish-queue",
        "candidate_version": "1.2.3",
        "appid": "example-guid",
        "app_name": "Example",
        "enqueued_at": now,
        "not_before": now,
    }
    values.update(overrides)
    return PublishQueueItem(**values)


class FakeGitHubClient:
    def __init__(self):
        self.labels = set(LABELS)
        self.issues = []
        self.next_issue = 1

    def request(self, method, path, **kwargs):
        if path.endswith("/labels") and method == "GET":
            return [{"name": label} for label in self.labels]
        if path.endswith("/labels") and method == "POST":
            self.labels.add(kwargs["json"]["name"])
            return kwargs["json"]
        if path.endswith("/issues") and method == "GET":
            params = kwargs.get("params", {})
            requested_labels = set(filter(None, params.get("labels", "").split(",")))
            state = params.get("state", "open")
            matching = []
            for issue in self.issues:
                if state != "all" and issue["state"] != state:
                    continue
                issue_labels = {label["name"] for label in issue["labels"]}
                if requested_labels <= issue_labels:
                    matching.append(issue.copy())
            return matching
        if path.endswith("/issues") and method == "POST":
            data = kwargs["json"]
            issue = {
                "number": self.next_issue,
                "title": data["title"],
                "body": data["body"],
                "state": data.get("state", "open"),
                "labels": [{"name": label} for label in data.get("labels", [])],
            }
            self.next_issue += 1
            self.issues.append(issue)
            return issue.copy()

        issue_number = int(path.rsplit("/", 1)[-1])
        issue = next(issue for issue in self.issues if issue["number"] == issue_number)
        if method == "GET":
            return issue.copy()
        if method == "PATCH":
            data = kwargs["json"]
            issue.update({key: value for key, value in data.items() if key != "labels"})
            if "labels" in data:
                issue["labels"] = [{"name": label} for label in data["labels"]]
            return issue.copy()
        raise AssertionError(f"Unhandled request: {method} {path}")


def test_queue_body_round_trip_preserves_dedupe_fields():
    item = make_item()

    decoded = _decode_body(_encode_body(item))

    assert decoded.dedupe_key == item.dedupe_key
    assert decoded.asset_name == item.asset_name
    assert "Codex-authored" in _encode_body(item)


def test_duplicate_enqueue_reuses_one_issue():
    client = FakeGitHubClient()
    queue = GitHubIssuePublishQueue(client, "splunk-soar-connectors/.github")

    first = queue.enqueue(make_item())
    second = queue.enqueue(make_item(run_id=456, run_attempt=2))

    assert first.issue_number == second.issue_number
    assert len(client.issues) == 1
    assert queue.get_item(first.issue_number).run_id == 456


def test_oldest_eligible_skips_future_item():
    client = FakeGitHubClient()
    queue = GitHubIssuePublishQueue(client, "splunk-soar-connectors/.github")
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    queue.enqueue(
        make_item(
            repository="splunk-soar-connectors/future",
            enqueued_at=format_datetime(now - timedelta(hours=2)),
            not_before=format_datetime(now + timedelta(minutes=1)),
        )
    )
    expected = queue.enqueue(
        make_item(
            repository="splunk-soar-connectors/eligible",
            enqueued_at=format_datetime(now - timedelta(hours=1)),
        )
    )

    selected = queue.oldest_eligible("soar-connectors-default", now)

    assert selected.issue_number == expected.issue_number


def test_active_item_is_recovered_only_after_its_lease_expires():
    client = FakeGitHubClient()
    queue = GitHubIssuePublishQueue(client, "splunk-soar-connectors/.github")
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    item = queue.enqueue(make_item())
    queue.activate(item, now + timedelta(minutes=15))

    assert queue.oldest_eligible(item.publisher_alias, now) is None
    assert (
        queue.oldest_eligible(
            item.publisher_alias,
            now + timedelta(minutes=16),
        ).issue_number
        == item.issue_number
    )


def test_verifying_item_is_selected_without_becoming_queued():
    client = FakeGitHubClient()
    queue = GitHubIssuePublishQueue(client, "splunk-soar-connectors/.github")
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    item = queue.enqueue(make_item())
    queue.verify(
        item,
        {
            "status": "verifying",
            "package_id": "package-123",
            "request_id": "request-123",
        },
        now,
        "Waiting for package validation.",
    )

    selected = queue.oldest_eligible(item.publisher_alias, now)

    assert selected.issue_number == item.issue_number
    assert selected.verification == {
        "status": "verifying",
        "package_id": "package-123",
        "request_id": "request-123",
    }
    assert client.issues[0]["labels"] == [
        {"name": "splunkbase-publish"},
        {"name": "splunkbase-verifying"},
    ]


def test_rate_ledger_never_reserves_more_than_twelve_in_a_rolling_hour():
    client = FakeGitHubClient()
    queue = GitHubIssuePublishQueue(client, "splunk-soar-connectors/.github")
    item = queue.enqueue(make_item())
    start = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)

    for index in range(MAX_ATTEMPTS_PER_HOUR):
        assert (
            queue.reserve_attempt(
                item.publisher_alias,
                item,
                start + index * MIN_ATTEMPT_INTERVAL,
            )
            is None
        )

    retry_at = queue.reserve_attempt(
        item.publisher_alias,
        item,
        start + timedelta(minutes=59),
    )

    assert retry_at == start + timedelta(hours=1)


def test_state_issue_contains_only_public_operational_metadata():
    client = FakeGitHubClient()
    queue = GitHubIssuePublishQueue(client, "splunk-soar-connectors/.github")
    item = queue.enqueue(make_item())
    queue.reserve_attempt(
        item.publisher_alias,
        item,
        datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
    )
    state_issue = next(issue for issue in client.issues if "queue state" in issue["title"])
    start = state_issue["body"].index(BODY_START) + len(BODY_START)
    end = state_issue["body"].index(BODY_END, start)
    state = json.loads(state_issue["body"][start:end].strip())

    assert set(state) == {"attempts", "publisher_alias"}
    assert "token" not in state_issue["body"].lower()
    assert "password" not in state_issue["body"].lower()


def test_blocked_issue_does_not_publish_raw_response_text():
    client = FakeGitHubClient()
    queue = GitHubIssuePublishQueue(client, "splunk-soar-connectors/.github")
    item = queue.enqueue(make_item())

    queue.block(
        item,
        {
            "status": "validation_failed",
            "status_code": 422,
            "request_id": "request-123",
            "message": "internal Splunkbase response details",
        },
    )

    body = client.issues[0]["body"]
    assert "validation_failed" in body
    assert "request-123" in body
    assert "internal Splunkbase response details" not in body
