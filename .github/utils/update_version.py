# This script updates the app json with the new version provided by semantic release, and creates release notes for that release
# Explored but currently not using: auto-generating release notes with ${nextRelease.notes} from semantic release
import os
import json
import argparse
from pathlib import Path
import re
from datetime import datetime, timezone


def find_app_json_name(json_filenames: list[str]) -> str:
    """
    Given a list of possible json files and the app repo name, return the name of the file
    that is most likely to be the app repo's main module json
    """
    # Multiple json files. Exclude known JSON filenames and expect only one at the end regardless of name.
    # Other places (e.g. Splunkbase) enforce a single top-level JSON file anyways.
    filtered_json_filenames = []
    for fname in json_filenames:
        # Ignore the postman collection JSON files
        if "postman_collection" in fname.lower():
            continue
        filtered_json_filenames.append(fname)

    if len(filtered_json_filenames) == 0:
        print("No JSON file found in top level of app repo! Aborting tests...")
        exit(1)

    if len(filtered_json_filenames) > 1:
        print(
            f"Multiple JSON files found in top level of app repo: {filtered_json_filenames}."
            "Aborting because there should be exactly one top level JSON file."
        )
        exit(1)

    # There's only one json file in the top level, so it must be the app's json
    return filtered_json_filenames[0]


def update_app_version_in_app_json(app_json_name: str, new_version: str) -> None:
    # First, determine indent level of app json
    indent = ""
    with open(app_json_name) as f:
        for line in f:
            if "appid" in line:
                indent = re.match(r"(\s*)", line).group(0)
                break

    # Extract existing json
    with open(app_json_name) as f:
        json_content = json.loads(f.read())

    # Update values
    json_content["app_version"] = new_version
    json_content["utctime_updated"] = datetime.now(tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )

    print(f"Updating app json with new version: {new_version}")
    # Write json back to file
    with open(app_json_name, "w") as f:
        json.dump(json_content, f, indent=len(indent), sort_keys=False, separators=(",", ": "))
        f.write("\n")


def update_app_version_in_readme(readme_path: Path, new_version: str) -> None:
    with open(readme_path) as f:
        lines = f.readlines()

    # Update the version in the matching line
    with open(readme_path, "w") as f:
        for line in lines:
            # Match the line with "Connector Version: x.x.x"
            if line.startswith("Connector Version:"):
                line = re.sub(
                    r"Connector Version: \d+\.\d+\.\d+", f"Connector Version: {new_version}", line
                )
            f.write(line)


def generate_release_notes(new_version: str) -> None:
    print("Generating new release notes from unreleased.md")
    # Get release notes from unreleased.md
    with open("release_notes/unreleased.md") as f:
        release_notes = f.read()

    # Copy release notes to new version.md
    with open(f"release_notes/{new_version}.md", "w") as f:
        if not release_notes:
            print("Release notes not formatted correctly, not adding to official release notes")
            exit(1)
        versioned_release_notes = []
        for line in release_notes.splitlines():
            if line.strip() and not ("unreleased" in line.lower() and "**" in line):
                versioned_release_notes.append(line)
        if versioned_release_notes:
            for line in versioned_release_notes[:-1]:
                f.write(line + "\n")
            # Write the last line without a newline
            f.write(versioned_release_notes[-1])
        else:
            print("No valid release notes found, not adding to official release notes")
            exit(1)

    # Clear release notes from unreleased.md
    with open("release_notes/unreleased.md", "w") as f:
        f.truncate(0)
        f.write("**Unreleased**\n")


def create_cmdline_parser() -> argparse.ArgumentParser:
    """
    Commandline parser for passing in necessary arguments
    """
    argparser = argparse.ArgumentParser()
    argparser.add_argument(
        "new_version", type=str, help="The new version the app json will be updated to"
    )
    argparser.add_argument("release_notes", type=str, nargs='?', help="The release notes for the new version (optional, can also be passed via RELEASE_NOTES env var)")

    return argparser


def main(**kwargs):
    if not kwargs.get("new_version") or not re.match(r"^\d+\.\d+\.\d+$", kwargs.get("new_version")):
        print(
            f"New version provided by semantic-release is not formatted correctly: {kwargs.get('new_version')}"
        )
        exit(1)

    new_version = kwargs.get("new_version")
    
    # Get release notes from argument or environment variable (env var fixes arg passing issues)
    release_notes = kwargs.get("release_notes") or os.environ.get("RELEASE_NOTES", "")

    # Look for the app json file in the current directory
    app_json_name = find_app_json_name([f for f in os.listdir(os.getcwd()) if f.endswith(".json")])
    print(f"Found one top-level json file: {app_json_name}")

    update_app_version_in_app_json(app_json_name, new_version)

    # Look for a file named "README" in the current directory
    readme_path = os.path.join(os.getcwd(), "README.md")
    if not os.path.exists(readme_path):
        print("README.md file not found in the current directory")
        exit(1)
    update_app_version_in_readme(readme_path, new_version)

    generate_release_notes(new_version)


if __name__ == "__main__":
    parser = create_cmdline_parser()
    options = vars(parser.parse_args())
    main(**options)
