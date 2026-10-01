import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("check_widget_template.py")
SPEC = importlib.util.spec_from_file_location("check_widget_template", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CheckWidgetTemplateTest(unittest.TestCase):
    def test_check_template(self):
        cases = [
            ("{% extends 'base/logo_header.html' %}\n{% block widget_content %}", True),
            (
                '{% extends "base/logo_header.html" %}\n{% block widget_content %}\n<!-- license -->',
                True,
            ),
            (
                '{% extends "base/logo_header.html" %}\n<!-- license -->\n{% block widget_content %}',
                False,
            ),
            ('  \n{% extends "base/logo_header.html" %}', False),
            ("<!-- license -->\n{% extends 'base/logo_header.html' %}", False),
            ("<div></div>\n{% extends 'base/logo_header.html' %}", False),
            ("{% extends 'base/logo_header.html' %}\n<!-- license -->", False),
            ("{% extends 'widgets/widget_template.html' %}\n{% block widget_content %}row", True),
            ("<!-- license -->\n{% extends 'widgets/widget_template.html' %}", False),
            ("<!-- license -->\n{% extends 'unrelated/base.html' %}", True),
        ]
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "widget.html"
            for content, expected in cases:
                with self.subTest(content=content):
                    template.write_text(content, encoding="utf-8")
                    self.assertEqual(MODULE.check_template(template), expected)

    def test_sdk_repo_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pyproject = root / "pyproject.toml"
            self.assertFalse(MODULE.is_sdk_repo(root))
            pyproject.write_text("[project]\nname = 'legacy'\n", encoding="utf-8")
            self.assertFalse(MODULE.is_sdk_repo(root))
            pyproject.write_text("[tool.soar.app]\nmain_module = 'src.app:app'\n", encoding="utf-8")
            self.assertTrue(MODULE.is_sdk_repo(root))


if __name__ == "__main__":
    unittest.main()
