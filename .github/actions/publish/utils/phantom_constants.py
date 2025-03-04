import os

DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

__curr_dir = os.getcwd()
os.chdir(DIR)

REPO_NAME_TO_APPID_FILEPATH = os.path.join(DIR, "data", "repo_name_to_appid.json")
RELEASE_QUEUE_REGION = 'us-west-2'
