import re

import requests
from requests.adapters import HTTPAdapter, Retry


class ApiSession(requests.Session):
    """
    The Base URL Session allows you to request URLs with relative pathing given a base.
    It makes the call you're making more readable from a defined api base.
    """

    def __init__(self, base_url):
        self.base_url = base_url
        if self.base_url.endswith("/"):
            self.base_url = self.base_url[:-1]

        requests.packages.urllib3.disable_warnings()
        self.verify = False

        super().__init__()

        retry_strategy = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[408, 409, 500, 502, 503, 504],
            method_whitelist=["GET", "POST"],
            raise_on_status=False,
        )
        self.mount(self.base_url, HTTPAdapter(max_retries=retry_strategy))

    def request(self, method, url, **kwargs):
        if "://" not in url:
            assert url.startswith("/")
            url = self.base_url + url
        # Port specification on https is often broken
        if url.startswith("https"):
            url = re.sub(r"(\.com):\d+/", r"\1/", url)
        print(f"kwargs are {kwargs}")
        resp = super().request(method, url, **kwargs)
        resp.raise_for_status()
        return resp
