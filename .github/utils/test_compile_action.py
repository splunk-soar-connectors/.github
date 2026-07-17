import unittest
from pathlib import Path


class CompileActionTest(unittest.TestCase):
    def test_traditional_compile_receives_phantom_password(self):
        action_path = Path(__file__).parents[1] / "actions" / "compile-app" / "action.yml"
        traditional_step = action_path.read_text().split("- name: Compile Traditional App", 1)[1]

        self.assertIn("PHANTOM_PASSWORD: ${{ inputs.phantom_password }}", traditional_step)


if __name__ == "__main__":
    unittest.main()
