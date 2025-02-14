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