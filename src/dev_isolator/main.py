"""CLI entry point for dev-isolator.

dev-isolator manages a per-project, podman based development
environment.  For every project that contains a ``.devisolator``
directory (holding a ``Containerfile`` and optional addon
configuration) it builds an image named
``dev-isolator:<slug_of_the_project_path>--<tag>`` and runs commands inside a
container created from it, with the project directory mounted at
``/workspace``.  This keeps build tools, caches and dependencies isolated
from the host while the sources stay in place.

The sub commands are:

``init``
    Initialize a local ``.devisolator`` configuration for the current
    project.
``build``
    Build the image for the current project.
``shell``
    Open an interactive shell inside the container (the default when
    no subcommand is given).
``run <command> ...``
    Run an arbitrary command inside the container.
``target <name> [args...]``
    Run a predefined target command from the ``targets`` section of
    ``config.yaml`` inside the container, passing the arguments
    following the target name to it.
``opencode`` / ``claude``
    Run the respective AI coding tool inside the container.
``clean``
    Stop, kill and remove the containers of the current project and
    remove its image (or, with ``--all``, every dev-isolator image and
    container, or with ``--local``, every image and container of the
    current project).
``list-base``
    List the base image definitions bundled with dev-isolator.
``list-targets``
    List the targets defined in the ``targets`` section of the active
    ``config.yaml``.
``help``
    Show the help message.

The ``--shell/-s`` option, given before the sub command, selects the
shell used by ``shell`` and by ``run``/``opencode``/``claude`` when they
start an interactive shell.

The ``target`` sub command takes only positional arguments: the first
one names a target from the ``targets`` section of ``config.yaml``,
whose command is a jinja2 template in which the extra arguments are
available as ``args[0]``, ``args[1]``, ... and as ``all_args`` (all of
them joined with spaces).  A placeholder marked with the ``optional``
filter is only used when the argument was given on the command line;
the token holding it (and a preceding option token) is dropped
otherwise.  The command may be a single command line (a string) or a
script given line by line in a list; the script lines run line by line
in one shell session inside the container, which stops at the first
failing line.

Copyright (C) 2026 Martin Köhler

This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it
under certain conditions; see LICENSE for details.
SPDX-License-Identifier: GPL-3.0-or-later
"""

import argparse
import sys
import os
import shlex
import shutil

import jinja2

from textwrap import dedent
from .container_manager import ContainerDefintion
from .container_image import replace_from_images
from .utils import set_verbosity, search_path, Verbosity, dprint
from .constants import (BUNDLE_DIR, CONFIG_FILE_NAME, ADDON_DIR,
                        DEV_ISOLATOR_DIR, DEFAULT_BASE)
from .base_image import BaseImage, detect_package_manager
from pathlib import Path
from .config import Configuration

DEFAULT_SHELL = "bash"

#: Sentinel returned by the ``optional`` jinja2 filter for an argument
#: that was not given on the command line; after rendering, every token
#: still containing it is dropped from the target's command.
_OPTIONAL_UNSET = "\x00dev-isolator-unset-optional\x00"


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the command line argument parser.

    Defines the global options (``--verbose/-v``, ``--shell/-s`` and
    ``--config/-c``) on the top level parser and one subparser per sub
    command.  The
    ``opencode``, ``claude``, ``run`` and ``target`` parsers disable
    their own help handling and define no arguments, so that unknown
    arguments following them are returned verbatim in the remainder of
    ``parse_known_args`` and passed through to the command inside the
    container (for ``target``, the first remainder token names the
    target to run).
    """
    description = """\
        Manage an isolated, podman based development environment
        for the current project.

        Images are named dev-isolator:<slug_of_the_project_path> and
        are built from the ``Containerfile`` found in the
        ``.devisolator`` directory of the project or form a default one.
        Commands started with dev-isolator run inside a container created
        from this image, with the project directory mounted at
        ``/workspace``, so tools and dependencies stay isolated from the
        host.
        """
    epilog = """\
examples:
  dev-isolator build              build the image of the current project
  dev-isolator --shell zsh        open a zsh in the container
  dev-isolator                    open an interactive shell in the container
  dev-isolator run make test      run "make test" inside the container
  dev-isolator target test        run the "test" target from the
                                  "targets" section of config.yaml
  dev-isolator opencode           run opencode inside the container
  dev-isolator clean --all        remove every dev-isolator image
  dev-isolator list-base          list the available base image definitions
  dev-isolator list-targets       list the targets of the active config.yaml

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
    parser.add_argument(
        "--shell",
        "-s",
        type=str,
        default=DEFAULT_SHELL,
        help="Shell to start inside the container. "
             f"Default: {DEFAULT_SHELL}.",
    )

    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default=None,
        help="Name of the sub-configuration (a subdirectory of the "
             ".devisolator directory holding its own ``Containerfile`` and "
             "``config.yaml``) to use instead of the project's default "
             "one."
    )

    init_parser = subparsers.add_parser(
            "init",
            help="Initialize a clean local configuration. Sub-configurations"
                 " are also allowed by specifying the ``--config``` flag.",
            description="Initialize a local configuration.")
    init_parser.add_argument(
            "-f",
            "--force",
            dest="init_force",
            action="store_true",
            help="Enforce overwriting existing files."
            )
    init_parser.add_argument(
            "-b",
            "--base",
            dest="init_base",
            type=str,
            default=DEFAULT_BASE,
            help="Specify the base image. See ``di list-base`` for possible "
                 f"values. Default = {DEFAULT_BASE}"
            )
    subparsers.add_parser(
            "list-base",
            help="List the base image definitions bundled with dev-isolator",
            description="List the base image definitions bundled with "
                        "dev-isolator, i.e. the images a project "
                        "``Containerfile`` can use as its base."
            )
    subparsers.add_parser(
            "list-targets",
            help="List the targets of the active config.yaml",
            description="List the target names defined in the "
                        "``targets`` section of the active "
                        "``config.yaml``, i.e. the targets a project "
                        "can run with ``target <name>``."
            )

    build_parser = subparsers.add_parser(
        "build",
        help="Build the current dev-isolator image",
        description="Build the image from the project settings or the "
                    "default one."
        )
    build_parser.add_argument(
            "-k",
            "--keep-container-file",
            dest="keep_container_file",
            action="store_true",
            help="Keep the generated Containerfile in case of an error."
            )

    subparsers.add_parser(
        "shell",
        help="Open a terminal/shell inside a container "
             "dev-isolator-<slug_of_the_current_path>--<tag>-<8 random "
             "characters>.",
    )

    clean_parser = subparsers.add_parser(
        "clean",
        help="Remove the current dev-isolator image.",
    )
    clean_group = clean_parser.add_mutually_exclusive_group()
    clean_group.add_argument(
        "-a",
        "--all",
        dest="clean_all",
        action="store_true",
        help="Remove all dev-isolator images instead of only "
             "the current one.",
    )
    clean_group.add_argument(
        "-l",
        "--local",
        dest="clean_local",
        action="store_true",
        help="Remove all images from the current .devisolator directory."
    )

    subparsers.add_parser(
        "opencode",
        add_help=False,
        help="Run opencode inside a container "
             "dev-isolator-<slug_of_the_current_path>--<tag>-<8 random "
             "characters>, passing all following arguments to it "
             "(for example ``opencode --port 4096``).",
    )

    subparsers.add_parser(
        "claude",
        add_help=False,
        help="Run claude inside a container "
             "dev-isolator-<slug_of_the_current_path>--<tag>-<8 random "
             "characters>, passing all following arguments to it "
             "(for example ``claude --model xyz``).",
    )

    subparsers.add_parser(
        "run",
        add_help=False,
        help="Run an arbitrary command inside the container, passing all "
             "following arguments through to it "
             "(for example ``run make test``).",
        )

    subparsers.add_parser(
        "target",
        add_help=False,
        help="Run a predefined target command from the 'targets' section "
             "of config.yaml inside the container.  Takes only positional "
             "arguments: the first one names the target, all following "
             "ones are passed to the target's command "
             "(for example ``target test -k foo``).",
        )

    subparsers.add_parser(
        "help",
        help="Show this help message and exit.",
        description="Show the dev-isolator help message and exit.",
    )

    return parser


def global_options_parser() -> argparse.ArgumentParser:
    """Return a parser exposing only the global options.

    The global options (``--verbose``, ``--shell`` and ``--config``)
    live on the top level parser and must precede the sub command.  A
    parser with just
    these options and no sub commands consumes the leading global
    options and leaves the sub command — together with everything
    following it and any options that precede it and are not global —
    in the remainder, so the tokens before the sub command can be
    validated on their own.
    """
    parser = argparse.ArgumentParser(prog="dev-isolator", add_help=False)
    parser.add_argument("--verbose", "-v", action="count", default=0)
    parser.add_argument("--shell", "-s", type=str, default=DEFAULT_SHELL)
    parser.add_argument("--config", "-c", type=str, default=None)
    return parser


def reject_precommand_options(parser, argv) -> None:
    """Abort on unknown options that appear before the sub command.

    The global options precede the sub command; everything after it
    belongs to the sub command (and, for the pass-through commands, to
    the command inside the container).  Parsing ``argv`` with the
    global-only parser leaves the sub command and any preceding unknown
    options in the remainder, so the options before the first
    non-option token — the sub command — are rejected as unrecognized.
    """
    _, remainder = global_options_parser().parse_known_args(argv)
    command_index = next(
        (i for i, token in enumerate(remainder)
         if not token.startswith("-")),
        len(remainder),
    )
    unknown = remainder[:command_index]
    if unknown:
        parser.error("unrecognized arguments: " + " ".join(unknown))


def parse_args(argv=None) -> argparse.Namespace:
    """Parse command line arguments into a namespace.

    Parse ``argv`` (by default ``sys.argv[1:]``) with the parser from
    ``create_parser``.  The global options (``--verbose``,
    ``--shell`` and ``--config``) live on the top level parser, so they
    must precede the sub command; unknown options before the sub command
    are rejected.

    The ``opencode``, ``claude``, ``run`` and ``target`` sub commands
    disable their own help handling and define no arguments, so every
    token following them is returned verbatim in the remainder by
    ``parse_known_args`` and stored on the ``opencode_args``,
    ``claude_args``, ``run_args`` and ``target_args`` attributes.  This
    keeps options of the command inside the container (for example
    ``opencode --port 4096``) from clashing with those of dev-isolator.
    Without an explicit subcommand, behave as if ``shell`` was requested
    with the default shell.
    """
    if argv is None:
        argv = sys.argv[1:]

    parser = create_parser()
    args, extras = parser.parse_known_args(argv)

    reject_precommand_options(parser, argv)

    if args.command is None:
        args.command = "shell"

    if args.command == "run":
        args.run_args = extras
    elif args.command == "opencode":
        args.opencode_args = extras
    elif args.command == "claude":
        args.claude_args = extras
    elif args.command == "target":
        args.target_args = extras
    return args


def shell(definition, args) -> int:
    """Handle the ``shell`` sub command.

    Look up the container definition of the current project and start
    an interactive shell (``args.shell``) inside its container.
    Returns the exit status of the container, or 1 on error.
    """
    try:
        return definition.run_container(args.shell)
    except Exception as e:
        print(f"An error occured: {e}", file=sys.stderr)
        return 1


def build(definition, args) -> int:
    """Handle the ``build`` sub command.

    Look up the container definition of the current project and build
    its image. Wwhen ``args.keep_container_file`` is set, keep the
    generated Containerfile in the current directory on a build error.
    Returns 0 on success, or 1 on error.
    """
    try:
        definition.build_container(keep_container_file=args.keep_container_file
                                   )
    except Exception as e:
        print(f"An error occured: {e}", file=sys.stderr)
        return 1
    return 0


def clean(definition, args) -> int:
    """Handle the ``clean`` sub command.

    Remove the image of the current project, every dev-isolator image
    when ``args.clean_all`` is set, or every image of the current project
    when ``args.clean_local`` is set.  Matching containers are stopped
    (eventually killed) and removed as well.  Returns 0 on success, or 1
    on error.
    """
    try:
        if args.clean_all:
            definition.clean_all_images()
        elif args.clean_local:
            definition.clean_local_images()
        else:
            definition.clean_image()
    except Exception as e:
        print(f"An error occured: {e}", file=sys.stderr)
        return 1
    return 0


def init_project(args) -> int:
    """Handle the ``init`` sub command.

    Create the ``.devisolator`` directory of the current project
    (or, with ``args.config``, its ``.devisolator/<config>``
    sub-configuration) and fill it with the ``Containerfile`` and
    ``config.yaml`` bundled with dev-isolator. The bundled
    ``Containerfile``'s ``FROM`` instruction is rewritten to
    reference the selected base image (when one is given):
    ``args.init_base`` (default ``ubuntu:26.04``) must match the
    ``DI_BASE`` value of one of the bundled base Containerfiles; an
    unknown base image aborts with an error.

    Existing files are kept and reported unless ``args.init_force``
    (``init -f``) is given, in which case they are overwritten.

    :param args: The parsed command line arguments (from
        :func:`parse_args`).
    :returns: 0 on success.
    :raises RuntimeError: if the requested base image is not bundled
        with dev-isolator.
    """
    force = args.init_force
    config = args.config

    if config:
        container_file = (Path(os.getcwd()) / DEV_ISOLATOR_DIR / config
                          / "Containerfile")
        config_file = (Path(os.getcwd()) / DEV_ISOLATOR_DIR / config
                       / CONFIG_FILE_NAME)
        user_dir = Path(os.getcwd()) / DEV_ISOLATOR_DIR / config
    else:
        container_file = (Path(os.getcwd()) / DEV_ISOLATOR_DIR
                          / "Containerfile")
        config_file = (Path(os.getcwd()) / DEV_ISOLATOR_DIR
                       / CONFIG_FILE_NAME)
        user_dir = Path(os.getcwd()) / DEV_ISOLATOR_DIR

    user_dir.mkdir(parents=True, exist_ok=True)
    src_container_file = Path(BUNDLE_DIR) / "Containerfile"
    src_config_file = Path(BUNDLE_DIR) / CONFIG_FILE_NAME

    if container_file.is_file() and not force:
        print(f"Containerfile {container_file} already exists and will not be "
              " overwritten. Use ``init -f`` to overwrite.")
    else:
        if args.init_base:
            base = BaseImage()
            lb = base.list_base_images()

            base_name = args.init_base
            if base_name not in lb:
                pkg_name = detect_package_manager(base_name)
                if not pkg_name:
                    raise RuntimeError(
                        f"Base image {args.init_base} unknown or "
                        "package manager not supported.")
            new_image = f"{base_name}"

            file_content = src_container_file.read_text(encoding="utf-8")
            file_content = replace_from_images(file_content, new_image)
            container_file.write_text(file_content)
        else:
            shutil.copy(src_container_file, container_file)
            # Works with python >= 3.14
            # src_container_file.copy(container_file)

    if config_file.is_file() and not force:
        print(f"Configuration file {config_file} already exists and will not "
              "be overwritten. Use ``init -f`` to overwrite.")
    else:
        shutil.copy(src_config_file, config_file)
        #  src_config_file.copy(config_file)

    print(f"Created new config in {user_dir}")

    return 0


def list_base() -> int:
    """Handle the ``list-base`` sub command.

    Print every base image definition bundled with dev-isolator, one per
    line, as ``<name>:<tag>`` (for example ``ubuntu:24.04``).  These are
    the names a
    project ``Containerfile`` may reference as its base image (see
    :mod:`dev_isolator.base_image`).  Unlike the project commands, it
    needs no ``.devisolator`` directory.  Returns 0.
    """
    base = BaseImage()
    for bi in base.list_base_images():
        print(f"{bi}")
    return 0


def list_targets(definition, args) -> int:
    """Handle the ``list-targets`` sub command.

    Print the target names defined in the ``targets`` section of the
    active ``config.yaml``, one per line, sorted alphabetically.  These
    are the targets a project can run with ``target <name>`` (see
    :func:`target`).  It only needs the ``config.yaml`` of the project,
    not a podman connection.  Returns 0.

    :param definition: The container definition of the current project
        (holding the active ``config.yaml``).
    :param args: The parsed command line arguments (from
        :func:`parse_args`).
    :returns: 0 on success.
    """
    targets = definition.config.config_targets()
    for name in sorted(targets):
        print(name)
    return 0


def in_container_command(definition, command, remainder) -> int:
    """Run ``command`` with ``remainder`` as its arguments inside the
    container of the current project.

    Used by the ``opencode``, ``claude``, ``run`` and ``target`` sub
    commands, which pass extra arguments through without parsing them.
    Returns the exit status of the container, or 1 on error.
    """
    try:
        return definition.run_container(
            command=[command, *remainder])
    except Exception as e:
        print(f"An error occured: {e}", file=sys.stderr)
        return 1


def _optional_filter(value):
    """Return the sentinel marking an unset optional argument.

    Used as the ``optional`` jinja2 filter of the ``target`` sub
    command: a placeholder written as ``{{ args[0] | optional }}``
    renders normally when ``args[0]`` was given on the command line;
    when it was not (the value is undefined or empty), the sentinel is
    returned instead and the token holding the placeholder is dropped
    from the command afterwards (see :func:`_drop_unset_optional`).
    """
    if value is None or isinstance(value, jinja2.Undefined) or value == "":
        return _OPTIONAL_UNSET
    return value


def _target_jinja_environment() -> jinja2.Environment:
    """Return the jinja2 environment for the ``target`` sub command.

    Like the addon template environment (see
    :func:`dev_isolator.base_image._jinja_environment`), it does not
    escape the rendered command (shell context, not HTML), but it uses
    strict undefined values, so a mandatory ``{{ args[0] }}`` without
    the ``optional`` filter aborts with an error instead of rendering
    as an empty string.  The ``optional`` filter marks arguments that
    may be omitted (see :func:`_optional_filter`).
    """
    environment = jinja2.Environment(
        autoescape=False,
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    )
    environment.filters["optional"] = _optional_filter
    return environment


def _drop_unset_optional(tokens: list[str]) -> list[str]:
    """Drop the tokens of a rendered target command that hold an unset
       optional argument.

    A token still containing the sentinel of the ``optional`` filter
    (see :func:`_optional_filter`) is dropped; when the token itself is
    not an option but the token before it is one, that preceding
    option is dropped as well, so an optional ``-k <value>`` pair
    disappears as a whole.

    :param tokens: The rendered command tokens.
    :returns: The tokens without the unset optional ones.
    """
    result: list[str] = []
    for token in tokens:
        if _OPTIONAL_UNSET in token:
            # An option token holding the unset placeholder itself
            # (for example ``-k={{ args[0] | optional }}``) is dropped
            # alone; a plain value token also drops its preceding
            # option.
            if (not token.startswith("-") and result
                    and result[-1].startswith("-")):
                result.pop()
            continue
        result.append(token)
    return result


def render_target_lines(template: str | list[str],
                        extra_args: list[str]) -> list[list[str]]:
    """Render the command template of a target into script lines.

    The template context provides the extra arguments given after the
    target name on the command line as ``args`` (the list of the
    arguments) and ``all_args`` (all of them joined with spaces).  In
    the string form, the whole template is rendered as one command line
    and split like a shell would (``shlex.split``); in the list form,
    every entry is one line of a script (a whole command line) and is
    rendered and split on its own.  Tokens holding an unset optional
    argument (see :func:`_drop_unset_optional`) are dropped from every
    line; a line that becomes empty afterwards is skipped.

    :param template: The target's command template (a string or a list
        of strings, as returned by
        :meth:`dev_isolator.config.Configuration.config_targets`).
    :param extra_args: The arguments given after the target name on
        the command line.
    :returns: The rendered script lines, each a list of command
        tokens.
    :raises RuntimeError: if the template references a mandatory
        argument (a ``{{ args[n] }}`` placeholder without the
        ``optional`` filter) that was not given.
    """
    environment = _target_jinja_environment()
    context = {"args": list(extra_args),
               "all_args": " ".join(extra_args)}

    if isinstance(template, str):
        template = [template]

    # List form: every entry is one line of a script.
    lines: list[list[str]] = []
    for entry in template:
        try:
            text = environment.from_string(entry).render(**context)
        except jinja2.UndefinedError as e:
            raise RuntimeError(
                f"Invalid target command {template}: {e}. A mandatory "
                "argument ({{ args[n] }} without the 'optional' filter) "
                "was not given on the command line; mark it optional in "
                "config.yaml with '{{ args[n] | optional }}' if it may "
                "be omitted.") from e
        tokens = _drop_unset_optional(shlex.split(text))
        if tokens:
            lines.append(tokens)
    return lines


def target(definition, args) -> int:
    """Handle the ``target`` sub command.

    Look up the target named by the first positional argument
    (``args.target_args[0]``) in the ``targets`` section of the active
    ``config.yaml`` and run its command inside the container of the
    current project, with the remaining positional arguments passed to
    the command (see :func:`render_target_lines`).  A bit like a
    predefined ``run``.  Takes only positional arguments.

    A command given as a string (or a single-line list) is executed
    directly; a multi-line list is a script: its lines run line by line
    in one shell session (``args.shell -ec script``), which stops at
    the first failing line.

    :param definition: The container definition of the current project.
    :param args: The parsed command line arguments (from
        :func:`parse_args`).
    :returns: The exit status of the container, or 1 on error.
    """
    targets = definition.config.config_targets()
    if not args.target_args:
        print("No target given. Available targets: "
              + (", ".join(sorted(targets)) if targets else "(none, "
                 "define them in the 'targets' section of config.yaml)"),
              file=sys.stderr)
        return 1
    name, *extra_args = args.target_args
    if name not in targets:
        print(f"Unknown target '{name}'. Available targets: "
              + (", ".join(sorted(targets)) if targets else "(none, "
                 "define them in the 'targets' section of config.yaml)"),
              file=sys.stderr)
        return 1
    try:
        lines = render_target_lines(targets[name], extra_args)
    except RuntimeError as e:
        print(f"An error occured: {e}", file=sys.stderr)
        return 1
    if not lines:
        print(f"An error occured: target '{name}' rendered an empty "
              "command.", file=sys.stderr)
        return 1
    if len(lines) == 1:
        command = lines[0]
        return in_container_command(definition, command[0], command[1:])
    # Several lines: run the script in one shell session; ``-e`` stops
    # it at the first failing line.
    script = "\n".join(" ".join(shlex.quote(token) for token in line)
                       for line in lines)
    return in_container_command(definition, args.shell, ["-ec", script])


def get_path(config):
    """Locate the build context, Containerfile, config file and addon
       directories for a project.

    The ``.devisolator`` directory is found by walking up from the
    current working directory (see :func:`search_path`); without one,
    dev-isolator's bundled ``data`` directory is used as build
    context. When ``config`` names a sub-configuration (a
    subdirectory of ``.devisolator`` holding its own Containerfile,
    ``config.yaml`` and addons), that sub-configuration is preferred
    over the project's own files.

    Every candidate is listed in precedence order: the
    sub-configuration first, then the ``.devisolator`` directory
    itself, then the bundled files. Only existing files (or
    directories, for the addon search paths) are kept.

    :param config: Name of the sub-configuration to use, or ``None``.
    :returns: The tuple ``(context_dir, container_file, config_file,
        addon_search_path)`` — build context directory, Containerfile
        path, ``config.yaml`` path and list of addon search
        directories, all resolved to absolute paths.
    :raises FileNotFoundError: if no Containerfile or no config file
        is found.
    :raises RuntimeError: if ``config`` is given but no
        ``.devisolator`` directory exists, or it has no such
        sub-directory.
    """
    di_path = search_path(DEV_ISOLATOR_DIR)
    bundle_path = BUNDLE_DIR

    if not di_path:
        context_dir = bundle_path
    else:
        context_dir = di_path.parent

    if (config and not di_path) or (config and
                                    not (di_path / config).is_dir()):
        raise RuntimeError("--config or DEVISOLATOR_CONFIG specified but "
                           f"'{config}' in the .devisolator directory "
                           "does not exists.")

    candidates_container_file: [Path] = list()
    candidates_config_file: [Path] = list()
    candidates_addon: [Path] = list()

    if di_path and config:
        candidates_container_file.append(di_path / config / "Containerfile")
        candidates_config_file.append(di_path / config / CONFIG_FILE_NAME)
        candidates_addon.append(di_path / config)
    if di_path:
        candidates_container_file.append(di_path / "Containerfile")
        candidates_config_file.append(di_path / CONFIG_FILE_NAME)
        candidates_addon.append(di_path)
    candidates_container_file.append(bundle_path / "Containerfile")
    candidates_config_file.append(bundle_path / CONFIG_FILE_NAME)
    candidates_addon.append(ADDON_DIR)

    config_file = [x.resolve() for x in candidates_config_file
                   if x.resolve().is_file()]
    container_file = [x.resolve() for x in
                      candidates_container_file
                      if x.resolve().is_file()]
    addon_search_path = [x.resolve() for x in candidates_addon
                         if x.resolve().is_dir()]

    if len(container_file) < 1:
        raise FileNotFoundError("No Containerfile found in "
                                f"{candidates_container_file}")
    container_file = container_file[0]

    if len(config_file) < 1:
        raise FileNotFoundError("No config file found in "
                                f"{candidates_config_file}")
    config_file = config_file[0]

    dprint(Verbosity.INFO, f"Containerfile found at {container_file}")
    dprint(Verbosity.INFO, f"Config file found at {config_file}")
    dprint(Verbosity.INFO, f"Addons seach path: {addon_search_path}")
    dprint(Verbosity.INFO, f"Context directory  {context_dir}")
    return context_dir, container_file, config_file, addon_search_path


def main() -> int:
    """Main CLI entry point.

    Parse the command line with ``parse_args``, apply the requested
    verbosity and dispatch to the handler of the selected sub command.
    Without an explicit subcommand, ``shell`` is executed with the
    default shell.

    The exit code of the selected sub command handler is returned
    unchanged (0 on success, 1 on error); any unhandled exception is
    reported as an error message and turned into the exit code 255.
    """
    try:
        ret_code = main_intern()
    except Exception as e:
        dprint(Verbosity.ERROR, f"{e}")
        ret_code = 255
    return ret_code


def main_intern() -> int:
    """Implement :func:`main`: parse the command line, set the
       verbosity and dispatch to the sub command's handler.

    Without an explicit subcommand, ``shell`` is executed with the
    default shell. Commands that need no project (``init``,
    ``list-base``, ``help``) are handled first; every other command
    needs the ``ContainerDefintion`` of the current project
    (located via :func:`get_path`). The ``DEVISOLATOR_CONFIG``
    environment variable acts as the default for ``--config``.
    """
    args = parse_args()

    if args.command == "help":
        create_parser().print_help()
        return 0

    set_verbosity(Verbosity(args.verbose))

    if os.getenv("DEVISOLATOR_CONFIG") and not args.config:
        args.config = os.getenv("DEVISOLATOR_CONFIG")
    if args.config == "default":
        args.config = None

    # Commands that do not require an initialized ContainerDefintion object
    if args.command == "init":
        return init_project(args)
    if args.command == "list-base":
        return list_base()

    context_dir, container_file, config_file, addon_search_path = get_path(
        args.config)
    config = Configuration(config_file, addon_search_path)
    definition = ContainerDefintion(context_dir, container_file, config,
                                    args.config)

    if args.command == "clean":
        return clean(definition, args)
    if args.command == "shell":
        return shell(definition, args)
    if args.command == "build":
        return build(definition, args)
    if args.command == "opencode":
        return in_container_command(definition, "opencode", args.opencode_args)
    if args.command == "claude":
        return in_container_command(definition, "claude", args.claude_args)
    if args.command == "run":
        if args.run_args:
            return in_container_command(definition, args.run_args[0],
                                        args.run_args[1:])
        # Without a command, behave like ``shell``.
        return shell(definition, args)
    if args.command == "target":
        return target(definition, args)
    if args.command == "list-targets":
        return list_targets(definition, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
