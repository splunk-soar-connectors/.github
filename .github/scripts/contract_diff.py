"""Compare the published connector contracts at both ends of a pull request."""

import argparse
import json
import subprocess
from pathlib import Path


PRESENTATION_FIELDS = {
    "description",
    "example_values",
    "order",
    "column_name",
    "column_order",
    "verbose",
}


def manifest(checkout: Path, output: Path, refresh_lock: bool = False) -> dict:
    """Return the first SDK or BaseConnector manifest in a checkout."""
    if not checkout.is_dir():
        raise ValueError(f"Connector checkout does not exist: {checkout}")

    project = next(
        (
            path.parent
            for path in checkout.rglob("uv.lock")
            if ".venv" not in path.parts and ".git" not in path.parts
        ),
        None,
    )
    if project is not None:
        manifest_path = output / "sdk.json"
        if refresh_lock:
            # Release automation can bump pyproject.toml without refreshing uv.lock.
            # The base checkout is temporary, so repair it before the locked run.
            completed = subprocess.run(
                ["uv", "lock"], cwd=project, capture_output=True, text=True
            )
            if completed.returncode:
                raise RuntimeError(
                    f"Could not refresh SDK lockfile for {project}:\n{completed.stderr}"
                )
        command = [
            "uv",
            "run",
            "--locked",
            "--no-dev",
            "soarapps",
            "manifests",
            "create",
            str(manifest_path),
            str(project),
        ]
        completed = subprocess.run(command, cwd=project, capture_output=True, text=True)
        if completed.returncode:
            raise RuntimeError(
                f"Could not generate SDK manifest for {project}:\n{completed.stderr}"
            )
        return validate_manifest(json.loads(manifest_path.read_text()), project)

    for path in sorted(checkout.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "actions" in data and "configuration" in data:
            return validate_manifest(data, path)

    raise ValueError(f"No connector manifest or SDK project found in {checkout}")


def validate_manifest(data: dict, source: Path) -> dict:
    appid = data.get("appid")
    if not isinstance(appid, str) or not appid:
        raise ValueError(f"Manifest {source} has no appid")
    if not isinstance(data.get("configuration"), dict) or not isinstance(data.get("actions"), list):
        raise ValueError(f"Invalid contract in {source}")
    return data


def contract_fields(fields: dict) -> dict:
    return {key: value for key, value in fields.items() if key not in PRESENTATION_FIELDS}


def actions_by_id(manifest: dict) -> dict:
    actions = {}
    for action in manifest["actions"]:
        identifier = action.get("identifier")
        if not identifier or identifier in actions:
            raise ValueError(f"Missing or duplicate action identifier: {identifier}")
        actions[identifier] = action
    return actions


def outputs_by_path(action: dict) -> dict:
    outputs = {}
    for output in action.get("output", []):
        path = output.get("data_path")
        if not path:
            raise ValueError("Missing output data_path")
        outputs.setdefault(path, []).append(output)
    return outputs


def output_name(path: str) -> str:
    return path.removeprefix("action_result.data.*.")


def compare_outputs(before: dict, after: dict) -> list[str]:
    changes = []
    for path in sorted(before.keys() | after.keys()):
        old, new = before.get(path, []), after.get(path, [])
        if len(old) <= 1 and len(new) <= 1:
            changes.extend(
                describe_change(
                    "Output",
                    output_name(path),
                    old[0] if old else None,
                    new[0] if new else None,
                )
            )
        else:
            normalized_old = sorted(
                json.dumps(contract_fields(item), sort_keys=True) for item in old
            )
            normalized_new = sorted(
                json.dumps(contract_fields(item), sort_keys=True) for item in new
            )
            if normalized_old != normalized_new:
                changes.append(f"**Output** `{escape(output_name(path))}` changed")
    return changes


def describe_change(kind: str, name: str, before, after) -> list[str]:
    label = (f"**{kind}** " if kind else "") + f"`{escape(name)}`"
    if before is None:
        return [f"{label} added"]
    if after is None:
        return [f"{label} removed"]
    old = contract_fields(before)
    new = contract_fields(after)
    changes = []
    missing = object()
    for key in sorted(old.keys() | new.keys()):
        if old.get(key, missing) == new.get(key, missing):
            continue
        if key == "data_type" and key in old and key in new:
            changes.append(
                f"{label} changed from {escape(str(old[key]))} to {escape(str(new[key]))} type"
            )
        elif key == "required" and key in old and key in new:
            changes.append(f"{label} changed required from {old[key]} to {new[key]}")
        else:
            changes.append(f"{label} changed `{escape(key)}`")
    return changes


def escape(value: str) -> str:
    return (
        value.replace("`", "\u2032")
        .replace("@", "\uff20")
        .replace("<", "&lt;")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def compare_fields(kind: str, before: dict, after: dict) -> list[str]:
    changes = []
    for name in sorted(before.keys() | after.keys()):
        changes.extend(describe_change(kind, name, before.get(name), after.get(name)))
    return changes


def compare(before: dict, after: dict) -> list[dict]:
    sections = []
    if before["appid"] != after["appid"]:
        return [
            {
                "heading": "Changes to connector:",
                "items": [
                    f"`{escape(before.get('name', before['appid']))}` removed",
                    f"`{escape(after.get('name', after['appid']))}` added",
                ],
            }
        ]
    asset_changes = compare_fields("", before["configuration"], after["configuration"])
    if asset_changes:
        sections.append({"heading": "Changes to asset parameters:", "items": asset_changes})
    old_actions, new_actions = actions_by_id(before), actions_by_id(after)
    for identifier in sorted(old_actions.keys() | new_actions.keys()):
        old_action, new_action = old_actions.get(identifier), new_actions.get(identifier)
        action_name = escape((new_action or old_action).get("action", identifier))
        action_changes = []
        if old_action is None or new_action is None:
            action = "added" if old_action is None else "removed"
            action_changes.append(f"Action {action}")
        else:
            if old_action.get("action") != new_action.get("action"):
                action_changes.append(
                    f"Action renamed from `{escape(old_action.get('action', identifier))}`"
                )
            action_changes.extend(
                compare_fields(
                    "Input", old_action.get("parameters", {}), new_action.get("parameters", {})
                )
            )
            action_changes.extend(
                compare_outputs(outputs_by_path(old_action), outputs_by_path(new_action))
            )
        if action_changes:
            sections.append(
                {"heading": f"Changes to action `{action_name}`:", "items": action_changes}
            )
    return sections


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base", type=Path)
    parser.add_argument("head", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    output = args.report.parent.resolve()
    output.mkdir(parents=True, exist_ok=True)
    base = manifest(args.base.resolve(), output, refresh_lock=True)
    head = manifest(args.head.resolve(), output)
    changes = compare(base, head)
    args.report.write_text(json.dumps({"changes": changes}, indent=2) + "\n")
    print(f"Found {sum(len(section['items']) for section in changes)} contract changes")


if __name__ == "__main__":
    main()
