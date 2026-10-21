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

ENV_REFERENCE_PATTERN = re.compile(r"ENV\[(\w+)\]")
DEV_ISOLATOR_DIR = ".devisolator"
CONFIG_FILE_NAME = "config.yaml"
ADDON_CONFIG_FILE_PREFIX = "config."
ADDON_CONFIG_FILE_SUFFIX = ".yaml"


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
