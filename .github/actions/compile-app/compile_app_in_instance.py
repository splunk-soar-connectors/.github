#!/usr/bin/env python3
"""
Compile Apps
"""
import argparse
import logging
import git
from contextlib import contextmanager

from utils import compile_app
from utils.phantom_constants import LOCAL_REPO_DIRECTORY

@contextmanager
def get_app_code(local_code_dir):
    local_repo = git.Repo(local_code_dir)
    for submodule in local_repo.submodules:
        try:
            submodule.update(init=True)
        except git.exc.GitCommandError as e:
            print(
                f"WARNING: Failed to clone Git submodules. Some dependency tests may fail. Error message: {e}"
            )
            break
    yield local_repo.working_tree_dir


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("app_repo", type=str, help="app repo name")
    parser.add_argument("--app-repo-branch", type=str, help="app repo branch")

    args = parser.parse_args()
    app_repo_name = args.app_repo


    with get_app_code(local_code_dir=LOCAL_REPO_DIRECTORY) as local_repo_location:
        print(f"Repo location: {local_repo_location}")
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
                for message in results.get("message"):
                    for line in message.split(","):
                        print(line)
            return 1
        else:
            print("No compile errors found")
            return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    exit(main())
