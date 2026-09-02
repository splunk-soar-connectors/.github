# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = [
#   "backoff>=2.2.1,<3.0.0",
#   "cairosvg==2.7.0",
#   "jinja2==3.1.5",
#   "packaging>=24.2,<26.0",
#   "requests>=2.32.3,<3.0.0",
#   "slack-sdk==3.41.0",
# ]
# ///
"""Drain eligible Splunkbase publications within one bounded worker run."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import importlib.util
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace


SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.resolve()
MAX_RUN_SECONDS = 60 * 60
MAX_UPLOAD_ATTEMPTS = 20
LOG_SEPARATOR = "=" * 80
LOG_FORMAT = "{asctime} - {levelname} - {message}"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRAIN = load_module(
    "drain_splunkbase_publish_queue",
    SCRIPT_DIR / "drain_splunkbase_publish_queue.py",
)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
        style="{",
        force=True,
    )


@dataclass
class ItemOutcome:
    queue_status: str
    attempts_started: int
    failed: bool


def parse_outputs(path: Path) -> dict[str, str]:
    outputs = {}
    if not path.exists():
        return outputs
    for line in path.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            outputs[key] = value
    return outputs


def run_checked(command: list[str], *, cwd: Path | None = None, env=None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def checkout_connector(repository: str, commit_sha: str, destination: Path) -> None:
    run_checked(["git", "init", "--quiet", "--initial-branch=main", str(destination)])
    run_checked(
        [
            "git",
            "-C",
            str(destination),
            "remote",
            "add",
            "origin",
            f"https://github.com/{repository}.git",
        ]
    )
    run_checked(
        [
            "git",
            "-C",
            str(destination),
            "fetch",
            "--quiet",
            "--depth=2",
            "origin",
            commit_sha,
        ]
    )
    run_checked(["git", "-C", str(destination), "checkout", "--quiet", "--detach", "FETCH_HEAD"])


def _sdk_project_root(connector: Path) -> Path:
    uv_locks = list(connector.rglob("uv.lock"))
    if len(uv_locks) != 1:
        raise RuntimeError(f"Expected one SDK uv.lock, found {len(uv_locks)}")
    return uv_locks[0].parent


def _prepare_sdk_metric_manifests(
    outputs: dict[str, str],
    connector: Path,
    artifact: Path,
    temporary: Path,
) -> tuple[Path, Path]:
    _sdk_project_root(connector)
    current_manifest = temporary / "current-app.json"
    current_app = DRAIN.load_publisher_module().get_app_json(artifact)
    current_manifest.write_text(json.dumps(current_app))

    previous_manifest = temporary / "old-app.json"
    previous_version = outputs.get("previous_release_version")
    if not previous_version:
        previous_manifest.write_text("{}")
        return current_manifest, previous_manifest

    tag_ref = f"refs/tags/{previous_version}"
    try:
        run_checked(
            [
                "git",
                "-C",
                str(connector),
                "fetch",
                "--quiet",
                "--depth=1",
                "origin",
                f"{tag_ref}:{tag_ref}",
            ]
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Could not fetch previous release tag {previous_version}") from exc
    previous_checkout = temporary / "previous-release"
    run_checked(
        [
            "git",
            "-C",
            str(connector),
            "worktree",
            "add",
            "--quiet",
            "--detach",
            str(previous_checkout),
            tag_ref,
        ]
    )
    previous_project = _sdk_project_root(previous_checkout)
    run_checked(
        [
            "uv",
            "run",
            "--project",
            str(previous_project),
            "soarapps",
            "manifests",
            "create",
            str(previous_manifest),
            str(previous_project),
        ]
    )
    return current_manifest, previous_manifest


def send_release_metrics(
    outputs: dict[str, str], connector: Path, artifact: Path, temporary: Path
) -> None:
    manifests = [
        path
        for path in connector.glob("*.json")
        if not path.name.endswith(".postman_collection.json")
    ]
    if len(manifests) == 1:
        current_manifest = manifests[0]
        old_manifest = temporary / "old-app.json"
        previous = subprocess.run(
            ["git", "show", f"HEAD^:{current_manifest.name}"],
            cwd=connector,
            check=False,
            capture_output=True,
            text=True,
        )
        old_manifest.write_text(previous.stdout if previous.returncode == 0 else "{}")
    elif manifests:
        raise RuntimeError(f"Expected one connector manifest, found {len(manifests)}")
    else:
        current_manifest, old_manifest = _prepare_sdk_metric_manifests(
            outputs,
            connector,
            artifact,
            temporary,
        )

    run_checked(
        [
            sys.executable,
            str(REPO_ROOT / "actions" / "metrics" / "send_metrics.py"),
            str(current_manifest),
            str(old_manifest),
            "--publish-code",
            outputs["publish_return_code"],
            "-t",
            "600",
        ]
    )


def send_release_notification(outputs: dict[str, str], connector: Path) -> None:
    if os.getenv("SEND_RELEASE_MESSAGE") != "true":
        return
    env = os.environ.copy()
    env.update(
        {
            "APP_NAME": outputs["app_name"],
            "APP_LOGO": outputs["app_logo"],
            "REPO_NAME": outputs["repo_name"],
            "RELEASE_VERSION": outputs["release_version"],
            "PREVIOUS_RELEASE_VERSION": outputs["previous_release_version"],
            "RELEASE_NOTES": outputs["release_notes"],
            "NEW_APP": outputs["new_app"],
            "SUPPORT_TAG": outputs["support_tag"],
            "SPLUNK_BASE_URL": outputs["splunk_base_url"],
            "CONNECTOR_WORKSPACE": str(connector),
        }
    )
    run_checked(
        [
            sys.executable,
            str(REPO_ROOT / "actions" / "notify-slack" / "notify_slack.py"),
        ],
        env=env,
    )


def send_blocked_notification(item, outputs: dict[str, str]) -> None:
    server_url = os.getenv("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    env = os.environ.copy()
    env.update(
        {
            "QUEUE_ISSUE_URL": (
                f"{server_url}/{os.environ['QUEUE_REPOSITORY']}/issues/{item.issue_number}"
            ),
            "WORKER_RUN_URL": DRAIN.worker_run_url() or "",
            "CONNECTOR_REPOSITORY": item.repository,
            "CONNECTOR_VERSION": item.candidate_version,
            "SPLUNKBASE_REQUEST_ID": outputs.get("request_id", ""),
            "SPLUNKBASE_PACKAGE_ID": outputs.get("package_id", ""),
            "FAILURE_REASON": outputs["failure_reason"],
        }
    )
    run_checked(
        [
            sys.executable,
            str(SCRIPT_DIR / "notify_splunkbase_publish_failure.py"),
        ],
        env=env,
    )


def process_item(queue, item, wait_until) -> ItemOutcome:
    attempts_before = len(item.attempts)
    with tempfile.TemporaryDirectory(prefix="splunkbase-publish-") as directory:
        temporary = Path(directory)
        artifact = temporary / item.asset_name
        connector = temporary / "connector"
        output_path = temporary / "github-output"

        queue.download_asset(item, artifact)
        checkout_connector(item.repository, item.commit_sha, connector)

        previous_output = os.environ.get("GITHUB_OUTPUT")
        os.environ["GITHUB_OUTPUT"] = str(output_path)
        try:
            return_code = DRAIN.publish_item(
                SimpleNamespace(
                    issue_number=item.issue_number,
                    artifact=str(artifact),
                    connector_workspace=str(connector),
                    result_path=str(temporary / "publish-result.json"),
                    publisher_output=str(temporary / "publisher-output"),
                    wait_for_budget_until=wait_until,
                )
            )
        finally:
            if previous_output is None:
                os.environ.pop("GITHUB_OUTPUT", None)
            else:
                os.environ["GITHUB_OUTPUT"] = previous_output

        outputs = parse_outputs(output_path)
        refreshed = queue.get_item(item.issue_number)
        attempts_started = max(len(refreshed.attempts) - attempts_before, 0)
        queue_status = outputs.get("queue_status", "failed")
        side_effect_failed = False

        if queue_status == "published":
            try:
                send_release_metrics(outputs, connector, artifact, temporary)
            except Exception as exc:
                side_effect_failed = True
                print(
                    f"Release metrics failed for {item.repository}: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )

            try:
                send_release_notification(outputs, connector)
            except Exception as exc:
                side_effect_failed = True
                print(
                    f"Slack notification failed for {item.repository}: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
        elif queue_status == "blocked":
            try:
                send_blocked_notification(item, outputs)
            except Exception as exc:
                side_effect_failed = True
                print(
                    f"Blocked-publication notification failed for {item.repository}: "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )

        return ItemOutcome(
            queue_status=queue_status,
            attempts_started=attempts_started,
            failed=return_code != 0 or side_effect_failed,
        )


def drain_queue(_args) -> int:
    started = time.monotonic()
    wait_until = DRAIN.utc_now() + timedelta(seconds=MAX_RUN_SECONDS)
    attempts_started = 0
    items_processed = 0
    had_failures = False

    while time.monotonic() - started < MAX_RUN_SECONDS and attempts_started < MAX_UPLOAD_ATTEMPTS:
        queue = DRAIN.queue_from_environment()
        item = queue.oldest_eligible("soar-connectors-default", DRAIN.utc_now())
        if item is None:
            print("No eligible Splunkbase publications remain.")
            break

        print(
            f"\n{LOG_SEPARATOR}\n"
            f"Queue issue #{item.issue_number} | "
            f"{item.repository} v{item.candidate_version}\n"
            f"{LOG_SEPARATOR}",
            flush=True,
        )
        outcome = process_item(queue, item, wait_until)
        print(
            f"{LOG_SEPARATOR}\n"
            f"Finished {item.repository} v{item.candidate_version}: "
            f"{outcome.queue_status}\n",
            flush=True,
        )
        attempts_started += outcome.attempts_started
        items_processed += 1
        had_failures = had_failures or outcome.failed

        if outcome.queue_status in {"deferred", "rate_limited"}:
            print(f"Stopping the drain because the global queue is {outcome.queue_status}.")
            break

    print(
        f"Drain finished after {items_processed} item(s) and "
        f"{attempts_started} started upload attempt(s)."
    )
    return 1 if had_failures else 0


def main() -> int:
    configure_logging()
    return drain_queue(SimpleNamespace())


if __name__ == "__main__":
    raise SystemExit(main())
