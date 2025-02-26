import ast
import importlib.util
import sys
from pathlib import Path
import json
import math


class TestCoverageError(Exception):
    
    def __init__(self, percent: int, actions_missing_coverage: Iterable[str]) -> None:
        self.percent = percent
        self.actions_missing_coverage = sorted(actions_missing_coverage)
        
    def __str__(self) -> str:
         action_str = "\n".join(f"    - {action}" for action in self.actions_missing_coverage)
        return f"Only {self.percent}% of actions have tests. These actions appear to be missing tests: {action_str}"


class SubclassActionFinder(ast.NodeVisitor):
    def __init__(self):
        self.subclasses = {}

    def visit_ClassDef(self, node):
        # Check if the class is a subclass of BaseAppSanityTest
        is_subclass = any(
            (isinstance(base, ast.Name) and base.id == "BaseAppSanityTest")
            or (isinstance(base, ast.Attribute) and base.attr == "BaseAppSanityTest")
            for base in node.bases
        )

        if is_subclass:
            self.subclasses[node.name] = None
            # Look for the actions variable in the class body
            for body_item in node.body:
                if isinstance(body_item, ast.Assign):
                    for target in body_item.targets:
                        if isinstance(target, ast.Name) and target.id == "actions":
                            assigned_value = body_item.value
                            if isinstance(assigned_value, (ast.Name, ast.Attribute)):
                                self.subclasses[node.name] = getattr(
                                    assigned_value, "id", None
                                ) or getattr(assigned_value, "attr", None)

        self.generic_visit(node)


def find_subclass_actions(source_code: str) -> dict:
    tree = ast.parse(source_code)
    finder = SubclassActionFinder()
    finder.visit(tree)
    return finder.subclasses


def find_sanity_test_file_names(directory: Path) -> list[str]:
    return [file.name for file in directory.rglob("*sanity_test.py")]


def find_app_json(directory: Path) -> str:
    json_filenames = [file.name for file in directory.rglob("*.json")]
    filtered_json_filenames = [
        fname for fname in json_filenames if "postman_collection" not in fname.lower()
    ]

    if len(filtered_json_filenames) == 0:
        raise ValueError("No JSON file found in top level of app repo! Aborting tests...")

    if len(filtered_json_filenames) > 1:
        raise ValueError(
            f"Multiple JSON files found in top level of app repo: {filtered_json_filenames}."
            "Aborting because there should be exactly one top level JSON file."
        )

    # There's only one json file in the top level, so it must be the app's json
    return filtered_json_filenames[0]


def get_actions_being_tested(apps_test_path: Path) -> set:
    sanity_test_files = find_sanity_test_file_names(apps_test_path)
    action_test_name = set()
    for test_file in sanity_test_files:
        apps_test_path_file = apps_test_path / test_file
        source = apps_test_path_file.read_text()
        subclass_actions = find_subclass_actions(source)
        action_test_name.update(set(actions for actions in subclass_actions.values()))

    module_name = "actions"
    actions_file_path = apps_test_path / "actions.py"

    # Load the module
    spec = importlib.util.spec_from_file_location(module_name, actions_file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    actions_tested = set()
    for action in action_test_name:
        action_list = getattr(module, action)
        actions_tested.update(
            {test["action_name"] for test in action_list if "action_name" in test}
        )

    return actions_tested


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
    apps_test_path = Path(directory) / "app-tests/suite/apps" / app_name
    actions_tested = get_actions_being_tested(apps_test_path)
    app_repo_path = Path(directory) / app_name
    app_actions = actions_in_app(app_repo_path)
    actions_not_tested = app_actions - actions_tested
    if actions_not_tested:
        test_coverage = math.ceil(len(actions_not_tested) / len(app_actions) * 100)
        raise TestCoverageError(test_coverage, actions_not_tested)
    else:
        print("Test coverage is 100%. All actions are being tested")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("app_name")
    parser.add_argument("--dir-location", required=True)

    args = parser.parse_args()
    determine_coverage(args.app_name, args.dir_location)
