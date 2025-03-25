import os
from typing import ClassVar

from utils.api import ApiSession
from urllib.parse import quote_plus

GITLAB_SERVER = "cd.splunkdev.com"
PH_GRP_NAME = "phantom"
PH_REPO_NAME = "phantom"
APP_GRP_NAME = "phantom apps"
QA_REPO_NAME = "qa"
GITLAB_API_TOKEN = os.environ.get("GITLAB_API_TOKEN")

SKIPPED_REPOS = {
    "phantom/assets"  # Deprecated repo to be deleted in the future
}


class GitLabApi:
    """
    Our internal GitLab utils class.
    """

    __initialized: ClassVar[None] = None
    grp_ids: ClassVar[dict] = {}
    proj_ids: ClassVar[dict] = {}

    def __init__(self, token=None):
        self.session = ApiSession(f"https://{GITLAB_SERVER}/api/v4")

        token = token if token else GITLAB_API_TOKEN
        assert token, f"Invalid value given as GitLab API token: {token}"
        self.session.headers.update({"Private-Token": token})

        # Only need the rest if we're using a new token
        if GitLabApi.__initialized != token:
            # Get groups information
            for group in self.iter_groups(search="phantom"):
                self.grp_ids[group["full_name"].lower()] = group["id"]

            # Save project ID's for the important groups
            for group in (PH_GRP_NAME, APP_GRP_NAME):
                for project in self.iter_repos(group):
                    if project["path_with_namespace"] in SKIPPED_REPOS:
                        continue
                    self.proj_ids[project["name"].lower()] = quote_plus(
                        project["path_with_namespace"]
                    )

        GitLabApi.__initialized = token

    def _iter_data(self, url, **kwargs):
        while url is not None:
            resp = self.session.get(url, params=kwargs.pop("params", None), **kwargs)
            page = resp.json()
            if isinstance(page, list):
                yield from page
            else:
                yield page

            try:
                url = resp.links["next"]["url"]
            except KeyError:
                url = None

    def iter_groups(self, search=None):
        params = {}
        if search:
            params["search"] = search
        yield from self._iter_data("/groups", params=params)

    def iter_repos(self, group_name=APP_GRP_NAME):
        group_id = self.grp_ids[group_name.lower()]
        yield from self._iter_data(f"/groups/{group_id}/projects", params={"per_page": 50})

    def create_pipeline_run(self, repo_name: str, git_ref: str, **pipeline_vars) -> dict:
        """
        Creates a pipeline run for the given repo/branch using the given
        variables
        """
        repo_id = self.proj_ids[repo_name.lower()]
        req_body = {"variables": [{"key": k, "value": v} for k, v in pipeline_vars.items()]}
        return self.session.post(
            f"/projects/{repo_id}/pipeline?ref={git_ref}", json=req_body
        ).json()

    def get_pipeline_run(self, repo_name: str, pipeline_id: int) -> dict:
        """
        Fetches details for a given pipeline run
        """
        repo_id = self.proj_ids[repo_name.lower()]
        return self.session.get(f"/projects/{repo_id}/pipelines/{pipeline_id}").json()
