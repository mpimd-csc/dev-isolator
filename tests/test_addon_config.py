"""Tests for per-addon config files (config.<addon_name>.yaml).

SPDX-License-Identifier: GPL-3.0-or-later
"""

import os
from pathlib import Path

import pytest
import yaml
from dev_isolator.addons_config import parse_volume_spec, \
    read_environment_and_volumes, resolve_env_references, \
    run_settings_from_config
from dev_isolator.container_manager import ContainerDefintion
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
    return ContainerDefintion(tmp_path, container_file,
                              config_dir / "config.yaml", [config_dir])


class TestAddonConfigFile:
    """Test finding the config.<addon>.yaml file of an addon."""

    def test_project_config_is_used_when_present(
            self, definition, tmp_path: Path):
        """The .devisolator config file wins over the bundled one."""
        config_dir = tmp_path / ".devisolator"
        (config_dir / "config.mine.yaml").write_text("environment: []\n")

        found = definition.addons.addon_config_file("mine")

        assert found == (config_dir / "config.mine.yaml").resolve()

    def test_missing_addon_config_does_not_use_other_addons(
            self, definition, tmp_path: Path):
        """A config file of another addon is not reused."""
        config_dir = tmp_path / ".devisolator"
        (config_dir / "config.other.yaml").write_text("environment: []\n")

        assert definition.addons.addon_config_file("mine") is None

    def test_no_config_file_at_all(self, definition):
        """Without any config file, None is returned."""
        assert definition.addons.addon_config_file("mine") is None


class TestReadEnvironmentAndVolumes:
    """Test parsing the environment/volumes content of a config file."""

    def test_environment_and_volumes_are_read(self, tmp_path: Path):
        """Both sections are returned as lists of strings."""
        config_file = tmp_path / "config.mine.yaml"
        config_file.write_text(
            "environment:\n  - VAR1\n  - VAR2\n"
            "volumes:\n  - /host:/container\n")

        config = read_environment_and_volumes(config_file)

        assert config == {
            "environment": ["VAR1", "VAR2"],
            "volumes": ["/host:/container"],
        }

    def test_missing_sections_default_to_empty(self, tmp_path: Path):
        """Absent sections are returned as empty lists."""
        config_file = tmp_path / "config.mine.yaml"
        config_file.write_text("environment:\n  - VAR1\n")

        config = read_environment_and_volumes(config_file)

        assert config == {"environment": ["VAR1"], "volumes": []}

    def test_empty_file_yields_empty_config(self, tmp_path: Path):
        """An empty addon config file is valid."""
        config_file = tmp_path / "config.mine.yaml"
        config_file.write_text("")

        assert read_environment_and_volumes(config_file) == {
            "environment": [], "volumes": []}

    def test_invalid_yaml_raises(self, tmp_path: Path):
        """Broken YAML raises a RuntimeError."""
        config_file = tmp_path / "config.mine.yaml"
        config_file.write_text("environment: [unclosed\n")

        with pytest.raises(RuntimeError, match="Invalid config"):
            read_environment_and_volumes(config_file)

    def test_non_mapping_raises(self, tmp_path: Path):
        """A list instead of a mapping raises a RuntimeError."""
        config_file = tmp_path / "config.mine.yaml"
        config_file.write_text("- VAR1\n")

        with pytest.raises(RuntimeError, match="expected a mapping"):
            read_environment_and_volumes(config_file)

    def test_non_string_environment_entries_raise(self, tmp_path: Path):
        """Non-string environment entries raise a RuntimeError."""
        config_file = tmp_path / "config.mine.yaml"
        config_file.write_text("environment:\n  - 1\n")

        with pytest.raises(RuntimeError, match="'environment' must be"):
            read_environment_and_volumes(config_file)

    def test_non_list_volumes_raise(self, tmp_path: Path):
        """A mapping instead of a list raises a RuntimeError."""
        config_file = tmp_path / "config.mine.yaml"
        config_file.write_text("volumes:\n  host: /container\n")

        with pytest.raises(RuntimeError, match="'volumes' must be"):
            read_environment_and_volumes(config_file)


class TestResolveEnvReferences:
    """Test resolving ENV[NAME] references in volume specs."""

    def test_reference_is_replaced(self, monkeypatch):
        monkeypatch.setenv("DEV_ISOLATOR_TEST_VAR", "/host/data")

        assert resolve_env_references(
            "ENV[DEV_ISOLATOR_TEST_VAR]:/container") \
            == "/host/data:/container"

    def test_multiple_references_are_replaced(self, monkeypatch):
        monkeypatch.setenv("DEV_ISOLATOR_TEST_VAR", "value")

        assert resolve_env_references(
            "ENV[DEV_ISOLATOR_TEST_VAR]:/c:ENV[DEV_ISOLATOR_TEST_VAR]") \
            == "value:/c:value"

    def test_unset_reference_returns_none(self, monkeypatch):
        """A reference to an unset environment variable cannot be
        replaced; the whole spec resolves to None."""
        monkeypatch.delenv("DEV_ISOLATOR_TEST_UNSET", raising=False)

        assert resolve_env_references(
            "ENV[DEV_ISOLATOR_TEST_UNSET]:/c") is None
        # One resolvable and one unresolvable reference do not mix.
        monkeypatch.setenv("DEV_ISOLATOR_TEST_VAR", "value")
        assert resolve_env_references(
            "ENV[DEV_ISOLATOR_TEST_VAR]:ENV[DEV_ISOLATOR_TEST_UNSET]") \
            is None


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
            f"  - ${{HOME}}/dev-isolator-test:/home/dev/data\n"
            "  - ENV[DEV_ISOLATOR_TEST_HOST]:/env/data\n")
        monkeypatch.setenv("DEV_ISOLATOR_TEST_VAR", "test-value")
        monkeypatch.setenv("DEV_ISOLATOR_TEST_HOST", "/env-host")
        return definition

    def test_environment_vars_are_taken_from_host(self, with_config):
        """Listed variables set on the host are passed with their
        value; unset ones are skipped."""
        environment, _ = with_config.addons.addon_run_settings()

        assert environment == {"DEV_ISOLATOR_TEST_VAR": "test-value"}

    def test_volumes_are_expanded(self, with_config):
        """~ and ${VAR} in the host path are expanded, ENV[VAR]
        references are resolved; the mode is kept."""
        _, volumes = with_config.addons.addon_run_settings()

        assert volumes == {
            "/host/data": {"bind": "/container/data", "mode": "ro"},
            str(Path.home() / "dev-isolator-test"):
                {"bind": "/home/dev/data", "mode": "rw"},
            "/env-host": {"bind": "/env/data", "mode": "rw"},
        }

    def test_unset_env_reference_skips_volume(self, definition, tmp_path: Path,
                                              monkeypatch):
        """A volume spec referencing an unset variable is skipped."""
        config_dir = tmp_path / ".devisolator"
        (config_dir / "config.mine.yaml").write_text(
            "volumes:\n"
            "  - ENV[DEV_ISOLATOR_TEST_UNSET]:/container\n")
        monkeypatch.delenv("DEV_ISOLATOR_TEST_UNSET", raising=False)

        _, volumes = definition.addons.addon_run_settings()

        assert volumes == {}

    def test_addons_without_config_contribute_nothing(
            self, definition):
        """Without any addon config file, empty settings are
        returned."""
        assert definition.addons.addon_run_settings() == ({}, {})


class TestRunSettingsFromConfig:
    """Test turning a parsed config mapping into run settings."""

    def test_environment_and_volumes_are_processed(self, monkeypatch):
        """Environment variables are taken from the host and volumes
        are expanded, exactly as for an addon config."""
        monkeypatch.setenv("DEV_ISOLATOR_TEST_VAR", "test-value")
        monkeypatch.setenv("DEV_ISOLATOR_TEST_HOST", "/env-host")

        environment, volumes = run_settings_from_config({
            "environment": ["DEV_ISOLATOR_TEST_VAR", "DEV_ISOLATOR_TEST_UNSET"],
            "volumes": ["/host/data:/container/data:ro",
                        "ENV[DEV_ISOLATOR_TEST_HOST]:/env/data"],
        })

        assert environment == {"DEV_ISOLATOR_TEST_VAR": "test-value"}
        assert volumes == {
            "/host/data": {"bind": "/container/data", "mode": "ro"},
            "/env-host": {"bind": "/env/data", "mode": "rw"},
        }

    def test_empty_config_yields_empty_settings(self):
        """An empty mapping yields empty environment and volumes."""
        assert run_settings_from_config(
            {"environment": [], "volumes": []}) == ({}, {})


class TestConfigRunSettings:
    """Test collecting environment variables and volumes from the
    active config.yaml."""

    def test_config_env_and_volumes_are_read(
            self, definition, tmp_path: Path, monkeypatch):
        """The environment and volumes sections of config.yaml are
        processed like an addon config."""
        config_dir = tmp_path / ".devisolator"
        (config_dir / "config.yaml").write_text(
            "environment:\n"
            "  - DEV_ISOLATOR_TEST_VAR\n"
            "  - DEV_ISOLATOR_TEST_UNSET\n"
            "volumes:\n"
            "  - /host/data:/container/data:ro\n"
            "  - ENV[DEV_ISOLATOR_TEST_HOST]:/env/data\n")
        monkeypatch.setenv("DEV_ISOLATOR_TEST_VAR", "test-value")
        monkeypatch.setenv("DEV_ISOLATOR_TEST_HOST", "/env-host")

        environment, volumes = definition.addons.config_run_settings()

        assert environment == {"DEV_ISOLATOR_TEST_VAR": "test-value"}
        assert volumes == {
            "/host/data": {"bind": "/container/data", "mode": "ro"},
            "/env-host": {"bind": "/env/data", "mode": "rw"},
        }

    def test_missing_sections_yield_empty_settings(
            self, definition, tmp_path: Path):
        """A config.yaml without environment/volumes yields empty
        settings."""
        config_dir = tmp_path / ".devisolator"
        (config_dir / "config.yaml").write_text("addons_enabled:\n  - mine\n")

        assert definition.addons.config_run_settings() == ({}, {})

    def test_no_config_file_yields_empty_settings(self, definition):
        """Without any config.yaml, empty settings are returned."""
        assert definition.addons.config_run_settings() == ({}, {})

    def test_invalid_config_file_raises(
            self, definition, tmp_path: Path):
        """An invalid config.yaml raises a RuntimeError."""
        config_dir = tmp_path / ".devisolator"
        (config_dir / "config.yaml").write_text("environment: [unclosed\n")

        with pytest.raises(RuntimeError, match="Invalid config"):
            definition.addons.config_run_settings()


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

    def remove(self):
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
            tmp_path, config_dir / "Containerfile",
            config_dir / "config.yaml", [config_dir])
        definition.run_container()

        assert containers.created["environment"] == {
            "DEV_ISOLATOR_TEST_VAR": "test-value"}
        assert containers.created["volumes"] == {
            "/host/data": {"bind": "/container/data", "mode": "ro"},
            os.getcwd(): {"bind": "/workspace", "mode": "rw"}}

    def test_without_addon_config_no_settings_are_passed(
            self, tmp_path: Path, monkeypatch, creation_stub):
        """Without addon config files, containers.create() receives
        an empty environment and only the workspace volume."""
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
            tmp_path, config_dir / "Containerfile",
            config_dir / "config.yaml", [config_dir])
        definition.run_container()

        assert containers.created["environment"] == {}
        assert containers.created["volumes"] == {
            os.getcwd(): {"bind": "/workspace", "mode": "rw"}}

    def test_config_yaml_settings_are_passed_to_create(
            self, tmp_path: Path, monkeypatch, creation_stub):
        """The environment and volumes declared in config.yaml reach
        containers.create()."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        (config_dir / "Containerfile").write_text("FROM ubuntu:26.04\n")
        (config_dir / "config.yaml").write_text(
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
            tmp_path, config_dir / "Containerfile",
            config_dir / "config.yaml", [config_dir])
        definition.run_container()

        assert containers.created["environment"] == {
            "DEV_ISOLATOR_TEST_VAR": "test-value"}
        assert containers.created["volumes"] == {
            "/host/data": {"bind": "/container/data", "mode": "ro"},
            os.getcwd(): {"bind": "/workspace", "mode": "rw"}}

    def test_addon_settings_override_config_yaml_settings(
            self, tmp_path: Path, monkeypatch, creation_stub):
        """Addon settings override those from config.yaml, the
        workspace mount always wins."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        (config_dir / "Containerfile").write_text("FROM ubuntu:26.04\n")
        (config_dir / "addon.mine").write_text("RUN echo custom\n")
        (config_dir / "config.yaml").write_text(
            "addons_enabled:\n  - mine\n"
            "environment:\n  - DEV_ISOLATOR_TEST_VAR\n"
            "volumes:\n  - /host/data:/from/config:rw\n")
        (config_dir / "config.mine.yaml").write_text(
            "volumes:\n  - /host/data:/from/addon:ro\n")
        monkeypatch.setenv("DEV_ISOLATOR_TEST_VAR", "config-value")

        containers = FakeContainers()
        monkeypatch.setattr(
            "dev_isolator.container_manager.podman.PodmanClient",
            lambda: FakeRunClient(FakeRunImages(), containers))
        monkeypatch.setattr(
            "dev_isolator.container_manager.subprocess.call",
            lambda cmd: 0)

        definition = ContainerDefintion(
            tmp_path, config_dir / "Containerfile",
            config_dir / "config.yaml", [config_dir])
        definition.run_container()

        assert containers.created["environment"] == {
            "DEV_ISOLATOR_TEST_VAR": "config-value"}
        assert containers.created["volumes"] == {
            "/host/data": {"bind": "/from/addon", "mode": "ro"},
            os.getcwd(): {"bind": "/workspace", "mode": "rw"}}
