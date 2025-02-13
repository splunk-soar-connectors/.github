import re
import sys
import os
from distutils.version import LooseVersion
from enum import Enum

DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

__curr_dir = os.getcwd()
os.chdir(DIR)

APPID_TO_NAME_FILEPATH = os.path.join(DIR, "data", "appid_to_name.json")
APPID_TO_PACKAGE_NAME_FILEPATH = os.path.join(DIR, "data", "appid_to_package_name.json")
# AWS S3 bucket to store build artifacts
APP_ARTIFACTS_BUCKET = os.getenv("APP_ARTIFACTS_BUCKET", "soar-apps-artifacts-stg")
BUILD_FILE_EXCLUDES_FILEPATH = os.path.join(DIR, "data", "global_build_file_excludes.json")
GITHUB_API_KEY = os.environ.get("SOAR_APPS_GITHUB_KEY")
GITHUB_APP_REPO_BRANCH = "next" #TODO: mike
APP_QA_OVERLORD = "mnordby" #TODO: mike
APP_EXTS = (".py", ".html", ".json", ".svg", ".png")
SKIPPED_MODULE_PATHS = os.path.join(DIR, "data", "skipped_module_paths.json")
APP_JSON_KEYS = [
    "appid",
    "name",
    "description",
    "publisher",
    "package_name",
    "type",
    "main_module",
    "app_version",
    "product_vendor",
    "product_name",
    "product_version_regex",
    "min_phantom_version",
    "logo",
    "configuration",
    "actions",
]
