import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


WORKFLOW = Path(__file__).parent / "workflows" / "publish.yml"
JOBS = yaml.safe_load(WORKFLOW.read_text())["jobs"]
ARCHIVE_NAME = "example.tgz"
REPOSITORY = "splunk-soar-connectors/example"
VERSION = "1.2.3"


def run_step(script, directory, environment):
    return subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        cwd=directory,
        env={**os.environ, **environment},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("release_version", ["", VERSION])
def test_semantic_release_outputs_describe_only_the_new_release(tmp_path, release_version):
    for args in (
        ["init", "--quiet"],
        ["config", "user.name", "Release Test"],
        ["config", "user.email", "release-test@example.com"],
        ["commit", "--quiet", "--allow-empty", "-m", "Initial commit"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True)
    tools = tmp_path / "bin"
    tools.mkdir()
    npx = tools / "npx"
    npx.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, subprocess\n"
        "if version := os.environ['TEST_RELEASE_VERSION']:\n"
        "    pathlib.Path('release_version.txt').write_text(version + '\\n')\n"
        "    subprocess.run(['git', 'commit', '--quiet', '--allow-empty', '-m', 'Release'], check=True)\n"
    )
    npx.chmod(0o755)
    # A file from an earlier attempt must not cause a false release.
    (tmp_path / "release_version.txt").write_text("9.9.9\n")
    output = tmp_path / "outputs"
    output.touch()
    step = next(step for step in JOBS["semantic-version"]["steps"] if step.get("id") == "release")
    result = run_step(
        step["run"],
        tmp_path,
        {
            "PATH": f"{tools}:{os.environ['PATH']}",
            "GITHUB_OUTPUT": str(output),
            "TEST_RELEASE_VERSION": release_version,
        },
    )
    assert result.returncode == 0, result.stderr
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
    if release_version:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
        ).strip()
        assert outputs == {"release_version": release_version, "release_commit": commit}
    else:
        assert outputs == {}


@pytest.fixture
def release_job(tmp_path):
    tools = tmp_path / "bin"
    tools.mkdir()
    published = tmp_path / "published"
    published.mkdir()
    state = tmp_path / "release.json"
    state.write_text(json.dumps({"assets": {}, "uploads": []}))
    gh = tools / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import hashlib, json, os, pathlib, sys\n"
        "state_path = pathlib.Path(os.environ['TEST_RELEASE_STATE'])\n"
        "state = json.loads(state_path.read_text())\n"
        "args = sys.argv[1:]\n"
        "if args == ['api', 'repos/splunk-soar-connectors/example/releases/tags/1.2.3']:\n"
        "    print(json.dumps({'assets': [{'name': name, 'digest': digest} for name, digest in state['assets'].items()]}))\n"
        "else:\n"
        "    assert args[:3] == ['release', 'upload', '1.2.3'], args\n"
        "    assert args[-2:] == ['--repo', 'splunk-soar-connectors/example'], args\n"
        "    for name in args[3:-2]:\n"
        "        assert name not in state['assets'], 'Existing assets must not be overwritten'\n"
        "        content = pathlib.Path(name).read_bytes()\n"
        "        (state_path.parent / 'published' / name).write_bytes(content)\n"
        "        state['assets'][name] = 'sha256:' + hashlib.sha256(content).hexdigest()\n"
        "        state['uploads'].append(name)\n"
        "    state_path.write_text(json.dumps(state))\n"
    )
    gh.chmod(0o755)
    contents = {ARCHIVE_NAME: b"original SDK build output"}
    contents[f"{ARCHIVE_NAME}.sha256"] = (
        f"{hashlib.sha256(contents[ARCHIVE_NAME]).hexdigest()}  {ARCHIVE_NAME}\n".encode()
    )
    (tmp_path / ARCHIVE_NAME).write_bytes(contents[ARCHIVE_NAME])
    step = next(step for step in JOBS["publish-sdk-release"]["steps"] if step.get("run"))
    environment = {
        "PATH": f"{tools}:{os.environ['PATH']}",
        "TEST_RELEASE_STATE": str(state),
        "ARCHIVE_NAME": ARCHIVE_NAME,
        "RELEASE_VERSION": VERSION,
        "GITHUB_REPOSITORY": REPOSITORY,
    }
    return tmp_path, published, state, contents, step["run"], environment


@pytest.mark.parametrize(
    "existing_assets",
    [(), (ARCHIVE_NAME,), (f"{ARCHIVE_NAME}.sha256",), (ARCHIVE_NAME, f"{ARCHIVE_NAME}.sha256")],
)
def test_release_upload_preserves_build_bytes_and_recovers_partial_uploads(
    release_job, existing_assets
):
    directory, published, state, contents, script, environment = release_job
    assets = {}
    for name in existing_assets:
        assets[name] = f"sha256:{hashlib.sha256(contents[name]).hexdigest()}"
        (published / name).write_bytes(contents[name])
    state.write_text(json.dumps({"assets": assets, "uploads": []}))

    result = run_step(script, directory, environment)

    assert result.returncode == 0, result.stderr
    assert json.loads(state.read_text())["uploads"] == [
        name for name in contents if name not in existing_assets
    ]
    for name, content in contents.items():
        assert (published / name).read_bytes() == content
    # A job retry must leave both existing assets untouched.
    before_retry = state.read_text()
    retry = run_step(script, directory, environment)
    assert retry.returncode == 0, retry.stderr
    assert state.read_text() == before_retry


@pytest.mark.parametrize("name", [ARCHIVE_NAME, f"{ARCHIVE_NAME}.sha256"])
@pytest.mark.parametrize("digest", [None, "sha256:different-content"])
def test_release_conflicts_fail_before_uploading_any_asset(release_job, name, digest):
    directory, _, state, _, script, environment = release_job
    state.write_text(json.dumps({"assets": {name: digest}, "uploads": []}))

    result = run_step(script, directory, environment)

    assert result.returncode != 0
    assert "Refusing to replace release asset" in result.stderr
    assert json.loads(state.read_text())["uploads"] == []
