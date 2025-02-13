import base64
import re
import json
import os
import shutil
import tarfile
import functools
import requests
import time

from contextlib import contextmanager, closing

from utils.phantom_constants import APP_JSON_KEYS, APPID_TO_NAME_FILEPATH


def clear_requests_warnings():
    try:
        requests.packages.urllib3.disable_warnings()  # @UndefinedVariable
    except Exception:
        from requests.packages.urllib3.exceptions import InsecureRequestWarning

        requests.packages.urllib3.disable_warnings(InsecureRequestWarning)


def decode_email_attachment(path_to_attachment, desired_destination):
    with open(path_to_attachment) as source_file:
        with open(desired_destination, "w") as destination_file:
            destination_file.write(base64.b64decode(source_file.read()))

    return


_func_caches = {}


def memoize(func=None, ignore_self=False):
    """
    Memoization decorator for functions that might be useful to remember rather than recompute.
    If ignore_self is set to True, the first arg is ignored when determining whether to recompute. Useful for
    memoizing class functions when you want to remember output across multiple instances
    """
    if func is None:
        return functools.partial(memoize, ignore_self=ignore_self)

    func.cache = {}
    _func_caches[func] = func.cache

    @functools.wraps(func)
    def decorator(*args, **kwargs):
        if ignore_self:
            key = str((args[1:], kwargs))
        else:
            key = str((args, kwargs))

        if key not in func.cache:
            func.cache[key] = func(*args, **kwargs)
        return func.cache[key]

    return decorator


def clear_memorization(func):
    """
    Clear the cache for a memoized function
    """
    assert hasattr(func, "cache")
    assert isinstance(func.cache, dict)
    func.cache = {}


def clear_all_memorization():
    """
    Clears the caches of all memoized functions
    """
    for _, cache in _func_caches.items():
        cache.clear()


def sanitize(s):
    return re.sub(r"[-_ ]", "", s).lower()


@contextmanager
def manage_data_file(data_file_name, save=True):
    """
    Any stateful changes to the data file are managed and saved

    :param basestring data_file_name:
    :rtype: NoneType
    """
    with open(data_file_name) as f:
        data = json.loads(f.read())
    yield data

    if save:
        with open(data_file_name, "w") as f:
            f.write(json.dumps(data, indent=4, sort_keys=True) + "\n")


def find_app_json_name(json_filenames):
    """
    Given a list of possible json files and the app repo name, return the name of the file
    that is most likely to be the app repo's main module json
    """
    # Multiple json files. Exclude known JSON filenames and expect only one at the end regardless of name.
    # Other places (e.g. Splunkbase) enforce a single top-level JSON file anyways.
    filtered_json_filenames = []
    for fname in json_filenames:
        # Ignore the postman collection JSON files
        if "postman_collection" in fname.lower():
            continue
        filtered_json_filenames.append(fname)

    if len(filtered_json_filenames) == 0:
        raise ValueError("No JSON file found in top level of app repo! Aborting tests...")

    if len(filtered_json_filenames) > 1:
        raise ValueError(
            f"Multiple JSON files found in top level of app repo: {filtered_json_filenames}."
            "Aborting because there should be exactly one top level JSON file."
        )

    # There's only one json file in the top level, so it must be the app's json
    return filtered_json_filenames[0]


def is_valid_app_json(app_json):
    """
    Determines whether dictionary provided is a phantom app_json
    :param dict|basestring app_json:
    :rtype: bool
    """
    if not isinstance(app_json, dict):
        try:
            with open(app_json) as f:
                app_json = json.load(f)
        except (OSError, ValueError):
            return False

    return all(el in app_json for el in APP_JSON_KEYS)


def app_json_match(left, right):
    """
    Given two different app jsons,
    :param dict left:
    :param dict right:
    :rtype: bool
    """
    return all(left[key] == right[key] for key in APP_JSON_KEYS)


def get_app_json(blob, **kwargs):
    # could be path to a tarball or a directory itself (only two assumptions made if a string)
    if os.path.isdir(blob):
        app_code_dir = blob
        json_files = [f for f in os.listdir(app_code_dir) if f.endswith(".json")]
        app_json_name = find_app_json_name(json_files)

        with open(os.path.join(app_code_dir, app_json_name)) as f:
            return json.load(f)
    elif os.path.isfile(blob):
        tarball = tarfile.open(blob)
        json_files = [
            member.name
            for member in tarball.getmembers()
            if member.name.endswith(".json") and "._" not in member.name
        ]
        assert len(json_files) == 1, f"Found {len(json_files)} JSON files!"
        with closing(tarball.extractfile(json_files[0])) as f:
            return json.load(f)
    else:
        raise ValueError(f"`{blob}` is neither a filepath or a directory path")


def update_app_json(json_file, new_content, separators=(",", ": ")):
    """
    Update the given json_file with the dict given by new_content. Strongly recommended, but not strictly required, that
    new_content be an instance of OrderedDict to avoid unnecessarily jumbling json
    """
    # First, determine indent level of app json
    indent = ""
    with open(json_file) as f:
        for line in f:
            if "appid" in line:
                indent = re.match(r"(\s*)", line).group(0)
                break

    # Now, we update the actual file with the new contents
    with open(json_file, "w") as f:
        json.dump(new_content, f, indent=len(indent), sort_keys=False, separators=separators)


def validate_app_id(appid, app_name):
    """
    Makes sure that an app id is in the correct format and doesn't already map to a different app
    """
    with manage_data_file(APPID_TO_NAME_FILEPATH, save=False) as app_guid_to_name:
        try:
            return app_guid_to_name[appid] == app_name
        except KeyError:
            return bool(
                re.match(
                    r"[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89aAbB][a-f0-9]{3}-[a-f0-9]{12}",
                    appid,
                    flags=re.I,
                )
            )


def create_test_result_response(success, message=None, verbose=None):
    """
    Create a test result response object.
    """
    response = {
        "success": success,
        "message": message,
    }

    if verbose:
        response["verbose"] = verbose

    return response
