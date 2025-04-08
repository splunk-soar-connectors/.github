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

import boto3

# Add utils to the import path
REPO_ROOT = Path(__file__).parent.parent.parent.resolve()
sys.path.append(str(REPO_ROOT))

from utils.update_version import find_app_json_name
from utils.api.splunkbase import (
    APACHE2_LICENSE_STRING,
    APACHE2_LICENSE_URL,
    SGT_LICENSE_STRING,
    SGT_LICENSE_URL,
    Splunkbase,
)

NEW_APP_WARNING_MESSAGE = (
    "Successfully uploaded a NEW APP to Splunkbase. "
    "Please notify the Splunkbase team. "
    "See: http://go/new-soar-app-in-splunkbase for more."
)

RELEASE_QUEUE_URL = os.getenv("RELEASE_QUEUE_URL")
SOAR_APPS_TOKEN = os.getenv("SOAR_APPS_TOKEN")
SPLUNKBASE_USER = os.getenv("SPLUNKBASE_USER")
SPLUNKBASE_PASSWORD = os.getenv("SPLUNKBASE_PASSWORD")
RELEASE_QUEUE_REGION = "us-west-2"


def parse_args() -> argparse.Namespace:
    help_str = " ".join(line.strip() for line in (__doc__ or "").splitlines())
    parser = argparse.ArgumentParser(description=help_str)
    parser.add_argument("app_repo_name", help="Name of the app's GitHub repo.")
    return parser.parse_args()


def get_release_notes(tarball: str, version: str) -> Optional[str]:
    filename = f"release_notes/{version}.md"
    with tarfile.open(tarball, "r") as tar:
        for name in tar.getnames():
            if filename in name:
                full_release_notes = tar.extractfile(name).read().decode()
                release_notes = []
                for line in full_release_notes.splitlines():
                    if "unreleased" in line.lower() and "**" in line:
                        pass
                    else:
                        release_notes.append(line)
                return "\n".join(release_notes)

    return None


def get_app_json(tarball: Union[str, Path]) -> dict[str, Any]:
    with tarfile.open(tarball, "r") as tar:
        names = tar.getnames()
        app_json_files = [n for n in names if n.endswith(".json") and n.count("/") == 1]
        app_json_name = find_app_json_name(app_json_files)
        app_json = tar.extractfile(app_json_name).read()
    return json.loads(app_json)


def get_license_info(app_json: dict[str, Any]) -> tuple[str, str]:
    if app_json["publisher"] == "Splunk":
        return (SGT_LICENSE_STRING, SGT_LICENSE_URL)

    return (APACHE2_LICENSE_STRING, APACHE2_LICENSE_URL)


def _send_release_message(
    repo_name: str, new_app: bool, release_notes: str, app_json: dict[str, Any]
) -> None:
    sqs = boto3.resource("sqs", region_name=RELEASE_QUEUE_REGION)
    queue = sqs.Queue(RELEASE_QUEUE_URL)

    message = {
        "app_id": app_json["appid"],
        "app_name": app_json["name"],
        "app_logo": app_json["logo"],
        "repo_name": repo_name,
        "release_notes": release_notes,
        "release_version": app_json["app_version"],
        "new_app": new_app,
    }

    queue.send_message(MessageBody=json.dumps(message))


def main(args):
    app_repo_name = args.app_repo_name

    tarball = os.getenv("UPLOAD_PATH")
    logging.info("Downloaded tarball to %s", tarball)
    app_json = get_app_json(tarball)
    app_version = app_json["app_version"]
    appid = app_json["appid"]

    logging.info("Candidate version for release: %s", app_version)
    sb_client = Splunkbase(SPLUNKBASE_USER, SPLUNKBASE_PASSWORD)

    existing_releases = sb_client.get_existing_releases(appid)
    if existing_releases:
        latest_release = max(parse(r["release_name"]) for r in existing_releases)
        logging.info("Latest released version: %s", latest_release.public)

        if parse(app_version) <= latest_release:
            logging.error(
                "Candidate version %s must be greater than the latest released version %s",
                app_version,
                latest_release.public,
            )
            return 1
    else:
        logging.info("Version %s will be the first release", app_version)

    release_notes = get_release_notes(tarball, app_version)
    if not release_notes:
        logging.error("Could not find release notes in tarball for version %s!", app_version)
        return 1

    logging.info("Found release notes for version %s: %s", app_version, release_notes)

    license_string, license_url = get_license_info(app_json)
    logging.info("Using license info: %s: %s", license_string, license_url)

    apps = sb_client.get_apps({"appid": appid})
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
    response = sb_client.check_upload_status(package_id)
    sb_appid = response.get("details", {}).get("id")
    if sb_appid:
        logging.info("Upload validated successfully: \n%s", json.dumps(response, indent=2))
    else:
        logging.info("Failed to validate upload: \n%s", json.dumps(response, indent=2))
        return 1

    print(
        f"sending a release message with repo_name={app_repo_name}, new_app={not apps}, release_notes={release_notes}"
    )
    _send_release_message(
        repo_name=app_repo_name, new_app=not apps, app_json=app_json, release_notes=release_notes
    )

    if not apps:
        sb_client.add_app_editor(sb_appid)
        logging.warning(NEW_APP_WARNING_MESSAGE)
        return 2

    return 0


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    sys.exit(main(parse_args()))
