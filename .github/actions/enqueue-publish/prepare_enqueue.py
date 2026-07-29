"""Prepare immutable Splunkbase queue metadata from a connector package."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tarfile


def get_app_json(tarball: Path) -> dict:
    with tarfile.open(tarball, "r:*") as archive:
        matches = [
            name
            for name in archive.getnames()
            if name.endswith(".json")
            and name.count("/") == 1
            and "postman_collection" not in name.lower()
        ]
        if len(matches) != 1:
            raise ValueError(f"Expected one top-level app JSON, found: {matches}")
        app_file = archive.extractfile(matches[0])
        if app_file is None:
            raise ValueError(f"Could not read {matches[0]} from {tarball}")
        return json.load(app_file)


def safe_asset_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")


def write_output(name: str, value: str) -> None:
    output_path = os.getenv("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a") as output:
            output.write(f"{name}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--publisher-alias", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--payload-path", type=Path, required=True)
    args = parser.parse_args()

    app_json = get_app_json(args.artifact)
    version = str(app_json["app_version"])
    asset_name = (
        f"{safe_asset_part(args.publisher_alias)}--"
        f"{safe_asset_part(args.repository)}--"
        f"{safe_asset_part(version)}--"
        f"{safe_asset_part(args.commit_sha[:12])}.tgz"
    )
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = {
        "event_type": "splunkbase-publish-enqueue",
        "client_payload": {
            "schema_version": 1,
            "publisher_alias": args.publisher_alias,
            "repository": args.repository,
            "run_id": args.run_id,
            "run_attempt": args.run_attempt,
            "commit_sha": args.commit_sha,
            "artifact_name": "app-tar",
            "asset_name": asset_name,
            "release_tag": args.release_tag,
            "candidate_version": version,
            "appid": str(app_json["appid"]),
            "app_name": str(app_json["name"]),
            "enqueued_at": now,
            "not_before": now,
        },
    }
    args.payload_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    write_output("asset_name", asset_name)
    write_output("candidate_version", version)
    write_output("appid", str(app_json["appid"]))
    write_output("dedupe_key", f"{args.publisher_alias}:{args.repository}:{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
