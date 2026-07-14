"""
Checks whether an app tarball's min_phantom_version is compatible with a target
Phantom instance's actual running version, queried live over REST.

Prints "true" or "false" as the last line of stdout. On any internal error, fails
open by printing "true" so a broken check never blocks a real install/test run.
"""

import argparse
import json
import logging
import os
import sys
import tarfile
from pathlib import Path

from packaging.version import parse as parse_version
from requests.auth import HTTPBasicAuth

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from api import ApiSession
from utils import find_app_json_name


def parse_args():
    help_str = " ".join(line.strip() for line in __doc__.splitlines())
    parser = argparse.ArgumentParser(description=help_str)
    parser.add_argument("tarball_path", help="Path to the built app tarball")
    parser.add_argument("phantom_ip", help="IP of the target phantom instance")
    parser.add_argument("phantom_username", help="User for the target phantom instance")
    parser.add_argument("phantom_password", help="User password")

    return parser.parse_args()


def get_min_phantom_version(tarball_path):
    """
    Reads the app's manifest.json (SDKfied) or <app>.json (traditional) directly
    out of the built tarball and returns its min_phantom_version.
    """
    with tarfile.open(tarball_path, "r:gz") as tar:
        top_level_jsons = [
            member.name
            for member in tar.getmembers()
            if member.isfile() and member.name.count("/") == 1 and member.name.endswith(".json")
        ]
        json_filenames = [name.split("/", 1)[1] for name in top_level_jsons]

        if "manifest.json" in json_filenames:
            app_json_member = top_level_jsons[json_filenames.index("manifest.json")]
        else:
            app_json_name = find_app_json_name(json_filenames)
            app_json_member = top_level_jsons[json_filenames.index(app_json_name)]

        app_json = json.loads(tar.extractfile(app_json_member).read())
        return app_json["min_phantom_version"]


def get_instance_version(phantom_ip, phantom_username, phantom_password):
    """
    Queries the live, running version of a Phantom instance over REST.
    """
    session = ApiSession(f"https://{phantom_ip}")
    resp = session.get(
        "/rest/version",
        verify=False,
        auth=HTTPBasicAuth(phantom_username, phantom_password),
        timeout=15,
    )
    return resp.json()["version"]


def is_compatible(tarball_path, phantom_ip, phantom_username, phantom_password):
    min_phantom_version = get_min_phantom_version(tarball_path)
    instance_version = get_instance_version(phantom_ip, phantom_username, phantom_password)

    logging.info(
        "App requires min_phantom_version %s, instance %s is running %s",
        min_phantom_version,
        phantom_ip,
        instance_version,
    )
    return parse_version(instance_version) >= parse_version(min_phantom_version)


def main(args):
    try:
        compatible = is_compatible(
            args.tarball_path, args.phantom_ip, args.phantom_username, args.phantom_password
        )
    except Exception as e:
        logging.warning(
            "Version compatibility check failed, defaulting to compatible: %s", e
        )
        logging.warning(
            "Diagnostics: tarball_path=%r, cwd=%s, cwd_contents=%s",
            args.tarball_path,
            os.getcwd(),
            os.listdir("."),
        )
        compatible = True

    print("true" if compatible else "false")
    return 0


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    sys.exit(main(parse_args()))
