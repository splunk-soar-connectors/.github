"""
Generates a NOTICE file for an app using the results of a given WhiteSource scan.
The NOTICE file lists the copyrights and licenses of the third-party libraries used by the app.
"""

import argparse
import json
import logging
import os
from pathlib import Path
import sys
import tarfile
from collections import namedtuple
from typing import Any, Union

from requests_toolbelt import sessions

from utils.update_version import find_app_json_name
from phantom_constants import (
    WHITE_SOURCE_API_URL,
    RESOLVED_MODULE_LICENSES_FILEPATH,
    WS_USER_KEY,
    WS_PRODUCT_ID,
    ATTRIBUTIONS_FOR_MODIFIED_LIBS,
    DIR,
)

with open(RESOLVED_MODULE_LICENSES_FILEPATH) as f:
    raw_data, cleaned_data = json.load(f), {}
    for lib, data in raw_data.items():
        cleaned_data[lib.replace("-", "_")] = data
    RESOLVED_MODULE_LICENSES = cleaned_data

ThirdPartyAttribution = namedtuple(
    "ThirdPartyAttribution", ["name", "version", "licenses", "copyrights"]
)


def parse_args():
    help_str = " ".join(line.strip() for line in __doc__.strip().splitlines())
    parser = argparse.ArgumentParser(description=help_str)
    parser.add_argument(
        "project_name",
        help='Name of the WhiteSource scan (eg, "awslambda:next - latest") to use'
        "to generate the NOTICE file",
    )
    parser.add_argument(
        "app_dir",
        help="Local app directory containing the app's JSON file and to write "
        "the NOTICE file under",
    )
    return parser.parse_args()

def get_app_json(tarball: Union[str, Path]) -> dict[str, Any]:
    with tarfile.open(tarball, "r") as tar:
        names = tar.getnames()
        app_json_files = [n for n in names if n.endswith(".json") and n.count("/") == 1]
        app_json_name = find_app_json_name(app_json_files)
        app_json = tar.extractfile(app_json_name).read()
    return json.loads(app_json)


class WhiteSourceApi:
    def __init__(self, user_key, product_id):
        if not user_key or not product_id:
            err_msg = "Provided user_key and product_id are invalid"
            logging.error(err_msg)
            raise ValueError(err_msg)

        self._user_key = user_key
        self._product_id = product_id

        self._session = sessions.BaseUrlSession(base_url=WHITE_SOURCE_API_URL)
        self._session.hooks["response"] = [lambda resp, *args, **kwargs: resp.raise_for_status()]

    def request(self, request_type, project_token=None, **kwargs):
        req_body = {"requestType": request_type, "userKey": self._user_key, **kwargs}
        if project_token:
            req_body["projectToken"] = project_token
        else:
            req_body["productToken"] = self._product_id

        return self._session.post("", json=req_body).json()


def generate_notice_file(app_dir, ws_api, project_token):
    repo = os.path.basename(app_dir)
    logging.info("Writing NOTICE file for %s", repo)

    # App name to be included at the top of the notice file
    app_json = get_app_json(app_dir)
    app_name = app_json["name"]
    app_copyright = app_json.get("copyright") or app_json.get("license")

    if not app_copyright:
        logging.error(
            'Could not find the copyright for the app from neither the "copyright" or "license" key'
        )
        return -1

    # Attributions to write
    attributions = []

    # Fetch libraries for the given project
    unknown_licenses, unknown_copyrights = set(), set()
    for lib in ws_api.request("getProjectLicenses", project_token)["libraries"]:
        if not lib["directDependency"]:
            continue
        manually_resolved_lib_data = RESOLVED_MODULE_LICENSES.get(
            "{}:{}".format(lib["name"].replace("-", "_"), lib["version"])
        )

        licenses = []
        if manually_resolved_lib_data and manually_resolved_lib_data["licenses"]:
            licenses.extend(sorted(manually_resolved_lib_data["licenses"]))
        elif lib["licenses"]:
            for lc in sorted(lib["licenses"], key=lambda lc: lc["name"]):
                # https://saas.whitesourcesoftware.com/Wss/license/Unspecified_License.txt"
                if lc["name"] == "Unspecified License":
                    logging.warning("Publisher did not specify a license for %s", lib["name"])
                licenses.append(lc["name"])
        else:
            unknown_licenses.add(lib["name"])

        copyrights = []
        if manually_resolved_lib_data and manually_resolved_lib_data["copyrights"]:
            copyrights.extend(sorted(manually_resolved_lib_data["copyrights"]))
        elif lib["copyrightReferences"]:
            for cr in sorted(lib["copyrightReferences"], key=lambda cr: cr["copyright"]):
                copyrights.append(cr["copyright"])
        else:
            unknown_copyrights.add(lib["name"])

        attributions.append(
            ThirdPartyAttribution(
                name=lib["name"], version=lib["version"], licenses=licenses, copyrights=copyrights
            )
        )

    if unknown_licenses or unknown_copyrights:
        missing_both = sorted(unknown_licenses & unknown_copyrights)
        if missing_both:
            logging.error("Could not resolve a license and copyright for the following libraries:")
            for lib in missing_both:
                logging.error(lib)

        missing_license = sorted(unknown_licenses - unknown_copyrights)
        if missing_license:
            logging.error("Could not resolve a license for the following libraries:")
            for lib in missing_license:
                logging.error(lib)

        missing_copyright = sorted(unknown_copyrights - unknown_licenses)
        if missing_copyright:
            logging.error("Could not resolve a copyright for the following libraries:")
            for lib in missing_copyright:
                logging.error(lib)

        logging.error(
            "Please manually resolve the licenses and/or copyrights for these libraries and "
            "add them to %s before re-running this script.",
            os.path.relpath(RESOLVED_MODULE_LICENSES_FILEPATH, DIR),
        )
        return 1

    # Collect any attributions for modified third party libraries
    with open(ATTRIBUTIONS_FOR_MODIFIED_LIBS) as fp:
        modified_libraries_json = json.load(fp)

    for lib in modified_libraries_json.get(app_name, []):
        attributions.append(
            ThirdPartyAttribution(
                name=lib["name"],
                version=lib["version"],
                licenses=lib["licenses"],
                copyrights=lib["copyrights"],
            )
        )

    notice_fp = os.path.join(app_dir, "NOTICE")
    with open(notice_fp, "w") as notice:
        notice.write(f"Splunk SOAR {app_name}\n")
        notice.write(f"{app_copyright}\n\n")

        # For each library, append an entry into the notice file containing the
        #  - library name
        #  - library version
        #  - licenses
        #  - copyright
        notice.write("Third-party Software Attributions:\n")
        for attr in sorted(attributions):
            notice.write("\n")
            notice.write(f"Library: {attr.name}\n")
            notice.write(f"Version: {attr.version}\n")
            for lc in attr.licenses:
                notice.write(f"License: {lc}\n")
            for cp in attr.copyrights:
                notice.write(f"{cp}\n")

    return 0


def main(args):
    # fetch all projects for our 'phantom-apps' product, and filter by prefix
    ws_api = WhiteSourceApi(user_key=WS_USER_KEY, product_id=WS_PRODUCT_ID)

    all_projects = ws_api.request("getAllProjects")["projects"]
    project_tokens = {prj["projectName"]: prj["projectToken"] for prj in all_projects}

    if args.project_name not in project_tokens:
        logging.error("Could not find a WhiteSource scan under %s", args.project_name)
        return 1

    return generate_notice_file(args.app_dir, ws_api, project_tokens[args.project_name])


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stdout, level=logging.INFO)
    sys.exit(main(parse_args()))
