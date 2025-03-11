import logging
from typing import Optional
import backoff
import requests

from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry


APACHE2_LICENSE_STRING = "Apache License Version 2.0"
APACHE2_LICENSE_URL = "https://www.apache.org/licenses/LICENSE-2.0.html"
SGT_LICENSE_STRING = "Splunk General Terms"
SGT_LICENSE_URL = "https://www.splunk.com/en_us/legal/splunk-general-terms.html"
APP_REPO_BASE_URL = "https://github.com/splunk-soar-connectors/"

POST_WITH_FILES_NUM_RETRIES = 10
SPLUNKBASE_API_VERSION = "v2"
SPLUNKBASE_SOAR_PRODUCT = "soar"
SPLUNKBASE_SOAR_APP_TYPE = "connector"
SPLUNKBASE_SOAR_APP_EDITORS = ["nastor_splunk", "coh_splunk"]
SPLUNKBASE_SUCCESSFUL_UPLOAD_RESPONSES = [
    "App was successfully uploaded and is being validated.",
    "Release was successfully uploaded and is being validated.",
]
SPLUNKBASE_BASE_URL = f"https://splunkbase.splunk.com/api/{SPLUNKBASE_API_VERSION}/apps"
SPLUNKBASE_EDITOR_URL = "https://splunkbase.splunk.com/api/v0.1/app/{sb_appid}/editors/"
STATUS_CODES_TO_RETRY = [403, 502, 504]
RESPONSE_MESSAGES_TO_RETRY = [
    "Network error communicating with endpoint",
    "Endpoint request timed out",
    "Package validation still in progress.",
]
MAX_MESSAGE_RETRY_TIME = 120


def _post_request(auth_tuple: tuple[str, str], url: str, data: Union[str, bytes, bool, list, dict], check_response: bool = True) -> Union[list, dict, str, bool]:
    session = requests.Session()
    session.auth = auth_tuple

    response = session.post(url, data)
    if check_response and not response.ok:
        raise RuntimeError(f"Bad response status: {response.status_code}. Details: {response.text}")

    return response.json()


def _post_request_with_files(auth_tuple, url, data, files, check_response=True, retry_codes=None):
    session = requests.Session()
    session.auth = auth_tuple

    if retry_codes:
        retry = Retry(
            total=POST_WITH_FILES_NUM_RETRIES,
            method_whitelist=frozenset(["POST"]),
            status_forcelist=retry_codes,
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))

    response = session.post(url, data, files=files)
    if check_response and not response.ok:
        raise RuntimeError(f"Bad response status: {response.status_code}. Details: {response.text}")

    return response.json()


def _get_request(url, return_json=True, params=None, auth_tuple=None):
    session = requests.Session()
    if auth_tuple:
        session.auth = auth_tuple
    response = session.get(url, params=params)
    if return_json:
        return response.json()
    return response.text


class Splunkbase:
    def __init__(self, splunkbase_user, splunkbase_password):
        self.env = "PROD"
        self._apps_base_url = SPLUNKBASE_BASE_URL
        self._splunkbase_editor_url = SPLUNKBASE_EDITOR_URL
        self.splunkbase_user = splunkbase_user
        self.splunkbase_password = splunkbase_password
        self.auth = self._get_basic_auth()


    def _get_basic_auth(self) -> Optional[tuple[str, str]]:
        user = self.splunkbase_user
        password = self.splunkbase_password
        if not user and password:
            logging.info("Splunkbase username and password not provided")
        return (user, password) if user and password else None

    def _is_retryable_response(response):
        if isinstance(response, dict):
            response = response.get("message")
        return response in RESPONSE_MESSAGES_TO_RETRY

    @backoff.on_predicate(backoff.expo, _is_retryable_response, max_time=MAX_MESSAGE_RETRY_TIME)
    def _upload(self, app_repo_name, package_file, url, release_notes, license_string, license_url):
        if not self.auth:
            raise ValueError("Authentication must be configured for POST requests")

        data = {
            "product": SPLUNKBASE_SOAR_PRODUCT,
            "release_notes": release_notes,
            "visibility": "true",
            "license_name": license_string,
            "license_url": license_url,
            "app_type": SPLUNKBASE_SOAR_APP_TYPE,
            "repo_name": app_repo_name,
            "repo_url": APP_REPO_BASE_URL + app_repo_name,
        }
        with open(package_file, "rb") as file:
            logging.info("About to post request with url: %s", url)
            response = _post_request_with_files(
                self.auth, url, data, {"package_file": file}, True, STATUS_CODES_TO_RETRY
            )
            if response.get("message", "") in SPLUNKBASE_SUCCESSFUL_UPLOAD_RESPONSES:
                return response.get("package_id")
            return response

    @property
    def apps_base_url(self):
        return self._apps_base_url

    def upload_app_version(
        self, app_id, app_repo_name, package_file, release_notes, license_string, license_url
    ):
        url = f"{self.apps_base_url}/{app_id}/releases"
        return self._upload(
            app_repo_name, package_file, url, release_notes, license_string, license_url
        )

    def upload_app(self, app_repo_name, package_file, release_notes, license_string, license_url):
        url = f"{self.apps_base_url}/"  # https://splunk.atlassian.net/browse/PAPP-34507
        return self._upload(
            app_repo_name, package_file, url, release_notes, license_string, license_url
        )

    @backoff.on_predicate(backoff.expo, _is_retryable_response, max_time=MAX_MESSAGE_RETRY_TIME)
    def check_upload_status(self, package_id):
        url = f"{self.apps_base_url}/validation/{package_id}"
        return _get_request(url, auth_tuple=self.auth)

    @staticmethod
    def get_app_id(results_dict, app_guid):
        return results_dict.get(app_guid, {}).get("id")

    @staticmethod
    def get_app_releases(results_dict, app_guid):
        releases = results_dict.get(app_guid, {}).get("releases", [])
        return {r["release_name"]: r for r in releases}

    def get_apps(self, extra_params=None):
        if extra_params is None:
            extra_params = {}
        url = self.apps_base_url
        limit = 100
        params = {
            "product": SPLUNKBASE_SOAR_PRODUCT,
            "include": "releases,support",
            "offset": 0,
            "limit": limit,
        }
        params = {**params, **extra_params}

        all_response_data = []
        done = False
        while not done:
            logging.info(
                "Getting a page (%s) of apps from Splunkbase. Offset: %s...",
                limit,
                params["offset"],
            )
            response = _get_request(url, return_json=True, params=params, auth_tuple=self.auth)
            all_response_data.extend(response["results"])

            if len(all_response_data) >= response["total"]:
                done = True
            else:
                params["offset"] += limit

        return all_response_data

    def get_existing_releases(self, app_id):
        params = {
            "product": SPLUNKBASE_SOAR_PRODUCT,
            "include": "releases,support",
            "appid": app_id,
        }
        response = _get_request(
            self.apps_base_url, return_json=True, params=params, auth_tuple=self.auth
        )
        logging.info(response)
        apps_returned = response["results"]

        if len(apps_returned) > 1:
            err_msg = (
                f"Expected to find at most one app given the id {app_id} but found: {apps_returned}"
            )
            logging.error(err_msg)
            raise ValueError(err_msg)

        if len(apps_returned) == 0:
            return []

        return apps_returned[-1]["releases"]

    def add_app_editor(self, sb_appid):
        if not self.auth:
            raise ValueError("Authentication must be configured for POST requests")

        for user in SPLUNKBASE_SOAR_APP_EDITORS:
            data = {"username": user}
            editor_url = self._splunkbase_editor_url.replace("{sb_appid}", str(sb_appid))
            logging.info(editor_url)
            logging.info(f"Adding editor {user} to splunkbase appid {sb_appid}")
            response = _post_request(self.auth, editor_url, data=data)
            logging.info(response)
