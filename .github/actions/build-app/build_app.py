"""
build_app.py is a script that builds a tarball and uploads it to AWS S3
Written by: michellel & jacobd @ Splunk, January 2019
Updated: mnordby @ Splunk, Feb 2025
"""

import argparse
import inspect
import json
import os
from pathlib import Path
import subprocess
import tarfile
from contextlib import contextmanager
import sys
from typing import Union
from collections.abc import Iterator

import boto3
import botocore
import botocore.exceptions
import git

# Add utils to the import path
REPO_ROOT = Path(__file__).parent.parent.parent.resolve()
sys.path.append(str(REPO_ROOT))

from utils import validate_app_id
from utils.api.github import GitHubApi
from utils.app_parser import AppParser
from utils.phantom_constants import (
    APP_ARTIFACTS_BUCKET,
    BUILD_FILE_EXCLUDES_FILEPATH,
    GITHUB_API_KEY,
)

DIR = os.path.realpath(os.path.dirname(__file__))
LOCAL_REPO_DIRECTORY = os.getenv("GITHUB_WORKSPACE", ".")

# Required json fields and how to validate them
REQ_FIELDS = {
    "name": lambda x, y: len(x) > 2,  # WMI is a 3 letter word
    "description": None,
    "publisher": lambda x, y: len(x) > 0,
    "type": lambda x, y: len(x) >= 3,
    "main_module": None,
    "app_version": lambda x, y: len(x) > 0,
    "product_vendor": None,
    "product_name": None,
    "configuration": None,
    "actions": None,
    "package_name": lambda x, y: len(x) > 2,
    "appid": validate_app_id,
}


def log(message):
    """
    Auto-indent print messages according to stack depth and immediately flush stdout
    every time so we actually get output in GitLab
    """
    print("{}{}".format(" " * (len(inspect.stack()) - 1), message))
    sys.stdout.flush()


class AppBuilder:
    """
    AppBuilder class creates app tarballs and posts them to AWS S3.
    Only method that should be used from the outside is run()
    """

    def __init__(self, app_repo_name: str, app_branch: str, **kwargs) -> None:
        log("Initializing app builder")
        self._dry_run = bool(kwargs.get("dry_run", False))
        self.app_repo_name = app_repo_name
        self.branch = app_branch

        self._local_code = LOCAL_REPO_DIRECTORY

        self.git_api = GitHubApi(token=GITHUB_API_KEY)

        self.s3 = boto3.resource("s3")
        self.artifacts_bucket = self.s3.Bucket(APP_ARTIFACTS_BUCKET)
        self.s3_output_path = kwargs.pop("output", None)
        self.output_dir = kwargs.get("output_dir")

        # The following attributes are re-defined in run()
        self.app_code_dir = None
        self.app_json = None
        self.download_files = None

    def run(self):
        """
        Main method of AppBuilder. Does the following:
            1.  Validate the args passed to __init__
            2.  Clones the app repo if not already present locally
            3.  Determines build config of specific app (i.e. dont_post_rpm or download extra files)
            4.  Downloads any extra files the app might need to do a build (see build_config)
            5.  Determines which packages to build and where to post them (no tgz on main, no rpm if dont_post_rpm)
            6.  Builds necessary packages (either tgz, rpm, none, or both)
            7. Posts them to appropriate location in AWS S3, based on git branch
        """
        log("Running app builder")

        # Get a repo object to work on
        with self._get_app_code() as app_repo:
            self.app_code_dir = app_repo.working_tree_dir
            self.commit_sha = f"app-{app_repo.head.commit.hexsha[:8]}"
            with change_current_directory(self.app_code_dir):
                log("Getting build config")
                self._get_build_config()

                # App Parser finds the json for us, among other things
                log("Getting and validating app json")
                app_parser = AppParser(self.app_code_dir)
                self.app_json = app_parser.app_json
                self._validate_app_json()

                # Download extra build files as necessary
                self._download_build_files()

                # Compile the app source before packaging
                self._compile_app()

                # Create tgz and rpm
                log("Creating tgz package")
                tarfile_path = self._create_tar(self.app_repo_name, app_parser.excludes)
                log(tarfile_path)

    @contextmanager
    def _get_app_code(self) -> Iterator[git.Repo]:
        """
        If necessary, clone the app repo. Either way, make a repo object out of the directory
        """
        if self._local_code:
            log(f"Building existing local copy of app: {self._local_code}")
            repo = git.Repo(self._local_code)
            for submodule in repo.submodules:
                try:
                    submodule.update(init=True)
                except git.exc.GitCommandError as e:
                    print(
                        f"WARNING: Failed to clone Git submodules. Some dependency tests may fail. Error message: {e}"
                    )
                    break
            yield repo

        else:
            with self.git_api.clone_and_manage_app_repo(
                self.app_repo_name, branch=self.branch
            ) as local_repo_location:
                log(f"Cloned app to this location: {local_repo_location}")
                repo = git.Repo(local_repo_location)
                for submodule in repo.submodules:
                    submodule.update(init=True)
                yield repo

    def _get_build_config(self) -> None:
        """
        Gets the app's build config, also checks if it indicates 'deprecated'.
        Stop the build if deprecated
        """
        config = {}
        build_file = os.path.join(self.app_code_dir, "build_config")
        if os.path.isfile(build_file):
            try:
                with open(build_file) as f:
                    config = json.load(f)
            except OSError:
                pass

        if config.get("deprecated", os.path.isfile(os.path.join(self.app_code_dir, "deprecated"))):
            log(f"App {self.app_repo_name} is deprecated. Build aborted without errors.")
            exit(0)

        # Some apps have files that need to be downloaded during build
        self.download_files = config.get("download_files", [])

    def _validate_app_json(self) -> None:
        """
        Validates the app's json by checking if required fields are present and in the correct format
        """
        for req_field, validator in REQ_FIELDS.items():
            if req_field not in self.app_json:
                raise ValueError(f"App json is missing the required field: {req_field}")
            if validator and not validator(self.app_json[req_field], self.app_json["name"]):
                raise ValueError(
                    f'App json failed validation on required field "{req_field}" with value "{self.app_json[req_field]}"'
                )

    def _download_build_files(self) -> None:
        """
        If the app's build_config specifies any files to download, grab them before building
        """
        for f in self.download_files:
            source = f.get("source", None)
            dest = f.get("destination", None)
            if not source or not dest:
                continue
            dest = os.path.normpath(os.path.join(os.getcwd(), dest))
            log(f"Downloading file {source} to {dest}")

            bucket, key = source.replace("s3://", "").split("/", 1)
            log(f"S3 bucket is {bucket} and key is {key}")
            try:
                self.s3.Bucket(bucket).download_file(key, dest)
            except botocore.exceptions.ClientError as e:
                if e.response["Error"]["Code"] == "404":
                    print("The object does not exist.")
                else:
                    raise

    def _compile_app(self) -> None:
        """
        Compile the app directory
        """
        compile_cmd = f"python -m compileall -q -f {self.app_code_dir}"
        log("Compiling app with python")
        run_command(compile_cmd)

    def _create_tar(self, app_repo_name: str, excludes: set[str]) -> str:
        """
        Creates a tar file of the app's source code and returns it
        """
        tarfile_name = f"{app_repo_name}.tgz"
        tarfile_path = os.path.join("/tmp", tarfile_name)
        exclude_cmds = self._get_tar_excludes(excludes)
        tar_command = f"tar {exclude_cmds} --dereference -zcf {tarfile_path} {os.path.basename(self.app_code_dir)}"

        with change_current_directory("../"):
            run_command(tar_command)

        # Just make sure everything went well before continuing
        self._validate_tar(tarfile_path)
        return tarfile_path

    @staticmethod
    def _get_tar_excludes(excludes: set[str]) -> str:
        """
        Generate --excludes options for a tar command
        """
        with open(BUILD_FILE_EXCLUDES_FILEPATH) as global_excludes_file:
            global_excludes_list = set(json.load(global_excludes_file)["patterns"])
            return " ".join([f'--exclude="{x}"' for x in excludes | global_excludes_list])

    @staticmethod
    def _validate_tar(tarfile_path: str) -> None:
        """
        Validates if a tar file is legitimate and returns the app name, if not, throw an exception
        """
        # Get the first file of the tar, which provides the app info
        tar_info = tarfile.open(tarfile_path, "r|*").next()

        # Checks if there is a file existing in the tar
        if tar_info is None:
            raise Exception("Tarball that was created looks empty! Aborting...")

        app_path = tar_info.name
        # Checks if the app's path contains '/' or '..', it should not
        if app_path.startswith(("/", "..")):
            raise Exception("App path starts with '/' or '..'")


def run_command(cmd, console=False, suppress=False):
    """
    Simple wrapper around subprocess to call and print outputs
    """
    try:
        output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
        if console:
            log(output)
    except subprocess.CalledProcessError as e:
        if not suppress:
            log(e.output)
        raise


@contextmanager
def change_current_directory(to_directory: Union[Path, str]) -> Iterator[str]:
    """
    Safer chdir method that takes you back to where you were when it's done
    """
    current_directory = os.getcwd()
    os.chdir(to_directory)
    log(f"Changed to directory. Relative: {to_directory} | Absolute: {os.getcwd()}")

    yield current_directory
    os.chdir(current_directory)
    log(f"Changed back to directory {current_directory} from {to_directory}")


def create_cmdline_parser():
    """
    Commandline parser for passing in necessary arguments
    """
    help_str = " ".join(line.strip() for line in __doc__.splitlines())
    argparser = argparse.ArgumentParser(description=help_str)
    argparser.add_argument(
        "app", type=str, help="Repo name or local directory location for app under test"
    )
    argparser.add_argument("branch", type=str, help="Branch to work on")
    argparser.add_argument(
        "-o",
        "--output",
        help="Optional filepath to write the S3 URI of where the built tarball was uploaded",
    )
    argparser.add_argument(
        "--output-dir",
        type=str,
        default=".",
        help="Directory where the tar file will be created",
    )
    argparser.add_argument(
        "--dry-run",
        action="store_true",
        help="When this argument is given, nothing will be uploaded to S3",
    )
    return argparser


def main(**kwargs):
    """
    Script entrypoint. Creates an AppBuilder object and runs it
    """
    log("Starting script")

    if kwargs.get("dry_run", False):
        log(
            "THIS IS A DRY-RUN. NOTHING WILL BE POSTED TO AWS S3, AND NO VERSION BUMPS WILL BE COMMITTED"
        )
    kwargs["app_repo_name"] = kwargs.pop("app")
    kwargs["app_branch"] = kwargs.pop("branch")
    AppBuilder(**kwargs).run()


if __name__ == "__main__":
    log("Processing args")
    parser = create_cmdline_parser()
    options = vars(parser.parse_args())
    main(**options)
