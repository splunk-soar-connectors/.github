import glob
import json
import os
import re
import socket
import string
import random
import logging
from contextlib import contextmanager

import backoff
import paramiko
from scp import SCPClient

from utils.phantom_constants import (
    PHANTOM_INSTANCE_CURRENT_VERSION_IP,
    PHANTOM_INSTANCE_PREVIOUS_VERSION_IP,
    PHANTOM_PASSWORD,
    PHANTOM_SSH_USER,
)

ANSI_ESCAPE = re.compile(r"(\x1b|\x1B)(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
OUTPUT = re.compile(r"Output\:([^\x1b]*?)Error output\:")

ALL_HOSTS = {
    "current_phantom_version": PHANTOM_INSTANCE_CURRENT_VERSION_IP,
    "previous_phantom_version": PHANTOM_INSTANCE_PREVIOUS_VERSION_IP,
}

TEST_APP_DIRECTORY_TEMPLATE = "/home/phantom/app_tests/{app_name}"
TEST_DIRECTORY = "/home/phantom/app_tests"
RANDOM_STRING = "/{}/".format(
    "".join(random.choices(string.ascii_uppercase + string.ascii_lowercase, k=7))
)


def compile_app(phantom_version, phantom_client, test_directory):
    logging.info(f"running {phantom_version} test")
    compile_command = f"cd {test_directory}; pwd; ls; phenv compile_app -c"
    logging.info(compile_command)

    _, stdout, stderr = phantom_client.exec_command(compile_command)

    exit_code = int(stdout.channel.recv_exit_status())
    logging.info(f"Compile Command Exit Code: {exit_code}")

    lines = stdout.readlines()
    error_lines = stderr.readlines()

    lines = [ANSI_ESCAPE.sub("", line) for line in lines]
    print(lines)
    if error_lines:
        error_lines = [ANSI_ESCAPE.sub("", line) for line in error_lines]
        print(error_lines)
        error_message = ",".join(error_lines).replace("\n", "")
        error_message = OUTPUT.findall(error_message)[:1]

    response = {"success": exit_code == 0, "message": lines if (exit_code == 0) else error_message}

    return response


def make_folder(phantom_version, phantom_client, app_name, test_directory):
    commands = f"mkdir -p {test_directory}; cd {test_directory}"
    logging.info(commands)
    logging.info(f"Creating folder for {app_name} on phantom {phantom_version}")
    phantom_client.exec_command(commands)


def delete_folder(phantom_client, test_directory):
    commands = f"rm -rf {test_directory}"
    logging.info("Deleting folder %s", test_directory)
    logging.info(commands)
    if " " not in test_directory and TEST_DIRECTORY in TEST_APP_DIRECTORY_TEMPLATE:
        phantom_client.exec_command(commands)


@contextmanager
def upload_app_files(phantom_version, phantom_client, local_app_path, app_name):
    remote_path = TEST_APP_DIRECTORY_TEMPLATE.format(app_name=app_name + RANDOM_STRING)
    make_folder(phantom_version, phantom_client, app_name, remote_path)

    logging.info(f"Uploading files on {phantom_version}")
    with SCPClient(phantom_client.get_transport()) as scp:
        scp.put(local_app_path, recursive=True, remote_path=remote_path)

    yield os.path.join(remote_path, os.path.basename(local_app_path))

    delete_folder(phantom_client, remote_path)


@backoff.on_exception(backoff.expo, socket.error, max_tries=3)
def run_compile(app_name, local_app_path):
    results = {}

    for version, host in ALL_HOSTS.items():
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            logging.info(f"Connecting to host {version}: {host}")
            client.connect(
                hostname=host, username=PHANTOM_SSH_USER, password=PHANTOM_PASSWORD, port=22
            )
            with upload_app_files(version, client, local_app_path, app_name) as test_dir:
                results[version] = compile_app(version, client, test_dir)
        finally:
            client.close()

    return results
