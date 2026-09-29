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


def manifests(checkout: Path, output: Path) -> dict[str, dict]:
    """Read BaseConnector JSON or generate the canonical SDK manifest."""
    if not checkout.is_dir():
        raise ValueError(f"Connector checkout does not exist: {checkout}")

    result = {}
    projects = sorted(
        path.parent
        for path in checkout.rglob("uv.lock")
        if ".venv" not in path.parts and ".git" not in path.parts
    )
    app_jsons = []
    for path in sorted(checkout.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "actions" in data and "configuration" in data:
            app_jsons.append((path, data))

    sources = [*projects, *(path for path, _ in app_jsons)]
    if len(sources) != 1:
        names = ", ".join(str(path.relative_to(checkout)) for path in sources)
        raise ValueError(
            f"Expected exactly one connector manifest or SDK project in {checkout}; "
            f"found {len(sources)} ({names})"
        )

    if projects:
        project = projects[0]
        manifest_path = output / "sdk.json"
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
        add_manifest(result, json.loads(manifest_path.read_text()), project)
    else:
        path, data = app_jsons[0]
        add_manifest(result, data, path)
    return result


def add_manifest(result: dict, data: dict, source: Path) -> None:
    appid = data.get("appid")
    if not isinstance(appid, str) or not appid:
        raise ValueError(f"Manifest {source} has no appid")
    if appid in result:
        raise ValueError(f"Duplicate appid {appid} in {source}")
    if not isinstance(data.get("configuration"), dict) or not isinstance(data.get("actions"), list):
        raise ValueError(f"Invalid contract in {source}")
    result[appid] = data


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


def compare_outputs(before: dict, after: dict, context: str) -> list[str]:
    changes = []
    for path in sorted(before.keys() | after.keys()):
        old, new = before.get(path, []), after.get(path, [])
        if len(old) <= 1 and len(new) <= 1:
            changes.extend(
                describe_change(
                    "Action output",
                    path,
                    context,
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
                changes.append(f"**Action output** `{escape(path)}`{context} changed")
    return changes


def describe_change(kind: str, name: str, context: str, before, after) -> list[str]:
    label = f"**{kind}** `{escape(name)}`{context}"
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


def compare_fields(kind: str, before: dict, after: dict, context: str = "") -> list[str]:
    changes = []
    for name in sorted(before.keys() | after.keys()):
        changes.extend(describe_change(kind, name, context, before.get(name), after.get(name)))
    return changes


def compare(before: dict[str, dict], after: dict[str, dict]) -> list[str]:
    changes = []
    for appid in sorted(before.keys() | after.keys()):
        old_app, new_app = before.get(appid), after.get(appid)
        if old_app is None or new_app is None:
            action = "added" if old_app is None else "removed"
            changes.append(
                f"**Connector** `{escape((new_app or old_app).get('name', appid))}` {action}"
            )
            continue
        changes.extend(
            compare_fields("Asset parameter", old_app["configuration"], new_app["configuration"])
        )
        old_actions, new_actions = actions_by_id(old_app), actions_by_id(new_app)
        for identifier in sorted(old_actions.keys() | new_actions.keys()):
            old_action, new_action = old_actions.get(identifier), new_actions.get(identifier)
            if old_action is None or new_action is None:
                action = "added" if old_action is None else "removed"
                changes.append(
                    f"**Action** `{escape((new_action or old_action).get('action', identifier))}` {action}"
                )
                continue
            if old_action.get("action") != new_action.get("action"):
                changes.append(
                    f"**Action** `{escape(old_action.get('action', identifier))}` renamed to "
                    f"`{escape(new_action.get('action', identifier))}`"
                )
            action_name = escape(new_action.get("action", identifier))
            context = f" for action `{action_name}`"
            changes.extend(
                compare_fields(
                    "Action input",
                    old_action.get("parameters", {}),
                    new_action.get("parameters", {}),
                    context,
                )
            )
            changes.extend(
                compare_outputs(outputs_by_path(old_action), outputs_by_path(new_action), context)
            )
    return changes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base", type=Path)
    parser.add_argument("head", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    output = args.report.parent.resolve()
    output.mkdir(parents=True, exist_ok=True)
    base = manifests(args.base.resolve(), output)
    head = manifests(args.head.resolve(), output)
    changes = compare(base, head)
    args.report.write_text(json.dumps({"changes": changes}, indent=2) + "\n")
    print(f"Found {len(changes)} contract changes")


if __name__ == "__main__":
    main()
