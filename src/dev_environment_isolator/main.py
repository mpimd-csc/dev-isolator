"""CLI entry point for dev-environment-isolator.

Copyright (C) 2026 Captain Hook

This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it
under certain conditions; see LICENSE for details.
SPDX-License-Identifier: GPL-3.0-or-later
"""

import argparse
import sys
import podman.errors
from .container_manager import ContainerDefintion
from .functions import demo_function


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="dev-environment-isolator",
        description="Bla",
    )

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser(
        "build",
        help="Build the image isolator:<slug_of_the_current_path>.",
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
        default="bash",
        help="Shell to start inside the container. Default: bash.",
    )

    return parser


def main() -> int:
    """Main CLI entry point."""
    parser = create_parser()
    args = parser.parse_args()

    definition = ContainerDefintion()

    if args.command == "build":
        definition.build_container()
        return 0

    if args.command == "run":
        try:
            return definition.run_container(args.shell)
        except podman.errors.ImageNotFound:
            print(
                f"No image for the current path ({definition.image_name()}). "
                "Run 'dev-environment-isolator build' first.",
                file=sys.stderr,
            )
            return 1

    # Diagnose why just connecting would fail
    try:
        with podman.PodmanClient() as client:
            client.ping()
    except Exception as e:
        print(f"Cannot connect to podman: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
