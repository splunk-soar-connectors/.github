import json
import logging
import os
import random
import re
import socket
import string
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Union

import backoff
import paramiko
from scp import SCPClient

from utils import find_app_json_name
from utils.version_compat import supports_minimum_version

ANSI_ESCAPE = re.compile(r"(\x1b|\x1B)(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
OUTPUT = re.compile(r"Output\:([^\x1b]*?)Error output\:")

TEST_APP_DIRECTORY_TEMPLATE = "/home/phantom/app_tests/{app_name}"
TEST_DIRECTORY = "/home/phantom/app_tests"
RANDOM_STRING = "/{}/".format(
    "".join(random.choices(string.ascii_uppercase + string.ascii_lowercase, k=7))
)


def get_min_phantom_version(local_app_path: Path) -> str:
    local_app_path = Path(local_app_path)
    json_filenames = [path.name for path in local_app_path.glob("*.json")]
    app_json_name = (
        "manifest.json" if "manifest.json" in json_filenames else find_app_json_name(json_filenames)
    )

    with (local_app_path / app_json_name).open() as app_json_file:
        return json.load(app_json_file)["min_phantom_version"]


def is_local_app_compatible(
    local_app_path: Path,
    phantom_ip: str,
    phantom_username: str,
    phantom_password: str,
) -> bool:
    try:
        min_phantom_version = get_min_phantom_version(local_app_path)
        return supports_minimum_version(
            min_phantom_version,
            phantom_ip,
            phantom_username,
            phantom_password,
        )
    except Exception as error:
        logging.warning(
            "Version compatibility check failed for traditional app, defaulting to compatible: %s",
            error,
        )
        return True


def compile_app(
    phantom_version: str,
    phantom_client: paramiko.SSHClient,
    test_directory: Path,
) -> dict[str, Union[bool, str]]:
    logging.info(f"running {phantom_version} test")
    compile_command = f"cd {test_directory}; pwd; ls; phenv compile_app -i"
    logging.info(compile_command)

    _, stdout, stderr = phantom_client.exec_command(compile_command)

    exit_code = int(stdout.channel.recv_exit_status())
    logging.info(f"Compile Command Exit Code: {exit_code}")

    stdout_lines = stdout.readlines()
    stdout_lines = [ANSI_ESCAPE.sub("", line) for line in stdout_lines]
    stdout_lines = [line.strip() for line in stdout_lines]

    error_lines = stderr.readlines()
    if error_lines:
        error_lines = [ANSI_ESCAPE.sub("", line) for line in error_lines]
        error_lines = [line.strip() for line in error_lines]
        error_message = ",".join(error_lines).replace("\n", "")
        error_message = OUTPUT.findall(error_message)[:1]
        for line in error_lines:
            logging.info(line)

    response = {
        "success": exit_code == 0,
        "message": stdout_lines if (exit_code == 0) else error_message,
    }

    return response


def make_folder(
    phantom_version: str, phantom_client: paramiko.SSHClient, app_name: str, test_directory: Path
) -> None:
    commands = f"mkdir -p {test_directory}; cd {test_directory}"
    logging.info(commands)
    logging.info(f"Creating folder for {app_name} on phantom {phantom_version}")
    phantom_client.exec_command(commands)


def delete_folder(phantom_client: paramiko.SSHClient, test_directory: Path) -> None:
    commands = f"rm -rf {test_directory}"
    logging.info("Deleting folder %s", test_directory)
    logging.info(commands)
    if " " not in test_directory and TEST_DIRECTORY in TEST_APP_DIRECTORY_TEMPLATE:
        phantom_client.exec_command(commands)


@contextmanager
def upload_app_files(
    phantom_version: str, phantom_client: paramiko.SSHClient, local_app_path: Path, app_name: str
) -> Iterator[Path]:
    remote_path = TEST_APP_DIRECTORY_TEMPLATE.format(app_name=app_name + RANDOM_STRING)
    make_folder(phantom_version, phantom_client, app_name, remote_path)

    logging.info(f"Uploading files on {phantom_version}")
    with SCPClient(phantom_client.get_transport()) as scp:
        scp.put(local_app_path, recursive=True, remote_path=remote_path)

    yield os.path.join(remote_path, os.path.basename(local_app_path))

    delete_folder(phantom_client, remote_path)


@backoff.on_exception(backoff.expo, socket.error, max_tries=3)
def run_compile(
    app_name: str,
    local_app_path: Path,
    current_phantom_ip: str,
    next_phantom_ip: str,
    previous_phantom_ip: str,
    phantom_username: str,
    phantom_password: str,
) -> dict[str, dict[str, Union[bool, str]]]:
    results = {}
    hosts = {
        "current_phantom_version": current_phantom_ip,
        "next_phantom_version": next_phantom_ip,
        "previous_phantom_version": previous_phantom_ip,
    }

    for version, host in hosts.items():
        if not is_local_app_compatible(
            local_app_path,
            host,
            phantom_username,
            phantom_password,
        ):
            message = (
                f"Skipping compile on {version}: app's min_phantom_version "
                "is not supported by this instance"
            )
            logging.info(message)
            results[version] = {"success": True, "message": [message]}
            continue

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            logging.info(f"Connecting to host {version}: {host}")
            client.connect(
                hostname=host, username=phantom_username, password=phantom_password, port=22
            )
            with upload_app_files(version, client, local_app_path, app_name) as test_dir:
                results[version] = compile_app(version, client, test_dir)
        finally:
            client.close()

    return results
