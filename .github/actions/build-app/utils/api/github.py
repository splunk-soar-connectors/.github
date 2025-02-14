import os
import shutil
from enum import Enum
from contextlib import contextmanager

import git

from utils.api import ApiSession
from utils.phantom_constants import GITHUB_APP_REPO_BRANCH


class StrEnum(str, Enum):
    pass


class GitHubOrganization(StrEnum):
    CONNECTORS = "splunk-soar-connectors"
    GENERAL = "phantomcyber"


DEFAULT_AUTHOR = {"name": "root", "email": "root@splunksoar"}

# Pipeline job triggers are skipped for commits made by this author
ADMIN_AUTHOR = {"name": "splunk-soar-connectors-admin", "email": "admin@splunksoar"}


class GitHubApi:
    """
    Our internal GitHub utils class.
    """

    def __init__(self, token=None, owner_repo=None):
        self.session = ApiSession("https://api.github.com")

        self._github_repo_owner = owner_repo if owner_repo else GitHubOrganization.CONNECTORS
        self.base_repo_path = f"/repos/{self._github_repo_owner}/"
        self._token = token
        if self._token:
            self.session.headers.update({"Authorization": f"Token {self._token}"})

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

    def iter_branches(self, repo_name):
        yield from self._iter_data(f"/repos/{self._github_repo_owner}/{repo_name}/branches")

    def iter_repos(self, group_name=GitHubOrganization.CONNECTORS):
        yield from self._iter_data(f"/orgs/{group_name}/repos", params={"per_page": 50})

    @staticmethod
    def _setup_dirpath(dir_path):
        if os.path.exists(dir_path):
            shutil.rmtree(dir_path)
        os.makedirs(dir_path)

    def _clone(self, repo_name, local_repo_dir, branch=GITHUB_APP_REPO_BRANCH):
        if self._token:
            clone_url = "{}/{}".format(
                f"https://{self._token}@github.com/{self._github_repo_owner}", repo_name
            )
        else:
            clone_url = f"https://github.com/{self._github_repo_owner}/{repo_name}"
        self._setup_dirpath(local_repo_dir)
        branch_name = branch or GITHUB_APP_REPO_BRANCH
        print(
            "clone_url: {}, local_repo_dir: {}, branch: {}".format(
                clone_url.replace(f"{self._token}@", ""), local_repo_dir, branch_name
            )
        )
        repo = git.Repo.clone_from(clone_url, to_path=local_repo_dir, branch=branch)

        for submodule in repo.submodules:
            submodule.update(init=True)

        return local_repo_dir

    @contextmanager
    def clone_and_manage(self, repo_name, local_repo_dir, branch=GITHUB_APP_REPO_BRANCH):
        """
        This is method calls the _clone method with required parameters depending the mode of the testing.
        It yields the repo directory to wrapper function.
        """
        try:
            yield self._clone(repo_name, local_repo_dir, branch=branch)
        finally:
            if os.path.exists(local_repo_dir):
                shutil.rmtree(local_repo_dir)

    @contextmanager
    def clone_and_manage_app_repo(
        self, repo_name, local_repo_dir="/tmp", branch=GITHUB_APP_REPO_BRANCH
    ):
        """
        This is a wrapper function to call the clone_and_manage method with required parameters depending the mode of the testing.
        """
        local_repo_dir = os.path.join(local_repo_dir, repo_name)

        with self.clone_and_manage(repo_name, local_repo_dir, branch=branch) as repo_dir:
            if not repo_dir:
                raise ValueError(f"Error while cloning the repo {repo_name}!")
            yield repo_dir
