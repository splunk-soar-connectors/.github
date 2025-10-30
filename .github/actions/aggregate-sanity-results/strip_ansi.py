#!/usr/bin/env python3
"""
Strip ANSI escape codes from a file.
More reliable than sed for handling various ANSI code formats.
"""

import re
import sys


def strip_ansi_codes(text: str) -> str:
    """
    Remove all ANSI escape sequences from text.

    Handles:
    - Standard CSI sequences: ESC[...m (colors, styles)
    - OSC sequences: ESC]...BEL or ESC]...ST
    - Other escape sequences
    """
    # Pattern for ESC character (both hex \x1b and octal \033)
    # Matches most common ANSI codes including CSI and OSC sequences
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)  # CSI sequences (hex)
    text = re.sub(r"\033\[[0-9;]*[a-zA-Z]", "", text)  # CSI sequences (octal)
    text = re.sub(r"\x1b\][^\x07]*\x07", "", text)  # OSC with BEL
    text = re.sub(r"\x1b\][^\x1b]*\x1b\\", "", text)  # OSC with ST
    text = re.sub(r"\x1b[^\[]", "", text)  # Other ESC sequences

    return text


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <input_file> <output_file>", file=sys.stderr)
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]

    try:
        with open(input_file, encoding="utf-8", errors="replace") as f:
            content = f.read()

        clean_content = strip_ansi_codes(content)

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(clean_content)

        print(f"Successfully stripped ANSI codes from {input_file} -> {output_file}")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
