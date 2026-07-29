import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("enqueue_splunkbase_publish.py")
SPEC = importlib.util.spec_from_file_location("enqueue_splunkbase_publish", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_nested_repository_dispatch_payload_is_unwrapped():
    queue_item = {"repository": "splunk-soar-connectors/example"}

    assert MODULE.unwrap_queue_item({"queue_item": queue_item}) == queue_item


def test_legacy_flat_repository_dispatch_payload_remains_accepted():
    queue_item = {"repository": "splunk-soar-connectors/example"}

    assert MODULE.unwrap_queue_item(queue_item) == queue_item


def test_non_object_queue_item_is_rejected():
    with pytest.raises(ValueError, match="must be an object"):
        MODULE.unwrap_queue_item({"queue_item": "invalid"})
