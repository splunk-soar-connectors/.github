import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest
import yaml


WORKFLOW = Path(__file__).parent / "workflows" / "publish-existing-release.yml"
JOBS = yaml.safe_load(WORKFLOW.read_text())["jobs"]


def run_step(name, directory, environment):
    step = next(step for step in JOBS["build"]["steps"] if step["name"] == name)
    return subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
        cwd=directory,
        env={
            **os.environ,
            "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}",
            **environment,
        },
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("connector,tag", [("slack", "2.12.0"), ("virustotalv3", "3.0.9")])
def test_existing_release_is_checked_before_building(tmp_path, connector, tag):
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        f'test "$*" = "release view --repo splunk-soar-connectors/{connector} -- {tag}"\n'
    )
    gh.chmod(0o755)

    result = run_step(
        "Validate existing connector release",
        tmp_path,
        {"PATH": f"{tmp_path}:{os.environ['PATH']}", "CONNECTOR": connector, "RELEASE_TAG": tag},
    )

    assert result.returncode == 0, result.stderr
    gh.write_text("#!/bin/sh\nexit 1\n")
    missing = run_step(
        "Validate existing connector release",
        tmp_path,
        {"PATH": f"{tmp_path}:{os.environ['PATH']}", "CONNECTOR": connector, "RELEASE_TAG": tag},
    )
    assert missing.returncode != 0


@pytest.mark.parametrize(
    "connector,tag",
    [
        ("../other", "1.2.3"),
        (".github", "1.2.3"),
        ("connector-template", "1.2.3"),
        ("slack", "main..other"),
        ("slack", "--help"),
        ("slack", "1.2.3\nother"),
    ],
)
def test_invalid_inputs_never_reach_github(tmp_path, connector, tag):
    gh = tmp_path / "gh"
    gh.write_text("#!/bin/sh\ntouch github-called\n")
    gh.chmod(0o755)

    result = run_step(
        "Validate existing connector release",
        tmp_path,
        {"PATH": f"{tmp_path}:{os.environ['PATH']}", "CONNECTOR": connector, "RELEASE_TAG": tag},
    )

    assert result.returncode != 0
    assert not (tmp_path / "github-called").exists()


@pytest.mark.parametrize("sdk_directory", [None, ".", "src/connector"])
def test_build_uses_the_selected_checkout_without_changing_its_version(tmp_path, sdk_directory):
    connector = f"release-test-{tmp_path.name}"
    source = tmp_path / connector
    source.mkdir()
    (source / "version.txt").write_text("1.2.3")
    project = source if sdk_directory is None else source / sdk_directory
    if sdk_directory is not None:
        project.mkdir(parents=True, exist_ok=True)
        (project / "uv.lock").write_text("locked dependencies")
    tools = tmp_path / "bin"
    tools.mkdir()
    # Package builders stand in for external tooling. The workflow shell must select
    # the right checkout, keep its version, and deliver the package to the next job.
    builder = tmp_path / "build.py"
    builder.write_text(
        "import io, json, os, pathlib, sys, tarfile\n"
        'if pathlib.Path(sys.argv[0]).name == "uv":\n'
        '    assert pathlib.Path.cwd() == pathlib.Path(os.environ["TEST_PROJECT"])\n'
        '    if sys.argv[1:] == ["sync", "--locked"]:\n'
        "        sys.exit(0)\n"
        '    assert sys.argv[1:8] == ["run", "--locked", "soarapps", "package", "build", ".", "--output-file"]\n'
        "    output = pathlib.Path(sys.argv[8])\n"
        "else:\n"
        '    assert sys.argv[1:] == [os.environ["CONNECTOR"], "1.2.3"]\n'
        '    assert pathlib.Path(os.environ["GITHUB_WORKSPACE"]) == pathlib.Path(os.environ["TEST_SOURCE"])\n'
        '    output = pathlib.Path("/tmp") / (os.environ["CONNECTOR"] + ".tgz")\n'
        'version = (pathlib.Path(os.environ["TEST_SOURCE"]) / "version.txt").read_text()\n'
        'manifest = json.dumps({"appid": "example-id", "app_version": version}).encode()\n'
        'with tarfile.open(output, "w:gz") as archive:\n'
        '    member = tarfile.TarInfo("example/example.json")\n'
        "    member.size = len(manifest)\n"
        "    archive.addfile(member, io.BytesIO(manifest))\n"
    )
    (tools / "uv").write_text(f"#!{sys.executable}\n" + builder.read_text())
    (tools / "uv").chmod(0o755)
    python = tools / "python"
    python.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        'if sys.argv[1:3] == ["-m", "pip"]:\n'
        "    sys.exit(0)\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
    )
    python.chmod(0o755)
    artifact = tmp_path / "output" / f"{connector}.tgz"
    artifact.parent.mkdir()
    environment = {
        "PATH": f"{tools}:{os.environ['PATH']}",
        "CONNECTOR": connector,
        "RELEASE_TAG": "1.2.3",
        "GITHUB_WORKSPACE": str(tmp_path),
        "BUILD_SCRIPT": str(builder),
        "ARTIFACT_PATH": str(artifact),
        "TEST_SOURCE": str(source),
        "TEST_PROJECT": str(project),
    }

    built = run_step("Build the selected connector version", tmp_path, environment)
    verified = run_step("Verify the packaged version", tmp_path, environment)

    assert built.returncode == 0, built.stderr
    assert verified.returncode == 0, verified.stderr
    assert artifact.is_file()
    assert (source / "version.txt").read_text() == "1.2.3"


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2.3"])
@pytest.mark.parametrize(
    "manifest_count,version,success",
    [(1, "1.2.3", True), (1, "1.2.4", False), (0, "", False), (2, "1.2.3", False)],
)
def test_only_the_requested_version_can_be_published(
    tmp_path, tag, manifest_count, version, success
):
    artifact = tmp_path / "connector.tgz"
    with tarfile.open(artifact, "w:gz") as archive:
        for index in range(manifest_count):
            contents = json.dumps({"appid": "example-id", "app_version": version}).encode()
            member = tarfile.TarInfo(f"connector/app{index}.json")
            member.size = len(contents)
            archive.addfile(member, io.BytesIO(contents))

    result = run_step(
        "Verify the packaged version",
        tmp_path,
        {"ARTIFACT_PATH": str(artifact), "RELEASE_TAG": tag},
    )

    assert (result.returncode == 0) is success, result.stderr


def test_manual_workflow_publishes_tagged_source_without_versioning_or_splunkbase():
    build = JOBS["build"]
    checkout = next(
        step for step in build["steps"] if step["name"] == "Check out the existing version"
    )
    assert checkout["with"]["ref"] == "refs/tags/${{ inputs.release_tag }}"
    assert "semantic_release_pk" not in json.dumps(build)
    workflow = WORKFLOW.read_text()
    assert "semantic-release" not in workflow
    assert "splunkbase" not in workflow.lower()
    publisher = next(
        step for step in JOBS["publish"]["steps"] if step["name"] == "Publish release assets"
    )
    assert publisher["with"]["release_tag"] == "${{ inputs.release_tag }}"
