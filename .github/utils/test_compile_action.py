import unittest
from pathlib import Path


class CompileActionTest(unittest.TestCase):
    def setUp(self):
        action_path = Path(__file__).parents[1] / "actions" / "compile-app" / "action.yml"
        self.action = action_path.read_text()

    def test_traditional_compile_receives_phantom_password(self):
        traditional_step = self.action.split("- name: Compile Traditional App", 1)[1]

        self.assertIn("PHANTOM_PASSWORD: ${{ inputs.phantom_password }}", traditional_step)

    def test_sdk_installs_retry_transient_failures(self):
        sdk_step = self.action.split("- name: Build and Install SDKfied App", 1)[1].split(
            "- name: Compile Traditional App", 1
        )[0]

        self.assertIn("install_sdk_app()", sdk_step)
        self.assertIn("for attempt in 1 2 3", sdk_step)
        self.assertEqual(sdk_step.count('install_sdk_app "${{ inputs.'), 3)


if __name__ == "__main__":
    unittest.main()
