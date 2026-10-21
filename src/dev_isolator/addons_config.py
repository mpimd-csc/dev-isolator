"""Read and parse the addon configuration files of dev-isolator.

This module is only about the configuration files themselves:
which addons are enabled (``config.yaml``), what environment
variables and volumes an addon requests
(``config.<addon_name>.yaml``), and the syntax of volume specs.

Locating the files inside a build context is done by
:class:`dev_isolator.container_manager.ContainerDefintion`.

Copyright (C) 2026 Martin Köhler

This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it
under certain conditions; see LICENSE for details.
SPDX-License-Identifier: GPL-3.0-or-later
"""

import os
import re
from pathlib import Path

import yaml

from .utils import Verbosity, dprint

ENV_REFERENCE_PATTERN = re.compile(r"ENV\[(\w+)\]")
DEV_ISOLATOR_DIR = ".devisolator"
CONFIG_FILE_NAME = "config.yaml"
ADDON_CONFIG_FILE_PREFIX = "config."
ADDON_CONFIG_FILE_SUFFIX = ".yaml"
BUNDLE_DIR = "data"

ADDON_FILE_PREFIX = "addon."
ADDON_FILE_SUFFIX = ".jinja2"

# Sentinel marking a not-yet-computed cached value.
_UNSET = object()


def addon_name(path: Path) -> str:
    """Return the name of an addon file (``addon.<name>.jinja2``).

    :param path: Path to an addon file; the addon name is the part
        after ``addon.``, with the ``.jinja2`` template suffix
        removed, e.g. ``opencode`` for ``addon.opencode.jinja2``.
    :returns: The addon name.
    """
    return path.name.removeprefix(ADDON_FILE_PREFIX).removesuffix(
        ADDON_FILE_SUFFIX)


def read_enabled_addons(addons_file: Path) -> set[str]:
    """Return the addon names enabled in a config (``config.yaml``) file.

    The file is expected to have the shape::

        addons_enabled:
          - opencode
          - my-addon

    :param addons_file: Path to the config file (``config.yaml``).
    :returns: The set of enabled addon names (possibly empty).
    :raises RuntimeError: if the file is not valid YAML, does not
        contain a mapping, or the ``addons_enabled`` entry is not a
        list.
    """
    try:
        data = yaml.safe_load(addons_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RuntimeError(
            f"Invalid addons file {addons_file}: {e}") from e
    if data is None:
        return set()
    if not isinstance(data, dict):
        raise RuntimeError(
            f"Invalid addons file {addons_file}: "
            "expected a mapping with an 'addons_enabled' key")
    enabled = data.get("addons_enabled")
    if enabled is None:
        return set()
    if not isinstance(enabled, list):
        raise RuntimeError(
            f"Invalid addons file {addons_file}: "
            "'addons_enabled' must be a list of addon names")
    return {str(name) for name in enabled}


def read_addon_config(config_file: Path) -> dict:
    """Return the ``environment`` and ``volumes`` entries of an
    addon config file (``config.<addon_name>.yaml``).

    The file is expected to have the shape::

        environment:
          - VAR1
          - VAR2
        volumes:
          - path:/path/in/container
          - path:/path/in/container:ro

    Missing (or empty) sections are returned as empty lists.

    :param config_file: Path to the addon config file.
    :returns: A dict with the keys ``environment`` and ``volumes``,
        each holding a list of strings.
    :raises RuntimeError: if the file is not valid YAML, does not
        contain a mapping, or one of the sections is not a list of
        strings.
    """
    try:
        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RuntimeError(
            f"Invalid addon config file {config_file}: {e}") from e
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise RuntimeError(
            f"Invalid addon config file {config_file}: "
            "expected a mapping with 'environment' and/or 'volumes' keys")

    config = {"environment": [], "volumes": []}
    for key in config:
        entries = data.get(key)
        if entries is None:
            continue
        if not isinstance(entries, list) \
                or not all(isinstance(entry, str) for entry in entries):
            raise RuntimeError(
                f"Invalid addon config file {config_file}: "
                f"'{key}' must be a list of strings")
        config[key] = entries
    return config


def resolve_env_references(spec: str) -> str | None:
    """Replace ``ENV[NAME]`` references in a volume spec part with the
    value of the environment variable ``NAME``.

    :param spec: The volume spec part; every ``ENV[NAME]`` occurrence
        (with ``NAME`` an identifier) is replaced in place.
    :returns: The spec with all references resolved, or ``None``
        when a referenced environment variable is not set (i.e.
        could not be replaced).
    """
    # An unset variable turns a None replacement into an empty
    # string, so unresolvable references must be detected before
    # replacing them.
    names = ENV_REFERENCE_PATTERN.findall(spec)
    if any(os.environ.get(name) is None for name in names):
        return None
    return ENV_REFERENCE_PATTERN.sub(
        lambda match: os.environ[match.group(1)], spec)


def parse_volume_spec(spec: str) -> tuple[str, str, str]:
    """Split a volume spec ``host:container[:mode]`` into its parts.

    :returns: The tuple ``(host_path, container_path, mode)``; the
        mode defaults to ``rw`` when the spec has no third part.
    :raises RuntimeError: if the spec does not have a host and a
        container path part.
    """
    parts = spec.split(":")
    if len(parts) == 2:
        host, container_path, mode = *parts, "rw"
    elif len(parts) == 3:
        host, container_path, mode = parts
    else:
        raise RuntimeError(
            f"Invalid volume spec '{spec}': expected "
            "'host_path:container_path[:mode]'")
    if not host or not container_path:
        raise RuntimeError(
            f"Invalid volume spec '{spec}': "
            "host and container path must be non-empty")
    return host, container_path, mode


class AddonsConfiguration:
    """Locate and read the configuration files controlling the addons.

    All configuration management lives here: finding the active
    ``config.yaml`` (see :meth:`config_file`), the ``addon.*`` files
    to append to the Containerfile (see :meth:`addon_files`) and the
    per-addon ``config.<addon_name>.yaml`` files with the environment
    variables and volumes of every addon (see
    :meth:`addon_run_settings`).

    The ``.devisolator`` directory inside the build context takes
    precedence over the directory bundled with dev-isolator: the
    first configuration file found is used and the bundled list is
    ignored entirely. Without any configuration file, every
    ``addon.*`` file is used.

    Computed results are cached for the lifetime of the instance, so
    repeated calls (e.g. during a build) evaluate each configuration
    file only once.
    """

    def __init__(self, context_dir=None, bundled_dir=None):
        """Set up the configuration for a build context.

        :param context_dir: Path to the build context directory.
            When ``None``, the bundled directory is used.
        :param bundled_dir: Optional path to the directory bundled
            with dev-isolator; only used for testing. When ``None``,
            the package's bundled ``data`` directory is used.
        """
        if context_dir:
            self.context_dir = Path(context_dir)
        else:
            self.context_dir = Path(__file__).parent / BUNDLE_DIR
        if bundled_dir:
            self.bundled_dir = Path(bundled_dir)
        else:
            self.bundled_dir = Path(__file__).parent / BUNDLE_DIR
        self._enabled_addons = _UNSET
        self._addon_files = _UNSET

    def config_file(self) -> Path | None:
        """Return the active ``config.yaml`` file controlling the addons.

        The ``.devisolator/config.yaml`` in the build context is used
        when it exists; otherwise the bundled ``config.yaml`` is used.

        :returns: The resolved (absolute) path of the active
                  ``config.yaml`` file, or ``None`` when neither
                  exists.
        """
        for directory in (self.context_dir / DEV_ISOLATOR_DIR,
                          self.bundled_dir):
            candidate = directory / CONFIG_FILE_NAME
            dprint(Verbosity.TRACE, f"Try addon file {candidate}")
            resolved = candidate.resolve()
            if resolved.is_file():
                return resolved
        return None

    def enabled_addons(self) -> set[str] | None:
        """Return the names of the enabled addons.

        Only the first ``config.yaml`` file found by
        :meth:`config_file` is used: the
        ``.devisolator/config.yaml`` in the build context when it
        exists, otherwise the bundled one.

        The result is computed once and cached for the lifetime of
        this instance, so repeated calls (e.g. from
        :meth:`addon_files` during a build) do not print the addon
        managing messages again.

        :returns: The set of enabled addon names, or ``None`` when
            no ``config.yaml`` file exists at all (in which case
            every ``addon.*`` file is used).
        :raises RuntimeError: if a ``config.yaml`` file is invalid.
        """
        if self._enabled_addons is _UNSET:
            addon_file = self.config_file()
            if not addon_file:
                enabled = None
            else:
                names = read_enabled_addons(addon_file)
                enabled = set(names)
                dprint(Verbosity.INFO,
                       f"Addons enabled in {addon_file}: {sorted(names)}")
            self._enabled_addons = enabled
        return self._enabled_addons

    def addon_files(self):
        """Return all ``addon.*`` files appended to the Containerfile.

        The addons bundled with dev-isolator come first, followed by
        any ``addon.*`` files in the ``.devisolator`` directory
        inside the build context (sorted by file name). Duplicates
        are removed, so when the build context *is* the bundled
        directory, every file is only returned once.

        Only addons whose name (the part after ``addon.`` with the
        ``.jinja2`` suffix removed, e.g. ``opencode`` for
        ``addon.opencode.jinja2``) is enabled via
        :meth:`enabled_addons` are returned. Without any
        ``config.yaml`` file, every ``addon.*`` file is returned.

        Like :meth:`enabled_addons`, the result is computed once and
        cached for the lifetime of this instance, so the addon
        managing messages are only printed once per build.

        :returns: List of resolved (absolute) paths to the addon files.
        """
        if self._addon_files is _UNSET:
            enabled = self.enabled_addons()

            addons = []
            seen = set()
            for directory in (self.bundled_dir,
                              self.context_dir / DEV_ISOLATOR_DIR):
                if not directory.is_dir():
                    continue
                for addon in sorted(directory.glob("addon.*")):
                    if not addon.is_file():
                        continue
                    name = addon_name(addon)
                    if enabled is not None and name not in enabled:
                        dprint(Verbosity.INFO,
                               f"Skipping disabled addon: {addon.name}")
                        continue
                    resolved = addon.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        addons.append(resolved)
            self._addon_files = addons
        return list(self._addon_files)

    def addon_config_file(self, addon_name: str) -> Path | None:
        """Return the config file (``config.<addon_name>.yaml``) of
        an addon.

        The ``.devisolator/config.<addon_name>.yaml`` in the build
        context is used when it exists; otherwise the bundled
        ``config.<addon_name>.yaml`` is used. Like
        :meth:`config_file`, the first found file wins.

        :param addon_name: Name of the addon (the part after
            ``addon.`` in the addon file name, with the ``.jinja2``
            suffix removed).
        :returns: The resolved (absolute) path of the addon's config
            file, or ``None`` when neither exists.
        """
        file_name = (f"{ADDON_CONFIG_FILE_PREFIX}{addon_name}"
                     f"{ADDON_CONFIG_FILE_SUFFIX}")
        for directory in (self.context_dir / DEV_ISOLATOR_DIR,
                          self.bundled_dir):
            candidate = directory / file_name
            dprint(Verbosity.TRACE, f"Try addon config file {candidate}")
            resolved = candidate.resolve()
            if resolved.is_file():
                return resolved
        return None

    def addon_run_settings(self) -> tuple[dict[str, str], dict[str, dict]]:
        """Return the environment variables and volume mounts
        requested by the enabled addons' config files.

        Every enabled addon with a ``config.<addon_name>.yaml`` file
        (see :meth:`addon_config_file`) contributes its entries:

        - ``environment`` names environment variables which are
          taken from the host environment (variables that are not
          set on the host are skipped with a message).
        - ``volumes`` holds ``host:container[:mode]`` specs;
          ``ENV[VAR]`` references are resolved (a spec referencing an
          unset variable is skipped), then ``~`` and ``${VAR}`` in
          the host path are expanded.

        :returns: The tuple ``(environment, volumes)`` where
            ``environment`` maps variable names to their host values
            and ``volumes`` maps expanded host paths to
            ``{"bind": container_path, "mode": mode}`` dicts, in the
            shape expected by the podman bindings'
            ``containers.create()``.
        :raises RuntimeError: if an addon config file is invalid.
        """
        environment: dict[str, str] = {}
        volumes: dict[str, dict] = {}
        for addon in self.addon_files():
            name = addon_name(addon)
            config_file = self.addon_config_file(name)
            if not config_file:
                continue
            config = read_addon_config(config_file)
            dprint(Verbosity.INFO,
                   f"Addon config for {addon_name}: {config_file}")
            for name in config["environment"]:
                value = os.environ.get(name)
                if value is None:
                    dprint(Verbosity.TRACE,
                           f"Environment variable {name} requested by "
                           f"{config_file.name} is not set; skipping")
                    continue
                environment[name] = value
            for spec in config["volumes"]:
                host, container_path, mode = parse_volume_spec(spec)
                host = resolve_env_references(host)
                container_path = resolve_env_references(container_path)
                mode = resolve_env_references(mode)
                if host is None or container_path is None or mode is None:
                    dprint(Verbosity.TRACE, f"Skipping {spec} due to missing"
                                            " environment variables.")
                    continue
                host = os.path.expanduser(os.path.expandvars(host))
                container_path = os.path.expanduser(
                        os.path.expandvars(container_path))
                volumes[host] = {"bind": container_path, "mode": mode}
        dprint(Verbosity.TRACE, f"Environment: {str(environment)}")
        dprint(Verbosity.TRACE, f"Volumes: {str(volumes)}")

        return environment, volumes
