import json
import logging
import re
import socket
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Union
import shlex

import backoff
import paramiko
from scp import SCPClient

from utils import find_app_json_name
from utils.version_compat import supports_minimum_version

ANSI_ESCAPE = re.compile(r"(\x1b|\x1B)(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
OUTPUT = re.compile(r"Output\:([^\x1b]*?)Error output\:")

COMPILE_STAGING_DIRECTORY = Path("/home/phantom/.soar-compile")
STAGING_DIRECTORY_PREFIX = "compile-"


def get_app_json_name(local_app_path: Path) -> str:
    local_app_path = Path(local_app_path)
    json_filenames = [path.name for path in local_app_path.glob("*.json")]
    return (
        "manifest.json" if "manifest.json" in json_filenames else find_app_json_name(json_filenames)
    )


def get_min_phantom_version(local_app_path: Path) -> str:
    local_app_path = Path(local_app_path)
    app_json_name = get_app_json_name(local_app_path)

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


def run_remote_command(phantom_client: paramiko.SSHClient, command: str, description: str) -> str:
    """Run a remote command and fail closed when setup or verification fails."""
    logging.info(command)
    _, stdout, stderr = phantom_client.exec_command(command)
    exit_code = int(stdout.channel.recv_exit_status())
    output = "".join(stdout.readlines()).strip()
    if exit_code != 0:
        error = "".join(stderr.readlines()).strip()
        raise RuntimeError(f"{description} failed with exit code {exit_code}: {error}")
    return output


def is_owned_staging_directory(test_directory: Path) -> bool:
    """Return whether the requested cleanup path is one created by this helper."""
    test_directory = Path(test_directory)
    return test_directory.parent == COMPILE_STAGING_DIRECTORY and test_directory.name.startswith(
        STAGING_DIRECTORY_PREFIX
    )


def create_staging_directory(phantom_version: str, phantom_client: paramiko.SSHClient) -> Path:
    staging_template = COMPILE_STAGING_DIRECTORY / f"{STAGING_DIRECTORY_PREFIX}XXXXXXXX"
    command = (
        "set -e; umask 077; "
        f"mkdir -p {shlex.quote(str(COMPILE_STAGING_DIRECTORY))}; "
        f"mktemp -d {shlex.quote(str(staging_template))}"
    )
    logging.info("Creating isolated compile directory on phantom %s", phantom_version)
    staging_directory = Path(
        run_remote_command(phantom_client, command, "create compile staging directory")
    )
    if not is_owned_staging_directory(staging_directory):
        raise RuntimeError(
            f"Remote mktemp returned an unexpected compile directory: {staging_directory}"
        )
    return staging_directory


def delete_folder(phantom_client: paramiko.SSHClient, test_directory: Path) -> None:
    """Remove only an isolated staging directory that this helper owns."""
    test_directory = Path(test_directory)
    if not is_owned_staging_directory(test_directory):
        logging.warning("Refusing to remove unowned compile directory: %s", test_directory)
        return

    command = f"rm -rf -- {shlex.quote(str(test_directory))}"
    logging.info("Deleting isolated compile directory %s", test_directory)
    run_remote_command(phantom_client, command, "remove compile staging directory")


@contextmanager
def upload_app_files(
    phantom_version: str, phantom_client: paramiko.SSHClient, local_app_path: Path, _app_name: str
) -> Iterator[Path]:
    staging_directory = create_staging_directory(phantom_version, phantom_client)
    incoming_directory = staging_directory / ".incoming"
    uploaded_app_directory = incoming_directory / local_app_path.name
    test_directory = staging_directory / "app"
    manifest_filename = get_app_json_name(local_app_path)

    try:
        run_remote_command(
            phantom_client,
            f"mkdir {shlex.quote(str(incoming_directory))}",
            "create incoming compile directory",
        )
        logging.info("Uploading files on %s", phantom_version)
        with SCPClient(phantom_client.get_transport()) as scp:
            scp.put(local_app_path, recursive=True, remote_path=str(incoming_directory))

        promote_command = (
            "set -e; "
            f"test -d {shlex.quote(str(uploaded_app_directory))}; "
            f"mv {shlex.quote(str(uploaded_app_directory))} {shlex.quote(str(test_directory))}; "
            f"test -d {shlex.quote(str(test_directory))}; "
            f"test -f {shlex.quote(str(test_directory / manifest_filename))}"
        )
        run_remote_command(
            phantom_client, promote_command, "promote and verify uploaded compile directory"
        )
        yield test_directory
    finally:
        delete_folder(phantom_client, staging_directory)


@backoff.on_exception(backoff.expo, socket.error, max_tries=3)
def run_compile(
    app_name: str,
    local_app_path: Path,
    current_phantom_ip: str,
    next_phantom_ip: str,
    previous_phantom_ip: str,
    ssh_username: str,
    rest_username: str,
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
            rest_username,
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
            client.connect(hostname=host, username=ssh_username, password=phantom_password, port=22)
            with upload_app_files(version, client, local_app_path, app_name) as test_dir:
                results[version] = compile_app(version, client, test_dir)
        finally:
            client.close()

    return results
