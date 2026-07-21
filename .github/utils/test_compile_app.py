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
            "soar_local_admin",
            "password",
        )

        ssh_client.assert_not_called()
        self.assertEqual(_is_compatible.call_count, 3)
        for call in _is_compatible.call_args_list:
            self.assertEqual(call.args[2:], ("soar_local_admin", "password"))
        self.assertEqual(
            set(results),
            {
                "current_phantom_version",
                "next_phantom_version",
                "previous_phantom_version",
            },
        )
        self.assertTrue(all(result["success"] for result in results.values()))


class CompileAppStagingTest(unittest.TestCase):
    def test_accepts_only_owned_staging_directories_for_cleanup(self):
        owned_directory = compile_app.COMPILE_STAGING_DIRECTORY / "compile-ABC12345"

        self.assertTrue(compile_app.is_owned_staging_directory(owned_directory))
        self.assertFalse(compile_app.is_owned_staging_directory(Path("/tmp/compile-ABC12345")))
        self.assertFalse(
            compile_app.is_owned_staging_directory(
                compile_app.COMPILE_STAGING_DIRECTORY / "not-a-compile-directory"
            )
        )

    @mock.patch.object(compile_app, "run_remote_command")
    def test_deletes_only_owned_staging_directory(self, run_remote_command):
        owned_directory = compile_app.COMPILE_STAGING_DIRECTORY / "compile-ABC12345"

        compile_app.delete_folder(mock.Mock(), owned_directory)
        compile_app.delete_folder(mock.Mock(), Path("/tmp/compile-ABC12345"))

        run_remote_command.assert_called_once()
        command = run_remote_command.call_args.args[1]
        self.assertEqual(command, f"rm -rf -- {owned_directory}")

    @mock.patch.object(
        compile_app,
        "run_remote_command",
        return_value="/home/phantom/.soar-compile/compile-ABC12345",
    )
    def test_creates_mktemp_staging_directory(self, run_remote_command):
        staging_directory = compile_app.create_staging_directory("previous", mock.Mock())

        self.assertEqual(
            staging_directory, compile_app.COMPILE_STAGING_DIRECTORY / "compile-ABC12345"
        )
        command = run_remote_command.call_args.args[1]
        self.assertIn("mktemp -d", command)
        self.assertIn("/home/phantom/.soar-compile/compile-XXXXXXXX", command)

    @mock.patch.object(compile_app, "delete_folder")
    @mock.patch.object(compile_app, "run_remote_command")
    @mock.patch.object(
        compile_app,
        "create_staging_directory",
        return_value=Path("/home/phantom/.soar-compile/compile-ABC12345"),
    )
    @mock.patch.object(compile_app, "SCPClient")
    def test_upload_promotes_and_verifies_before_compile(
        self, scp_client, _create_staging_directory, run_remote_command, delete_folder
    ):
        with tempfile.TemporaryDirectory() as temp_directory:
            app_path = Path(temp_directory) / "example"
            app_path.mkdir()
            (app_path / "example.json").write_text(json.dumps({"min_phantom_version": "8.6.0"}))
            phantom_client = mock.Mock()

            with compile_app.upload_app_files(
                "previous", phantom_client, app_path, "example"
            ) as test_dir:
                self.assertEqual(test_dir, Path("/home/phantom/.soar-compile/compile-ABC12345/app"))

        scp_client.return_value.__enter__.return_value.put.assert_called_once_with(
            app_path,
            recursive=True,
            remote_path="/home/phantom/.soar-compile/compile-ABC12345/.incoming",
        )
        self.assertEqual(run_remote_command.call_count, 2)
        promote_command = run_remote_command.call_args_list[1].args[1]
        self.assertIn("mv", promote_command)
        self.assertIn("test -d /home/phantom/.soar-compile/compile-ABC12345/app", promote_command)
        self.assertIn(
            "test -f /home/phantom/.soar-compile/compile-ABC12345/app/example.json", promote_command
        )
        delete_folder.assert_called_once_with(
            phantom_client, Path("/home/phantom/.soar-compile/compile-ABC12345")
        )


if __name__ == "__main__":
    unittest.main()
