import importlib.util
import io
import json
from pathlib import Path
import tarfile


MODULE_PATH = Path(__file__).with_name("prepare_enqueue.py")
SPEC = importlib.util.spec_from_file_location("prepare_enqueue", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_app_metadata_and_asset_name_are_stable(tmp_path):
    tarball = tmp_path / "connector.tgz"
    manifest = json.dumps(
        {
            "appid": "example-guid",
            "app_version": "2.3.4",
            "name": "Example",
        }
    ).encode()
    info = tarfile.TarInfo("example/example.json")
    info.size = len(manifest)
    with tarfile.open(tarball, "w:gz") as archive:
        archive.addfile(info, io.BytesIO(manifest))

    assert MODULE.get_app_json(tarball)["app_version"] == "2.3.4"
    assert (
        MODULE.safe_asset_part("splunk-soar-connectors/example") == "splunk-soar-connectors-example"
    )


def test_dispatch_payload_nests_queue_metadata_under_one_property():
    queue_item = {
        "repository": "splunk-soar-connectors/example",
        "candidate_version": "2.3.4",
        "appid": "example-guid",
    }

    payload = MODULE.build_dispatch_payload(queue_item)

    assert payload["event_type"] == "splunkbase-publish-enqueue"
    assert payload["client_payload"] == {"queue_item": queue_item}
    assert len(payload["client_payload"]) == 1
