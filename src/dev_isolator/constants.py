"""Manage container definitions (build/create) for dev-isolator.

Copyright (C) 2026 Martin Köhler

This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it
under certain conditions; see LICENSE for details.
SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

IMAGE_PREFIX = "dev-isolator"
OCI_IMAGE_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
CONTAINER_NAME_SUFFIX_LENGTH = 8
TEMPLATES_DIR = Path(__file__).parent / "templates"
BUNDLE_DIR = "data"

DEV_ISOLATOR_DIR = ".devisolator"
CONFIG_FILE_NAME = "config.yaml"
ADDON_CONFIG_FILE_PREFIX = "config."
ADDON_CONFIG_FILE_SUFFIX = ".yaml"

ADDON_FILE_PREFIX = "addon."
ADDON_FILE_SUFFIX = ".jinja2"


