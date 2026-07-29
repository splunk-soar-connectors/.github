"""
Uploads a version of an app to Splunkbase
"""

import argparse
import json
import logging
import os
from pathlib import Path
import sys
import tarfile
from packaging.version import parse
from typing import Any, Optional, Union

# Add utils to the import path
REPO_ROOT = Path(__file__).parent.parent.parent.resolve()
sys.path.append(str(REPO_ROOT))

from utils.api.splunkbase import (
    APACHE2_LICENSE_STRING,
    APACHE2_LICENSE_URL,
    SGT_LICENSE_STRING,
    SGT_LICENSE_URL,
    Splunkbase,
    SplunkbaseAmbiguousUpload,
    SplunkbasePermissionDenied,
    SplunkbaseRateLimited,
    SplunkbaseUploadError,
    SplunkbaseValidationFailed,
)

NEW_APP_WARNING_MESSAGE = (
    "Successfully uploaded a NEW APP to Splunkbase. "
    "Please notify the Splunkbase team. "
    "See: http://go/new-soar-app-in-splunkbase for more."
)

SPLUNKBASE_USER = os.getenv("SPLUNKBASE_USER")
SPLUNKBASE_PASSWORD = os.getenv("SPLUNKBASE_PASSWORD")
PUBLISH_RESULT_PATH = os.getenv("PUBLISH_RESULT_PATH")

RESULT_CODES = {
    "published": 0,
    "already_published": 0,
    "failed": 1,
    "new_app": 2,
    "rate_limited": 10,
    "permission_denied": 11,
    "ambiguous": 12,
    "validation_failed": 13,
    "verifying": 14,
}


def is_successful_rerun_of_existing_version(candidate_version, latest_release, run_attempt=None):
    attempt = int(run_attempt or os.getenv("GITHUB_RUN_ATTEMPT", "1"))
    return candidate_version == latest_release and attempt > 1


def parse_args() -> argparse.Namespace:
    help_str = " ".join(line.strip() for line in (__doc__ or "").splitlines())
    parser = argparse.ArgumentParser(description=help_str)
    parser.add_argument("app_repo_name", help="Name of the app's GitHub repo.")
    return parser.parse_args()


def get_release_notes(version: str, workspace_path: Optional[Path] = None) -> Optional[str]:
    # Use GITHUB_WORKSPACE if provided, otherwise fallback to cwd
    search_root = workspace_path or Path.cwd()

    # Debug: Show where we're searching
    logging.info(f"Searching for release_notes from: {search_root}")
    if search_root.exists():
        logging.info(f"Contents: {list(search_root.iterdir())}")
    else:
        logging.error(f"Search root does not exist: {search_root}")
        return None

    release_notes_file = search_root / "release_notes" / f"{version}.md"
    logging.info(f"Looking for release notes at: {release_notes_file}")

    if not release_notes_file.exists():
        return None

    with open(release_notes_file) as f:
        full_release_notes = f.read()
        release_notes = []
        for line in full_release_notes.splitlines():
            if not ("unreleased" in line.lower() and "**" in line):
                release_notes.append(line)
        return "\n".join(release_notes)


def get_release_notes_from_tarball(tarball: Union[str, Path], version: str) -> Optional[str]:
    with tarfile.open(tarball, "r") as tar:
        expected_suffix = f"/release_notes/{version}.md"
        matches = [
            name
            for name in tar.getnames()
            if name == f"release_notes/{version}.md" or name.endswith(expected_suffix)
        ]
        if len(matches) != 1:
            logging.error("Expected one release note file in tarball, found: %s", matches)
            return None

        release_file = tar.extractfile(matches[0])
        if release_file is None:
            return None
        full_release_notes = release_file.read().decode()
        return "\n".join(
            line
            for line in full_release_notes.splitlines()
            if not ("unreleased" in line.lower() and "**" in line)
        )


def get_app_json(tarball: Union[str, Path]) -> dict[str, Any]:
    with tarfile.open(tarball, "r") as tar:
        names = tar.getnames()

        app_json_files = [
            n
            for n in names
            if n.endswith(".json") and n.count("/") == 1 and "postman_collection" not in n.lower()
        ]
        if len(app_json_files) == 0 or len(app_json_files) > 1:
            raise ValueError(
                f"No or multiple JSON files found in top level of app repo: {app_json_files}."
            )
        app_json_name = app_json_files[0]
        app_json = tar.extractfile(app_json_name).read()
        return json.loads(app_json)


def get_license_info(app_json: dict[str, Any]) -> tuple[str, str]:
    if app_json["publisher"] == "Splunk":
        return (SGT_LICENSE_STRING, SGT_LICENSE_URL)

    return (APACHE2_LICENSE_STRING, APACHE2_LICENSE_URL)


def _write_github_outputs(
    app_json: dict[str, Any],
    repo_name: str,
    release_notes: str,
    new_app: bool,
    sb_appid: str,
    support_tag: str,
) -> None:
    github_output = os.getenv("GITHUB_OUTPUT")
    if not github_output:
        logging.info("GITHUB_OUTPUT not set, skipping output writing")
        return

    splunk_base_url = f"https://splunkbase.splunk.com/app/{sb_appid}"
    release_notes_json = json.dumps(release_notes.split("\n"))

    with open(github_output, "a") as f:
        f.write(f"app_name={app_json['name']}\n")
        f.write(f"app_logo={app_json['logo']}\n")
        f.write(f"repo_name={repo_name}\n")
        f.write(f"release_version={app_json['app_version']}\n")
        f.write(f"new_app={'true' if new_app else 'false'}\n")
        f.write(f"support_tag={support_tag}\n")
        f.write(f"splunk_base_url={splunk_base_url}\n")
        f.write(f"release_notes={release_notes_json}\n")

    logging.info("Wrote GitHub outputs for %s v%s", app_json["name"], app_json["app_version"])


def _write_publish_result(status: str, **details: Any) -> None:
    if not PUBLISH_RESULT_PATH:
        return

    result = {
        "status": status,
        **{key: value for key, value in details.items() if value is not None},
    }
    Path(PUBLISH_RESULT_PATH).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


def _existing_publish_result() -> dict[str, Any]:
    if not PUBLISH_RESULT_PATH:
        return {}
    try:
        return json.loads(Path(PUBLISH_RESULT_PATH).read_text())
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return {}


def _check_post_upload_validation(sb_client: Splunkbase, package_id: str) -> tuple[str, dict]:
    """Classify one GET-only validation check without risking another upload."""

    try:
        response = sb_client.check_upload_status(package_id)
    except Exception:
        logging.exception(
            "Could not confirm package %s after Splunkbase accepted the upload; "
            "leaving it in verification",
            package_id,
        )
        return "verifying", {}

    if not isinstance(response, dict):
        logging.error("Package %s returned a malformed validation response", package_id)
        return "verifying", {}
    if response.get("details", {}).get("id"):
        return "published", response
    if sb_client._is_retryable_response(response):
        logging.info("Package %s is still being validated: %s", package_id, response)
        return "verifying", response
    if Splunkbase.is_definitive_validation_failure(response):
        return "validation_failed", response
    logging.error(
        "Package %s returned an inconclusive validation response: %s", package_id, response
    )
    return "verifying", response


def main(args):
    app_repo_name = args.app_repo_name

    tarball = os.getenv("UPLOAD_PATH")
    logging.info("Downloaded tarball to %s", tarball)
    app_json = get_app_json(tarball)
    app_version = app_json["app_version"]
    appid = app_json["appid"]

    logging.info("Candidate version for release: %s", app_version)
    sb_client = Splunkbase(
        SPLUNKBASE_USER,
        SPLUNKBASE_PASSWORD,
        request_context={
            "repo": app_repo_name,
            "version": app_version,
            "run_id": os.getenv("SOURCE_GITHUB_RUN_ID"),
            "run_attempt": os.getenv("SOURCE_GITHUB_RUN_ATTEMPT"),
        },
    )

    existing_releases = sb_client.get_existing_releases(appid)
    if existing_releases:
        latest_release = max(parse(r["release_name"]) for r in existing_releases)
        logging.info("Latest released version: %s", latest_release.public)

        candidate_version = parse(app_version)
        if is_successful_rerun_of_existing_version(candidate_version, latest_release):
            logging.info(
                "Version %s is already present on Splunkbase; treating this rerun as successful",
                app_version,
            )
            apps = sb_client.get_apps({"appid": appid})
            if not apps:
                logging.error(
                    "Could not find Splunkbase app metadata for existing version %s", app_version
                )
                return 1

            release_notes = get_release_notes(
                app_version,
                Path(os.environ["GITHUB_WORKSPACE"]) if os.getenv("GITHUB_WORKSPACE") else None,
            )
            if not release_notes:
                logging.error("Could not find release notes for existing version %s", app_version)
                return 1

            _write_github_outputs(
                app_json,
                app_repo_name,
                release_notes,
                new_app=False,
                sb_appid=apps[0]["id"],
                support_tag=apps[0]["support"],
            )
            _write_publish_result(
                "already_published",
                appid=appid,
                app_name=app_json["name"],
                release_version=app_version,
                splunkbase_app_id=apps[0]["id"],
            )
            return 0

        if candidate_version <= latest_release:
            logging.error(
                "Candidate version %s must be greater than the latest released version %s",
                app_version,
                latest_release.public,
            )
            return 1
    else:
        logging.info("Version %s will be the first release", app_version)

    # Get GITHUB_WORKSPACE from environment to find release_notes
    workspace = os.getenv("GITHUB_WORKSPACE")
    workspace_path = Path(workspace) if workspace else None
    logging.info(f"GITHUB_WORKSPACE from environment: {workspace}")

    release_notes = get_release_notes(app_version, workspace_path)
    if not release_notes:
        release_notes = get_release_notes_from_tarball(tarball, app_version)
    if not release_notes:
        logging.error("Could not find release notes in tarball for version %s!", app_version)
        return 1

    logging.info("Found release notes for version %s: %s", app_version, release_notes)

    license_string, license_url = get_license_info(app_json)
    logging.info("Using license info: %s: %s", license_string, license_url)

    apps = sb_client.get_apps({"appid": appid})
    publish_details = {
        "appid": appid,
        "app_existed_before_upload": bool(apps),
        "app_name": app_json["name"],
        "release_version": app_version,
    }
    _write_publish_result("uploading", **publish_details)
    if apps:
        sb_appid = apps[0]["id"]
        logging.info("Found existing app with appid: %s: %s", appid, sb_appid)
        package_id = sb_client.upload_app_version(
            sb_appid, app_repo_name, tarball, release_notes, license_string, license_url
        )
    else:
        logging.info("Could not find an app with appid: %s", appid)
        package_id = sb_client.upload_app(
            app_repo_name, tarball, release_notes, license_string, license_url
        )

    logging.info("Package ID: %s", package_id)
    request_id = getattr(sb_client, "last_upload_request_id", None)
    publish_details.update({"package_id": package_id, "request_id": request_id})
    _write_publish_result("verifying", **publish_details)
    if PUBLISH_RESULT_PATH:
        return RESULT_CODES["verifying"]

    validation_status, response = _check_post_upload_validation(sb_client, package_id)
    if validation_status == "verifying":
        return RESULT_CODES["verifying"]
    if validation_status == "validation_failed":
        logging.error("Splunkbase definitively rejected package %s: %s", package_id, response)
        _write_publish_result("validation_failed", **publish_details)
        return RESULT_CODES["validation_failed"]

    sb_appid = response.get("details", {}).get("id")
    logging.info("Upload validated successfully: \n%s", json.dumps(response, indent=2))

    if not apps:
        support_tag = "splunk" if app_json.get("publisher") == "Splunk" else "developer"
        _write_github_outputs(
            app_json,
            app_repo_name,
            release_notes,
            new_app=True,
            sb_appid=sb_appid,
            support_tag=support_tag,
        )
        _write_publish_result(
            "new_app",
            **publish_details,
            splunkbase_app_id=sb_appid,
        )
        sb_client.ensure_app_editors(sb_appid)
        logging.warning(NEW_APP_WARNING_MESSAGE)
        return 2

    _write_github_outputs(
        app_json,
        app_repo_name,
        release_notes,
        new_app=False,
        sb_appid=sb_appid,
        support_tag=apps[0]["support"],
    )
    _write_publish_result(
        "published",
        **publish_details,
        splunkbase_app_id=sb_appid,
    )
    return 0


def _record_upload_error(status: str, exc: SplunkbaseUploadError) -> int:
    logging.error("%s: %s", status, exc)
    context = {
        key: value
        for key, value in _existing_publish_result().items()
        if key not in {"status", "message", "request_id", "retry_after", "status_code"}
    }
    _write_publish_result(
        status,
        **context,
        message=str(exc),
        request_id=exc.request_id,
        retry_after=exc.retry_after,
        status_code=exc.status_code,
    )
    return RESULT_CODES[status]


def cli() -> int:
    try:
        return main(parse_args())
    except SplunkbaseRateLimited as exc:
        return _record_upload_error("rate_limited", exc)
    except SplunkbasePermissionDenied as exc:
        return _record_upload_error("permission_denied", exc)
    except SplunkbaseAmbiguousUpload as exc:
        return _record_upload_error("ambiguous", exc)
    except SplunkbaseValidationFailed as exc:
        return _record_upload_error("validation_failed", exc)
    except Exception:
        logging.exception("Unexpected Splunkbase publisher failure")
        if _existing_publish_result().get("status") == "verifying":
            logging.error("The upload was already accepted; preserving GET-only verification state")
            return RESULT_CODES["verifying"]
        _write_publish_result("failed")
        return RESULT_CODES["failed"]


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    sys.exit(cli())
