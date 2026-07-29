"""Select and publish one item from the shared-user Splunkbase queue."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from packaging.version import parse


REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO_ROOT))

from utils.api.splunkbase import Splunkbase
from utils.publish_queue import (
    MAX_ATTEMPTS_PER_HOUR,
    MIN_ATTEMPT_INTERVAL,
    GitHubClient,
    GitHubIssuePublishQueue,
    PublishAttempt,
    format_datetime,
    parse_datetime,
    utc_now,
)


def write_output(name: str, value: str | int | bool) -> None:
    output_path = os.getenv("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a") as output:
        output.write(f"{name}={str(value).lower() if isinstance(value, bool) else value}\n")


def queue_from_environment() -> GitHubIssuePublishQueue:
    return GitHubIssuePublishQueue(
        GitHubClient(os.environ["GITHUB_TOKEN"]),
        os.environ["QUEUE_REPOSITORY"],
    )


def select_item(args) -> int:
    item = queue_from_environment().oldest_eligible(args.publisher_alias, utc_now())
    write_output("has_item", item is not None)
    if item is None:
        print("No eligible Splunkbase publications are queued.")
        return 0

    write_output("issue_number", item.issue_number)
    write_output("repository", item.repository)
    write_output("repository_name", item.repository.split("/")[-1])
    write_output("commit_sha", item.commit_sha)
    write_output("asset_name", item.asset_name)
    write_output("release_tag", item.release_tag)
    write_output("candidate_version", item.candidate_version)
    print(f"Selected queue issue #{item.issue_number}: {item.repository} v{item.candidate_version}")
    return 0


def download_item(args) -> int:
    queue = queue_from_environment()
    item = queue.get_item(args.issue_number)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    queue.download_asset(item, destination)
    print(f"Downloaded {item.asset_name} to {destination}.")
    return 0


def retry_after_datetime(value: str | None, now: datetime) -> datetime:
    if not value:
        return now + MIN_ATTEMPT_INTERVAL
    try:
        return now + timedelta(seconds=max(int(value), 0))
    except ValueError:
        try:
            return parsedate_to_datetime(value).astimezone(timezone.utc)
        except (TypeError, ValueError):
            return now + MIN_ATTEMPT_INTERVAL


def version_exists(client: Splunkbase, appid: str, version: str) -> bool:
    return any(
        parse(str(release["release_name"])) == parse(version)
        for release in client.get_existing_releases(appid)
    )


def append_outputs(source: Path) -> None:
    target = os.getenv("GITHUB_OUTPUT")
    if target and source.exists():
        with open(target, "a") as output:
            output.write(source.read_text())


def load_publisher_module():
    module_path = REPO_ROOT / "actions" / "publish" / "upload_to_splunkbase.py"
    spec = importlib.util.spec_from_file_location("queued_upload_to_splunkbase", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_publisher(args, item) -> tuple[int, dict]:
    result_path = Path(args.result_path)
    publisher_output = Path(args.publisher_output)
    env = os.environ.copy()
    env.update(
        {
            "UPLOAD_PATH": str(Path(args.artifact).resolve()),
            "PUBLISH_RESULT_PATH": str(result_path.resolve()),
            "GITHUB_OUTPUT": str(publisher_output.resolve()),
            "GITHUB_WORKSPACE": str(Path(args.connector_workspace).resolve()),
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "actions" / "publish" / "upload_to_splunkbase.py"),
            item.repository.split("/")[-1],
        ],
        cwd=args.connector_workspace,
        env=env,
        check=False,
    )
    result = json.loads(result_path.read_text()) if result_path.exists() else {"status": "failed"}
    append_outputs(publisher_output)
    return completed.returncode, result


def complete_existing(queue, client, item, args) -> int:
    publisher = load_publisher_module()
    app_json = publisher.get_app_json(args.artifact)
    release_notes = publisher.get_release_notes(
        item.candidate_version,
        Path(args.connector_workspace),
    ) or publisher.get_release_notes_from_tarball(args.artifact, item.candidate_version)
    apps = client.get_apps({"appid": item.appid})
    result = {
        "status": "already_published",
        "release_version": item.candidate_version,
    }
    if apps:
        result["splunkbase_app_id"] = apps[0]["id"]
        write_output("splunk_base_url", f"https://splunkbase.splunk.com/app/{apps[0]['id']}")
        write_output("support_tag", apps[0]["support"])
    write_output("app_name", item.app_name)
    write_output("app_logo", app_json["logo"])
    write_output("repo_name", item.repository.split("/")[-1])
    write_output("release_version", item.candidate_version)
    write_output("release_notes", json.dumps((release_notes or "").split("\n")))
    write_output("new_app", False)
    write_output("publish_return_code", 0)
    write_output("queue_status", "published")
    queue.complete(item, result)
    queue.delete_asset(item)
    print(
        f"{item.repository} v{item.candidate_version} already exists; "
        "no upload attempt was consumed."
    )
    return 0


def publish_item(args) -> int:
    queue = queue_from_environment()
    item = queue.get_item(args.issue_number)
    now = utc_now()
    splunkbase = Splunkbase(
        os.environ["SPLUNKBASE_USER"],
        os.environ["SPLUNKBASE_PASSWORD"],
        request_context={
            "repo": item.repository.split("/")[-1],
            "version": item.candidate_version,
            "run_id": item.run_id,
            "run_attempt": item.run_attempt,
        },
    )

    if version_exists(splunkbase, item.appid, item.candidate_version):
        return complete_existing(queue, splunkbase, item, args)

    retry_at = queue.reserve_attempt(item.publisher_alias, item, now)
    if retry_at:
        queue.requeue(
            item,
            retry_at,
            f"Per-user upload budget is unavailable until {format_datetime(retry_at)}.",
        )
        write_output("queue_status", "deferred")
        print(f"Upload budget unavailable until {format_datetime(retry_at)}.")
        return 0

    queue.activate(item, now + timedelta(minutes=15))
    queue.record_attempt(
        item,
        PublishAttempt(started_at=format_datetime(now), outcome="started"),
    )
    return_code, result = run_publisher(args, item)
    status = result.get("status", "failed")
    write_output("publish_return_code", return_code)
    write_output("request_id", result.get("request_id", ""))

    if status in {"published", "new_app", "already_published"}:
        queue.complete(item, result)
        queue.delete_asset(item)
        write_output("queue_status", "published")
        return 0

    if status == "rate_limited":
        not_before = max(
            now + MIN_ATTEMPT_INTERVAL,
            retry_after_datetime(result.get("retry_after"), now) + timedelta(seconds=30),
        )
        queue.requeue(
            item,
            not_before,
            "Splunkbase returned HTTP 429; no automatic POST retry was made.",
            rate_limited=True,
        )
        write_output("queue_status", "rate_limited")
        return 0

    if status == "ambiguous":
        if version_exists(splunkbase, item.appid, item.candidate_version):
            result["status"] = "reconciled_published"
            queue.complete(item, result)
            queue.delete_asset(item)
            write_output("queue_status", "published")
            return 0
        queue.requeue(
            item,
            now + MIN_ATTEMPT_INTERVAL,
            "Ambiguous transport result did not reconcile; retry deferred to a future slot.",
        )
        write_output("queue_status", "ambiguous")
        return 0

    queue.block(item, result)
    write_output("queue_status", "blocked")
    return 1


def simulate(args) -> int:
    raw_items = json.loads(Path(args.queue_file).read_text())
    slot = parse_datetime(args.now)
    attempts = []
    seen = set()
    for item in raw_items:
        dedupe_key = (
            item.get("publisher_alias", args.publisher_alias),
            item["repository"],
            item["candidate_version"],
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        cutoff = slot - timedelta(hours=1)
        attempts = [attempt for attempt in attempts if attempt > cutoff]
        if attempts:
            slot = max(slot, attempts[-1] + MIN_ATTEMPT_INTERVAL)
        if len(attempts) >= MAX_ATTEMPTS_PER_HOUR:
            slot = max(slot, attempts[0] + timedelta(hours=1))
            cutoff = slot - timedelta(hours=1)
            attempts = [attempt for attempt in attempts if attempt > cutoff]

        print(f"{item['repository']} v{item['candidate_version']} {format_datetime(slot)}")
        attempts.append(slot)
    return 0


def parse_args():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    select = subparsers.add_parser("select")
    select.add_argument("--publisher-alias", default="soar-connectors-default")
    select.set_defaults(func=select_item)

    publish = subparsers.add_parser("publish")
    publish.add_argument("--issue-number", type=int, required=True)
    publish.add_argument("--artifact", required=True)
    publish.add_argument("--connector-workspace", required=True)
    publish.add_argument("--result-path", required=True)
    publish.add_argument("--publisher-output", required=True)
    publish.set_defaults(func=publish_item)

    download = subparsers.add_parser("download")
    download.add_argument("--issue-number", type=int, required=True)
    download.add_argument("--output", required=True)
    download.set_defaults(func=download_item)

    simulation = subparsers.add_parser("simulate")
    simulation.add_argument("queue_file")
    simulation.add_argument("--now", required=True)
    simulation.add_argument("--publisher-alias", default="soar-connectors-default")
    simulation.set_defaults(func=simulate)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
