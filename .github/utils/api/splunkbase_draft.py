"""Splunkbase v2 draft and presigned-upload API.

Contract: https://stage.splunkbase.splunk.com/api-docs-v2/
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from urllib.parse import quote, urlsplit

import requests

from .splunkbase import (
    REQUEST_TIMEOUT,
    Splunkbase,
    SplunkbaseAmbiguousUpload,
    SplunkbasePermissionDenied,
    SplunkbaseRateLimited,
    SplunkbaseResponseError,
    SplunkbaseValidationFailed,
    _request_id,
    _retrying_session,
    _single_attempt_session,
    build_user_agent,
)


def path_id(value):
    return quote(str(value), safe="")


class DraftSplunkbase(Splunkbase):
    def __init__(self, user, password, request_context=None, environment="PROD"):
        hosts = {
            "PROD": ("splunkbase.splunk.com", "api.splunk.com"),
            "STAGE": ("stage.splunkbase.splunk.com", "api.staging.splunk.com"),
        }
        api_host, login_host = hosts[environment]
        self.env = environment
        self.api_url = f"https://{api_host}/api/v2"
        self._apps_base_url = f"{self.api_url}/apps"
        self._splunkbase_editor_url = f"https://{api_host}/api/v0.1/app/{{sb_appid}}/editors/"
        self.login_url = f"https://{login_host}/2.0/rest/login/splunk"
        self.splunkbase_user = user
        self.splunkbase_password = password
        self.user_agent = build_user_agent(**(request_context or {}))
        self.expires_at = None
        self.auth = self._get_bearer_auth()

    def _get_bearer_auth(self):
        if not self.splunkbase_user or not self.splunkbase_password:
            raise SplunkbasePermissionDenied("Splunkbase publishing credentials are missing")
        session = _single_attempt_session(user_agent=self.user_agent)
        session.trust_env = False
        try:
            response = session.post(
                self.login_url,
                data={"username": self.splunkbase_user, "password": self.splunkbase_password},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            )
            data = response.json().get("data", {}) if response.status_code == 200 else {}
        except (requests.RequestException, ValueError, AttributeError):
            raise SplunkbasePermissionDenied("Splunkbase login failed") from None
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("token"), str)
            or not data["token"]
        ):
            raise SplunkbasePermissionDenied("Splunkbase login did not return a token")
        expiry = data.get("expires")
        self.expires_at = None
        if expiry:
            try:
                self.expires_at = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                if self.expires_at.tzinfo is None:
                    self.expires_at = self.expires_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError, AttributeError):
                raise SplunkbaseResponseError(
                    "Splunkbase returned an invalid token expiry"
                ) from None
        return {"Authorization": f"Bearer {data['token']}"}

    def request(self, method, path, *, authenticated=True, **kwargs):
        if (
            authenticated
            and self.expires_at
            and (self.expires_at <= datetime.now(timezone.utc) + timedelta(seconds=30))
        ):
            self.auth = self._get_bearer_auth()
        session = _retrying_session() if method == "GET" else _single_attempt_session()
        session.trust_env = False
        session.headers["User-Agent"] = self.user_agent
        for attempt in range(2):
            try:
                response = session.request(
                    method,
                    f"{self.api_url}/{path.lstrip('/')}",
                    headers=self.auth if authenticated else {},
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=False,
                    **kwargs,
                )
            except requests.RequestException:
                error = SplunkbaseResponseError if method == "GET" else SplunkbaseAmbiguousUpload
                raise error(f"Splunkbase {method} result is unknown") from None
            # An explicit auth rejection has not applied the mutation. Refresh once.
            if authenticated and response.status_code in (401, 403) and attempt == 0:
                self.auth = self._get_bearer_auth()
                continue
            break
        request_id = _request_id(response)
        if response.status_code in (401, 403):
            raise SplunkbasePermissionDenied(
                "Splunkbase denied access after authentication refresh",
                status_code=response.status_code,
                request_id=request_id,
            )
        if response.status_code == 429:
            raise SplunkbaseRateLimited(
                "Splunkbase quota exhausted",
                status_code=429,
                retry_after=response.headers.get("Retry-After"),
                request_id=request_id,
            )
        if not 200 <= response.status_code < 300:
            error = (
                SplunkbaseAmbiguousUpload
                if response.status_code >= 500 or 300 <= response.status_code < 400
                else SplunkbaseValidationFailed
            )
            raise error(
                f"Splunkbase {method} returned HTTP {response.status_code}",
                status_code=response.status_code,
                request_id=request_id,
            )
        self.last_upload_request_id = request_id
        if response.status_code == 204 or not response.content:
            return {}
        try:
            result = response.json()
        except ValueError:
            error = SplunkbaseResponseError if method == "GET" else SplunkbaseAmbiguousUpload
            raise error(f"Splunkbase {method} returned unreadable JSON") from None
        if not isinstance(result, dict):
            raise SplunkbaseResponseError("Splunkbase returned an invalid response shape")
        return result

    def get_app(self, app_id):
        return self.request("GET", f"apps/{path_id(app_id)}/")

    def find_app(self, app_guid):
        try:
            app = self.get_app(app_guid)
        except SplunkbaseValidationFailed as exc:
            if exc.status_code == 404:
                return None
            raise
        if app.get("app_id") != app_guid:
            raise SplunkbaseResponseError("Editor app lookup returned a different connector")
        return app

    def public_app(self, app_id):
        response = self.request(
            "GET", "apps/", authenticated=False, params={"ids": str(app_id), "skip_cache": "true"}
        )
        apps = response.get("results")
        if not isinstance(apps, list):
            raise SplunkbaseResponseError("Public app listing is missing results")
        return next((app for app in apps if str(app.get("id")) == str(app_id)), None)

    def public_app_by_guid(self, app_guid):
        response = self.request(
            "GET",
            "apps/",
            authenticated=False,
            params={"appid": app_guid, "product": "soar", "skip_cache": "true"},
        )
        apps = response.get("results")
        if not isinstance(apps, list) or len(apps) > 1:
            raise SplunkbaseResponseError("Public app lookup returned invalid results")
        if apps and apps[0].get("app_id") != app_guid:
            raise SplunkbaseResponseError("Public app lookup returned a different connector")
        return apps[0] if apps else None

    def create_draft(self, metadata):
        result = self.request("POST", "apps/", json={"draft": "true", **metadata})
        if not result.get("id"):
            raise SplunkbaseAmbiguousUpload("Draft creation returned no app ID")
        return result["id"]

    def get_apps(self, extra_params=None):
        params = {
            "product": "soar",
            "include": "releases",
            "skip_cache": "true",
            **(extra_params or {}),
        }
        apps = []
        offset = 0
        while True:
            response = self.request(
                "GET",
                "apps/",
                authenticated=False,
                params={**params, "offset": offset, "limit": 100},
            )
            results = response.get("results")
            count = response.get("count", response.get("total"))
            if not isinstance(results, list) or not isinstance(count, int):
                raise SplunkbaseResponseError("Public app listing has invalid pagination")
            apps.extend(results)
            if len(apps) >= count:
                return apps
            if not results:
                raise SplunkbaseResponseError(
                    "Public app pagination stopped before its reported count"
                )
            offset += len(results)

    def get_existing_releases(self, app_id):
        app = self.public_app_by_guid(app_id)
        return app["releases"] if app else []

    def upload_package(self, app_id, package_file, save_s3_key):
        package_file = Path(package_file)
        presigned = self.request("POST", "presigned_url/", json={"filename": package_file.name})
        url = presigned.get("url", "")
        fields = presigned.get("fields")
        if not isinstance(url, str):
            raise SplunkbaseValidationFailed("Splunkbase returned invalid S3 upload parameters")
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except (ValueError, TypeError):
            raise SplunkbaseValidationFailed(
                "Splunkbase returned invalid S3 upload parameters"
            ) from None
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or port not in (None, 443)
            or not re.fullmatch(
                r"(?:[a-z0-9.-]+\.)?s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com", parsed.hostname or ""
            )
            or not isinstance(fields, dict)
            or not isinstance(fields.get("key"), str)
            or "file" in fields
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in fields.items()
            )
        ):
            raise SplunkbaseValidationFailed("Splunkbase returned invalid S3 upload parameters")
        session = _single_attempt_session(user_agent=self.user_agent)
        # Do not attach Splunkbase auth, cookies, .netrc credentials, or follow redirects.
        session.trust_env = False
        try:
            with package_file.open("rb") as stream:
                response = session.post(
                    url,
                    data=fields,
                    files={"file": (package_file.name, stream)},
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=False,
                )
        except requests.RequestException:
            raise SplunkbaseAmbiguousUpload("S3 upload result is unknown") from None
        if not 200 <= response.status_code < 300:
            raise SplunkbaseValidationFailed(f"S3 rejected the upload: HTTP {response.status_code}")
        # Persist only the object key, never the signing fields or URL.
        save_s3_key(fields["key"])
        return self.register_package(app_id, package_file.name, fields["key"])

    def register_package(self, app_id, filename, s3_key):
        result = self.request(
            "POST", f"apps/{path_id(app_id)}/upload/", json={"filename": filename, "s3_key": s3_key}
        )
        if not result.get("id"):
            raise SplunkbaseAmbiguousUpload("Package registration returned no package ID")
        return result["id"]

    def get_upload_status(self, package_id):
        return self.request("GET", f"apps/validation/{path_id(package_id)}/")

    def patch_metadata(self, app_id, metadata):
        return self.request("PATCH", f"apps/{path_id(app_id)}/", json=metadata)

    def publish_release(self, app_id, release_id, release_notes, *, publish=True):
        metadata = {"release_notes": release_notes}
        if publish:
            metadata["public"] = True
        return self.request(
            "PATCH",
            f"apps/{path_id(app_id)}/releases/{path_id(release_id)}/",
            json=metadata,
        )

    def required_fields(self, app_id):
        result = self.request("GET", f"apps/{path_id(app_id)}/required_fields/")
        missing = result.get("missing_fields")
        if not isinstance(missing, list) or any(
            not isinstance(field, str) or not re.fullmatch(r"[a-z_]+", field) for field in missing
        ):
            raise SplunkbaseResponseError("Splunkbase did not report required fields")
        return missing

    def submit_draft(self, app_id):
        return self.request("POST", f"apps/{path_id(app_id)}/submit/", json={})
