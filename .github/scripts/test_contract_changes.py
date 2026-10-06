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
                {"heading": "Changes to asset parameters:", "items": ["`new_setting` added"]},
                {
                    "heading": "Changes to action `change password`:",
                    "items": [
                        "**Input** `temporary password` changed from string to password type",
                        "**Output** `transaction_id` removed",
                    ],
                },
            ],
        )
        self.assertEqual(
            reporter.summary(diff.compare(before, after)),
            "<!-- soar-contract-summary -->\n"
            "## ⚠️ Contract changes\n\n"
            "### Changes to asset parameters:\n\n"
            "- `new_setting` added\n\n"
            "### Changes to action `change password`:\n\n"
            "- **Input** `temporary password` changed from string to password type\n"
            "- **Output** `transaction_id` removed",
        )

    def test_presentation_only_changes_do_not_flag(self):
        before = app()
        after = json.loads(json.dumps(before))
        after["configuration"]["tls_verify"].update({"description": "New help", "order": 1})
        after["actions"][0]["output"][0]["example_values"] = ["sample"]
        self.assertEqual(diff.compare(before, after), [])

    def test_sdk_manifest_defaults_and_display_metadata_do_not_flag(self):
        before = app()
        before["configuration"]["tls_verify"].pop("required")
        before["actions"][0]["parameters"]["temporary password"].pop("required")
        after = json.loads(json.dumps(before))
        after["configuration"]["tls_verify"].update({"required": False, "category": "connectivity"})
        after["actions"][0]["parameters"]["temporary password"].update(
            {
                "required": False,
                "primary": False,
                "allow_list": False,
                "name": "temporary password",
            }
        )
        self.assertEqual(diff.compare(before, after), [])

        after["actions"][0]["parameters"]["temporary password"]["required"] = True
        self.assertEqual(
            diff.compare(before, after),
            [
                {
                    "heading": "Changes to action `change password`:",
                    "items": ["**Input** `temporary password` changed `required`"],
                }
            ],
        )

        after["actions"][0]["parameters"]["temporary password"]["name"] = "new name"
        self.assertIn(
            "**Input** `temporary password` changed `name`",
            diff.compare(before, after)[0]["items"],
        )

    def test_output_prefix_alone_is_not_rendered_as_empty_name(self):
        before = app()
        before["actions"][0]["output"] = [
            {"data_path": "action_result.data.*.", "data_type": "string"}
        ]
        after = json.loads(json.dumps(before))
        after["actions"][0]["output"] = []
        self.assertEqual(
            diff.compare(before, after)[0]["items"],
            ["**Output** `action_result.data.*.` removed"],
        )

    def test_appid_change_reports_connector_replacement(self):
        before = app()
        after = json.loads(json.dumps(before))
        after["appid"] = "new-id"
        self.assertEqual(
            diff.compare(before, after),
            [
                {
                    "heading": "Changes to connector:",
                    "items": ["`Example` removed", "`Example` added"],
                }
            ],
        )

    def test_action_changes_have_their_own_sections(self):
        before = app()
        before["actions"].append(
            {"identifier": "old_action", "action": "old action", "parameters": {}, "output": []}
        )
        after = json.loads(json.dumps(before))
        after["actions"][0]["action"] = "reset password"
        after["actions"].pop()
        after["actions"].append(
            {"identifier": "new_action", "action": "new action", "parameters": {}, "output": []}
        )
        self.assertEqual(
            diff.compare(before, after),
            [
                {
                    "heading": "Changes to action `reset password`:",
                    "items": ["Action renamed from `change password`"],
                },
                {"heading": "Changes to action `new action`:", "items": ["Action added"]},
                {"heading": "Changes to action `old action`:", "items": ["Action removed"]},
            ],
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
                {
                    "heading": "Changes to action `change password`:",
                    "items": ["**Output** `transaction_id` changed"],
                }
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

    def test_base_manifest_refreshes_lock_before_locked_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "uv.lock").touch()
            (root / "sdk.json").write_text(json.dumps(app()))
            with patch.object(diff.subprocess, "run") as run:
                run.return_value.returncode = 0
                self.assertEqual(
                    diff.manifest(root, root, refresh_lock=True)["appid"], "example-id"
                )
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args_list[0].args[0], ["uv", "lock"])
                self.assertEqual(run.call_args_list[0].kwargs["cwd"], root)
                self.assertEqual(run.call_args_list[1].args[0][:3], ["uv", "run", "--locked"])

    def test_base_manifest_reports_lock_refresh_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "uv.lock").touch()
            with patch.object(diff.subprocess, "run") as run:
                run.return_value.returncode = 1
                run.return_value.stderr = "resolution failed"
                with self.assertRaisesRegex(RuntimeError, "resolution failed"):
                    diff.manifest(root, root, refresh_lock=True)
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
        changed["actions"][1]["parameters"]["username"]["required"] = True
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
        self.assertEqual(sum(len(section["items"]) for section in expected), 4)
        self.assertEqual(diff.compare(before, reordered_changed), expected)


class ContractReportTests(unittest.TestCase):
    def test_unchanged_summary_makes_no_write(self):
        changes = [{"heading": "Changes to asset parameters:", "items": ["`tls_verify` added"]}]
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
            reporter.report(
                "owner/repo",
                5,
                "abc",
                [{"heading": "Changes to asset parameters:", "items": ["`tls_verify` added"]}],
            )
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
