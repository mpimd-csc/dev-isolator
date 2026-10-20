"""CLI entry point for dev-environment-isolator.

Copyright (C) 2026 Captain Hook

This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it
under certain conditions; see LICENSE for details.
SPDX-License-Identifier: GPL-3.0-or-later
"""

import argparse
import sys
from .functions import demo_function

def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="dev-environment-isolator",
        description="python-cli-tool-tempalte - A template for Python CLI tools",
    )
    parser.add_argument(
        "--who",
        type=str,
        default="Captain Hook",
        help="Name to shout.",
    )

    return parser


def main() -> int:
    """Main CLI entry point."""
    parser = create_parser()
    args = parser.parse_args()

    print("I say: {:s}".format(demo_function(args.who)))

    return 0

if __name__ == "__main__":
    sys.exit(main())
