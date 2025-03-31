#!/usr/bin/env python3
"""
Compile Apps
"""

import os
import argparse
import logging
from pathlib import Path
from collections.abc import Iterator
import git
from contextlib import contextmanager
import sys

# Add utils to the import path
REPO_ROOT = Path(__file__).parent.parent.parent.resolve()
sys.path.append(str(REPO_ROOT))

from utils import compile_app

LOCAL_REPO_DIRECTORY = os.getenv("GITHUB_WORKSPACE", ".")
PHANTOM_PASSWORD = os.getenv("PHANTOM_PASSWORD", ".")


@contextmanager
def get_app_code(local_code_dir: str) -> Iterator[git.Repo]:
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
    parser.add_argument("--current-phantom-ip", type=str, help="current phantom ip")
    parser.add_argument("--next-phantom-ip", type=str, help="next phantom ip")
    parser.add_argument("--previous-phantom-ip", type=str, help="previous phantom ip")
    parser.add_argument("--phantom-username", type=str, help="phantom username")

    args = parser.parse_args()
    app_repo_name = args.app_repo
    current_phantom_ip = args.current_phantom_ip
    next_phantom_ip = args.next_phantom_ip
    previous_phantom_ip = args.previous_phantom_ip
    phantom_username = args.phantom_username

    with get_app_code(local_code_dir=LOCAL_REPO_DIRECTORY) as local_repo_location:
        print(f"Repo location: {local_repo_location}")
        responses = compile_app.run_compile(
            app_repo_name,
            local_repo_location,
            current_phantom_ip,
            next_phantom_ip,
            previous_phantom_ip,
            phantom_username,
            PHANTOM_PASSWORD,
        )

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
