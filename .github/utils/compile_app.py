import os
from pathlib import Path
import re
import socket
import string
import random
import logging
from contextlib import contextmanager
from typing import Union
from collections.abc import Iterator

import backoff
import paramiko
from scp import SCPClient

ANSI_ESCAPE = re.compile(r"(\x1b|\x1B)(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
OUTPUT = re.compile(r"Output\:([^\x1b]*?)Error output\:")

TEST_APP_DIRECTORY_TEMPLATE = "/home/phantom/app_tests/{app_name}"
TEST_DIRECTORY = "/home/phantom/app_tests"
RANDOM_STRING = "/{}/".format(
    "".join(random.choices(string.ascii_uppercase + string.ascii_lowercase, k=7))
)


def compile_app(
    phantom_version: str, phantom_client: paramiko.SSHClient, test_directory: Path, version: str
) -> dict[str, Union[bool, str]]:
    logging.info(f"running {phantom_version} test")

    # As of 5/7/25 compile_command uses phenv compile_app -i on both current and next
    # The next test instance upgrade will make this on previous_phantom_version as well
    # and we can get rid of this if statement
    if version == "next_phantom_version" or version == "current_phantom_version":
        compile_command = f"cd {test_directory}; pwd; ls; phenv compile_app -i"
    else:
        compile_command = (
            f"cd {test_directory}; pwd; ls; phenv compile_app --compile-app --exclude-flake"
        )
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
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            logging.info(f"Connecting to host {version}: {host}")
            client.connect(
                hostname=host, username=phantom_username, password=phantom_password, port=22
            )
            with upload_app_files(version, client, local_app_path, app_name) as test_dir:
                results[version] = compile_app(version, client, test_dir, version)
        finally:
            client.close()

    return results
