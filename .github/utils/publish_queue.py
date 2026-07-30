"""Durable GitHub-issue storage for serialized Splunkbase uploads."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Protocol
from urllib.parse import quote

import requests


QUEUE_MARKER = "splunkbase-publish"
QUEUE_STATES = {
    "queued": "splunkbase-queued",
    "active": "splunkbase-active",
    "verifying": "splunkbase-verifying",
    "rate_limited": "splunkbase-rate-limited",
    "blocked": "splunkbase-blocked",
    "published": "splunkbase-published",
}
STATE_LABEL = "splunkbase-publish-state"
BODY_START = "<!-- splunkbase-publish-queue-json"
BODY_END = "splunkbase-publish-queue-json -->"
MAX_ATTEMPTS_PER_HOUR = 20
MIN_ATTEMPT_INTERVAL = timedelta(minutes=3)

LABELS = {
    QUEUE_MARKER: ("5319e7", "Managed by Splunkbase queue automation."),
    QUEUE_STATES["queued"]: ("fbca04", "Waiting for the shared Splunkbase publishing user."),
    QUEUE_STATES["active"]: ("1d76db", "A worker is processing this publication."),
    QUEUE_STATES["verifying"]: (
        "bfd4f2",
        "Splunkbase accepted the upload; only GET reconciliation is permitted.",
    ),
    QUEUE_STATES["rate_limited"]: ("d93f0b", "Splunkbase asked the shared user to wait."),
    QUEUE_STATES["blocked"]: ("b60205", "Publication needs human intervention."),
    QUEUE_STATES["published"]: ("0e8a16", "The connector version is present on Splunkbase."),
    STATE_LABEL: ("c5def5", "Rate ledger for the Splunkbase queue."),
}
PUBLIC_RESULT_FIELDS = {
    "app_existed_before_upload",
    "status",
    "status_code",
    "request_id",
    "retry_after",
    "release_version",
    "package_id",
    "splunkbase_app_id",
    "worker_run_url",
    "failure_reason",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class PublishQueueItem:
    publisher_alias: str
    repository: str
    run_id: int
    run_attempt: int
    commit_sha: str
    artifact_name: str
    asset_name: str
    release_tag: str
    candidate_version: str
    appid: str
    app_name: str
    enqueued_at: str
    not_before: str
    schema_version: int = 1
    issue_number: int | None = None
    attempts: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] | None = None

    @property
    def dedupe_key(self) -> str:
        return f"{self.publisher_alias}:{self.repository}:{self.candidate_version}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PublishQueueItem:
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in data.items() if key in allowed})

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PublishAttempt:
    started_at: str
    outcome: str
    app_existed_before_upload: bool
    status_code: int | None = None
    request_id: str | None = None
    retry_after: str | None = None


class PublishQueue(Protocol):
    def enqueue(self, item: PublishQueueItem) -> PublishQueueItem: ...

    def oldest_eligible(
        self,
        publisher_alias: str,
        now: datetime,
    ) -> PublishQueueItem | None: ...

    def record_attempt(self, item: PublishQueueItem, attempt: PublishAttempt) -> None: ...

    def requeue(
        self,
        item: PublishQueueItem,
        not_before: datetime,
        reason: str,
        *,
        rate_limited: bool = False,
    ) -> None: ...

    def verify(
        self,
        item: PublishQueueItem,
        result: dict[str, Any],
        not_before: datetime,
        reason: str,
    ) -> None: ...

    def complete(self, item: PublishQueueItem, result: dict[str, Any]) -> None: ...

    def block(self, item: PublishQueueItem, result: dict[str, Any]) -> None: ...


class GitHubClient:
    def __init__(self, token: str, api_url: str = "https://api.github.com"):
        self.api_url = api_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Splunk-SOAR-Publish-Queue/1.0",
            }
        )

    def request(self, method: str, path: str, **kwargs):
        response = self.session.request(
            method,
            f"{self.api_url}/{path.lstrip('/')}",
            timeout=(10, 60),
            **kwargs,
        )
        if not response.ok:
            raise RuntimeError(
                f"GitHub API {method} {path} failed: {response.status_code}: {response.text}"
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def download(self, path: str, destination) -> None:
        response = self.session.get(
            f"{self.api_url}/{path.lstrip('/')}",
            headers={"Accept": "application/octet-stream"},
            timeout=(10, 120),
            stream=True,
        )
        if not response.ok:
            raise RuntimeError(
                f"GitHub API GET {path} failed: {response.status_code}: {response.text}"
            )
        with open(destination, "wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                output.write(chunk)


def _encode_body(item: PublishQueueItem, note: str | None = None) -> str:
    note_text = f"\n\nStatus note: {note}" if note else ""
    return (
        "Created and managed by Splunkbase queue automation."
        f"{note_text}\n\n{BODY_START}\n"
        f"{json.dumps(item.as_dict(), indent=2, sort_keys=True)}\n"
        f"{BODY_END}\n"
    )


def _decode_body(body: str) -> PublishQueueItem:
    start = body.index(BODY_START) + len(BODY_START)
    end = body.index(BODY_END, start)
    return PublishQueueItem.from_dict(json.loads(body[start:end].strip()))


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in result.items()
        if key in PUBLIC_RESULT_FIELDS and value is not None
    }


class GitHubIssuePublishQueue:
    def __init__(self, client: GitHubClient, repository: str):
        self.client = client
        self.repository = repository
        self.repo_path = f"repos/{repository}"

    def ensure_labels(self) -> None:
        existing = {
            label["name"]
            for label in self.client.request(
                "GET",
                f"{self.repo_path}/labels",
                params={"per_page": 100},
            )
        }
        for name, (color, description) in LABELS.items():
            if name not in existing:
                try:
                    self.client.request(
                        "POST",
                        f"{self.repo_path}/labels",
                        json={"name": name, "color": color, "description": description},
                    )
                except RuntimeError as create_error:
                    try:
                        self.client.request(
                            "GET",
                            f"{self.repo_path}/labels/{quote(name, safe='')}",
                        )
                    except RuntimeError:
                        raise create_error

    def _issues(self, *, state: str = "open", labels: str = QUEUE_MARKER):
        page = 1
        while True:
            issues = self.client.request(
                "GET",
                f"{self.repo_path}/issues",
                params={
                    "state": state,
                    "labels": labels,
                    "per_page": 100,
                    "page": page,
                },
            )
            for issue in issues:
                if "pull_request" not in issue:
                    yield issue
            if len(issues) < 100:
                return
            page += 1

    @staticmethod
    def _label_names(issue: dict[str, Any]) -> set[str]:
        return {label["name"] for label in issue.get("labels", [])}

    def _find_dedupe(self, dedupe_key: str):
        for issue in self._issues(state="all"):
            try:
                item = _decode_body(issue.get("body") or "")
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
            if item.dedupe_key == dedupe_key:
                return issue, item
        return None, None

    def enqueue(self, item: PublishQueueItem) -> PublishQueueItem:
        self.ensure_labels()
        issue, existing_item = self._find_dedupe(item.dedupe_key)
        if issue:
            existing_item.issue_number = issue["number"]
            labels = self._label_names(issue)
            if QUEUE_STATES["published"] in labels:
                return existing_item
            if (
                QUEUE_STATES["blocked"] in labels
                or QUEUE_STATES["active"] in labels
                or QUEUE_STATES["verifying"] in labels
            ):
                return existing_item

            item.issue_number = issue["number"]
            self.client.request(
                "PATCH",
                f"{self.repo_path}/issues/{issue['number']}",
                json={
                    "body": _encode_body(item, "Duplicate enqueue updated the immutable asset."),
                    "state": "open",
                    "labels": [QUEUE_MARKER, QUEUE_STATES["queued"]],
                },
            )
            return item

        created = self.client.request(
            "POST",
            f"{self.repo_path}/issues",
            json={
                "title": (f"[Splunkbase queue] {item.repository} v{item.candidate_version}"),
                "body": _encode_body(item),
                "labels": [QUEUE_MARKER, QUEUE_STATES["queued"]],
            },
        )
        item.issue_number = created["number"]
        self.client.request(
            "PATCH",
            f"{self.repo_path}/issues/{created['number']}",
            json={"body": _encode_body(item)},
        )
        return item

    def oldest_eligible(
        self,
        publisher_alias: str,
        now: datetime,
    ) -> PublishQueueItem | None:
        candidates = []
        for issue in self._issues(state="open", labels=QUEUE_MARKER):
            labels = self._label_names(issue)
            if not (
                QUEUE_STATES["queued"] in labels
                or QUEUE_STATES["active"] in labels
                or QUEUE_STATES["verifying"] in labels
            ):
                continue
            try:
                item = _decode_body(issue.get("body") or "")
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
            if item.publisher_alias != publisher_alias:
                continue
            if parse_datetime(item.not_before) > now:
                continue
            item.issue_number = issue["number"]
            candidates.append(item)
        if not candidates:
            return None
        return min(candidates, key=lambda candidate: parse_datetime(candidate.enqueued_at))

    def get_item(self, issue_number: int) -> PublishQueueItem:
        issue = self.client.request(
            "GET",
            f"{self.repo_path}/issues/{issue_number}",
        )
        item = _decode_body(issue.get("body") or "")
        item.issue_number = issue_number
        return item

    def _update(
        self,
        item: PublishQueueItem,
        *,
        state: str,
        note: str,
        close: bool = False,
        extra_labels: list[str] | None = None,
    ) -> None:
        if item.issue_number is None:
            raise ValueError("Queue item has no GitHub issue number")
        labels = [QUEUE_MARKER, QUEUE_STATES[state], *(extra_labels or [])]
        self.client.request(
            "PATCH",
            f"{self.repo_path}/issues/{item.issue_number}",
            json={
                "body": _encode_body(item, note),
                "labels": labels,
                "state": "closed" if close else "open",
            },
        )

    def activate(self, item: PublishQueueItem, lease_until: datetime) -> None:
        item.not_before = format_datetime(lease_until)
        self._update(item, state="active", note="Selected by the central publisher.")

    def record_attempt(self, item: PublishQueueItem, attempt: PublishAttempt) -> None:
        item.attempts.append(asdict(attempt))
        self._update(
            item,
            state="active",
            note=f"Counted upload attempt started at {attempt.started_at}.",
        )

    def requeue(
        self,
        item: PublishQueueItem,
        not_before: datetime,
        reason: str,
        *,
        rate_limited: bool = False,
    ) -> None:
        item.not_before = format_datetime(not_before)
        extra_labels = [QUEUE_STATES["rate_limited"]] if rate_limited else []
        self._update(
            item,
            state="queued",
            note=reason,
            extra_labels=extra_labels,
        )

    def verify(
        self,
        item: PublishQueueItem,
        result: dict[str, Any],
        not_before: datetime,
        reason: str,
    ) -> None:
        public_result = _public_result(result)
        item.verification = public_result
        item.not_before = format_datetime(not_before)
        if item.attempts:
            item.attempts[-1].update(public_result)
        else:
            item.attempts.append(public_result)
        self._update(item, state="verifying", note=reason)

    def complete(self, item: PublishQueueItem, result: dict[str, Any]) -> None:
        public_result = _public_result(result)
        if item.attempts:
            item.attempts[-1].update(public_result)
        else:
            item.attempts.append(public_result)
        self._update(
            item,
            state="published",
            note="The candidate version is present on Splunkbase.",
            close=True,
        )

    def block(self, item: PublishQueueItem, result: dict[str, Any]) -> None:
        public_result = _public_result(result)
        if item.attempts:
            item.attempts[-1].update(public_result)
        else:
            item.attempts.append(public_result)
        worker_run_url = public_result.get("worker_run_url")
        reason = public_result.get("failure_reason")
        note = reason or "Publication stopped without an automatic retry."
        if worker_run_url:
            note = f"{note} See the [worker run]({worker_run_url})."
        self._update(
            item,
            state="blocked",
            note=note,
        )

    def _state_issue(self, publisher_alias: str):
        title = f"[Splunkbase queue state] {publisher_alias}"
        for issue in self._issues(state="open", labels=STATE_LABEL):
            if issue["title"] == title:
                return issue
        return self.client.request(
            "POST",
            f"{self.repo_path}/issues",
            json={
                "title": title,
                "body": (
                    "Managed by Splunkbase queue automation.\n\n"
                    f"{BODY_START}\n"
                    f"{json.dumps({'publisher_alias': publisher_alias, 'attempts': []}, indent=2)}\n"
                    f"{BODY_END}\n"
                ),
                "labels": [STATE_LABEL],
            },
        )

    def reserve_attempt(
        self,
        publisher_alias: str,
        item: PublishQueueItem,
        now: datetime,
    ) -> datetime | None:
        issue = self._state_issue(publisher_alias)
        body = issue.get("body") or ""
        start = body.index(BODY_START) + len(BODY_START)
        end = body.index(BODY_END, start)
        state = json.loads(body[start:end].strip())
        cutoff = now - timedelta(hours=1)
        attempts = [
            attempt
            for attempt in state.get("attempts", [])
            if parse_datetime(attempt["started_at"]) > cutoff
        ]

        retry_at = None
        if attempts:
            last_attempt = max(parse_datetime(attempt["started_at"]) for attempt in attempts)
            retry_at = last_attempt + MIN_ATTEMPT_INTERVAL
        if len(attempts) >= MAX_ATTEMPTS_PER_HOUR:
            hourly_retry = min(parse_datetime(attempt["started_at"]) for attempt in attempts)
            hourly_retry += timedelta(hours=1)
            retry_at = max(retry_at or hourly_retry, hourly_retry)
        if retry_at and retry_at > now:
            return retry_at

        attempts.append(
            {
                "issue_number": item.issue_number,
                "repository": item.repository,
                "version": item.candidate_version,
                "started_at": format_datetime(now),
            }
        )
        state["attempts"] = attempts
        state_body = (
            "Managed by Splunkbase queue automation.\n\n"
            f"{BODY_START}\n{json.dumps(state, indent=2, sort_keys=True)}\n{BODY_END}\n"
        )
        self.client.request(
            "PATCH",
            f"{self.repo_path}/issues/{issue['number']}",
            json={"body": state_body},
        )
        return None

    def delete_asset(self, item: PublishQueueItem) -> None:
        release = self.client.request(
            "GET",
            f"{self.repo_path}/releases/tags/{item.release_tag}",
        )
        for asset in release.get("assets", []):
            if asset["name"] == item.asset_name:
                self.client.request(
                    "DELETE",
                    f"{self.repo_path}/releases/assets/{asset['id']}",
                )
                return

    def download_asset(self, item: PublishQueueItem, destination) -> None:
        release = self.client.request(
            "GET",
            f"{self.repo_path}/releases/tags/{item.release_tag}",
        )
        for asset in release.get("assets", []):
            if asset["name"] == item.asset_name:
                self.client.download(
                    f"{self.repo_path}/releases/assets/{asset['id']}",
                    destination,
                )
                return
        raise RuntimeError(f"Queue asset {item.asset_name} was not found")
