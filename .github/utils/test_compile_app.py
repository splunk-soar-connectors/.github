import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from utils import compile_app


class CompileAppVersionCompatibilityTest(unittest.TestCase):
    def test_reads_minimum_version_from_traditional_app_json(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            app_path = Path(temp_directory)
            (app_path / "example_postman_collection.json").write_text("{}")
            (app_path / "example.json").write_text(json.dumps({"min_phantom_version": "8.6.0"}))

            self.assertEqual(compile_app.get_min_phantom_version(app_path), "8.6.0")

    @mock.patch.object(compile_app, "supports_minimum_version", return_value=False)
    def test_uses_shared_version_comparison(self, supports_minimum_version):
        with tempfile.TemporaryDirectory() as temp_directory:
            app_path = Path(temp_directory)
            (app_path / "example.json").write_text(json.dumps({"min_phantom_version": "8.6.0"}))

            compatible = compile_app.is_local_app_compatible(
                app_path, "phantom.example", "phantom", "password"
            )

        self.assertFalse(compatible)
        supports_minimum_version.assert_called_once_with(
            "8.6.0", "phantom.example", "phantom", "password"
        )

    @mock.patch.object(
        compile_app,
        "get_min_phantom_version",
        side_effect=ValueError("missing app JSON"),
    )
    def test_version_check_fails_open(self, _get_min_phantom_version):
        self.assertTrue(
            compile_app.is_local_app_compatible(
                Path("/missing"), "phantom.example", "phantom", "password"
            )
        )

    @mock.patch.object(compile_app.paramiko, "SSHClient")
    @mock.patch.object(compile_app, "is_local_app_compatible", return_value=False)
    def test_skips_unsupported_instances(self, _is_compatible, ssh_client):
        results = compile_app.run_compile(
            "example",
            Path("/example"),
            "current.example",
            "next.example",
            "previous.example",
            "phantom",
            "password",
        )

        ssh_client.assert_not_called()
        self.assertEqual(
            set(results),
            {
                "current_phantom_version",
                "next_phantom_version",
                "previous_phantom_version",
            },
        )
        self.assertTrue(all(result["success"] for result in results.values()))


if __name__ == "__main__":
    unittest.main()
