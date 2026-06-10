#!/usr/bin/env python3
"""Unit tests for assign_pr_labels.

These tests stub out the ``requests`` and ``github`` dependencies and never
touch the network, GitHub, Jira, or Splunkbase, so they are safe to run
locally with a plain ``python3 test_assign_pr_labels.py`` (no pip installs
required).
"""

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


def _install_fake_dependencies():
    """Provide minimal stand-ins for the third party imports in the target."""
    if "requests" not in sys.modules:
        requests_mod = types.ModuleType("requests")
        exceptions_mod = types.ModuleType("requests.exceptions")

        class RequestException(Exception):
            pass

        exceptions_mod.RequestException = RequestException
        requests_mod.exceptions = exceptions_mod

        def _unconfigured_call(*_args, **_kwargs):
            raise AssertionError("requests.get/post must be patched within each test")

        requests_mod.post = _unconfigured_call
        requests_mod.get = _unconfigured_call
        sys.modules["requests"] = requests_mod
        sys.modules["requests.exceptions"] = exceptions_mod

    if "github" not in sys.modules:
        github_mod = types.ModuleType("github")

        class Github:
            def __init__(self, *_args, **_kwargs):
                pass

        github_mod.Github = Github
        sys.modules["github"] = github_mod


_install_fake_dependencies()

_MODULE_PATH = Path(__file__).with_name("assign_pr_labels.py")
_spec = importlib.util.spec_from_file_location("assign_pr_labels", _MODULE_PATH)
assign_pr_labels = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(assign_pr_labels)


class FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


class FakePR:
    def __init__(self, html_url="https://github.com/org/app/pull/1", body="desc"):
        self.html_url = html_url
        self.body = body


class FakeContentFile:
    def __init__(self, name, decoded=b"{}"):
        self.name = name
        self.decoded_content = decoded


def _sdk_app_py(publisher=None, appid=None, name=None):
    """Render an SDK ``src/app.py`` with an ``App(...)`` call like real apps."""
    lines = ["from soar_sdk.app import App", "", "app = App("]
    if name is not None:
        lines.append(f'    name="{name}",')
    lines.append('    app_type="sandbox",')
    lines.append('    product_vendor="vendor",')
    if publisher is not None:
        lines.append(f'    publisher="{publisher}",')
    if appid is not None:
        lines.append(f'    appid="{appid}",')
    lines += [
        ")",
        "",
        '@app.action(name="some action", identifier="act")',
        "def act():",
        "    pass",
        "",
    ]
    return "\n".join(lines)


class FakeRepo:
    """Mimics enough of a PyGithub Repository for metadata reading.

    ``default`` and ``head`` describe what exists at each ref. Each is either
    None (no app), or a dict:
      {"kind": "traditional", "manifest": {...}}
      {"kind": "sdk", "app": {"publisher": ..., "appid": ..., "name": ...}}
    """

    def __init__(self, default=None, head=None):
        self.default_branch = "main"
        self._default = default
        self._head = head

    def _spec_for_ref(self, ref):
        return self._default if ref == self.default_branch else self._head

    def get_contents(self, path, ref=None):
        spec = self._spec_for_ref(ref)
        if spec is None:
            raise RuntimeError(f"nothing at ref {ref}")
        kind = spec.get("kind")

        if path == "":  # top-level directory listing
            if kind == "traditional":
                return [FakeContentFile("app.json")]
            return [FakeContentFile("pyproject.toml"), FakeContentFile("README.md")]

        if kind == "traditional" and path.endswith(".json"):
            return FakeContentFile(path, json.dumps(spec["manifest"]).encode("utf-8"))

        if kind == "sdk" and path == assign_pr_labels.SDK_APP_ENTRY:
            return FakeContentFile(path, _sdk_app_py(**spec.get("app", {})).encode("utf-8"))

        raise RuntimeError(f"no {path} at ref {ref}")

    def get_pull(self, _number):
        return types.SimpleNamespace(head=types.SimpleNamespace(sha="deadbeef"))


class FakeClient:
    def __init__(self, repo):
        self._repo = repo

    def get_repo(self, _name):
        return self._repo


class FakePullRequest:
    def __init__(self, login="extuser"):
        self.user = types.SimpleNamespace(login=login)
        self.labels = []
        self.set_labels_args = None

    def set_labels(self, *labels):
        self.set_labels_args = labels

    def create_issue_comment(self, _comment):
        pass


def _fake_myself(account_id="acc-123", status=200):
    def _get(_url, headers=None, auth=None, timeout=None):
        if status >= 400:
            return FakeResponse(status, text="myself failed")
        return FakeResponse(status, {"accountId": account_id})

    return _get


class CreateJiraTicketTests(unittest.TestCase):
    def test_missing_credentials_returns_none(self):
        self.assertIsNone(
            assign_pr_labels.create_jira_ticket("", "", "app", False, FakePR())
        )

    def test_new_app_developer_supported_payload(self):
        captured = {}

        def fake_post(_url, headers=None, json=None, auth=None, timeout=None):
            captured["json"] = json
            return FakeResponse(201, {"key": "PAPP-123"})

        with mock.patch.object(assign_pr_labels.requests, "get", side_effect=_fake_myself()), \
                mock.patch.object(assign_pr_labels.requests, "post", side_effect=fake_post):
            key = assign_pr_labels.create_jira_ticket(
                "user", "key", "myapp", is_certified=False, pr_info=FakePR(), is_new_app=True
            )

        self.assertEqual(key, "PAPP-123")
        fields = captured["json"]["fields"]
        self.assertTrue(fields["summary"].startswith(assign_pr_labels.NEW_APP_SUMMARY_PREFIX))
        self.assertIn("Developer-supported", fields["summary"])
        self.assertIn(assign_pr_labels.NOT_CERTIFIED_LABEL, fields["labels"])
        # The required reporter field must be set explicitly to the auth'd account.
        self.assertEqual(fields["reporter"], {"accountId": "acc-123"})

    def test_certified_summary_has_no_new_app_prefix(self):
        captured = {}

        def fake_post(_url, headers=None, json=None, auth=None, timeout=None):
            captured["json"] = json
            return FakeResponse(201, {"key": "PAPP-200"})

        with mock.patch.object(assign_pr_labels.requests, "get", side_effect=_fake_myself()), \
                mock.patch.object(assign_pr_labels.requests, "post", side_effect=fake_post):
            assign_pr_labels.create_jira_ticket(
                "user", "key", "myapp", is_certified=True, pr_info=FakePR(), is_new_app=False
            )

        summary = captured["json"]["fields"]["summary"]
        self.assertFalse(summary.startswith(assign_pr_labels.NEW_APP_SUMMARY_PREFIX))
        self.assertIn("Splunk-supported", summary)

    def test_400_logs_body_and_returns_none(self):
        attempts = []

        def fake_post(_url, headers=None, json=None, auth=None, timeout=None):
            attempts.append(json["fields"])
            return FakeResponse(400, text='{"errors":{"reporter":"reporter is required."}}')

        with mock.patch.object(assign_pr_labels.requests, "get", side_effect=_fake_myself()), \
                mock.patch.object(assign_pr_labels.requests, "post", side_effect=fake_post):
            with self.assertLogs(level="ERROR") as log:
                key = assign_pr_labels.create_jira_ticket("u", "k", "app", True, FakePR())

        self.assertIsNone(key)
        # Exactly one create attempt (no speculative fallback retries).
        self.assertEqual(len(attempts), 1)
        # The real Jira validation body is surfaced in the logs.
        self.assertTrue(any("reporter is required" in line for line in log.output))

    def test_reporter_omitted_when_myself_unavailable(self):
        captured = {}

        def fake_post(_url, headers=None, json=None, auth=None, timeout=None):
            captured["json"] = json
            return FakeResponse(201, {"key": "PAPP-300"})

        with mock.patch.object(
            assign_pr_labels.requests, "get", side_effect=_fake_myself(status=401)
        ), mock.patch.object(assign_pr_labels.requests, "post", side_effect=fake_post):
            with self.assertLogs(level="WARNING"):
                key = assign_pr_labels.create_jira_ticket("u", "k", "app", False, FakePR())

        self.assertEqual(key, "PAPP-300")
        self.assertNotIn("reporter", captured["json"]["fields"])


class AppContextTests(unittest.TestCase):
    """Format-agnostic metadata + new-app detection (traditional and SDK)."""

    def test_traditional_existing_app_is_not_new(self):
        repo = FakeRepo(
            default={"kind": "traditional", "manifest": {"publisher": "Splunk", "appid": "guid-1"}},
            head={"kind": "traditional", "manifest": {"publisher": "Splunk", "appid": "guid-1"}},
        )
        meta, is_new = assign_pr_labels.get_app_context(FakeClient(repo), "org/app", 1)
        self.assertFalse(is_new)
        self.assertEqual(meta["format"], "traditional")
        self.assertEqual(meta["publisher"], "Splunk")
        self.assertEqual(meta["appid"], "guid-1")

    def test_traditional_new_app_only_in_head(self):
        repo = FakeRepo(
            default=None,
            head={"kind": "traditional", "manifest": {"publisher": "Acme", "appid": "guid-2"}},
        )
        meta, is_new = assign_pr_labels.get_app_context(FakeClient(repo), "org/app", 1)
        self.assertTrue(is_new)
        self.assertEqual(meta["publisher"], "Acme")

    def test_sdk_new_app_reads_app_py(self):
        repo = FakeRepo(
            default=None,
            head={"kind": "sdk", "app": {"publisher": "Splunk", "appid": "c46c", "name": "urlscan.io"}},
        )
        meta, is_new = assign_pr_labels.get_app_context(FakeClient(repo), "org/urlscan", 1)
        self.assertTrue(is_new)
        self.assertEqual(meta["format"], "sdk")
        self.assertEqual(meta["publisher"], "Splunk")
        self.assertEqual(meta["appid"], "c46c")
        self.assertEqual(meta["name"], "urlscan.io")

    def test_sdk_existing_app_is_not_new(self):
        repo = FakeRepo(
            default={"kind": "sdk", "app": {"publisher": "Acme", "appid": "c99"}},
            head={"kind": "sdk", "app": {"publisher": "Acme", "appid": "c99"}},
        )
        meta, is_new = assign_pr_labels.get_app_context(FakeClient(repo), "org/urlscan", 1)
        self.assertFalse(is_new)
        self.assertEqual(meta["appid"], "c99")

    def test_no_app_anywhere(self):
        repo = FakeRepo(default=None, head=None)
        meta, is_new = assign_pr_labels.get_app_context(FakeClient(repo), "org/docs", 1)
        self.assertIsNone(meta)
        self.assertFalse(is_new)


class DetermineIsCertifiedTests(unittest.TestCase):
    def test_new_app_publisher_splunk_is_certified(self):
        meta = {"publisher": "Splunk", "appid": "x"}
        self.assertTrue(assign_pr_labels.determine_is_certified(meta, is_new_app=True))

    def test_new_app_publisher_other_is_not_certified(self):
        meta = {"publisher": "Acme", "appid": "x"}
        self.assertFalse(assign_pr_labels.determine_is_certified(meta, is_new_app=True))

    def test_existing_app_uses_splunkbase_when_decided(self):
        meta = {"publisher": "Acme", "appid": "guid"}
        with mock.patch.object(assign_pr_labels, "splunkbase_is_supported", return_value=True):
            self.assertTrue(assign_pr_labels.determine_is_certified(meta, is_new_app=False))
        with mock.patch.object(assign_pr_labels, "splunkbase_is_supported", return_value=False):
            self.assertFalse(assign_pr_labels.determine_is_certified(meta, is_new_app=False))

    def test_existing_app_falls_back_to_publisher_when_splunkbase_undecided(self):
        meta = {"publisher": "Splunk", "appid": "guid"}
        with mock.patch.object(assign_pr_labels, "splunkbase_is_supported", return_value=None):
            self.assertTrue(assign_pr_labels.determine_is_certified(meta, is_new_app=False))


class SplunkbaseLookupTests(unittest.TestCase):
    def test_skips_without_credentials(self):
        with mock.patch.dict(assign_pr_labels.os.environ, {}, clear=True):
            with self.assertLogs(level="WARNING"):
                self.assertIsNone(assign_pr_labels.splunkbase_is_supported("guid"))


class AssignPrLabelsTests(unittest.TestCase):
    BASE_ENV = {
        "GITHUB_TOKEN": "token",
        "REPO_NAME": "org/myapp",
        "PR_NUMBER": "1",
        "JIRA_USER": "u",
        "JIRA_API_KEY": "k",
    }

    def _run_with(self, pr, *, is_internal, metadata, is_new_app, create_jira_mock,
                  splunkbase_supported=None):
        repo = mock.Mock()
        repo.get_pull.return_value = pr
        client = mock.Mock()
        client.get_repo.return_value = repo

        with mock.patch.dict(assign_pr_labels.os.environ, self.BASE_ENV, clear=False), \
                mock.patch.object(assign_pr_labels, "Github", return_value=client), \
                mock.patch.object(
                    assign_pr_labels, "check_if_internal_contributor", return_value=is_internal
                ), \
                mock.patch.object(
                    assign_pr_labels, "get_app_context", return_value=(metadata, is_new_app)
                ), \
                mock.patch.object(
                    assign_pr_labels, "splunkbase_is_supported", return_value=splunkbase_supported
                ), \
                mock.patch.object(assign_pr_labels, "post_acknowledging_comment"), \
                mock.patch.object(
                    assign_pr_labels, "create_jira_ticket", side_effect=create_jira_mock
                ):
            assign_pr_labels.assign_pr_labels()

    def test_external_new_app_non_splunk_publisher_is_developer_supported(self):
        pr = FakePullRequest(login="extuser")
        captured = {}

        def fake_create(_juser, _jkey, _app, is_certified, _pr_info, is_new_app=False):
            captured["is_certified"] = is_certified
            captured["is_new_app"] = is_new_app
            return "PAPP-500"

        # Realistic external new app: publisher is the contributor, not Splunk.
        self._run_with(
            pr,
            is_internal=False,
            metadata={"publisher": "Acme", "appid": "guid", "format": "sdk"},
            is_new_app=True,
            create_jira_mock=fake_create,
        )

        self.assertFalse(captured["is_certified"])
        self.assertTrue(captured["is_new_app"])
        self.assertIn(assign_pr_labels.EXTERNAL_CONTRIBUTOR_LABEL, pr.set_labels_args)
        self.assertIn(assign_pr_labels.NOT_CERTIFIED_LABEL, pr.set_labels_args)
        self.assertIn("PAPP-500", pr.set_labels_args)
        self.assertNotIn(assign_pr_labels.CERTIFIED_LABEL, pr.set_labels_args)

    def test_external_sdk_new_app_is_processed(self):
        """Regression: SDK apps (no committed manifest) must still get labelled."""
        pr = FakePullRequest(login="extuser")

        def fake_create(*_args, **_kwargs):
            return "PAPP-777"

        self._run_with(
            pr,
            is_internal=False,
            metadata={"publisher": "Acme", "appid": "guid", "format": "sdk"},
            is_new_app=True,
            create_jira_mock=fake_create,
        )

        self.assertIn("PAPP-777", pr.set_labels_args)
        self.assertIn(assign_pr_labels.NOT_CERTIFIED_LABEL, pr.set_labels_args)

    def test_external_existing_app_supported_via_splunkbase(self):
        pr = FakePullRequest(login="extuser")
        captured = {}

        def fake_create(_juser, _jkey, _app, is_certified, _pr_info, is_new_app=False):
            captured["is_certified"] = is_certified
            return "PAPP-600"

        self._run_with(
            pr,
            is_internal=False,
            metadata={"publisher": "Acme", "appid": "guid", "format": "traditional"},
            is_new_app=False,
            create_jira_mock=fake_create,
            splunkbase_supported=True,
        )

        self.assertTrue(captured["is_certified"])
        self.assertIn(assign_pr_labels.CERTIFIED_LABEL, pr.set_labels_args)
        self.assertIn("PAPP-600", pr.set_labels_args)

    def test_non_app_pr_gets_no_support_label_or_jira(self):
        pr = FakePullRequest(login="extuser")
        jira_calls = []

        def fake_create(*_args, **_kwargs):
            jira_calls.append(_args)
            return "SHOULD-NOT-HAPPEN"

        self._run_with(
            pr,
            is_internal=False,
            metadata=None,
            is_new_app=False,
            create_jira_mock=fake_create,
        )

        # External label still applied, but no support label and no Jira.
        self.assertIn(assign_pr_labels.EXTERNAL_CONTRIBUTOR_LABEL, pr.set_labels_args)
        self.assertNotIn(assign_pr_labels.CERTIFIED_LABEL, pr.set_labels_args)
        self.assertNotIn(assign_pr_labels.NOT_CERTIFIED_LABEL, pr.set_labels_args)
        self.assertEqual(jira_calls, [])

    def test_internal_certified_app_gets_supported_label_and_no_jira(self):
        pr = FakePullRequest(login="internaluser")
        jira_calls = []

        def fake_create(*_args, **_kwargs):
            jira_calls.append(_args)
            return "SHOULD-NOT-HAPPEN"

        self._run_with(
            pr,
            is_internal=True,
            metadata={"publisher": "Splunk", "appid": "guid", "format": "traditional"},
            is_new_app=False,
            create_jira_mock=fake_create,
            splunkbase_supported=True,
        )

        self.assertIn(assign_pr_labels.CERTIFIED_LABEL, pr.set_labels_args)
        self.assertNotIn(assign_pr_labels.EXTERNAL_CONTRIBUTOR_LABEL, pr.set_labels_args)
        self.assertEqual(jira_calls, [], "Jira ticket must not be created for internal PRs")


if __name__ == "__main__":
    unittest.main(verbosity=2)
