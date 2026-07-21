import importlib.util
from pathlib import Path

from packaging.version import parse


MODULE_PATH = Path(__file__).with_name("upload_to_splunkbase.py")
SPEC = importlib.util.spec_from_file_location("upload_to_splunkbase", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_existing_version_is_only_successful_on_rerun():
    version = parse("2.3.4")

    assert not MODULE.is_successful_rerun_of_existing_version(version, version, run_attempt="1")
    assert MODULE.is_successful_rerun_of_existing_version(version, version, run_attempt="2")


def test_rerun_does_not_accept_an_older_candidate():
    assert not MODULE.is_successful_rerun_of_existing_version(
        parse("2.3.3"), parse("2.3.4"), run_attempt="2"
    )
