"""
Installs an app tarball on a target Phantom instance over REST.
"""

import argparse
import logging
import socket
import sys
import os
import tempfile
from json import JSONDecodeError

import boto3
from contextlib import contextmanager

from requests.exceptions import HTTPError

from utils.api import ApiSession


NRI_PORT = 9999


def parse_args():
    help_str = " ".join(line.strip() for line in __doc__.splitlines())
    parser = argparse.ArgumentParser(description=help_str)
    parser.add_argument("tarball_path", help="Path to the app tarball to install")
    parser.add_argument("phantom_ip", help="IP of the target phantom instance")
    parser.add_argument("phantom_username", help="User for the target phantom instance")
    parser.add_argument("phantom_password", help="User password")

    return parser.parse_args()


def _is_port_in_use(host, port):
    """
    True if the port is in use on the host.

    Source: https://stackoverflow.com/a/52872579
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex((host, port)) == 0


@contextmanager
def _open_phantom_session(phantom_instance_ip, phantom_username, phantom_password):
    session = ApiSession(f"https://{phantom_instance_ip}")
    login_url = f"{session.base_url}/login"
    try:
        session.get(login_url, verify=False)

        login_body = {
            "username": phantom_username,
            "password": phantom_password,
            "csrfmiddlewaretoken": session.cookies["csrftoken"],
        }
        login_headers = {"referer": login_url}
        session.post(login_url, verify=False, data=login_body, headers=login_headers)
        session.headers.update(
            {
                "cookie": "csrftoken={};sessionid={}".format(
                    session.cookies["csrftoken"], session.cookies["sessionid"]
                )
            }
        )

        yield session
    finally:
        session.close()


def download_tarball(tarball_link):
    s3 = boto3.resource("s3")
    bucket, key = tarball_link.replace("s3://", "").split("/", 1)
    app_repo_name = key.split("/")[0]
    tarball_name = key.split("/")[1]
    dest = os.path.join(tempfile.mkdtemp(prefix=f"app_build_{app_repo_name}"), tarball_name)
    logging.info(f"Downloading file {tarball_link} to {dest}")
    s3.Bucket(bucket).download_file(key, dest)
    return dest


def main(args):
    try:
        if _is_port_in_use(args.phantom_ip, NRI_PORT):
            phantom_ip = f"{args.phantom_ip}:{NRI_PORT}"
        else:
            phantom_ip = args.phantom_ip

        logging.info("Installing %s on instance %s", args.tarball_path, phantom_ip)
        if os.path.isfile(args.tarball_path):
            tarball = args.tarball_path
        else:
            logging.error(f"{args.tarball_path} not in direcrory")

        with (
            _open_phantom_session(
                phantom_ip, args.phantom_username, args.phantom_password
            ) as session,
            open(tarball, "rb") as tarball,
        ):
            install_url = f"{session.base_url}/app_install"
            resp = session.post(
                install_url,
                verify=False,
                data={"csrfmiddlewaretoken": session.cookies["csrftoken"]},
                files={"app": tarball},
                headers={"referer": install_url},
            )

            logging.info("Install succeeded with response: %s", resp.json())
            return 0
    except HTTPError as ex:
        try:
            logging.info("Install failed with response: %s", ex.response.json())
        except JSONDecodeError:
            logging.info("Install failed with response: %s", ex.response.text)
        return 1


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    sys.exit(main(parse_args()))
