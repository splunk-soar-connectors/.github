import os
import backoff
import requests

from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry


READ_REQUEST_NUM_RETRIES = 10
REQUEST_TIMEOUT = (10, 60)
USER_AGENT = (
    "Splunk-SOAR-Connector-Publisher/1.0 (+https://github.com/splunk-soar-connectors/.github)"
)
SPLUNKBASE_SOAR_APP_EDITORS = ["nastor_splunk", "coh_splunk"]
READ_STATUS_CODES_TO_RETRY = [429, 500, 502, 503, 504]
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
    data: str | bytes | bool | list | dict,
    check_response: bool = True,
) -> list | dict | str | bool:
    session = _single_attempt_session(headers=headers)
    response = session.post(url, data, timeout=REQUEST_TIMEOUT)
    if check_response:
        return _response_json(response)
    return response.json()


@backoff.on_exception(backoff.expo, SplunkbaseResponseError, max_time=MAX_MESSAGE_RETRY_TIME)
def _get_request(url, return_json=True, params=None, headers=None, auth=None, required_keys=None):
    session = _retrying_session(headers=headers, auth=auth)
    response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    if return_json:
        return _response_json(response, required_keys=required_keys)
    if not response.ok:
        raise RuntimeError(f"Bad response status: {response.status_code}. Details: {response.text}")
    return response.text
