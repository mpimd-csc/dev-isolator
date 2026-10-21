"""Tests for per-addon config files (config.<addon_name>.yaml).

SPDX-License-Identifier: GPL-3.0-or-later
"""

import os
from pathlib import Path

import pytest
import yaml
from dev_isolator.container_manager import (
    ContainerDefintion,
    parse_volume_spec,
    read_addon_config,
)
from dev_isolator.utils import Verbosity


@pytest.fixture
def definition(tmp_path: Path) -> ContainerDefintion:
    """A container definition for a minimal project with the
    (bundled) opencode and claude addons disabled."""
    config_dir = tmp_path / ".devisolator"
    config_dir.mkdir()
    container_file = config_dir / "Containerfile"
    container_file.write_text("FROM ubuntu:26.04\n")
    (config_dir / "addon.mine").write_text("RUN echo custom\n")
    (config_dir / "config.yaml").write_text("addons_enabled:\n  - mine\n")
    return ContainerDefintion(container_file, tmp_path)


class TestAddonConfigFile:
    """Test finding the config.<addon>.yaml file of an addon."""

    def test_project_config_is_used_when_present(
            self, definition, tmp_path: Path):
        """The .devisolator config file wins over the bundled one."""
        config_dir = tmp_path / ".devisolator"
        (config_dir / "config.mine.yaml").write_text("environment: []\n")

        found = definition.addon_config_file("mine")

        assert found == (config_dir / "config.mine.yaml").resolve()

    def test_missing_addon_config_does_not_use_other_addons(
            self, definition, tmp_path: Path):
        """A config file of another addon is not reused."""
        config_dir = tmp_path / ".devisolator"
        (config_dir / "config.other.yaml").write_text("environment: []\n")

        assert definition.addon_config_file("mine") is None

    def test_no_config_file_at_all(self, definition):
        """Without any config file, None is returned."""
        assert definition.addon_config_file("mine") is None


class TestReadAddonConfig:
    """Test parsing the content of config.<addon>.yaml files."""

    def test_environment_and_volumes_are_read(self, tmp_path: Path):
        """Both sections are returned as lists of strings."""
        config_file = tmp_path / "config.mine.yaml"
        config_file.write_text(
            "environment:\n  - VAR1\n  - VAR2\n"
            "volumes:\n  - /host:/container\n")

        config = read_addon_config(config_file)

        assert config == {
            "environment": ["VAR1", "VAR2"],
            "volumes": ["/host:/container"],
        }

    def test_missing_sections_default_to_empty(self, tmp_path: Path):
        """Absent sections are returned as empty lists."""
        config_file = tmp_path / "config.mine.yaml"
        config_file.write_text("environment:\n  - VAR1\n")

        config = read_addon_config(config_file)

        assert config == {"environment": ["VAR1"], "volumes": []}

    def test_empty_file_yields_empty_config(self, tmp_path: Path):
        """An empty addon config file is valid."""
        config_file = tmp_path / "config.mine.yaml"
        config_file.write_text("")

        assert read_addon_config(config_file) == {
            "environment": [], "volumes": []}

    def test_invalid_yaml_raises(self, tmp_path: Path):
        """Broken YAML raises a RuntimeError."""
        config_file = tmp_path / "config.mine.yaml"
        config_file.write_text("environment: [unclosed\n")

        with pytest.raises(RuntimeError, match="Invalid addon config"):
            read_addon_config(config_file)

    def test_non_mapping_raises(self, tmp_path: Path):
        """A list instead of a mapping raises a RuntimeError."""
        config_file = tmp_path / "config.mine.yaml"
        config_file.write_text("- VAR1\n")

        with pytest.raises(RuntimeError, match="expected a mapping"):
            read_addon_config(config_file)

    def test_non_string_environment_entries_raise(self, tmp_path: Path):
        """Non-string environment entries raise a RuntimeError."""
        config_file = tmp_path / "config.mine.yaml"
        config_file.write_text("environment:\n  - 1\n")

        with pytest.raises(RuntimeError, match="'environment' must be"):
            read_addon_config(config_file)

    def test_non_list_volumes_raise(self, tmp_path: Path):
        """A mapping instead of a list raises a RuntimeError."""
        config_file = tmp_path / "config.mine.yaml"
        config_file.write_text("volumes:\n  host: /container\n")

        with pytest.raises(RuntimeError, match="'volumes' must be"):
            read_addon_config(config_file)


class TestParseVolumeSpec:
    """Test splitting host:container[:mode] volume specs."""

    def test_read_write_default(self):
        assert parse_volume_spec("/host:/container") \
            == ("/host", "/container", "rw")

    def test_explicit_mode(self):
        assert parse_volume_spec("/host:/container:ro") \
            == ("/host", "/container", "ro")

    def test_missing_parts_raise(self):
        with pytest.raises(RuntimeError, match="Invalid volume spec"):
            parse_volume_spec("/only-host")

    def test_empty_parts_raise(self):
        with pytest.raises(RuntimeError, match="Invalid volume spec"):
            parse_volume_spec(":/container")


class TestAddonRunSettings:
    """Test collecting environment variables and volumes from
    addon config files."""

    @pytest.fixture
    def with_config(self, definition, tmp_path: Path, monkeypatch):
        """Add an addon config with env and volume entries."""
        config_dir = tmp_path / ".devisolator"
        (config_dir / "config.mine.yaml").write_text(
            "environment:\n"
            "  - DEV_ISOLATOR_TEST_VAR\n"
            "  - DEV_ISOLATOR_TEST_UNSET\n"
            "volumes:\n"
            "  - /host/data:/container/data:ro\n"
            f"  - ${{HOME}}/dev-isolator-test:/home/dev/data\n")
        monkeypatch.setenv("DEV_ISOLATOR_TEST_VAR", "test-value")
        return definition

    def test_environment_vars_are_taken_from_host(self, with_config):
        """Listed variables set on the host are passed with their
        value; unset ones are skipped."""
        environment, _ = with_config.addon_run_settings()

        assert environment == {"DEV_ISOLATOR_TEST_VAR": "test-value"}

    def test_volumes_are_expanded(self, with_config):
        """~ and ${VAR} in the host path are expanded; the mode is
        kept."""
        _, volumes = with_config.addon_run_settings()

        assert volumes == {
            "/host/data": {"bind": "/container/data", "mode": "ro"},
            str(Path.home() / "dev-isolator-test"):
                {"bind": "/home/dev/data", "mode": "rw"},
        }

    def test_addons_without_config_contribute_nothing(
            self, definition):
        """Without any addon config file, empty settings are
        returned."""
        assert definition.addon_run_settings() == ({}, {})


class FakeCreatedContainer:
    """Minimal stand-in for a created podman Container."""

    def __init__(self):
        self.status = "running"

    def reload(self):
        pass

    def start(self):
        pass

    def stop(self, timeout=0):
        pass


class FakeContainers:
    """Minimal stand-in for the podman containers manager.

    Simulates "no existing container" and records the arguments of
    the create call.
    """

    def __init__(self):
        self.created = None

    def get(self, name):
        import podman.errors
        raise podman.errors.NotFound("no such container")

    def create(self, image, **kwargs):
        self.created = kwargs
        return FakeCreatedContainer()


class FakeRunImages:
    """Minimal stand-in for the podman images manager."""

    def __init__(self):
        self.tags = ["dev-isolator:test"]
        self.id = "sha256:fake"

    def get(self, image_id):
        return self


class FakeRunClient:
    """Minimal stand-in for a podman.PodmanClient for run_container."""

    def __init__(self, images, containers):
        self.images = images
        self.containers = containers

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestRunContainerSettings:
    """Test that addon settings reach the container create call."""

    @pytest.fixture
    def creation_stub(self, monkeypatch):
        """Stub out the build step of run_container."""
        monkeypatch.setattr(
            ContainerDefintion, "build_container",
            lambda self, nocache=False: None)

    def test_environment_and_volumes_are_passed_to_create(
            self, tmp_path: Path, monkeypatch, creation_stub):
        """The environment variables and volumes of the addon
        config files are passed to containers.create()."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        (config_dir / "Containerfile").write_text("FROM ubuntu:26.04\n")
        (config_dir / "addon.mine").write_text("RUN echo custom\n")
        (config_dir / "config.yaml").write_text("addons_enabled:\n  - mine\n")
        (config_dir / "config.mine.yaml").write_text(
            "environment:\n  - DEV_ISOLATOR_TEST_VAR\n"
            "volumes:\n  - /host/data:/container/data:ro\n")
        monkeypatch.setenv("DEV_ISOLATOR_TEST_VAR", "test-value")

        containers = FakeContainers()
        monkeypatch.setattr(
            "dev_isolator.container_manager.podman.PodmanClient",
            lambda: FakeRunClient(FakeRunImages(), containers))
        monkeypatch.setattr(
            "dev_isolator.container_manager.subprocess.call",
            lambda cmd: 0)

        definition = ContainerDefintion(
            config_dir / "Containerfile", tmp_path)
        definition.run_container()

        assert containers.created["environment"] == {
            "DEV_ISOLATOR_TEST_VAR": "test-value"}
        assert containers.created["volumes"] == {
            "/host/data": {"bind": "/container/data", "mode": "ro"}}

    def test_without_addon_config_no_settings_are_passed(
            self, tmp_path: Path, monkeypatch, creation_stub):
        """Without addon config files, containers.create() receives
        empty environment and volumes."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        (config_dir / "Containerfile").write_text("FROM ubuntu:26.04\n")
        (config_dir / "addon.mine").write_text("RUN echo custom\n")
        (config_dir / "config.yaml").write_text("addons_enabled:\n  - mine\n")

        containers = FakeContainers()
        monkeypatch.setattr(
            "dev_isolator.container_manager.podman.PodmanClient",
            lambda: FakeRunClient(FakeRunImages(), containers))
        monkeypatch.setattr(
            "dev_isolator.container_manager.subprocess.call",
            lambda cmd: 0)

        definition = ContainerDefintion(
            config_dir / "Containerfile", tmp_path)
        definition.run_container()

        assert containers.created["environment"] == {}
        assert containers.created["volumes"] == {}
