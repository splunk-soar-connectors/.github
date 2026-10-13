from copy import deepcopy
import io
import tarfile
from unittest.mock import Mock

import pytest

from .api.splunkbase import SplunkbaseAmbiguousUpload, SplunkbaseValidationFailed
from .draft_publish import advance_publication, package_metadata


MANIFEST = {
    "appid": "guid",
    "app_version": "1.2.3",
    "name": "Example",
    "description": "An example SOAR connector for testing publication",
    "publisher": "Splunk",
}


def artifact(tmp_path, files):
    path = tmp_path / "example-1.2.3.tgz"
    with tarfile.open(path, "w:gz") as archive:
        for name, body in files.items():
            data = body.encode()
            member = tarfile.TarInfo("example/" + name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return path


def test_tab_sources_and_omitted_metadata(tmp_path):
    path = artifact(
        tmp_path,
        {
            "manual_readme_content.md": "## Details\nDetails text\n## Installation\nInstall text\n## Contact\nContact text",
            "installation.md": "Dedicated installation",
        },
    )
    metadata = package_metadata(
        path, MANIFEST, "splunk-soar-connectors/example", "license", "https://example.test/license"
    )
    assert metadata["documentation_source"] == "Details text"
    assert metadata["installation_source"] == "Dedicated installation"
    assert metadata["contact_notes_source"] == "Contact text"
    assert "troubleshooting_source" not in metadata
    assert "contact_email" not in metadata
    assert metadata["support"] == "splunk"


def test_sdk_readme_is_markdown_and_missing_content_is_omitted(tmp_path):
    path = artifact(
        tmp_path, {"README.md": "# SDK connector\n\n**Documentation**", "contact.md": ""}
    )
    metadata = package_metadata(
        path,
        {**MANIFEST, "publisher": "Partner"},
        "splunk-soar-connectors/example",
        "license",
        "url",
    )
    assert metadata["documentation_source"] == "# SDK connector\n\n**Documentation**"
    assert "contact_notes_source" not in metadata
    assert "support" not in metadata


def test_oversized_documentation_requires_dedicated_details(tmp_path):
    path = artifact(tmp_path, {"README.md": "a" * 20001})
    with pytest.raises(SplunkbaseValidationFailed, match=r"details\.md"):
        package_metadata(path, MANIFEST, "splunk-soar-connectors/example", "license", "url")


class Client:
    def __init__(self, draft=True):
        self.draft = draft
        self.pending = False
        self.visible = False
        self.missing = []
        self.calls = []

    def create_draft(self, metadata):
        self.calls.append("create")
        return 11

    def upload_package(self, app_id, path, saved):
        self.calls.append("s3")
        saved("media/uploaded/object.tgz")
        self.calls.append("register")
        return 22

    def get_upload_status(self, package_id):
        if self.pending:
            return {"result": "Pending"}
        return {
            "result": "Pass",
            "details": {"id": 11, "app_id": "guid", "release": {"id": 33, "name": "1.2.3"}},
        }

    def get_app(self, app_id):
        return {
            "id": 11,
            "app_id": "guid",
            "draft": self.draft,
            "releases": [{"id": 33, "name": "1.2.3"}],
        }

    def patch_metadata(self, app_id, metadata):
        self.calls.append("metadata")

    def publish_release(self, app_id, release_id, notes, *, publish):
        self.calls.append("publish_release" if publish else "release_notes")

    def required_fields(self, app_id):
        return self.missing

    def submit_draft(self, app_id):
        self.calls.append("submit")
        self.draft = False

    def public_app(self, app_id):
        if self.visible:
            return {"id": 11, "app_id": "guid", "releases": [{"release_name": "1.2.3"}]}
        return None


def run(client, state, checkpoint=lambda state: None):
    return advance_publication(
        client,
        "example.tgz",
        MANIFEST,
        {"description": MANIFEST["description"]},
        "* Fix",
        state,
        checkpoint,
    )


def test_new_app_validation_does_not_complete_before_submission_and_public_visibility():
    client = Client()
    state = {}
    checkpoints = []
    assert run(client, state, lambda value: checkpoints.append(deepcopy(value))) is False
    assert client.calls == ["create", "s3", "register", "metadata", "release_notes", "submit"]
    assert state["publication_requested"] is True
    assert checkpoints[0]["pending_operation"] == "create_draft"
    assert checkpoints[1]["splunkbase_app_id"] == 11
    assert checkpoints[1]["pending_operation"] == ""
    assert any(
        value.get("pending_operation") == "register_package" and value.get("s3_key")
        for value in checkpoints
    )
    assert any(value.get("pending_operation") == "submit_draft" for value in checkpoints)
    client.visible = True
    assert run(client, state) is True
    assert client.calls.count("s3") == client.calls.count("submit") == 1


def test_existing_listing_publishes_release_without_submitting_app():
    client = Client(draft=False)
    client.visible = True
    assert run(client, {"splunkbase_app_id": 11}) is True
    assert client.calls == ["s3", "register", "metadata", "publish_release"]


def test_pending_validation_resumes_without_upload():
    client = Client()
    client.pending = True
    state = {}
    assert run(client, state) is False
    assert client.calls == ["create", "s3", "register"]
    client.pending = False
    run(client, state)
    assert client.calls.count("register") == 1


@pytest.mark.parametrize("operation", ["create_draft", "register_package"])
def test_unknown_non_idempotent_mutation_is_held(operation):
    client = Client()
    with pytest.raises(SplunkbaseValidationFailed, match="reconcile"):
        run(client, {"pending_operation": operation})
    assert client.calls == []


def test_failed_durable_checkpoint_prevents_mutation():
    client = Client()
    with pytest.raises(RuntimeError, match="storage"):
        run(client, {}, lambda state: (_ for _ in ()).throw(RuntimeError("storage failed")))
    assert client.calls == []


def test_unknown_registration_keeps_s3_key_and_blocks_duplicate():
    client = Client()

    def upload(app_id, path, saved):
        saved("media/uploaded/object.tgz")
        raise SplunkbaseAmbiguousUpload("unknown registration")

    client.upload_package = upload
    state = {"splunkbase_app_id": 11}
    with pytest.raises(SplunkbaseAmbiguousUpload):
        run(client, state)
    assert state["pending_operation"] == "register_package"
    with pytest.raises(SplunkbaseValidationFailed, match="reconcile"):
        run(client, state)


def test_rejected_registration_can_resume_the_existing_s3_object():
    client = Client()
    client.register_package = Mock(return_value=22)
    state = {"splunkbase_app_id": 11, "s3_key": "media/uploaded/object.tgz"}
    run(client, state)
    client.register_package.assert_called_once_with(11, "example.tgz", "media/uploaded/object.tgz")
    assert "s3" not in client.calls


def test_unknown_submission_reconciles_success_before_any_resubmission():
    client = Client(draft=False)
    state = {"splunkbase_app_id": 11, "release_id": 33, "pending_operation": "submit_draft"}
    run(client, state)
    assert "submit" not in client.calls


def test_unknown_submission_of_a_still_draft_app_requires_reconciliation():
    client = Client()
    with pytest.raises(SplunkbaseValidationFailed, match="submission outcome"):
        run(
            client, {"splunkbase_app_id": 11, "release_id": 33, "pending_operation": "submit_draft"}
        )
    assert client.calls == []


def test_missing_metadata_prevents_submission():
    client = Client()
    client.missing = ["categories", "version_compatibility"]
    with pytest.raises(SplunkbaseValidationFailed, match="categories, version_compatibility"):
        run(client, {"splunkbase_app_id": 11, "release_id": 33})
    assert "submit" not in client.calls


def test_wrong_validated_version_cannot_publish():
    client = Client()
    client.get_upload_status = lambda package_id: {
        "result": "Pass",
        "details": {"id": 11, "app_id": "guid", "release": {"id": 33, "name": "9.9.9"}},
    }
    with pytest.raises(SplunkbaseValidationFailed, match="does not match"):
        run(client, {"splunkbase_app_id": 11, "package_id": 22})
    assert client.calls == []


def test_editor_release_shape_from_openapi_does_not_require_numeric_id():
    client = Client(draft=False)
    client.visible = True
    client.get_app = lambda app_id: {
        "id": 11,
        "app_id": "guid",
        "draft": False,
        "releases": [{"release_name": "1.2.3", "public": False}],
    }
    assert run(client, {"splunkbase_app_id": 11, "package_id": 22}) is True
    assert client.calls == ["metadata", "publish_release"]


def test_existing_contact_and_support_are_preserved():
    client = Client(draft=False)
    client.get_app = lambda app_id: {
        "id": 11,
        "app_id": "guid",
        "draft": False,
        "support": "developer",
        "contact_email": "owner@example.test",
        "releases": [{"release_name": "1.2.3"}],
    }
    client.patch_metadata = Mock()
    advance_publication(
        client,
        "example.tgz",
        MANIFEST,
        {"support": "splunk", "contact_email": "new@example.test", "documentation_source": "Docs"},
        "* Fix",
        {"splunkbase_app_id": 11, "release_id": 33},
        lambda value: None,
    )
    client.patch_metadata.assert_called_once_with(11, {"documentation_source": "Docs"})
