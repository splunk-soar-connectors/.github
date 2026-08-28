"""Tests for pre-commit wheel selection."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("prewarm_precommit_wheels.py")
SPEC = importlib.util.spec_from_file_location("prewarm_precommit_wheels", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class WheelSpecsTests(TestCase):
    def test_selects_supported_hook_wheels(self) -> None:
        config = {
            "repos": [
                {"repo": "https://github.com/astral-sh/ruff-pre-commit", "rev": "v0.15.22"},
                {"repo": "https://github.com/charliermarsh/ruff-pre-commit", "rev": "v0.11.2"},
                {
                    "repo": "https://github.com/returntocorp/semgrep.git",
                    "rev": "1.165.0",
                    "hooks": [
                        {"additional_dependencies": ["setuptools==81.0.0", "typing-extensions"]}
                    ],
                },
            ]
        }
        with patch.object(MODULE, "load_config", return_value=config):
            self.assertEqual(
                MODULE.wheel_specs(Path("ignored")),
                [
                    "ruff==0.11.2",
                    "ruff==0.15.22",
                    "semgrep==1.165.0",
                    "setuptools==81.0.0",
                ],
            )

    def test_rejects_non_version_hook_revisions(self) -> None:
        config = {
            "repos": [{"repo": "https://github.com/astral-sh/ruff-pre-commit", "rev": "main"}]
        }
        with patch.object(MODULE, "load_config", return_value=config):
            with self.assertRaisesRegex(ValueError, "Unsupported ruff hook revision"):
                MODULE.wheel_specs(Path("ignored"))

    def test_ignores_unrelated_hooks(self) -> None:
        with patch.object(MODULE, "load_config", return_value={"repos": []}):
            self.assertEqual(MODULE.wheel_specs(Path("ignored")), [])


if __name__ == "__main__":
    main()
