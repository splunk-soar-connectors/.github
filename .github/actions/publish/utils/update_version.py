# This script should find the instances where we store the app version in code and update them (app.json, README.md, etc...)
import os
import json
from collections import OrderedDict
from datetime import datetime, timezone
import argparse
import re

def find_app_json_name(json_filenames):
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

def create_cmdline_parser():
    """
    Commandline parser for passing in necessary arguments
    """
    argparser = argparse.ArgumentParser()
    argparser.add_argument(
        "new_version", type=str, help="The new version the app json will be updated to"
    )
    argparser.add_argument(
        "release_notes", type=str, help="The release notes for the new version"
    )

    return argparser

def main(**kwargs):

    if not kwargs.get("new_version") or not re.match(r'^\d+\.\d+\.\d+$', kwargs.get("new_version")):
        print(f'New version provided by semantic-release is not formatted correctly: {kwargs.get("new_version")}')
        exit(1)

    new_version = kwargs.get("new_version")

    if not kwargs.get("release_notes"):
        print("Release notes not generated")
        exit(1)

    release_notes = kwargs.get("release_notes")

    main_app_json_name = find_app_json_name([f for f in os.listdir(os.getcwd()) if f.endswith('.json')])
    print(f"Found one top-level json file: {main_app_json_name}")

    # First, determine indent level of app json
    indent = ""
    with open(main_app_json_name) as f:
        for line in f:
            if "appid" in line:
                indent = re.match(r"(\s*)", line).group(0)
                break

    with open(main_app_json_name, 'r') as f:
        json_content = json.loads(f.read(), object_pairs_hook=OrderedDict)
    json_content["app_version"] = new_version
    # Update the last update time
    json_content["utctime_updated"] = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with open(main_app_json_name, 'w') as f:
        json.dump(json_content, f, indent=len(indent), sort_keys=False, separators=(",", ": "))
        f.write('\n')

    with open(f"release_notes/{new_version}.md", 'w') as f:
        f.write(release_notes)


if __name__ == "__main__":
    parser = create_cmdline_parser()
    options = vars(parser.parse_args())
    main(**options)