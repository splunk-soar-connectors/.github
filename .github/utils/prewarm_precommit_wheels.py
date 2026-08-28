#!/usr/bin/env python3
"""Prepare verified wheels required by supported pre-commit hooks."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

from pre_commit.clientlib import load_config


HOOK_PACKAGES = {
    "https://github.com/astral-sh/ruff-pre-commit": "ruff",
    "https://github.com/charliermarsh/ruff-pre-commit": "ruff",
    "https://github.com/returntocorp/semgrep": "semgrep",
}
VERSION_RE = re.compile(r"\d+(?:\.\d+)+(?:[._+-][A-Za-z0-9.]+)?$")


def wheel_specs(config_path: Path) -> list[str]:
    specs: set[str] = set()
    for repo in load_config(config_path)["repos"]:
        package = HOOK_PACKAGES.get(str(repo.get("repo", "")).removesuffix(".git"))
        if package is None:
            continue
        version = str(repo.get("rev", "")).removeprefix("v")
        if not VERSION_RE.fullmatch(version):
            raise ValueError(f"Unsupported {package} hook revision: {version!r}")
        specs.add(f"{package}=={version}")
        if package == "semgrep":
            for hook in repo.get("hooks", []):
                for dependency in hook.get("additional_dependencies", []):
                    if str(dependency).startswith("setuptools"):
                        specs.add(str(dependency))
    return sorted(specs)


def write_outputs(specs: list[str], output_path: Path) -> None:
    digest = hashlib.sha256("\n".join(specs).encode()).hexdigest()
    with output_path.open("a", encoding="utf-8") as output:
        output.write(f"enabled={'true' if specs else 'false'}\n")
        output.write(f"digest={digest}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path(".pre-commit-config.yaml"))
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    specs = wheel_specs(args.config)
    if args.github_output:
        write_outputs(specs, args.github_output)
    if args.wheelhouse and specs:
        args.wheelhouse.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--only-binary=:all:",
                "--dest",
                str(args.wheelhouse),
                *specs,
            ],
            check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
