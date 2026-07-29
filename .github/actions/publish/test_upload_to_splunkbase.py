import importlib.util
import io
import json
from pathlib import Path
import tarfile

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


def test_release_notes_can_be_read_from_queued_tarball(tmp_path):
    tarball = tmp_path / "connector.tgz"
    release_notes = b"**Unreleased**\n\n* Fixed upload handling.\n"
    info = tarfile.TarInfo("connector/release_notes/2.3.4.md")
    info.size = len(release_notes)
    with tarfile.open(tarball, "w:gz") as archive:
        archive.addfile(info, io.BytesIO(release_notes))

    assert MODULE.get_release_notes_from_tarball(tarball, "2.3.4") == ("\n* Fixed upload handling.")


def test_structured_rate_limit_result_is_written(tmp_path, monkeypatch):
    result_path = tmp_path / "publish-result.json"
    monkeypatch.setattr(MODULE, "PUBLISH_RESULT_PATH", str(result_path))
    error = MODULE.SplunkbaseRateLimited(
        "rate limited",
        status_code=429,
        retry_after="300",
        request_id="request-123",
    )

    code = MODULE._record_upload_error("rate_limited", error)

    assert code == 10
    assert json.loads(result_path.read_text()) == {
        "message": "rate limited",
        "request_id": "request-123",
        "retry_after": "300",
        "status": "rate_limited",
        "status_code": 429,
    }
