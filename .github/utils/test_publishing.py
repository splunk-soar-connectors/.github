import io
import tarfile

from . import publishing as MODULE


def test_previous_release_version_uses_highest_version_below_candidate():
    releases = [
        {"release_name": "2.0.0"},
        {"release_name": "1.2.3"},
        {"release_name": "1.0.0"},
    ]

    assert MODULE.get_previous_release_version(releases, "2.0.0") == "1.2.3"


def test_previous_release_version_is_empty_for_first_release():
    assert MODULE.get_previous_release_version([], "1.0.0") is None


def test_release_notes_can_be_read_from_queued_tarball(tmp_path):
    tarball = tmp_path / "connector.tgz"
    release_notes = b"**Unreleased**\n\n* Fixed upload handling.\n"
    info = tarfile.TarInfo("connector/release_notes/2.3.4.md")
    info.size = len(release_notes)
    with tarfile.open(tarball, "w:gz") as archive:
        archive.addfile(info, io.BytesIO(release_notes))

    assert MODULE.get_release_notes_from_tarball(tarball, "2.3.4") == ("\n* Fixed upload handling.")
