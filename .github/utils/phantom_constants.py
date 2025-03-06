import os

DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

__curr_dir = os.getcwd()
os.chdir(DIR)

GITHUB_API_KEY = os.environ.get("SOAR_APPS_GITHUB_KEY")
APP_ARTIFACTS_BUCKET = os.getenv("APP_ARTIFACTS_BUCKET", "soar-apps-artifacts-stg")
BUILD_FILE_EXCLUDES_FILEPATH = os.path.join(DIR, "data", "global_build_file_excludes.json")
APPID_TO_NAME_FILEPATH = os.path.join(DIR, "data", "appid_to_name.json")
GITHUB_APP_REPO_BRANCH = "main"
APP_QA_OVERLORD = "mnordby"
APP_EXTS = (".py", ".html", ".json", ".svg", ".png")
SKIPPED_MODULE_PATHS = os.path.join(DIR, "data", "skipped_module_paths.json")
