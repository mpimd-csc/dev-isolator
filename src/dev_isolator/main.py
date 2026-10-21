"""CLI entry point for dev-isolator.

dev-isolator manages a per-project, podman based development
environment.  For every project that contains a ``.devisolator``
directory (holding a ``Containerfile`` and optional addon
configuration) it builds an image named
``dev-isolator-<slug_of_the_project_path>`` and runs commands inside a
container created from it, with the project directory mounted at the
same path.  This keeps build tools, caches and dependencies isolated
from the host while the sources stay in place.

The sub commands are:

``build``
    Build the image for the current project.
``shell``
    Open an interactive shell inside the container (the default when
    no subcommand is given).
``run <command> ...``
    Run an arbitrary command inside the container.
``opencode`` / ``claude``
    Run the respective AI coding tool inside the container.
``clean``
    Remove the image of the current project (or, with ``--all``, every
    dev-isolator image).

Copyright (C) 2026 Martin Köhler

This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it
under certain conditions; see LICENSE for details.
SPDX-License-Identifier: GPL-3.0-or-later
"""

import argparse
import sys
from textwrap import dedent
from .container_manager import ContainerDefintion
from .utils import set_verbosity, config_path, Verbosity, dprint

DEFAULT_SHELL = "bash"

# Sub commands that take unknown arguments verbatim instead of
# rejecting them, so that options intended for the command inside the
# container (for example ``opencode --port 4096``) are not parsed by
# dev-isolator itself.
PASS_THROUGH_COMMANDS = ("opencode", "claude", "run")


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the command line argument parser.

    Defines the global options (only ``--verbose/-v`` so far) and one
    subparser per sub command.  The ``opencode``, ``claude`` and
    ``run`` parsers disable their own help handling so that unknown
    arguments are passed through to the command inside the container.
    """
    description = """\
        Manage an isolated, podman based development environment
        for the current project.

        Images are named dev-isolator-<slug_of_the_project_path> and
        are built from the ``Containerfile`` found in the
        ``.devisolator`` directory of the project or form a default one.
        Commands started with dev-isolator run inside a container created
        from this image, with the project directory mounted at its original
        path, so tools and dependencies stay isolated from the host.
        """
    epilog = """\
examples:
  dev-isolator build              build the image of the current project
  dev-isolator --nocache build    same, but without using the build cache
  dev-isolator                    open an interactive shell in the container
  dev-isolator run make test      run "make test" inside the container
  dev-isolator opencode           run opencode inside the container
  dev-isolator clean --all        remove every dev-isolator image

The dev-isolator command can also be abbreviated by `di`
        """
    parser = argparse.ArgumentParser(
        prog="dev-isolator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=dedent(description),
        epilog=dedent(epilog),
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


def _split_subcommand_argv(argv):
    """Split ``argv`` around the first pass-through sub command.

    Return a tuple ``(head, command, tail)`` where ``head`` holds the
    arguments before the sub command, ``command`` is the pass-through
    sub command token itself (``opencode``, ``claude`` or ``run``) and
    ``tail`` everything after it.  A token that is the value of an
    option taking one (``--shell``/``-s``) is never mistaken for a
    sub command.  When no pass-through sub command is present,
    ``command`` is ``None`` and ``tail`` is empty.
    """
    value_expected = False
    for index, token in enumerate(argv):
        if value_expected:
            value_expected = False
            continue
        if token.startswith("-"):
            if token in ("--shell", "-s"):
                value_expected = True
            continue
        if token in PASS_THROUGH_COMMANDS:
            return argv[:index], token, argv[index + 1:]
    return argv, None, []


def _extract_shell_arguments(tail):
    """Split the arguments of the ``run`` sub command.

    Return a tuple ``(shell, arguments)`` where ``shell`` is the value
    of a ``--shell``/``-s`` option found in ``tail`` (``None`` when it
    is not used) and ``arguments`` holds the remaining tokens, that is
    the command to run inside the container.
    """
    shell = None
    arguments = []
    value_expected = False
    for token in tail:
        if value_expected:
            shell = token
            value_expected = False
        elif token in ("--shell", "-s"):
            value_expected = True
        elif token.startswith("--shell="):
            shell = token[len("--shell="):]
        else:
            arguments.append(token)
    return shell, arguments


def parse_args(argv=None) -> argparse.Namespace:
    """Parse command line arguments into a namespace.

    Parse ``argv`` (by default ``sys.argv[1:]``) with the parser from
    ``create_parser``.  Unknown arguments following the ``opencode``,
    ``claude`` and ``run`` sub commands are kept verbatim in the
    ``opencode_args``, ``claude_args`` and ``run_args`` attributes, so
    that options of the command inside the container do not clash with
    those of dev-isolator.  Without an explicit subcommand, behave as
    if ``shell`` was requested with the default shell.
    """
    if argv is None:
        argv = sys.argv[1:]

    parser = create_parser()
    head, command, tail = _split_subcommand_argv(argv)

    if command is None:
        args, _remainder = parser.parse_known_args(head)
        if args.command is None:
            args.command = "shell"
            args.shell = DEFAULT_SHELL
        return args

    args, _remainder = parser.parse_known_args([*head, command])

    if command == "run":
        shell, arguments = _extract_shell_arguments(tail)
        args.shell = shell if shell is not None else DEFAULT_SHELL
        args.run_args = arguments
    elif command == "opencode":
        args.opencode_args = tail
    elif command == "claude":
        args.claude_args = tail
    return args


def shell(args) -> int:
    """Handle the ``shell`` sub command.

    Look up the container definition of the current project and start
    an interactive shell (``args.shell``) inside its container.
    Returns the exit status of the container, or 1 on error.
    """
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
    """Handle the ``build`` sub command.

    Look up the container definition of the current project and build
    its image.  Pass ``--nocache`` to podman when ``args.nocache`` is
    set.  Returns 0 on success, or 1 on error.
    """
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
    """Handle the ``clean`` sub command.

    Remove the image of the current project, or every dev-isolator
    image when ``args.clean_all`` is set.  Returns 0 on success, or 1
    on error.
    """
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
    """Run ``command`` with ``remainder`` as its arguments inside the
    container of the current project.

    Used by the ``opencode``, ``claude`` and ``run`` sub commands,
    which pass extra arguments through without parsing them.  Returns
    the exit status of the container, or 1 on error.
    """
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
    """Main CLI entry point.

    Parse the command line with ``parse_args``, apply the requested
    verbosity and dispatch to the handler of the selected sub command.
    Without an explicit subcommand, ``shell`` is executed with the
    default shell.
    """
    args = parse_args()

    set_verbosity(Verbosity(args.verbose))

    if args.command == "shell":
        return shell(args)
    if args.command == "build":
        return build(args)
    if args.command == "clean":
        return clean(args)
    if args.command == "opencode":
        return in_container_command("opencode", args.opencode_args)
    if args.command == "claude":
        return in_container_command("claude", args.claude_args)
    if args.command == "run":
        if args.run_args:
            return in_container_command(args.run_args[0], args.run_args[1:])
        # Without a command, behave like ``shell``.
        return in_container_command(args.shell)
    return 0


if __name__ == "__main__":
    sys.exit(main())
