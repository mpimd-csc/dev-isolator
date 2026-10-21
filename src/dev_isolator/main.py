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

    build_parser = subparsers.add_parser(
        "build",
        help="Build the current devisolator image")
    build_parser.add_argument(
            "--nocache",
            "-n",
            dest="nocache",
            action="store_true",
            help="Pass the --nocache option to podman build."
            )

    shell_parser = subparsers.add_parser(
        "shell",
        help="Open a terminal/shell inside the container "
             "isolator-<slug_of_the_current_path>.",
    )
    shell_parser.add_argument(
        "--shell",
        "-s",
        type=str,
        default=DEFAULT_SHELL,
        help=f"Shell to start inside the container. Default: {DEFAULT_SHELL}.",
    )

    clean_parser = subparsers.add_parser(
        "clean",
        help="Remove the current dev-isolator image.",
    )
    clean_parser.add_argument(
        "-a",
        "--all",
        dest="clean_all",
        action="store_true",
        help="Remove all dev-isolator images instead of only "
             "the current one.",
    )

    subparsers.add_parser(
        "opencode",
        add_help=False,
        help="Run opencode inside the container "
             "isolator-<slug_of_the_current_path>, "
             "passing all following arguments to it "
             "(for example ``opencode --port 4096``).",
    )

    subparsers.add_parser(
        "claude",
        add_help=False,
        help="Run claude inside the container "
             "isolator-<slug_of_the_current_path>, "
             "passing all following arguments to it "
             "(for example ``claude --model xyz``).",
    )

    subparsers.add_parser(
        "run",
        add_help=False,
        help="Run a command inside the container.",
        )

    return parser


def shell(args) -> int:
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

    if not args.nocache:
        nocache = False
    else:
        nocache = args.nocache

    try:
        definition.build_container(nocache=nocache)
    except Exception as e:
        print(f"An error occured: {e}", file=sys.stderr)
        return 1
    return 0


def clean(args) -> int:
    try:
        if args.clean_all:
            ContainerDefintion().clean_all_images()
        else:
            ContainerDefintion().clean_image()
    except Exception as e:
        print(f"An error occured: {e}", file=sys.stderr)
        return 1
    return 0


def in_container_command(command, remainder) -> int:
    config = config_path(".devisolator")
    context_dir = config.parent
    container_file = config / "Containerfile"

    dprint(Verbosity.INFO, f"Found .devisolator directory in {config}")

    if config:
        definition = ContainerDefintion(container_file, context_dir)
    else:
        definition = ContainerDefintion()

    try:
        return definition.run_container(
            command=[command, *remainder])
    except Exception as e:
        print(f"An error occured: {e}", file=sys.stderr)
        return 1


def main() -> int:
    """Main CLI entry point."""
    parser = create_parser()
    args, remainder = parser.parse_known_args()

    set_verbosity(Verbosity(args.verbose))

    # Without a subcommand, behave as if "run" was specified.
    if args.command is None:
        args.command = "shell"
        args.shell = DEFAULT_SHELL

    if args.command == "shell":
        return shell(args)
    if args.command == "build":
        return build(args)
    if args.command == "clean":
        return clean(args)
    if args.command == "opencode":
        return in_container_command("opencode", remainder)
    if args.command == "claude":
        return in_container_command("claude", remainder)
    if args.command == "run":
        return in_container_command(remainder[0], remainder[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
