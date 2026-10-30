import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).parent
ACTION = yaml.safe_load((ROOT / "actions/trigger-connector-distribution/action.yml").read_text())
SCRIPT = ACTION["runs"]["steps"][0]["run"]
ARCHIVE_URL = (
    "https://github.com/splunk-soar-connectors/example/releases/download/1.2.3/example.tgz"
)
DIGEST = "a" * 64
PIPELINE_URL = "https://cd.splunkdev.com/security-automation-response/soar-services/-/pipelines/123"


@pytest.fixture
def trigger(tmp_path):
    release = tmp_path / "release.json"
    release.write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "name": "example.tgz",
                        "digest": "sha256:" + DIGEST,
                        "browser_download_url": ARCHIVE_URL,
                    }
                ]
            }
        )
    )
    response = tmp_path / "pipeline.json"
    response.write_text(json.dumps({"id": 123, "web_url": PIPELINE_URL}))
    calls = tmp_path / "calls.jsonl"
    statuses = tmp_path / "statuses.json"
    statuses.write_text(json.dumps(["success"]))
    clock = tmp_path / "clock"
    clock.write_text("0")
    for name, code in {
        "date": 'print(pathlib.Path(os.environ["TEST_CLOCK"]).read_text())',
        "sleep": 'p=pathlib.Path(os.environ["TEST_CLOCK"]); p.write_text(str(int(p.read_text()) + int(os.environ.get("TEST_SLEEP_ADVANCE", "15"))))',
    }.items():
        executable = tmp_path / name
        executable.write_text(f"#!{sys.executable}\nimport os, pathlib\n{code}\n")
        executable.chmod(0o755)
    curl = tmp_path / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        'with open(os.environ["TEST_CALLS"], "a") as output:\n'
        '    output.write(json.dumps(args) + "\\n")\n'
        'if args[-1].startswith("https://api.github.com/"):\n'
        '    print(pathlib.Path(os.environ["TEST_RELEASE"]).read_text())\n'
        'elif args[-1] == "https://cd.splunkdev.com/api/v4/projects/265010/pipeline":\n'
        '    if os.environ.get("TEST_POST_FAIL"):\n'
        "        sys.exit(22)\n"
        '    print(pathlib.Path(os.environ["TEST_RESPONSE"]).read_text())\n'
        "else:\n"
        '    assert args[-1] == "https://cd.splunkdev.com/api/v4/projects/265010/pipelines/123"\n'
        '    if os.environ.get("TEST_GET_FAIL"):\n'
        "        sys.exit(22)\n"
        '    path = pathlib.Path(os.environ["TEST_STATUSES"])\n'
        "    states = json.loads(path.read_text())\n"
        "    status = states.pop(0) if len(states) > 1 else states[0]\n"
        "    path.write_text(json.dumps(states))\n"
        '    print(json.dumps({"status": status}))\n'
    )
    curl.chmod(0o755)
    checker = tmp_path / "python3"
    checker.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        'assert pathlib.Path(sys.argv[1]).name == "check_publisher.py"\n'
        f'assert sys.argv[2:] == ["--archive-url", {ARCHIVE_URL!r}, "--sha256", {DIGEST!r}]\n'
        'if os.environ.get("TEST_PUBLISHER_ERROR"):\n'
        "    sys.exit(1)\n"
        'print(os.environ.get("TEST_ELIGIBLE", "true"))\n'
    )
    checker.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "TEST_CALLS": str(calls),
        "TEST_RELEASE": str(release),
        "TEST_RESPONSE": str(response),
        "TEST_STATUSES": str(statuses),
        "TEST_CLOCK": str(clock),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "RELEASE_REPOSITORY": "splunk-soar-connectors/example",
        "RELEASE_TAG": "1.2.3",
        "CONNECTOR_ENVIRONMENT": "playground",
        "GITHUB_TOKEN": "test-github-token",
        "GITLAB_API_TOKEN": "test-gitlab-token",
        "DISTRIBUTION_ACTION_PATH": str(ROOT / "actions/trigger-connector-distribution"),
    }

    def run(**overrides):
        result = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", SCRIPT],
            env={**environment, **overrides},
            capture_output=True,
            text=True,
            check=False,
        )
        requests = (
            [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
        )
        return result, requests

    return run, release, response


@pytest.mark.parametrize("target", ["playground", "staging"])
def test_release_asset_triggers_protected_main_with_exact_inputs(trigger, target):
    run, _, _ = trigger
    result, calls = run(CONNECTOR_ENVIRONMENT=target)

    assert result.returncode == 0, result.stderr
    assert len(calls) == 3
    github, gitlab, poll = calls
    assert (
        github[-1]
        == "https://api.github.com/repos/splunk-soar-connectors/example/releases/tags/1.2.3"
    )
    assert "Authorization: Bearer test-github-token" in github
    assert "test-gitlab-token" not in " ".join(github)
    assert "PRIVATE-TOKEN: test-gitlab-token" in gitlab
    assert "test-github-token" not in " ".join(gitlab)
    assert json.loads(gitlab[gitlab.index("--data-binary") + 1]) == {
        "ref": "main",
        "inputs": {
            "connector-archive-url": ARCHIVE_URL,
            "connector-sha256": DIGEST,
            "connector-environment": target,
        },
    }
    assert PIPELINE_URL in result.stdout
    assert "pipeline status: success" in result.stdout
    assert "PRIVATE-TOKEN: test-gitlab-token" in poll
    assert "test-github-token" not in " ".join(poll)
    assert "test-gitlab-token" not in result.stdout + result.stderr


def test_invalid_environment_never_makes_a_request(trigger):
    run, _, _ = trigger
    result, calls = run(CONNECTOR_ENVIRONMENT="production")
    assert result.returncode != 0
    assert calls == []


def test_missing_gitlab_token_fails_only_for_an_eligible_archive(trigger):
    run, _, _ = trigger
    result, calls = run(GITLAB_API_TOKEN="")
    assert result.returncode != 0
    assert len(calls) == 1


@pytest.mark.parametrize("target", ["playground", "staging"])
def test_other_publishers_skip_distribution_without_a_gitlab_credential(trigger, target):
    run, _, response = trigger
    result, calls = run(TEST_ELIGIBLE="false", GITLAB_API_TOKEN="", CONNECTOR_ENVIRONMENT=target)
    assert result.returncode == 0, result.stderr
    assert len(calls) == 1
    assert "Distribution skipped" in result.stdout
    assert "Distribution skipped" in (response.parent / "summary.md").read_text()
    assert PIPELINE_URL not in result.stdout


@pytest.mark.parametrize(
    "overrides", [{"TEST_PUBLISHER_ERROR": "true"}, {"TEST_ELIGIBLE": "unknown"}]
)
def test_unverified_publishers_never_create_a_pipeline(trigger, overrides):
    run, _, _ = trigger
    result, calls = run(**overrides)
    assert result.returncode != 0
    assert len(calls) == 1


@pytest.mark.parametrize(
    "assets", [[], [{"name": "example.tgz", "digest": None, "browser_download_url": ARCHIVE_URL}]]
)
def test_unusable_release_never_creates_a_pipeline(trigger, assets):
    run, release, _ = trigger
    release.write_text(json.dumps({"assets": assets}))
    result, calls = run()
    assert result.returncode != 0
    assert len(calls) == 1


def test_failed_post_is_not_retried(trigger):
    run, _, _ = trigger
    result, calls = run(TEST_POST_FAIL="true")
    assert result.returncode != 0
    assert len(calls) == 2
    assert PIPELINE_URL not in result.stdout


def test_missing_pipeline_link_is_not_reported_as_success(trigger):
    run, _, response = trigger
    response.write_text("{}")
    result, calls = run()
    assert result.returncode != 0
    assert len(calls) == 2


@pytest.mark.parametrize(
    "filename,publication",
    [("publish.yml", "publish-release-assets"), ("publish-existing-release.yml", "publish")],
)
def test_staging_depends_on_successful_playground_after_publication(filename, publication):
    jobs = yaml.safe_load((ROOT / "workflows" / filename).read_text())["jobs"]
    playground = jobs["distribute-playground"]
    staging = jobs["distribute-staging"]
    needs = playground["needs"] if isinstance(playground["needs"], list) else [playground["needs"]]
    assert publication in needs
    staging_needs = staging["needs"] if isinstance(staging["needs"], list) else [staging["needs"]]
    assert "distribute-playground" in staging_needs
    for job, environment in [(playground, "playground"), (staging, "staging")]:
        assert "if" not in job  # GitHub's default needs condition requires success.
        assert not job.get("continue-on-error", False)
        assert job["runs-on"][0].startswith("codebuild-integration-tests-")
        action = job["steps"][-1]
        assert action["with"]["environment"] == environment
        assert not action.get("continue-on-error", False)


def test_monitoring_waits_for_pipeline_success_without_recreating_it(trigger):
    run, _, response = trigger
    (response.parent / "statuses.json").write_text(json.dumps(["pending", "running", "success"]))
    result, calls = run()
    assert result.returncode == 0, result.stderr
    assert sum("--request" in call for call in calls) == 1
    assert sum(call[-1].endswith("pipelines/123") for call in calls) == 3
    assert "Result: success" in (response.parent / "summary.md").read_text()


@pytest.mark.parametrize("status", ["failed", "canceled", "skipped", "manual", "unknown"])
def test_unsuccessful_pipeline_fails_the_github_job(trigger, status):
    run, _, response = trigger
    (response.parent / "statuses.json").write_text(json.dumps([status]))
    result, calls = run()
    assert result.returncode != 0
    assert "Distribution did not succeed" in result.stderr
    assert len(calls) == 3
    assert f"Result: {status}" in (response.parent / "summary.md").read_text()


def test_monitoring_timeout_fails_without_starting_another_pipeline(trigger):
    run, _, response = trigger
    (response.parent / "statuses.json").write_text(json.dumps(["pending"]))
    result, calls = run(TEST_SLEEP_ADVANCE="1501")
    assert result.returncode != 0
    assert "Timed out after 25 minutes" in result.stderr
    assert len(calls) == 3


def test_unreadable_pipeline_status_fails_closed(trigger):
    run, _, _ = trigger
    result, calls = run(TEST_GET_FAIL="true")
    assert result.returncode != 0
    assert len(calls) == 3
