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
    curl = tmp_path / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        'with open(os.environ["TEST_CALLS"], "a") as output:\n'
        '    output.write(json.dumps(args) + "\\n")\n'
        'if args[-1].startswith("https://api.github.com/"):\n'
        '    print(pathlib.Path(os.environ["TEST_RELEASE"]).read_text())\n'
        "else:\n"
        '    assert args[-1] == "https://cd.splunkdev.com/api/v4/projects/265010/pipeline"\n'
        '    if os.environ.get("TEST_POST_FAIL"):\n'
        "        sys.exit(22)\n"
        '    print(pathlib.Path(os.environ["TEST_RESPONSE"]).read_text())\n'
    )
    curl.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "TEST_CALLS": str(calls),
        "TEST_RELEASE": str(release),
        "TEST_RESPONSE": str(response),
        "RELEASE_REPOSITORY": "splunk-soar-connectors/example",
        "RELEASE_TAG": "1.2.3",
        "CONNECTOR_ENVIRONMENT": "playground",
        "GITHUB_TOKEN": "test-github-token",
        "GITLAB_API_TOKEN": "test-gitlab-token",
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
    assert len(calls) == 2
    github, gitlab = calls
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
    assert "test-gitlab-token" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "overrides", [{"GITLAB_API_TOKEN": ""}, {"CONNECTOR_ENVIRONMENT": "production"}]
)
def test_invalid_configuration_never_makes_a_request(trigger, overrides):
    run, _, _ = trigger
    result, calls = run(**overrides)
    assert result.returncode != 0
    assert calls == []


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
def test_distribution_waits_for_publication_and_targets_both_environments(filename, publication):
    job = yaml.safe_load((ROOT / "workflows" / filename).read_text())["jobs"][
        "distribute-connector"
    ]
    needs = job["needs"] if isinstance(job["needs"], list) else [job["needs"]]
    assert publication in needs
    assert job["strategy"]["matrix"]["environment"] == ["playground", "staging"]
    assert job["strategy"]["fail-fast"] is False
    assert job["runs-on"][0].startswith("codebuild-integration-tests-")
