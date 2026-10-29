"""Constants shared across the dev-isolator modules.

Copyright (C) 2026 Martin Köhler

This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it
under certain conditions; see LICENSE for details.
SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

#: Repository part of the images built for a project (the image name
#: is ``dev-isolator:<slug_of_the_project_path>--<tag>``).
IMAGE_PREFIX = "dev-isolator"

#: Length of the random suffix of created container names, so several
#: containers of the same project can run in parallel.
CONTAINER_NAME_SUFFIX_LENGTH = 8

#: Directory holding the templates shipped with dev-isolator (the
#: jinja2 Containerfile templates).
TEMPLATES_DIR = Path(__file__).parent / "data" / "setup"

#: Directory holding the addons shipped with dev-isolator (the
#: jinja2 Containerfile templates).
ADDON_DIR = Path(__file__).parent / "data" / "addons"


#: Directory bundled with dev-isolator holding the default
#: ``Containerfile``, the addon templates and the addon configs.
BUNDLE_DIR = Path(__file__).parent / "data"

#: Directory inside a project that holds the project-specific
#: Containerfile and configuration.
DEV_ISOLATOR_DIR = ".devisolator"

#: File name of the main configuration file (enabled addons,
#: environment and volumes).
CONFIG_FILE_NAME = "config.yaml"

#: File name prefix and suffix of a per-addon configuration file:
#: ``config.<addon_name>.yaml``.
ADDON_CONFIG_FILE_PREFIX = "config."
ADDON_CONFIG_FILE_SUFFIX = ".yaml"

#: File name prefix and suffix of an addon template:
#: ``addon.<name>.jinja2``.
ADDON_FILE_PREFIX = "addon."
ADDON_FILE_SUFFIX = ".jinja2"

DEFAULT_BASE = "localhost/dev-isolator-ubuntu:26.04"
