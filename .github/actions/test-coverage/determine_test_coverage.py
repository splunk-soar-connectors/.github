from pathlib import Path
import json
import math
from collections.abc import Iterable


class TestCoverageError(Exception):
    def __init__(self, percent: int, actions_missing_coverage: Iterable[str]) -> None:
        self.percent = percent
        self.actions_missing_coverage = sorted(actions_missing_coverage)

    def __str__(self) -> str:
        action_str = "\n".join(f"    - {action}" for action in self.actions_missing_coverage)
        return f"Only {self.percent}% of actions have tests. These actions appear to be missing tests:\n {action_str}"


def find_app_json(directory: Path) -> str:
    json_filenames = [file.name for file in directory.rglob("*.json")]
    filtered_json_filenames = [
        fname for fname in json_filenames if "postman_collection" not in fname.lower()
    ]

    if len(filtered_json_filenames) == 0:
        raise ValueError("No JSON file found in top level of app repo! Aborting tests...")

    if len(filtered_json_filenames) > 1:
        # special case for SDKfied apps
        if "temp_app.json" in filtered_json_filenames:
            return "temp_app.json"
        raise ValueError(
            f"Multiple JSON files found in top level of app repo: {filtered_json_filenames}."
            "Aborting because there should be exactly one top level JSON file."
        )

    # There's only one json file in the top level, so it must be the apps json
    return filtered_json_filenames[0]


def actions_in_app(app_repo_path: Path) -> set:
    app_json_name = find_app_json(app_repo_path)
    app_json_path = app_repo_path / app_json_name
    app_data = json.loads(app_json_path.read_text())
    return {
        action["action"]
        for action in app_data.get("actions", [])
        if action["action"] not in {"test connectivity", "on poll"}
    }


def determine_coverage(app_name: str, directory: str) -> None:
    json_file = Path(directory) / "app-tests/tested_actions.json"
    json_data = json.loads(json_file.read_text())
    actions_tested = set(json_data)
    app_repo_path = Path(directory) / app_name
    app_actions = actions_in_app(app_repo_path)
    actions_not_tested = app_actions - actions_tested
    if actions_not_tested:
        test_coverage = math.ceil(len(actions_not_tested) / len(app_actions) * 100)
        raise TestCoverageError(100 - test_coverage, actions_not_tested)
    else:
        print("Test coverage is 100%. All actions are being tested")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("app_name")
    parser.add_argument("--dir-location", required=True)

    args = parser.parse_args()
    try:
        determine_coverage(args.app_name, args.dir_location)
    except TestCoverageError as e:
        raise SystemExit(str(e))
