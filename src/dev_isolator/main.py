"""CLI entry point for dev-isolator.

Copyright (C) 2026 Martin Köhler

This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it
under certain conditions; see LICENSE for details.
SPDX-License-Identifier: GPL-3.0-or-later
"""

import argparse
import sys
from .container_manager import ContainerDefintion
from .utils import set_verbosity, config_path

DEFAULT_SHELL = "bash"


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="dev-isolator",
        description="Bla",
    )

    subparsers = parser.add_subparsers(dest="command")

    parser.add_argument(
        "--verbose",
        "-v",
        action="count",
        default=0,
        help="Increase verbosity. Repeat for more details (-vv, -vvv).",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Open a terminal/shell inside the container "
             "isolator-<slug_of_the_current_path>.",
    )
    run_parser.add_argument(
        "--shell",
        "-s",
        type=str,
        default=DEFAULT_SHELL,
        help=f"Shell to start inside the container. Default: {DEFAULT_SHELL}.",
    )

    return parser


def run(args) -> int:
    definition = ContainerDefintion()

    try:
        return definition.run_container(args.shell)
    except Exception as e:
        print(f"An error occured: {e}", file=sys.stderr)
        return 1


def main() -> int:
    """Main CLI entry point."""
    parser = create_parser()
    args = parser.parse_args()

    set_verbosity(args.verbose)

    if args.command == "run":
        return run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
