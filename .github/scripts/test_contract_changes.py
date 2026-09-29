import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).parent


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diff = load_script("contract_diff")
reporter = load_script("report_contract_changes")


def app():
    return {
        "appid": "example-id",
        "name": "Example",
        "configuration": {"tls_verify": {"data_type": "boolean", "required": False}},
        "actions": [
            {
                "identifier": "change_password",
                "action": "change password",
                "parameters": {"temporary password": {"data_type": "string", "required": True}},
                "output": [
                    {"data_path": "action_result.data.*.transaction_id", "data_type": "string"}
                ],
            }
        ],
    }


class ContractDiffTests(unittest.TestCase):
    def test_contract_changes_are_readable(self):
        before = app()
        after = json.loads(json.dumps(before))
        after["configuration"]["new_setting"] = {"data_type": "numeric"}
        after["actions"][0]["parameters"]["temporary password"]["data_type"] = "password"
        after["actions"][0]["output"] = []
        self.assertEqual(
            diff.compare(before, after),
            [
                "**Asset parameter** `new_setting` added",
                "**Action input** `temporary password` for action `change password` changed from string to password type",
                "**Action output** `action_result.data.*.transaction_id` for action `change password` removed",
            ],
        )

    def test_presentation_only_changes_do_not_flag(self):
        before = app()
        after = json.loads(json.dumps(before))
        after["configuration"]["tls_verify"].update({"description": "New help", "order": 1})
        after["actions"][0]["output"][0]["example_values"] = ["sample"]
        self.assertEqual(diff.compare(before, after), [])

    def test_appid_change_reports_connector_replacement(self):
        before = app()
        after = json.loads(json.dumps(before))
        after["appid"] = "new-id"
        self.assertEqual(
            diff.compare(before, after),
            ["**Connector** `Example` removed", "**Connector** `Example` added"],
        )

    def test_duplicate_output_paths_are_compared_without_failing(self):
        before = app()
        duplicate = dict(before["actions"][0]["output"][0])
        before["actions"][0]["output"].append(duplicate)
        after = json.loads(json.dumps(before))
        after["actions"][0]["output"][1]["data_type"] = "numeric"
        self.assertEqual(
            diff.compare(before, after),
            [
                "**Action output** `action_result.data.*.transaction_id` for action `change password` changed"
            ],
        )

    def test_legacy_manifest_discovery_ignores_other_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.json").write_text(json.dumps(app()))
            (root / "test_asset.json").write_text('{"api_key":"secret"}')
            self.assertEqual(diff.manifest(root, root)["name"], "Example")

    def test_manifest_discovery_uses_first_source_or_fails_when_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "No connector manifest"):
                diff.manifest(root, root)

            (root / "first.json").write_text(json.dumps(app()))
            second = app()
            second["appid"] = "second-id"
            (root / "second.json").write_text(json.dumps(second))
            self.assertEqual(diff.manifest(root, root)["appid"], "example-id")

            (root / "sdk" / "first").mkdir(parents=True)
            (root / "sdk" / "first" / "uv.lock").touch()
            (root / "sdk" / "second").mkdir(parents=True)
            (root / "sdk" / "second" / "uv.lock").touch()
            with patch.object(diff.subprocess, "run") as run:
                run.return_value.returncode = 0
                (root / "sdk.json").write_text(json.dumps(second))
                self.assertEqual(diff.manifest(root, root)["appid"], "second-id")
                run.assert_called_once()

    def test_manifest_entry_order_does_not_change_summary(self):
        before = app()
        before["configuration"]["api_key"] = {"data_type": "password"}
        first_action = before["actions"][0]
        first_action["parameters"]["username"] = {"data_type": "string"}
        first_action["output"].append(
            {"data_path": "action_result.data.*.status", "data_type": "string"}
        )
        before["actions"].append(
            {
                "identifier": "lock_account",
                "action": "lock account",
                "parameters": {"username": {"data_type": "string"}},
                "output": [{"data_path": "action_result.data.*.locked", "data_type": "boolean"}],
            }
        )

        reordered = json.loads(json.dumps(before))
        reordered["configuration"] = dict(reversed(list(reordered["configuration"].items())))
        reordered["actions"].reverse()
        changed_action = reordered["actions"][1]
        changed_action["parameters"] = dict(reversed(list(changed_action["parameters"].items())))
        changed_action["output"].reverse()
        self.assertEqual(diff.compare(before, reordered), [])

        changed = json.loads(json.dumps(before))
        changed["configuration"]["api_key"]["required"] = True
        changed["actions"][0]["parameters"]["username"]["required"] = True
        changed["actions"][0]["output"][0]["data_type"] = "numeric"
        reordered_changed = json.loads(json.dumps(changed))
        reordered_changed["configuration"] = dict(
            reversed(list(reordered_changed["configuration"].items()))
        )
        reordered_changed["actions"].reverse()
        reordered_changed["actions"][1]["parameters"] = dict(
            reversed(list(reordered_changed["actions"][1]["parameters"].items()))
        )
        reordered_changed["actions"][1]["output"].reverse()
        expected = diff.compare(before, changed)
        self.assertEqual(len(expected), 3)
        self.assertEqual(diff.compare(before, reordered_changed), expected)


class ContractReportTests(unittest.TestCase):
    def test_unchanged_summary_makes_no_write(self):
        changes = ["**Asset parameter** `tls_verify` added"]
        calls = []

        def fake_api(method, path, payload=None):
            calls.append((method, path))
            if path.endswith("/pulls/5"):
                return {"head": {"sha": "abc"}}
            if "/comments?" in path:
                return [
                    {"id": 7, "user": {"login": reporter.BOT}, "body": reporter.summary(changes)}
                ]
            raise AssertionError((method, path, payload))

        with patch.object(reporter, "api", side_effect=fake_api):
            reporter.report("owner/repo", 5, "abc", changes)
        self.assertEqual([method for method, _ in calls], ["GET", "GET"])

    def test_changed_summary_deletes_and_replaces_comment(self):
        calls = []

        def fake_api(method, path, payload=None):
            calls.append((method, path, payload))
            if path.endswith("/pulls/5"):
                return {"head": {"sha": "abc"}}
            if "/comments?" in path:
                return [
                    {
                        "id": 7,
                        "user": {"login": reporter.BOT},
                        "body": reporter.COMMENT_MARKER + "\nold",
                    }
                ]
            return {}

        with patch.object(reporter, "api", side_effect=fake_api):
            reporter.report("owner/repo", 5, "abc", ["**Asset parameter** `tls_verify` added"])
        self.assertEqual([method for method, _, _ in calls], ["GET", "GET", "DELETE", "POST"])
        self.assertEqual(calls[-1][2]["body"].splitlines()[1], "## ⚠️ Contract changes")

    def test_clean_summary_is_normal_comment(self):
        calls = []

        def fake_api(method, path, payload=None):
            calls.append((method, path, payload))
            if path.endswith("/pulls/5"):
                return {"head": {"sha": "abc"}}
            if "/comments?" in path:
                return []
            return {}

        with patch.object(reporter, "api", side_effect=fake_api):
            reporter.report("owner/repo", 5, "abc", [])
        self.assertEqual([method for method, _, _ in calls], ["GET", "GET", "POST"])
        self.assertIn("## \u2139\ufe0f No contract changes", calls[-1][2]["body"])

    def test_duplicate_bot_summaries_are_replaced_with_one(self):
        calls = []

        def fake_api(method, path, payload=None):
            calls.append((method, path))
            if path.endswith("/pulls/5"):
                return {"head": {"sha": "abc"}}
            if "/comments?" in path:
                return [
                    {
                        "id": identifier,
                        "user": {"login": reporter.BOT},
                        "body": reporter.summary([]),
                    }
                    for identifier in (7, 8)
                ]
            return {}

        with patch.object(reporter, "api", side_effect=fake_api):
            reporter.report("owner/repo", 5, "abc", [])
        self.assertEqual(
            [method for method, _ in calls], ["GET", "GET", "DELETE", "DELETE", "POST"]
        )

    def test_stale_run_does_not_post(self):
        calls = []

        def fake_api(method, path, payload=None):
            calls.append((method, path))
            return {"head": {"sha": "newer"}}

        with patch.object(reporter, "api", side_effect=fake_api):
            reporter.report("owner/repo", 5, "abc", [])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
