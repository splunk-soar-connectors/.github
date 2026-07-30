# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = [
#   "backoff>=2.2.1,<3.0.0",
#   "packaging>=24.2,<26.0",
#   "requests>=2.32.3,<3.0.0",
# ]
# ///
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
import time

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

VERIFICATION_POLL_INTERVAL_SECONDS = 10
VERIFICATION_POLL_TIMEOUT_SECONDS = 5 * 60


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
            "SOURCE_GITHUB_RUN_ID": str(item.run_id),
            "SOURCE_GITHUB_RUN_ATTEMPT": str(item.run_attempt),
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


def finalize_publication(queue, client, item, args, result, now) -> int:
    """Reconstruct release outputs and side effects before closing a queue item."""

    publisher = load_publisher_module()
    try:
        app_json = publisher.get_app_json(args.artifact)
        release_notes = publisher.get_release_notes(
            item.candidate_version,
            Path(args.connector_workspace),
        ) or publisher.get_release_notes_from_tarball(args.artifact, item.candidate_version)
        if not release_notes:
            raise RuntimeError("Release notes could not be reconstructed")

        apps = client.get_apps({"appid": item.appid})
        if len(apps) != 1:
            raise RuntimeError(f"Expected one Splunkbase app for {item.appid}, found {len(apps)}")
        app = apps[0]
        new_app = not result.get("app_existed_before_upload", True)
        previous_release_version = result.get("previous_release_version")
        if not new_app and not previous_release_version:
            previous_release_version = publisher.get_previous_release_version(
                client.get_existing_releases(item.appid),
                item.candidate_version,
            )
        if new_app:
            client.ensure_app_editors(app["id"])

        result.update(
            {
                "status": "reconciled_published",
                "release_version": item.candidate_version,
                "splunkbase_app_id": app["id"],
            }
        )
        write_output("splunk_base_url", f"https://splunkbase.splunk.com/app/{app['id']}")
        write_output("support_tag", app["support"])
        write_output("app_name", item.app_name)
        write_output("app_logo", app_json["logo"])
        write_output("repo_name", item.repository.split("/")[-1])
        write_output("release_version", item.candidate_version)
        write_output("previous_release_version", previous_release_version or "")
        write_output("release_notes", json.dumps(release_notes.split("\n")))
        write_output("new_app", new_app)
        write_output("publish_return_code", 2 if new_app else 0)
    except Exception as exc:
        return defer_verification(
            queue,
            item,
            result,
            now,
            f"Publication is visible but finalization failed ({type(exc).__name__}); "
            "GET-only recovery will retry.",
        )

    queue.complete(item, result)
    queue.delete_asset(item)
    write_output("queue_status", "published")
    print(f"Finalized {item.repository} v{item.candidate_version} without another upload.")
    return 0


def defer_verification(queue, item, result, now, reason) -> int:
    queue.verify(
        item,
        result,
        now + timedelta(seconds=VERIFICATION_POLL_INTERVAL_SECONDS),
        reason,
    )
    write_output("queue_status", "verifying")
    print(reason)
    return 0


def worker_run_url() -> str | None:
    server_url = os.getenv("GITHUB_SERVER_URL")
    repository = os.getenv("GITHUB_REPOSITORY")
    run_id = os.getenv("GITHUB_RUN_ID")
    if not all((server_url, repository, run_id)):
        return None
    return f"{server_url.rstrip('/')}/{repository}/actions/runs/{run_id}"


def block_publication(queue, item, result, reason, return_code=1) -> int:
    """Persist a terminal failure and expose sanitized notification fields."""

    result["worker_run_url"] = worker_run_url()
    queue.block(item, result)
    write_output("publish_return_code", return_code)
    write_output("request_id", result.get("request_id", ""))
    write_output("package_id", result.get("package_id", ""))
    write_output("failure_reason", reason)
    write_output("queue_status", "blocked")
    print(reason)
    return 1


def interrupted_attempt(item) -> dict | None:
    """Return a counted attempt whose POST outcome was never durably recorded."""

    attempts = getattr(item, "attempts", [])
    if not attempts or attempts[-1].get("outcome") != "started":
        return None
    result = dict(attempts[-1])
    result["status"] = "verifying"
    return result


def check_verification(queue, client, item, args, result, now) -> int | None:
    """Perform one GET-only publication check."""

    package_id = result.get("package_id")

    try:
        if version_exists(client, item.appid, item.candidate_version):
            return finalize_publication(queue, client, item, args, result, now)
    except Exception:
        print("The release listing could not be read; checking the accepted package directly.")

    if not package_id:
        return None

    try:
        response = client.get_upload_status(package_id)
    except Exception:
        return None

    if not isinstance(response, dict):
        return None

    splunkbase_app_id = response.get("details", {}).get("id")
    if splunkbase_app_id:
        result.update(
            {
                "status": "reconciled_published",
                "splunkbase_app_id": splunkbase_app_id,
            }
        )
        return finalize_publication(queue, client, item, args, result, now)

    if client._is_retryable_response(response) or not Splunkbase.is_definitive_validation_failure(
        response
    ):
        return None

    result["status"] = "validation_failed"
    return block_publication(
        queue,
        item,
        result,
        f"Splunkbase definitively rejected accepted package {package_id}.",
        return_code=13,
    )


def reconcile_verification(queue, client, item, args, _now) -> int:
    """Poll an accepted package every ten seconds using GETs only."""

    result = dict(item.verification or {})
    deadline = time.monotonic() + VERIFICATION_POLL_TIMEOUT_SECONDS

    while True:
        outcome = check_verification(queue, client, item, args, result, utc_now())
        if outcome is not None:
            return outcome

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return defer_verification(
                queue,
                item,
                result,
                utc_now(),
                "Splunkbase publication was not confirmed within five minutes; "
                "GET-only verification will retry.",
            )
        time.sleep(min(VERIFICATION_POLL_INTERVAL_SECONDS, remaining))


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

    if item.verification:
        return reconcile_verification(queue, splunkbase, item, args, now)

    interrupted = interrupted_attempt(item)
    if interrupted:
        item.verification = interrupted
        queue.verify(
            item,
            interrupted,
            now,
            "A counted upload attempt ended without a durable result; "
            "immediate GET-only reconciliation started.",
        )
        return reconcile_verification(queue, splunkbase, item, args, now)

    if version_exists(splunkbase, item.appid, item.candidate_version):
        return finalize_publication(
            queue,
            splunkbase,
            item,
            args,
            {
                "status": "already_published",
                "app_existed_before_upload": True,
            },
            now,
        )

    try:
        app_existed_before_upload = bool(splunkbase.get_apps({"appid": item.appid}))
    except Exception:
        return block_publication(
            queue,
            item,
            {"status": "pre_upload_failed"},
            "Splunkbase app metadata could not be read before upload; no POST was attempted.",
        )

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
        PublishAttempt(
            started_at=format_datetime(now),
            outcome="started",
            app_existed_before_upload=app_existed_before_upload,
        ),
    )
    return_code, result = run_publisher(args, item)
    result["app_existed_before_upload"] = app_existed_before_upload
    status = result.get("status", "failed")
    write_output("publish_return_code", return_code)
    write_output("request_id", result.get("request_id", ""))

    if status in {"published", "new_app", "already_published"}:
        return finalize_publication(queue, splunkbase, item, args, result, now)

    if status == "verifying":
        item.verification = result
        queue.verify(
            item,
            result,
            now,
            "Splunkbase accepted the upload; immediate GET-only verification started.",
        )
        return reconcile_verification(queue, splunkbase, item, args, now)

    if status == "rate_limited":
        item.attempts[-1]["outcome"] = "rate_limited"
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
            return finalize_publication(queue, splunkbase, item, args, result, now)
        item.verification = result
        queue.verify(
            item,
            result,
            now,
            "The upload result is ambiguous; immediate GET-only reconciliation started.",
        )
        return reconcile_verification(queue, splunkbase, item, args, now)

    return block_publication(
        queue,
        item,
        result,
        f"Publisher stopped with terminal status {status}.",
        return_code=return_code,
    )


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
