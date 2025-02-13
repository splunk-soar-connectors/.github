import json
import sys
import os
from clize import run  # @UnresolvedImport

credentials_file = ".app_rel_creds"


def _load_file_json(complete_filepath):
    try:
        with open(complete_filepath) as data_file:
            return_dict = json.load(data_file)
            print(f"success loading file {complete_filepath}")
        return return_dict
    except Exception:
        print(f"error loading file {complete_filepath}, {sys.exc_info()}")
        return {}


def _write_file_json(complete_filepath, output_dict):
    try:
        with open(complete_filepath, "w") as data_file:
            json.dump(output_dict, data_file, indent=4)
            print(f"success writing file {complete_filepath}")
        os.chmod(complete_filepath, 0o600)
        return True
    except Exception:
        print(f"error writing file {complete_filepath}, {sys.exc_info()}")
        return False


def get_credential(credname=None, key=None):
    """return either credname dict for all values in that credname or return a specific value when credname and key are specified, returns NoneType if not found or error.

    :param credname: Top level credential storage name.
    :param key: Key value to get under credential
    """

    if not os.path.exists(credentials_file):
        return None
    if not credname:
        print("Error: Must supply credential top level name")
        return None
    else:
        if not key:
            return _load_file_json(complete_filepath=credentials_file).get(credname)
        else:
            return _load_file_json(complete_filepath=credentials_file).get(credname, {}).get(key)
    return None


def add_credential(credname=None, key=None, value=None, init_file=False):
    """allows multiple key/value pairs to be stored within a single credential name.

    :param credname: Top level credential storage name.
    :param key: Key value to store under credential
    :param value: Value to store in the above key.
    :param init_file: Initialize the credentials file for the first time.
    """
    # e.g. python2.7 ./credential_helper.py add-credential derpc1 keyderp1 keyval1 True
    if not credname:
        print("Error: Must supply credential top level name")
        return False
    #
    if init_file:
        credentials = {}
    else:
        credentials = _load_file_json(complete_filepath=credentials_file)
        if not credentials:
            print("Error, seems that no credentials file exists. Try using init-file True.")
            return False
    if credname in credentials.keys():
        credentials[credname][key] = value
    else:
        credentials[credname] = {key: value}
    #
    _write_file_json(complete_filepath=credentials_file, output_dict=credentials)
    return True


def delete_credential(credname=None, key=None):
    """allows deletion of entire credname top level or individual key/value within credname

    :param credname: Top level credential storage name. (Can be deleted at this top level if no key is specified)
    :param key: Key value to delete under credential
    """
    if not credname:
        print("Error: Must supply credential top level name")
        return False
    #
    credentials = _load_file_json(complete_filepath=credentials_file)
    #
    if key:
        if credentials.get(credname, {}).get(key):
            del credentials[credname][key]
        else:
            print(f"Error: Credname: {credname} - Key: {key} - not found.")
            return False
    else:
        if credentials.get(credname):
            del credentials[credname]
        else:
            print(f"Error: Credname: {credname} - not found.")
            return False
    #
    _write_file_json(complete_filepath=credentials_file, output_dict=credentials)
    return True


if __name__ == "__main__":
    run(add_credential, delete_credential, get_credential)
