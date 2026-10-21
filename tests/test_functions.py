"""Tests for the demo_function

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

import pytest
from dev_isolator.container_manager import ContainerDefintion


class TestMergedContainerfile:
    """Test appending addon.* files to the used Containerfile"""

    def test_bundled_addons_are_appended(self, tmp_path: Path):
        """All addon.* files of the bundled container directory are
        appended to the Containerfile."""
        container_file = tmp_path / "Containerfile"
        container_file.write_text("FROM ubuntu:26.04\n")

        definition = ContainerDefintion(container_file, tmp_path)
        merged = definition.merged_container_file_content()

        assert merged.startswith("FROM ubuntu:26.04\n")
        for addon in definition.addon_files():
            assert addon.name in merged
        # The bundled opencode addon is part of the package.
        assert any(a.name == "addon.opencode" for a in definition.addon_files())
        assert "opencode" in merged

    def test_context_addons_are_appended(self, tmp_path: Path):
        """addon.* files in a custom build context are appended after
        the bundled ones."""
        container_file = tmp_path / "Containerfile"
        container_file.write_text("FROM ubuntu:26.04\n")
        (tmp_path / "addon.mine").write_text("RUN echo custom\n")

        definition = ContainerDefintion(container_file, tmp_path)
        merged = definition.merged_container_file_content()

        names = [a.name for a in definition.addon_files()]
        assert "addon.mine" in names
        assert names.index("addon.mine") == len(names) - 1
        assert "RUN echo custom" in merged

    def test_no_duplicate_addons(self, tmp_path: Path):
        """When the build context is the bundled container directory
        itself, each addon is only appended once."""
        bundled = Path(__import__("dev_isolator.container_manager",
                                  fromlist=["__file__"]).__file__).parent \
            / "container"

        definition = ContainerDefintion(bundled / "Containerfile", bundled)
        addons = definition.addon_files()

        assert len(addons) == len({a for a in addons})
        assert addons  # the bundled directory ships at least one addon
