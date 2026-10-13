import os
import re
import subprocess
import sys
from pathlib import Path

EXTENDS_RE = re.compile(
    r"{%-?\s*extends\s+['\"](?:base/logo_header|widgets/widget_template)\.html['\"]\s*-?%}"
)
WIDGET_BLOCK_RE = re.compile(r"{%-?\s*block\s+widget_content\s*-?%}")
SDK_CONFIG_RE = re.compile(r"^\[tool\.soar\.app\]\s*$", re.MULTILINE)


def check_template(path: Path) -> bool:
    content = path.read_text(encoding="utf-8")
    extends = EXTENDS_RE.search(content)
    if extends is None:
        return True

    widget_block = WIDGET_BLOCK_RE.search(content)
    if extends.start() != 0 or widget_block is None or "<!--" in content[: widget_block.start()]:
        print(
            f"{path}: start with the widget base extends tag "
            "and put HTML comments inside widget_content"
        )
        return False
    return True


def tracked_templates() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.html"],
        check=True,
        capture_output=True,
    )
    return [Path(os.fsdecode(name)) for name in result.stdout.split(b"\0") if name]


def is_sdk_repo(root: Path) -> bool:
    pyproject = root / "pyproject.toml"
    return (
        pyproject.is_file()
        and SDK_CONFIG_RE.search(pyproject.read_text(encoding="utf-8")) is not None
    )


def main() -> int:
    if len(sys.argv) == 1 and not is_sdk_repo(Path.cwd()):
        return 0
    paths = [Path(name) for name in sys.argv[1:]] if len(sys.argv) > 1 else tracked_templates()
    return int(sum(not check_template(path) for path in paths) > 0)


if __name__ == "__main__":
    sys.exit(main())
