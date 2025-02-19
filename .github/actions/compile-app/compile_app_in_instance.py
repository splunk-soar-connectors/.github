#!/usr/bin/env python3
"""
Compile Apps
"""

import os
import argparse
import logging
import git
from contextlib import contextmanager

from utils import compile_app


@contextmanager
def get_app_code(app_repo, app_repo_branch, local_code_dir, fork_repo_owner):
    if local_code_dir and os.path.isdir(local_code_dir):
        print(f"Running tests against existing local copy of app: {local_code_dir}")
        local_repo = git.Repo(local_code_dir)
        for submodule in local_repo.submodules:
            try:
                submodule.update(init=True)
            except git.exc.GitCommandError as e:
                print(
                    f"WARNING: Failed to clone Git submodules. Some dependency tests may fail. Error message: {e}"
                )
                break
        yield local_repo.git_dir
    else:
        github = 1#GitHubApi(owner_repo=fork_repo_owner, token=GITHUB_API_KEY)
        with github.clone_and_manage_app_repo(app_repo, branch=app_repo_branch) as local_repo:
            print(f"Cloned app from GitHub to this location: {local_repo}")
            yield local_repo


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("app_repo", type=str, help="app repo name")
    parser.add_argument(
        "--local-code-dir", type=str, help="local directory location for app under test"
    )
    parser.add_argument("--app-repo-branch", type=str, help="app repo branch")
    parser.add_argument(
        "--fork-repo",
        required=False,
        help="Name of a forked app repo, if we are testing against a fork.",
    )
    parser.add_argument(
        "--fork-repo-owner",
        required=False,
        help="Owner of the forked app repo, if we are testing against a fork.",
    )

    args = parser.parse_args()

    app_repo = args.fork_repo or args.app_repo
    local_code_dir = args.local_code_dir
    app_repo_name = args.app_repo
    app_repo_branch = args.app_repo_branch
    print(app_repo)
    print(local_code_dir)
    print(app_repo_name)
    print(app_repo_branch)
    print(os.getcwd())
    print(os.listdir())

    local_repo_location = get_app_code(local_code_dir=os.get_cwd())
    logging.info("Repo location: %s", local_repo_location)
    responses = compile_app.run_compile(app_repo_name, local_repo_location)

    failed = [
        (version, results)
        for version, results in responses.items()
        if results["success"] is False
    ]
    if failed:
        print("Compilation failed")
        for version, results in failed:
            print(version)
            for messaage in results.get("message"):
                for line in messaage.split(","):
                    print(line)
        return 1
    else:
        print("No compile errors found")
        return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    exit(main())
