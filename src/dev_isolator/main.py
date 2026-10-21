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
from .utils import set_verbosity, config_path, Verbosity, dprint

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

    subparsers.add_parser(
        "build",
        help="Build the current devisolator image")

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
    config = config_path(".devisolator")
    context_dir = config.parent
    container_file = config / "Containerfile"

    dprint(Verbosity.INFO, f"Found .devisolator directory in {config}")

    if config:
        definition = ContainerDefintion(container_file, context_dir)
    else:
        definition = ContainerDefintion()

    try:
        return definition.run_container(args.shell)
    except Exception as e:
        print(f"An error occured: {e}", file=sys.stderr)
        return 1


def build(args) -> int:
    config = config_path(".devisolator")
    context_dir = config.parent
    container_file = config / "Containerfile"

    dprint(Verbosity.INFO, f"Found .devisolator directory in {config}")

    if config:
        definition = ContainerDefintion(container_file, context_dir)
    else:
        definition = ContainerDefintion()

    try:
        definition.build_container()
    except Exception as e:
        print(f"An error occured: {e}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    """Main CLI entry point."""
    parser = create_parser()
    args = parser.parse_args()

    set_verbosity(Verbosity(args.verbose))

    # Without a subcommand, behave as if "run" was specified.
    if args.command is None:
        args.command = "run"
        args.shell = DEFAULT_SHELL

    if args.command == "run":
        return run(args)
    if args.command == "build":
        return build(args)
    return 0
if __name__ == "__main__":
    sys.exit(main())
