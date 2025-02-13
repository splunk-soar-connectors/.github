import subprocess

import os


def get_command_output(command, shell=False, quiet_stderr=False):
    try:
        if quiet_stderr:
            with open(os.devnull, "w") as f:
                return subprocess.check_output(command, shell=shell, stderr=f).decode()
        else:
            return subprocess.check_output(command, shell=shell).decode()
    except subprocess.CalledProcessError as e:
        print(f"`{e.cmd}` failed with return code `{e.returncode}` and output `{e.output}`")
        raise


def get_command_result(command, shell=False):
    with open(os.devnull, "w") as f:
        return subprocess.call(command, shell=shell, stdout=f, stderr=f)  # want command quiet


def check_running_internal_vpn():
    ping_result = get_command_result(["ping", "-c", "1", "10.1.10.10"])
    if ping_result != 0:
        raise ValueError(
            "Are you sure you are on the internal VPN? You should be on it to run this script..."
        )


def upload_to_drobo(filepath, drobopath):
    """upload file to a directory in the drobo"""
    check_running_internal_vpn()

    drobo_url = "/Volumes/Public"

    if not os.path.exists(drobo_url):
        raise OSError("Mount the Drobo and the 'Public' Volume to your system")
    if not os.path.exists(drobopath):
        raise OSError(f"{drobopath} doesn't exists in the Drobo")
    else:
        if not os.path.isdir(drobopath):
            raise OSError("This is not a directory")
    if not os.path.exists(filepath):
        raise OSError(f"{filepath} doesn't exists")
    else:
        if not os.path.isfile(filepath):
            raise OSError("This is not a file")
    filename = os.path.split(filepath)[1]
    filedrobopath = os.path.join(drobopath, filename)
    cp_result = get_command_result(["cp", filepath, filedrobopath])

    if cp_result != 0:
        print("Unexpected error occurred")
        return None
    else:
        return filedrobopath
