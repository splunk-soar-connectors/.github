import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("drain_splunkbase_publish_queue.py")
SPEC = importlib.util.spec_from_file_location("drain_splunkbase_publish_queue", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_retry_after_supports_seconds_and_http_dates():
    now = MODULE.parse_datetime("2026-07-29T12:00:00Z")

    assert MODULE.format_datetime(MODULE.retry_after_datetime("300", now)) == (
        "2026-07-29T12:05:00Z"
    )
    assert (
        MODULE.format_datetime(MODULE.retry_after_datetime("Wed, 29 Jul 2026 12:07:00 GMT", now))
        == "2026-07-29T12:07:00Z"
    )


def test_simulation_deduplicates_and_respects_hourly_budget(tmp_path, capsys):
    queue = [
        {
            "repository": f"splunk-soar-connectors/repo-{index:02}",
            "candidate_version": "1.0.0",
        }
        for index in range(13)
    ]
    queue.append(queue[0])
    queue_file = tmp_path / "queue.json"
    queue_file.write_text(json.dumps(queue))
    args = type(
        "Args",
        (),
        {
            "queue_file": str(queue_file),
            "now": "2026-07-29T12:00:00Z",
            "publisher_alias": "soar-connectors-default",
        },
    )

    assert MODULE.simulate(args) == 0

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 13
    assert lines[0].endswith("2026-07-29T12:00:00Z")
    assert lines[11].endswith("2026-07-29T12:55:00Z")
    assert lines[12].endswith("2026-07-29T13:00:00Z")
