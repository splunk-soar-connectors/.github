""" "
Wrapper for Google Drive API copied from
https://cd.splunkdev.com/phantom-apps/opensource-automated-testing/-/blob/master/upload_test_results.py
"""

import logging
import os
from typing import Optional, TextIO

from utils.phantom_constants import GDRIVE_CLIENT_SECRETS_FILEPATH

from pydrive.auth import GoogleAuth, ServiceAccountCredentials
from pydrive.drive import GoogleDrive


class GoogleDriveApi:
    def __init__(self, key_json_path: Optional[str] = None) -> None:
        if not key_json_path:
            key_json_path = GDRIVE_CLIENT_SECRETS_FILEPATH

        if key_json_path and os.path.exists(key_json_path):
            gauth = GoogleAuth()
            scope = ["https://www.googleapis.com/auth/drive"]
            gauth.credentials = ServiceAccountCredentials.from_json_keyfile_name(
                key_json_path, scope
            )
            self._gdrive_client = GoogleDrive(gauth)
        else:
            logging.warn('Path "%s" does not exist.', key_json_path)
            self._gdrive_client = None

    def upload(self, results_file: TextIO, file_name: Optional[str] = None) -> Optional[str]:
        if not self._gdrive_client:
            print("GDrive client not authenticated. Skipping upload.")
            return None

        if not file_name:
            file_name = os.path.basename(results_file.name)

        file_drive = self._gdrive_client.CreateFile({"title": file_name})
        file_drive.SetContentString(results_file.read())
        file_drive.Upload()

        # Insert the permission.
        file_drive.InsertPermission({"type": "anyone", "value": "splunk", "role": "reader"})

        return file_drive["alternateLink"]  # Return the sharable link.
