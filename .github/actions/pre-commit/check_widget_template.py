import os
import re
import subprocess
import sys
from pathlib import Path

EXTENDS_RE = re.compile(r"{%-?\s*extends\s+['\"]base/logo_header\.html['\"]\s*-?%}")
WIDGET_BLOCK_RE = re.compile(r"{%-?\s*block\s+widget_content\s*-?%}")


def check_template(path: Path) -> bool:
    content = path.read_text(encoding="utf-8")
    extends = EXTENDS_RE.search(content)
    if extends is None:
        return True

    widget_block = WIDGET_BLOCK_RE.search(content)
    if extends.start() != 0 or widget_block is None or "<!--" in content[: widget_block.start()]:
        print(
            f"{path}: start with the base/logo_header.html extends tag "
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


def main() -> int:
    paths = [Path(name) for name in sys.argv[1:]] if len(sys.argv) > 1 else tracked_templates()
    return int(sum(not check_template(path) for path in paths) > 0)


if __name__ == "__main__":
    sys.exit(main())
