import logging
import os
from typing import Optional, Union
import backoff
import requests

from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry


APACHE2_LICENSE_STRING = "Apache License Version 2.0"
APACHE2_LICENSE_URL = "https://www.apache.org/licenses/LICENSE-2.0.html"
SGT_LICENSE_STRING = "Splunk General Terms"
SGT_LICENSE_URL = "https://www.splunk.com/en_us/legal/splunk-general-terms.html"
APP_REPO_BASE_URL = "https://github.com/splunk-soar-connectors/"

READ_REQUEST_NUM_RETRIES = 10
REQUEST_TIMEOUT = (10, 60)
USER_AGENT = (
    "Splunk-SOAR-Connector-Publisher/1.0 (+https://github.com/splunk-soar-connectors/.github)"
)
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
SPLUNKBASE_LOGIN_URL = "https://api.splunk.com/2.0/rest/login/splunk"
READ_STATUS_CODES_TO_RETRY = [429, 500, 502, 503, 504]
RESPONSE_MESSAGES_TO_RETRY = [
    "Network error communicating with endpoint",
    "Endpoint request timed out",
    "Package validation still in progress.",
]
MAX_MESSAGE_RETRY_TIME = 120


class SplunkbaseResponseError(RuntimeError):
    """Raised when Splunkbase returns a response that cannot be consumed safely."""


class SplunkbaseUploadError(RuntimeError):
    """Base class for a single failed Splunkbase upload attempt."""

    def __init__(
        self,
        message,
        *,
        status_code=None,
        retry_after=None,
        request_id=None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.request_id = request_id


class SplunkbaseRateLimited(SplunkbaseUploadError):
    """Raised when Splunkbase rejects the counted attempt with HTTP 429."""


class SplunkbasePermissionDenied(SplunkbaseUploadError):
    """Raised when the publishing identity cannot upload the connector."""


class SplunkbaseAmbiguousUpload(SplunkbaseUploadError):
    """Raised when a counted attempt may have reached Splunkbase."""


class SplunkbaseValidationFailed(SplunkbaseUploadError):
    """Raised when Splunkbase definitively rejects the submitted package."""


def _is_retryable_status_response(response):
    if isinstance(response, dict):
        response = response.get("message")
    return response in RESPONSE_MESSAGES_TO_RETRY


def build_user_agent(repo=None, version=None, run_id=None, run_attempt=None):
    """Return a stable, non-secret User-Agent with release correlation fields."""

    fields = {
        "repo": repo or os.getenv("GITHUB_REPOSITORY", "").split("/")[-1],
        "version": version,
        "run": run_id or os.getenv("GITHUB_RUN_ID"),
        "attempt": run_attempt or os.getenv("GITHUB_RUN_ATTEMPT"),
    }
    suffix = " ".join(
        f"{key}/{str(value).replace(' ', '_')}"
        for key, value in fields.items()
        if value not in (None, "")
    )
    return f"{USER_AGENT} {suffix}".rstrip()


def _request_id(response):
    normalized_headers = {key.lower(): value for key, value in response.headers.items()}
    for header in (
        "x-request-id",
        "x-correlation-id",
        "splunk-request-id",
        "request-id",
    ):
        value = normalized_headers.get(header)
        if value:
            return value
    return None


def _log_response_metadata(response):
    metadata = {
        header: response.headers.get(header)
        for header in (
            "Retry-After",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
            "X-Request-ID",
            "X-Correlation-ID",
            "Splunk-Request-ID",
        )
        if response.headers.get(header)
    }
    if metadata:
        logging.info("Splunkbase response metadata: %s", metadata)


def _retrying_session(headers=None, auth=None):
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    if headers:
        session.headers.update(headers)
    if auth:
        session.auth = auth

    retry = Retry(
        total=READ_REQUEST_NUM_RETRIES,
        connect=READ_REQUEST_NUM_RETRIES,
        read=READ_REQUEST_NUM_RETRIES,
        status=READ_REQUEST_NUM_RETRIES,
        allowed_methods=frozenset(["GET"]),
        status_forcelist=READ_STATUS_CODES_TO_RETRY,
        backoff_factor=1,
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _single_attempt_session(headers=None, auth=None, user_agent=None):
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent or USER_AGENT})
    if headers:
        session.headers.update(headers)
    if auth:
        session.auth = auth

    retry = Retry(total=0, connect=0, read=0, status=0, redirect=0)
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _response_json(response, required_keys=None):
    if not response.ok:
        raise RuntimeError(f"Bad response status: {response.status_code}. Details: {response.text}")

    try:
        data = response.json()
    except ValueError as exc:
        raise SplunkbaseResponseError("Splunkbase returned a non-JSON response") from exc

    missing_keys = set(required_keys or ()) - set(data if isinstance(data, dict) else ())
    if missing_keys:
        raise SplunkbaseResponseError(
            f"Splunkbase response is missing required fields: {sorted(missing_keys)}"
        )
    return data


def _post_request(
    headers: dict[str, str],
    url: str,
    data: Union[str, bytes, bool, list, dict],
    check_response: bool = True,
) -> Union[list, dict, str, bool]:
    session = _single_attempt_session(headers=headers)
    response = session.post(url, data, timeout=REQUEST_TIMEOUT)
    if check_response:
        return _response_json(response)
    return response.json()


def _post_request_with_files(
    headers,
    url,
    data,
    files,
    check_response=True,
    user_agent=None,
):
    session = _single_attempt_session(headers=headers, user_agent=user_agent)
    try:
        response = session.post(url, data, files=files, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise SplunkbaseAmbiguousUpload(
            f"Splunkbase upload transport failed after one counted attempt: {exc}"
        ) from exc

    _log_response_metadata(response)
    request_id = _request_id(response)
    if response.status_code == 429:
        raise SplunkbaseRateLimited(
            "Splunkbase upload rate limit reached after one counted attempt",
            status_code=response.status_code,
            retry_after=response.headers.get("Retry-After"),
            request_id=request_id,
        )
    if response.status_code in (401, 403):
        raise SplunkbasePermissionDenied(
            "Splunkbase publishing identity is not permitted to upload this connector",
            status_code=response.status_code,
            request_id=request_id,
        )
    if response.status_code >= 500:
        raise SplunkbaseAmbiguousUpload(
            "Splunkbase returned a server error after one counted attempt",
            status_code=response.status_code,
            request_id=request_id,
        )
    if not response.ok:
        raise SplunkbaseValidationFailed(
            f"Splunkbase rejected the upload: HTTP {response.status_code}: {response.text}",
            status_code=response.status_code,
            request_id=request_id,
        )

    try:
        data = _response_json(response) if check_response else response.json()
    except SplunkbaseResponseError as exc:
        raise SplunkbaseAmbiguousUpload(
            "Splunkbase returned an unreadable response after one counted attempt",
            status_code=response.status_code,
            request_id=request_id,
        ) from exc
    if isinstance(data, dict) and request_id:
        data["_request_id"] = request_id
    return data


@backoff.on_exception(backoff.expo, SplunkbaseResponseError, max_time=MAX_MESSAGE_RETRY_TIME)
def _get_request(url, return_json=True, params=None, headers=None, auth=None, required_keys=None):
    session = _retrying_session(headers=headers, auth=auth)
    response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    if return_json:
        return _response_json(response, required_keys=required_keys)
    if not response.ok:
        raise RuntimeError(f"Bad response status: {response.status_code}. Details: {response.text}")
    return response.text


class Splunkbase:
    def __init__(self, splunkbase_user, splunkbase_password, request_context=None):
        self.env = "PROD"
        self._apps_base_url = SPLUNKBASE_BASE_URL
        self._splunkbase_editor_url = SPLUNKBASE_EDITOR_URL
        self.splunkbase_user = splunkbase_user
        self.splunkbase_password = splunkbase_password
        self.user_agent = build_user_agent(**(request_context or {}))
        self.auth = self._get_bearer_auth()

    def _get_bearer_auth(self) -> Optional[dict[str, str]]:
        user = self.splunkbase_user
        password = self.splunkbase_password
        if not user or not password:
            logging.info("Splunkbase username and password not provided")
            return None

        response = _get_request(
            SPLUNKBASE_LOGIN_URL,
            auth=(user, password),
            required_keys={"data"},
        )
        token = response.get("data", {}).get("token")
        if not token:
            raise RuntimeError(
                "Unable to obtain Splunkbase bearer token: token missing in response"
            )

        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _is_retryable_response(response):
        return _is_retryable_status_response(response)

    @staticmethod
    def is_definitive_validation_failure(response):
        if not isinstance(response, dict):
            return False
        if any(response.get(key) for key in ("error", "errors", "validation_errors")):
            return True
        status_text = " ".join(str(response.get(key, "")) for key in ("status", "message")).lower()
        return any(
            marker in status_text
            for marker in (
                "failed validation",
                "validation failed",
                "rejected",
                "invalid package",
            )
        )

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
                self.auth,
                url,
                data,
                {"package_file": file},
                True,
                user_agent=self.user_agent,
            )
            if response.get("message", "") in SPLUNKBASE_SUCCESSFUL_UPLOAD_RESPONSES:
                self.last_upload_request_id = response.get("_request_id")
                return response.get("package_id")
            if self._is_retryable_response(response):
                raise SplunkbaseAmbiguousUpload(
                    "Splunkbase reported an ambiguous upload result after one counted attempt"
                )
            raise SplunkbaseValidationFailed(
                f"Splunkbase rejected the upload: {response.get('message', response)}"
            )

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

    @backoff.on_predicate(
        backoff.expo,
        _is_retryable_status_response,
        max_time=MAX_MESSAGE_RETRY_TIME,
    )
    def check_upload_status(self, package_id):
        url = f"{self.apps_base_url}/validation/{package_id}"
        return _get_request(url, headers=self.auth)

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
            response = _get_request(
                url,
                return_json=True,
                params=params,
                headers=self.auth,
                required_keys={"results", "total"},
            )
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
            self.apps_base_url,
            return_json=True,
            params=params,
            headers=self.auth,
            required_keys={"results"},
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
