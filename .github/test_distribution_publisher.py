import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
from unittest.mock import patch
from urllib.error import HTTPError

import pytest


SPEC = importlib.util.spec_from_file_location(
    "check_publisher",
    Path(__file__).parent / "actions/trigger-connector-distribution/check_publisher.py",
)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


def bundle(documents):
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w:gz") as archive:
        for name, document in documents:
            body = json.dumps(document).encode() if not isinstance(document, bytes) else document
            member = tarfile.TarInfo(name)
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
    return content.getvalue()


def run_check(monkeypatch, archive, sha256=None):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_publisher.py",
            "--archive-url",
            "https://github.com/example/release.tgz",
            "--sha256",
            sha256 or hashlib.sha256(archive).hexdigest(),
        ],
    )
    with patch.object(publisher, "urlopen", return_value=io.BytesIO(archive)) as download:
        result = publisher.main()
    download.assert_called_once_with("https://github.com/example/release.tgz", timeout=60)
    return result


@pytest.mark.parametrize("name", ["app/app.json", "app/connector.json", "connector.json"])
@pytest.mark.parametrize(
    "declared,eligible",
    [
        ("Splunk", True),
        ("Splunk Community", False),
        ("Partner", False),
        ("splunk", False),
        ("SPLUNK", False),
        ("Splunk ", False),
        ("Splunk Inc.", False),
        ("", False),
        (None, False),
        (["Splunk"], False),
    ],
)
def test_only_exact_splunk_publisher_is_eligible(monkeypatch, capsys, name, declared, eligible):
    manifest = {"appid": "test-app-id", "app_version": "1.2.3"}
    if declared is not None:
        manifest["publisher"] = declared
    archive = bundle([(name, manifest), ("app/data/example.json", {"publisher": "Splunk"})])
    assert run_check(monkeypatch, archive) == 0
    assert capsys.readouterr().out.strip() == str(eligible).lower()


@pytest.mark.parametrize(
    "documents",
    [
        [],
        [("app/app.json", b"not JSON")],
        [("app/app.json", ["Splunk"])],
        [
            ("a/a.json", {"appid": "a", "app_version": "1", "publisher": "Splunk Community"}),
            ("b/nested/connector.json", {"appid": "b", "app_version": "1", "publisher": "Splunk"}),
        ],
    ],
)
def test_missing_invalid_or_ambiguous_metadata_fails_closed(monkeypatch, capsys, documents):
    assert run_check(monkeypatch, bundle(documents)) == 1
    assert "Expected exactly one connector manifest" in capsys.readouterr().err


@pytest.mark.parametrize("extra", [b'"note":"invalid \xff utf8"', b'"number":' + b"9" * 5000])
def test_unrelated_fields_cannot_hide_a_second_connector_manifest(monkeypatch, capsys, extra):
    community = b'{"appid":"a","app_version":"1","publisher":"Splunk Community",' + extra + b"}"
    archive = bundle(
        [
            ("a/a.json", community),
            ("b/b.json", {"appid": "b", "app_version": "1", "publisher": "Splunk"}),
        ]
    )
    assert run_check(monkeypatch, archive) == 1
    assert "Expected exactly one connector manifest" in capsys.readouterr().err


def test_checksum_mismatch_cannot_approve_a_splunk_label(monkeypatch, capsys):
    archive = bundle(
        [("app/app.json", {"appid": "app", "app_version": "1", "publisher": "Splunk"})]
    )
    assert run_check(monkeypatch, archive, "0" * 64) == 1
    assert "SHA-256 mismatch" in capsys.readouterr().err


def test_corrupt_archive_fails_closed(monkeypatch, capsys):
    assert run_check(monkeypatch, b"not an archive") == 1
    assert "Invalid connector" in capsys.readouterr().err


@pytest.mark.parametrize("limit", ["MAX_ARCHIVE_BYTES", "MAX_EXPANDED_BYTES"])
def test_archive_limits_fail_closed(monkeypatch, capsys, limit):
    monkeypatch.setattr(publisher, limit, 4)
    assert run_check(monkeypatch, bundle([])) == 1
    assert "limit" in capsys.readouterr().err


def test_download_errors_do_not_expose_urls(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_publisher.py",
            "--archive-url",
            "https://github.com/archive?secret=hidden",
            "--sha256",
            "0" * 64,
        ],
    )
    with patch.object(
        publisher, "urlopen", side_effect=HTTPError("secret=hidden", 403, "Forbidden", {}, None)
    ):
        assert publisher.main() == 1
    assert "secret=hidden" not in capsys.readouterr().err
