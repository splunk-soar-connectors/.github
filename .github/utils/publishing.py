"""Package metadata shared by queued publishing, finalization, and metrics."""

import json
import logging
from pathlib import Path
import tarfile
from typing import Any

from packaging.version import parse


APACHE2_LICENSE_STRING = "Apache License Version 2.0"
APACHE2_LICENSE_URL = "https://www.apache.org/licenses/LICENSE-2.0.html"
SGT_LICENSE_STRING = "Splunk General Terms"
SGT_LICENSE_URL = "https://www.splunk.com/en_us/legal/splunk-general-terms.html"


def get_previous_release_version(existing_releases, candidate_version):
    candidate = parse(candidate_version)
    previous_versions = [
        release["release_name"]
        for release in existing_releases
        if parse(release["release_name"]) < candidate
    ]
    return max(previous_versions, key=parse) if previous_versions else None


def get_release_notes(version: str, workspace_path: Path | None = None) -> str | None:
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


def get_release_notes_from_tarball(tarball: str | Path, version: str) -> str | None:
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


def get_app_json(tarball: str | Path) -> dict[str, Any]:
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
