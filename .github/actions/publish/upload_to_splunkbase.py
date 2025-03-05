"""
Uploads a version of an app to Splunkbase
"""

import argparse
import json
import logging
import os
import sys
import tarfile
from packaging.version import parse

import boto3

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
RELEASE_QUEUE_REGION = 'us-west-2'



def parse_args():
    help_str = " ".join(line.strip() for line in __doc__.splitlines())
    parser = argparse.ArgumentParser(description=help_str)
    parser.add_argument("app_repo_name", help="Name of the app's GitHub repo.")
    return parser.parse_args()


def get_release_notes(tarball, version):
    with tarfile.open(tarball, "r") as tar:
        filename = f"release_notes/{version}.md"
        names = tar.getnames()
        notes_for_version = [n for n in names if filename in n]
        if notes_for_version:
            return tar.extractfile(notes_for_version[0]).read()
    return None


def get_app_json(tarball):
    with tarfile.open(tarball, "r") as tar:
        names = tar.getnames()
        app_json_files = [n for n in names if n.endswith(".json") and n.count("/") == 1]
        app_json_name = find_app_json_name(app_json_files)
        app_json = tar.extractfile(app_json_name).read()
    return json.loads(app_json)


def get_license_info(app_json):
    if app_json["publisher"] == "Splunk":
        return (SGT_LICENSE_STRING, SGT_LICENSE_URL)

    return (APACHE2_LICENSE_STRING, APACHE2_LICENSE_URL)


def _send_release_message(repo_name, new_app, release_notes, app_json):
    sqs = boto3.resource("sqs", region_name=RELEASE_QUEUE_REGION)
    queue = sqs.Queue(RELEASE_QUEUE_URL)

    message = {
        "app_id": app_json["appid"],
        "app_name": app_json["name"],
        "app_logo": app_json["logo"],
        "repo_name": repo_name,
        "release_notes": release_notes.decode().split("\n"),
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
        logging.info("Latest released version: %s", latest_release.vstring)

        if parse(app_version) <= latest_release:
            logging.error(
                "Candidate version %s must be greater than the latest released version %s",
                app_version,
                latest_release.vstring,
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
        # package_id = sb_client.upload_app_version(
        #     sb_appid, app_repo_name, tarball, release_notes, license_string, license_url
        # )
        package_id = 123123123123
    else:
        logging.info("Could not find an app with appid: %s", appid)
        # package_id = sb_client.upload_app(
        #     app_repo_name, tarball, release_notes, license_string, license_url
        # )
        package_id = 123123123123

    logging.info("Package ID: %s", package_id)
    response = sb_client.check_upload_status(package_id)
    sb_appid = response.get("details", {}).get("id")
    if sb_appid:
        logging.info("Upload validated successfully: \n%s", json.dumps(response, indent=2))
    else:
        logging.info("Failed to validate upload: \n%s", json.dumps(response, indent=2))
        #uncomment this
        #return 1

    print(f"sending a release message with repo_name={app_repo_name}, new_app={not apps}")
    #     repo_name=app_repo_name, new_app=not apps, app_json=app_json, release_notes=release_notes
    # )

    if not apps:
        #sb_client.add_app_editor(sb_appid)
        # uncomment this
        logging.warning(NEW_APP_WARNING_MESSAGE)
        return 2

    return 0


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    sys.exit(main(parse_args()))
