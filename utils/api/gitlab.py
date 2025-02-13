import os
import re
import shutil

from contextlib import contextmanager
from base64 import b64encode

import git

from utils.api import ApiSession
from urllib.parse import quote_plus
from utils.command_utils import get_command_output
from utils.phantom_constants import (
    GITLAB_API_TOKEN,
    FILES_IN_EMPTY_APP_REPO,
    QA_OVERLORD,
    APP_QA_OVERLORD,
)
from utils import memoize
from utils.phantom_qa_logging import get_standard_logger

GITLAB_SERVER = "cd.splunkdev.com"
PH_GRP_NAME = "phantom"
PH_REPO_NAME = "phantom"
APP_GRP_NAME = "phantom apps"
QA_REPO_NAME = "qa"

logger = get_standard_logger()

SKIPPED_REPOS = {
    "phantom/assets"  # Deprecated repo to be deleted in the future
}


class GitLabApi:
    """
    Our internal GitLab utils class.
    """

    __initialized = None

    grp_ids = {}
    proj_ids = {}

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

    def list_repos(self, group_name=APP_GRP_NAME):
        # Possible issue: resp json will have different keys than BitBucket
        """
        List repos in the desired group. Defaults to phantom-apps group
        """
        return list(self.iter_repos(group_name))

    def list_directory_contents(
        self, path=None, repo_name=PH_REPO_NAME, branch=None, recursive=True
    ):
        """
        Takes relative repo directory path and returns the files in that directory.
        If repo_name isn't specified, the main phantom project is assumed
        """
        return list(self.iter_files(path, repo_name, branch))

    def iter_files(self, path=None, repo_name=PH_REPO_NAME, branch=None, recursive=True):
        """
        Generator version of list_directory_contents that returns one result at a time.
        Useful for searching for files in large directories by not always running through every page in the REST response
        """
        repo_id = self.proj_ids[repo_name.lower()]
        params = {"recursive": recursive, "per_page": 50}
        if path:
            params["path"] = path
        if branch:
            params["ref"] = branch

        for filedata in self._iter_data(f"/projects/{repo_id}/repository/tree", params=params):
            if path is None:
                yield filedata["path"]
            else:
                yield filedata["path"][len(path) :]

    def get_file_data(self, file_path, repo_name=PH_REPO_NAME, branch="master"):
        """
        Returns info about the file in question, like size, last_commit, path, etc...
        """
        repo_id = self.proj_ids[repo_name.lower()]
        params = {"ref": branch}
        resp = self.session.get(f"/projects/{repo_id}/repository/files/{file_path}", params=params)
        return resp.json()

    def get_file_contents(self, file_path, repo_name=PH_REPO_NAME, branch="master"):
        """
        Return the text of the file specified by file_path in the repo given
        """
        repo_id = self.proj_ids[repo_name.lower()]
        params = {"ref": branch}
        file_path = quote_plus(file_path)

        resp = self.session.get(
            f"/projects/{repo_id}/repository/files/{file_path}/raw", params=params
        )
        return resp.text

    def app_repo_empty(self, repo_name, branch=None):
        """
        Return True if the given app repo is "empty", False otherwise
        """
        return any(
            f not in FILES_IN_EMPTY_APP_REPO
            for f in self.list_directory_contents(repo_name=repo_name, branch=branch)
        )

    @memoize(ignore_self=True)
    def _get_clone_url(self, repo_name):
        """
        Given a repo name, return its ssh clone link. Also, minimize requests by memoizing results
        """
        repo_name = repo_name.lower()
        try:
            resp = self.session.get(f"/projects/{self.proj_ids[repo_name]}")
            return resp.json()["ssh_url_to_repo"]
        except Exception:
            raise ValueError(f"Could not find repo url for {repo_name}!") from None

    def get_single_commit(self, repo_name, ref):
        """
        Return data about a single commit, by either commit sha or branch/tag name
        """
        repo_id = self.proj_ids[repo_name.lower()]
        resp = self.session.get(f"/projects/{repo_id}/repository/commits/{ref}")
        return resp.json()

    def iter_commits(self, repo_name, branch=None, path=None):
        """
        Get info for many commits
        """
        # Possible issue: Commits had jira-key properties in BitBucket. See app_repo_mappings.py
        repo_id = self.proj_ids[repo_name.lower()]

        params = {}
        if branch:
            params["ref_name"] = branch
        if path:
            params["path"] = path

        yield from self._iter_data(f"/projects/{repo_id}/repository/commits", params=params)

    def get_commits(self, repo_name, branch=None, path=None, limit=500):
        commits = []
        for commit in self.iter_commits(repo_name, branch, path):
            commits.append(commit)
            if len(commits) == limit:
                break
        return commits

    def _commit_file(self, action, repo_name, branch, filepath, contents, commit_msg, skip_ci):
        """
        Boilerplate code for update_file and add_file functions
        """
        repo_id = self.proj_ids[repo_name.lower()]
        url = "/projects/{}/repository/files/{}".format(
            repo_id, quote_plus(filepath).replace(".", "%2E")
        )

        if not commit_msg:
            commit_msg = f"{action} file {os.path.basename(filepath)}."
        if skip_ci:
            commit_msg += " [skip ci]"
        post_data = {
            "branch": branch,
            "encoding": "base64",
            "content": b64encode(contents),
            "commit_message": commit_msg,
        }
        return url, post_data

    def update_file(
        self, repo_name, branch, filepath, new_contents, commit_msg=None, skip_ci=False
    ):
        """
        Overwrite an existing file in a repo with new contents. Fails if file doesn't already exist
        """
        url, post_data = self._commit_file(
            "Update", repo_name, branch, filepath, new_contents, commit_msg, skip_ci
        )
        return self.session.put(url, json=post_data).json()

    def add_file(self, repo_name, branch, filepath, contents, commit_msg=None, skip_ci=False):
        """
        Add a new file with given contents to a repo. Fails if file already exists
        """
        url, post_data = self._commit_file(
            "Add", repo_name, branch, filepath, contents, commit_msg, skip_ci
        )
        return self.session.post(url, json=post_data).json()

    def upsert_file(self, *args, **kwargs):
        """
        If a file exists in a repo, update it with new contents. Otherwise, add it to the repo
        """
        try:
            return self.update_file(*args, **kwargs)
        except Exception:
            return self.add_file(*args, **kwargs)

    def delete_file(self, repo_name, branch, filepath, commit_msg=None, skip_ci=False):
        """
        Delete a file in a repo. Fails if file did not already exist
        """
        repo_id = self.proj_ids[repo_name.lower()]

        if not commit_msg:
            commit_msg = f"Deleted file {os.path.basename(filepath)}"
        if skip_ci:
            commit_msg += " [skip ci]"
        params = {"branch": branch, "commit_message": commit_msg}
        resp = self.session.delete(
            f"/projects/{repo_id}/repository/files/{quote_plus(filepath)}", params=params
        )
        return resp.json()

    def iter_branches(self, repo_name):
        repo_id = self.proj_ids[repo_name.lower()]
        yield from self._iter_data(f"/projects/{repo_id}/repository/branches")

    def list_branches(self, repo_name):
        return list(self.iter_branches(repo_name))

    @staticmethod
    def _setup_dirpath(dir_path):
        if os.path.exists(dir_path):
            shutil.rmtree(dir_path)
        os.makedirs(dir_path)

    def _clone(self, repo_name, local_repo_dir, branch="next"):
        clone_url = self._get_clone_url(repo_name)
        self._setup_dirpath(local_repo_dir)
        repo = git.Repo.clone_from(clone_url, to_path=local_repo_dir, branch=branch)
        for submodule in repo.submodules:
            submodule.update(init=True)
        return local_repo_dir

    def clone_phantom_repo(self, local_repo_dir="/tmp/phantom_src/"):
        return self._clone("phantom", local_repo_dir)

    @contextmanager
    def clone_and_manage(self, repo_name, local_repo_dir, branch="next"):
        logger.info(f"Cloning {repo_name} repo, {branch} branch")
        try:
            yield self._clone(repo_name, local_repo_dir, branch=branch)

        finally:
            if os.path.exists(local_repo_dir):
                shutil.rmtree(local_repo_dir)

    @contextmanager
    def clone_and_manage_app_repo(self, repo_name, branch="next", local_repo_dir="/tmp"):
        local_repo_dir = os.path.join(local_repo_dir, repo_name)
        with self.clone_and_manage(repo_name, local_repo_dir, branch=branch) as repo_dir:
            if not repo_dir:
                raise ValueError(f"Error while cloning the repo {repo_name}!")
            yield repo_dir

    @memoize(ignore_self=True)
    def _get_user_id(self, username):
        try:
            users = self.session.get("/users", params={"username": username}).json()
            assert len(users) == 1
            return users[0]["id"]
        except Exception as e:
            raise ValueError(f"Could not find user {username} due to this error: {e}") from None

    def validate_username(self, username):
        try:
            self._get_user_id(username)
            return True
        except ValueError:
            return False

    def create_merge_request(
        self, from_branch, to_branch, title, repo_name=None, description="", assignee=None, **kwargs
    ):
        if repo_name is None:
            repo_id = re.search(
                rf"{GITLAB_SERVER}:(.+)\.git", get_command_output(["git", "remote", "-v"])
            ).group(1)
            repo_id = quote_plus(repo_id)
        else:
            repo_id = self.proj_ids[repo_name.lower()]

        post_data = {
            "source_branch": from_branch,
            "target_branch": to_branch,
            "title": title,
            "description": description,
        }

        # Use assignee we were given
        if assignee is not None:
            post_data["assignee_id"] = self._get_user_id(assignee)

        # Pass along any extra kwargs or overrides
        post_data.update(kwargs)
        resp = self.session.post(f"/projects/{repo_id}/merge_requests", json=post_data)
        return resp.json()["web_url"]

    def create_phantom_merge_request(
        self, from_branch, to_branch, title, description, assignee=QA_OVERLORD, **kwargs
    ):
        return self.create_merge_request(
            from_branch, to_branch, title, "phantom", description, assignee=assignee, **kwargs
        )

    def create_app_merge_request(
        self,
        from_branch,
        to_branch,
        title,
        repo_name,
        description,
        assignee=APP_QA_OVERLORD,
        **kwargs,
    ):
        return self.create_merge_request(
            from_branch, to_branch, title, repo_name, description, assignee=assignee, **kwargs
        )

    def create_app_release_merge_request(
        self, from_branch, to_branch, title, description, assignee=APP_QA_OVERLORD, **kwargs
    ):
        return self.create_merge_request(
            from_branch, to_branch, title, "app_release", description, assignee=assignee, **kwargs
        )

    def create_qa_merge_request(
        self, from_branch, to_branch, title, description, assignee=QA_OVERLORD, **kwargs
    ):
        return self.create_merge_request(
            from_branch, to_branch, title, "qa", description, assignee=assignee, **kwargs
        )

    def iter_tags(self, repo_name):
        repo_id = self.proj_ids[repo_name]
        yield from self._iter_data(f"/projects/{repo_id}/repository/tags")

    def list_tags(self, repo_name):
        return list(self.iter_tags(repo_name))

    def create_tag(self, repo_name, branch, tag_name, tag_message):
        repo_id = self.proj_ids[repo_name.lower()]
        post_data = {"ref": branch, "tag_name": tag_name, "message": tag_message}
        resp = self.session.post(f"/projects/{repo_id}/repository/tags", json=post_data)
        return resp.json()

    def delete_tag(self, repo_name, tag_name):
        repo_id = self.proj_ids[repo_name.lower()]
        resp = self.session.delete(f"/projects/{repo_id}/repository/tags/{tag_name}")
        return resp.json()

    """ Functions below this line are not yet ported from BitBucketApi class """

    def search_for_asset(self, product_vendor, product_name):
        """search_for_asset(self, product_vendor, product_name)"""

    def get_latest_version_tag(self, repo_name, version_message):
        for tag in self.iter_tags(repo_name):
            if tag["message"] == version_message:
                return tag["name"]
        return None

    def unprotect_branch(self, repo_name, branch, ignore_error=False):
        """
        If a branch is protected, unprotect it. By default, throws an error if branch wasn't already protected
        """
        repo_id = self.proj_ids[repo_name.lower()]
        try:
            self.session.delete(f"/projects/{repo_id}/protected_branches/{branch}")
        except Exception:
            if not ignore_error:
                raise

    def protect_branch(
        self, repo_name, branch, ignore_error=False, push_access_level=0, merge_access_level=40
    ):
        """
        Protect a branch. Access levels are kind of annoying since they're magic numbers, but the default
        values set push permissions for nobody and merge permissions for maintainers.
        By default, throws an error if branch was already protected, unless you set ignore_error to True
        """
        repo_id = self.proj_ids[repo_name.lower()]
        post_data = {
            "name": branch,
            "push_access_level": push_access_level,
            "merge_access_level": merge_access_level,
        }
        try:
            return self.session.post(
                f"/projects/{repo_id}/protected_branches", json=post_data
            ).json()
        except Exception:
            if not ignore_error:
                raise

    @contextmanager
    def work_on_protected_branch(self, repo_name, branch):
        """
        Easy way to quickly unprotect a branch, do work on it, and then re-protect it after you're done
        """
        try:
            self.unprotect_branch(repo_name, branch, ignore_error=True)
            yield branch
        finally:
            self.protect_branch(repo_name, branch, ignore_error=True)

    def create_pipeline_run(self, repo_name, git_ref, **pipeline_vars):
        """
        Creates a pipeline run for the given repo/branch using the given
        variables
        """
        repo_id = self.proj_ids[repo_name.lower()]
        req_body = {"variables": [{"key": k, "value": v} for k, v in pipeline_vars.items()]}
        return self.session.post(
            f"/projects/{repo_id}/pipeline?ref={git_ref}", json=req_body
        ).json()

    def get_pipeline_run(self, repo_name, pipeline_id):
        """
        Fetches details for a given pipeline run
        """
        repo_id = self.proj_ids[repo_name.lower()]
        return self.session.get(f"/projects/{repo_id}/pipelines/{pipeline_id}").json()

    """NO EQUIVALENT IN GITLAB API"""
    # def create_reviewer_list(self, reviewers):
    #     reviewer_list = []
    #     for reviewer in reviewers:
    #         try:
    #             reviewer_list.append({'user': {'name': self.validate_username(reviewer)}})
    #         except Exception:
    #             pass

    #     return reviewer_list
