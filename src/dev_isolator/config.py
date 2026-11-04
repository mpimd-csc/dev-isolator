"""Read and parse the addon configuration files of dev-isolator.

This module is only about the configuration files themselves:
which addons are enabled (``config.yaml``), the environment
variables and volumes requested in ``config.yaml`` and by an addon
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
from .constants import (ADDON_FILE_PREFIX, ADDON_FILE_SUFFIX,
                        ADDON_CONFIG_FILE_PREFIX, ADDON_CONFIG_FILE_SUFFIX,
                        DEV_ISOLATOR_DIR)
import yaml

from .utils import Verbosity, dprint

ENV_REFERENCE_PATTERN = re.compile(r"ENV\[(\w+)\]")
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


def read_environment_and_volumes(config_file: Path) -> dict:
    """Return the ``environment`` and ``volumes`` entries of a config
    file (the main ``config.yaml`` or an addon
    ``config.<addon_name>.yaml``).

    The file is expected to have the shape::

        environment:
          - VAR1
          - VAR2
          - VAR3=value3
        volumes:
          - path:/path/in/container
          - path:/path/in/container:ro

    Missing (or empty) sections are returned as empty lists.

    :param config_file: Path to the config file.
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
            f"Invalid config file {config_file}: {e}") from e
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise RuntimeError(
            f"Invalid config file {config_file}: "
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


class Configuration:
    """Locate and read the configuration files controlling the addons.

    All configuration management lives here: the active ``config.yaml``
    (the ``config_file`` given to the constructor), the ``addon.*``
    files to append to the Containerfile (see :meth:`list_addon_files`),
    the per-addon ``config.<addon_name>.yaml`` files with the environment
    variables and volumes of every addon (see
    :meth:`addon_run_settings`) and the environment variables and
    volumes declared directly in the ``config.yaml`` (see
    :meth:`config_environment` and :meth:`config_volumes`).

    The ``.devisolator`` directory inside the build context takes
    precedence over the directory bundled with dev-isolator: the
    first configuration file found is used and the bundled list is
    ignored entirely.

    Computed results are cached for the lifetime of the instance, so
    repeated calls (e.g. during a build) evaluate each configuration
    file only once.
    """

    def __init__(self, config_file,
                 addon_search_paths):
        """Set up the configuration for a build context.

        :param config_file: Path to the active ``config.yaml`` file
            determining the enabled addons.
        :param addon_search_paths: List of directories searched for
            ``addon.*`` files and ``config.<addon_name>.yaml`` files,
            in precedence order (the first existing file wins).
        """
        self.config_file = config_file
        self.addon_search_paths = addon_search_paths
        self._enabled_addons = _UNSET
        self._addon_files = _UNSET

        try:
            self.yaml_data = (yaml.safe_load(
                              config_file.read_text(encoding="utf-8")))
        except yaml.YAMLError as e:
            raise RuntimeError(
                f"Invalid config file {config_file}: {e}") from e
        if self.yaml_data is None:
            self.yaml_data = {}
        if not isinstance(self.yaml_data, dict):
            raise RuntimeError(f"Invalid config file {config_file}: "
                               "expected a mapping with an 'addons' key")

    def enabled_addons(self) -> set[str]:
        """Return the names of the enabled addons.

        Reads the ``addons`` key of the ``config.yaml`` file given to
        the constructor (the active config file).

        The result is computed once and cached for the lifetime of
        this instance, so repeated calls (e.g. from
        :meth:`list_addon_files` during a build) do not print the addon
        managing messages again.

        :returns: The set of enabled addon names, or an empty set when
            the ``addons`` key is missing (no addons enabled).
        :raises RuntimeError: if the ``config.yaml`` file is invalid.
        """
        if self._enabled_addons is _UNSET:
            enabled = self.yaml_data.get("addons", None)
            if enabled is None:
                return set()
            if not isinstance(enabled, list):
                raise RuntimeError(f"Invalid config file {self.config_file}: "
                                   "'addons' must be a list of addon names")
            enabled = set({str(name) for name in enabled})

            dprint(Verbosity.INFO,
                   f"Addons enabled in {self.config_file}: "
                   f"{sorted(enabled)}")
            self._enabled_addons = enabled
        return self._enabled_addons

    def list_addon_files(self):
        """Return all ``addon.*`` files appended to the Containerfile.

        The addons bundled with dev-isolator come first, followed by
        any ``addon.*`` files in the ``.devisolator`` directory
        inside the build context (sorted by file name). Duplicates
        are removed, so when the build context *is* the bundled
        directory, every file is only returned once.

        Only addons whose name (the part after ``addon.`` with the
        ``.jinja2`` suffix removed, e.g. ``opencode`` for
        ``addon.opencode.jinja2``) is enabled via
        :meth:`enabled_addons` are returned; addons whose name is not
        in the enabled set are skipped with a message.

        Like :meth:`enabled_addons`, the result is computed once and
        cached for the lifetime of this instance, so the addon
        managing messages are only printed once per build.

        :returns: List of resolved (absolute) paths to the addon files.
        """
        if self._addon_files is _UNSET:
            enabled = self.enabled_addons()

            addons = []
            seen = set()
            for directory in self.addon_search_paths:
                if not directory.is_dir():
                    continue
                for addon in sorted(directory.glob("addon.*")):
                    if not addon.is_file():
                        continue
                    name = addon_name(addon)
                    if name not in enabled:
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
        ``config.<addon_name>.yaml`` is used. As with the addon file
        lookup, the first found file wins (the search paths are in
        precedence order).

        :param addon_name: Name of the addon (the part after
            ``addon.`` in the addon file name, with the ``.jinja2``
            suffix removed).
        :returns: The resolved (absolute) path of the addon's config
            file, or ``None`` when neither exists.
        """
        file_name = (f"{ADDON_CONFIG_FILE_PREFIX}{addon_name}"
                     f"{ADDON_CONFIG_FILE_SUFFIX}")
        for directory in self.addon_search_paths:
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

        - ``environment`` entries are either ``NAME``, which is
          taken from the host environment (variables that are not
          set on the host are skipped with a message), or
          ``NAME=value``, where ``value`` is used as-is.
        - ``volumes`` holds ``host:container[:mode]`` specs;
          ``ENV[VAR]`` references are resolved (a spec referencing an
          unset variable is skipped), then ``~`` and ``${VAR}`` in
          the host path are expanded.

        :returns: The tuple ``(environment, volumes)`` where
            ``environment`` maps variable names to their values
            and ``volumes`` maps expanded host paths to
            ``{"bind": container_path, "mode": mode}`` dicts, in the
            shape expected by the podman bindings'
            ``containers.create()``.
        :raises RuntimeError: if an addon config file is invalid.
        """
        environment: dict[str, str] = {}
        volumes: dict[str, dict] = {}
        for addon in self.list_addon_files():
            name = addon_name(addon)
            config_file = self.addon_config_file(name)
            if not config_file:
                continue
            dprint(Verbosity.INFO,
                   f"Addon config for {name}: {config_file}")
            try:
                data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
            except Exception as e:
                raise RuntimeError(f"Failed to load addon config {config_file}"
                                   ": {e}") from e

            addon_environment = self._get_environment(data)
            addon_volumes = self._get_volumes(data)

            environment |= addon_environment
            volumes |= addon_volumes
        dprint(Verbosity.TRACE, f"Environment: {str(environment)}")
        dprint(Verbosity.TRACE, f"Volumes: {str(volumes)}")

        return environment, volumes

    def config_environment(self) -> dict[str, str]:
        """Return the environment variables declared in the active
        ``config.yaml``.

        The ``environment`` entries are either ``NAME`` (taken from
        the host) or ``NAME=value`` (used as given); both are
        processed exactly like those of an addon config file
        (see :meth:`_get_environment`).

        :returns: A dict mapping variable names to their values (the
            environment part only), or ``{}`` when none are declared.
        :raises RuntimeError: if the ``config.yaml`` is invalid.
        """
        return self._get_environment(self.yaml_data)

    def config_volumes(self) -> dict[str, dict]:
        """Return the volume mounts declared in the active
        ``config.yaml``.

        The ``volumes`` entries are ``host:container[:mode]`` specs;
        they are processed exactly like those of an addon config file
        (see :meth:`_get_volumes`).

        :returns: A dict mapping expanded host paths to
            ``{"bind": container_path, "mode": mode}`` dicts (the
            volumes part only), or ``{}`` when none are declared.
        :raises RuntimeError: if the ``config.yaml`` is invalid.
        """
        return self._get_volumes(self.yaml_data)

    def build_get_local_context(self):
        build = self.yaml_data.get("build", {})

        return bool(build.get("local_context", False))

    def build_get_nocache(self):
        build = self.yaml_data.get("build", {})
        return bool(build.get("nocache", False))

    #
    # Internal Routines
    #
    def _get_volumes(self, config: dict) -> dict[str, dict]:
        """Turn the ``volumes`` entries of a config mapping into the
        podman volume-mount dict.

        The ``volumes`` entries are ``host:container[:mode]`` specs;
        ``ENV[VAR]`` references are resolved (a spec referencing an unset
        variable is skipped) and then ``~`` and ``${VAR}`` in the host path
        are expanded. The same processing is used for both the main
        ``config.yaml`` and an addon ``config.<addon_name>.yaml``.

        :param config: A config mapping holding a ``volumes`` key (a list
            of ``host:container[:mode]`` specs).
        :returns: A dict mapping expanded host paths to
            ``{"bind": container_path, "mode": mode}`` dicts, in the shape
            expected by the podman bindings' ``containers.create()``.
        """
        volumes: dict[str, dict] = {}

        volume_entries = config.get("volumes", [])
        for spec in volume_entries:
            host, container_path, mode = parse_volume_spec(spec)
            host = resolve_env_references(host)
            container_path = resolve_env_references(container_path)
            mode = resolve_env_references(mode)
            if host is None or container_path is None or mode is None:
                dprint(Verbosity.TRACE, f"Skipping {spec} due to missing "
                                        "environment variables.")
                continue
            host = os.path.expanduser(os.path.expandvars(host))
            container_path = os.path.expanduser(
                    os.path.expandvars(container_path))
            host_path = Path(host)
            if host_path.exists():
                volumes[host] = {"bind": container_path, "mode": mode}
            else:
                dprint(Verbosity.DEBUG, f"Skipping non exitsing volume {host}")
        return volumes

    def _get_environment(self, config: dict) -> dict[str, str]:
        """Turn the ``environment`` entries of a config mapping into the
        podman environment dict.

        The ``environment`` entries are either ``NAME``, which is taken
        from the host environment (variables that are not set on the host
        are skipped with a message), or ``NAME=value``, where ``value`` is
        used as-is. The same processing is used for both the main
        ``config.yaml`` and an addon ``config.<addon_name>.yaml``.

        :param config: A config mapping holding an ``environment`` key (a
            list of ``NAME`` or ``NAME=value`` entries).
        :returns: A dict mapping variable names to their values.
        """
        environment: dict[str, str] = {}

        environment_entries = config.get("environment", [])
        for entry in environment_entries:
            name, sep, value = entry.partition("=")
            if name and not sep:
                # Only a name: copy the variable from the host environment.
                value = os.environ.get(name)
                if value is None:
                    dprint(Verbosity.TRACE,
                           f"Environment variable {name} is not set; skipping")
                    continue
            elif not name:
                dprint(Verbosity.TRACE,
                       f"Invalid environment entry '{entry}'; skipping")
                continue
            environment[name] = value

        return environment
