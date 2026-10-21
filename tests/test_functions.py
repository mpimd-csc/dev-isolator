"""Tests for the demo_function

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

import pytest
from dev_isolator.container_manager import ContainerDefintion
from dev_isolator.main import create_parser


class TestMergedContainerfile:
    """Test appending addon.* files to the used Containerfile"""

    def test_bundled_addons_are_appended(self, tmp_path: Path):
        """Addon.* files enabled in the bundled addons.yaml are
        appended to the Containerfile."""
        container_file = tmp_path / "Containerfile"
        container_file.write_text("FROM ubuntu:26.04\n")

        definition = ContainerDefintion(container_file, tmp_path)
        merged = definition.merged_container_file_content()

        assert merged.startswith("FROM ubuntu:26.04\n")
        for addon in definition.addon_files():
            assert addon.name in merged
        # The bundled opencode addon is enabled in the bundled
        # addons.yaml and part of the package.
        assert any(a.name == "addon.opencode" for a in definition.addon_files())
        assert "opencode" in merged

    def test_enabled_context_addons_are_appended(self, tmp_path: Path):
        """addon.* files in a custom build context are appended after
        the bundled ones when enabled in .devisolator/addons.yaml."""
        container_file = tmp_path / "Containerfile"
        container_file.write_text("FROM ubuntu:26.04\n")
        (tmp_path / "addon.mine").write_text("RUN echo custom\n")
        (tmp_path / "addons.yaml").write_text("enabled:\n  - mine\n")

        definition = ContainerDefintion(container_file, tmp_path)
        merged = definition.merged_container_file_content()

        names = [a.name for a in definition.addon_files()]
        assert "addon.mine" in names
        assert names.index("addon.mine") == len(names) - 1
        assert "RUN echo custom" in merged

    def test_disabled_context_addons_are_skipped(self, tmp_path: Path):
        """addon.* files not enabled in any addons.yaml are not
        appended to the Containerfile."""
        container_file = tmp_path / "Containerfile"
        container_file.write_text("FROM ubuntu:26.04\n")
        (tmp_path / "addon.mine").write_text("RUN echo custom\n")

        definition = ContainerDefintion(container_file, tmp_path)
        merged = definition.merged_container_file_content()

        names = [a.name for a in definition.addon_files()]
        assert "addon.mine" not in names
        assert "RUN echo custom" not in merged

    def test_enabled_addons_are_joined(self, tmp_path: Path):
        """The bundled and the project addons.yaml lists are joined."""
        container_file = tmp_path / "Containerfile"
        container_file.write_text("FROM ubuntu:26.04\n")
        (tmp_path / "addon.mine").write_text("RUN echo custom\n")
        (tmp_path / "addons.yaml").write_text("enabled:\n  - mine\n")

        definition = ContainerDefintion(container_file, tmp_path)
        enabled = definition.enabled_addons()

        # "opencode" comes from the bundled addons.yaml, "mine" from
        # the project's .devisolator/addons.yaml.
        assert enabled == {"opencode", "mine"}
        names = [a.name for a in definition.addon_files()]
        assert "addon.opencode" in names
        assert "addon.mine" in names

    def test_no_addons_yaml_enables_everything(self, tmp_path: Path,
                                               monkeypatch):
        """Without any addons.yaml file every addon.* file is used."""
        container_file = tmp_path / "Containerfile"
        container_file.write_text("FROM ubuntu:26.04\n")
        (tmp_path / "addon.mine").write_text("RUN echo custom\n")

        # Hide every addons.yaml file to simulate "no config at all".
        real_is_file = Path.is_file
        monkeypatch.setattr(
            Path, "is_file",
            lambda self: False if self.name == "addons.yaml"
            else real_is_file(self))

        definition = ContainerDefintion(container_file, tmp_path)

        assert definition.addons_config_files() == []
        assert definition.enabled_addons() is None
        names = [a.name for a in definition.addon_files()]
        assert "addon.mine" in names

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


class FakeImage:
    """Minimal stand-in for a podman Image."""

    def __init__(self, tags):
        self.tags = tags


class TestIsDevisolatorImage:
    """Test recognizing dev-isolator images by their tags."""

    def test_plain_prefix_tag_matches(self):
        image = FakeImage(["dev-isolator:my-project"])
        assert ContainerDefintion._is_devisolator_image(image)

    def test_localhost_prefixed_tag_matches(self):
        # podman tags images built locally with a "localhost/" prefix.
        image = FakeImage(["localhost/dev-isolator:my-project"])
        assert ContainerDefintion._is_devisolator_image(image)

    def test_registry_prefixed_tag_matches(self):
        image = FakeImage(["quay.io/example/dev-isolator:my-project"])
        assert ContainerDefintion._is_devisolator_image(image)

    def test_similar_prefix_does_not_match(self):
        image = FakeImage(["dev-isolator-smoketest:latest"])
        assert not ContainerDefintion._is_devisolator_image(image)

    def test_unrelated_tags_do_not_match(self):
        image = FakeImage(["ubuntu:24.04"])
        assert not ContainerDefintion._is_devisolator_image(image)

    def test_untagged_image_does_not_match(self):
        image = FakeImage([])
        assert not ContainerDefintion._is_devisolator_image(image)


class TestCleanParser:
    """Test the CLI arguments of the clean subcommand."""

    def test_clean_defaults_to_current_image(self):
        parser = create_parser()
        args = parser.parse_args(["clean"])
        assert args.command == "clean"
        assert args.clean_all is False

    def test_clean_all_long_option(self):
        parser = create_parser()
        args = parser.parse_args(["clean", "--all"])
        assert args.command == "clean"
        assert args.clean_all is True

    def test_clean_all_short_option(self):
        parser = create_parser()
        args = parser.parse_args(["clean", "-a"])
        assert args.command == "clean"
        assert args.clean_all is True
