"""Tests for per-addon config files (config.<addon_name>.yaml).

SPDX-License-Identifier: GPL-3.0-or-later
"""

import os
from pathlib import Path

import pytest
from dev_isolator.config import Configuration, parse_volume_spec, \
    read_environment_and_volumes, resolve_env_references
from dev_isolator.base_image import BaseImage
from dev_isolator.container_manager import ContainerDefintion


@pytest.fixture
def definition(tmp_path: Path, monkeypatch) -> ContainerDefintion:
    """A container definition for a minimal project with the
    (bundled) opencode and claude addons disabled."""
    config_dir = tmp_path / ".devisolator"
    config_dir.mkdir()
    container_file = config_dir / "Containerfile"
    container_file.write_text(
        "FROM ubuntu:26.04\n")
    (config_dir / "addon.mine").write_text("RUN echo custom\n")
    (config_dir / "config.yaml").write_text("version: 0.1\naddons:\n  - mine\n")
    # The base image build is not part of these tests; it is stubbed
    # out to return the (image, image_tag, content_hash) tuple the real
    # build produces, of which only the image tag is used (as the base
    # image name in the project Containerfile).
    monkeypatch.setattr(
        BaseImage, "build", lambda self, image: (None, image, None))
    config = Configuration(config_dir / "config.yaml", [config_dir])
    return ContainerDefintion(tmp_path, container_file, config)


class TestAddonConfigFile:
    """Test finding the config.<addon>.yaml file of an addon."""

    def test_project_config_is_used_when_present(
            self, definition, tmp_path: Path):
        """The .devisolator config file wins over the bundled one."""
        config_dir = tmp_path / ".devisolator"
        (config_dir / "config.mine.yaml").write_text("environment: []\n")

        found = definition.config.addon_config_file("mine")

        assert found == (config_dir / "config.mine.yaml").resolve()

    def test_missing_addon_config_does_not_use_other_addons(
            self, definition, tmp_path: Path):
        """A config file of another addon is not reused."""
        config_dir = tmp_path / ".devisolator"
        (config_dir / "config.other.yaml").write_text("environment: []\n")

        assert definition.config.addon_config_file("mine") is None

    def test_no_config_file_at_all(self, definition):
        """Without any config file, None is returned."""
        assert definition.config.addon_config_file("mine") is None


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
        """Add an addon config with env and volume entries.

        The volume host paths are created under ``tmp_path`` (and
        ``Path.home`` is redirected into it) so they exist: the config
        reader only keeps volumes whose host path exists.
        """
        config_dir = tmp_path / ".devisolator"
        host_data = tmp_path / "host_data"
        host_data.mkdir()
        # ${HOME} is expanded from the HOME environment variable (not
        # Path.home), so redirect HOME into tmp_path and create the
        # referenced directory below it.
        home = tmp_path / "home"
        (home / "dev-isolator-test").mkdir(parents=True)
        env_host = tmp_path / "env-host"
        env_host.mkdir()
        monkeypatch.setenv("HOME", str(home))
        (config_dir / "config.mine.yaml").write_text(
            "environment:\n"
            "  - DEV_ISOLATOR_TEST_VAR\n"
            "  - DEV_ISOLATOR_TEST_UNSET\n"
            "volumes:\n"
            f"  - {host_data}:/container/data:ro\n"
            "  - ${HOME}/dev-isolator-test:/home/dev/data\n"
            f"  - ENV[DEV_ISOLATOR_TEST_HOST]:/env/data\n")
        monkeypatch.setenv("DEV_ISOLATOR_TEST_VAR", "test-value")
        monkeypatch.setenv("DEV_ISOLATOR_TEST_HOST", str(env_host))
        return definition, host_data, home, env_host

    def test_environment_vars_are_taken_from_host(self, with_config):
        """Listed variables set on the host are passed with their
        value; unset ones are skipped."""
        definition, _, _, _ = with_config

        environment, _ = definition.config.addon_run_settings()

        assert environment == {"DEV_ISOLATOR_TEST_VAR": "test-value"}

    def test_volumes_are_expanded(self, with_config):
        """~ and ${VAR} in the host path are expanded, ENV[VAR]
        references are resolved; the mode is kept and only existing
        host paths are kept."""
        definition, host_data, home, env_host = with_config

        _, volumes = definition.config.addon_run_settings()

        assert volumes == {
            str(host_data): {"bind": "/container/data", "mode": "ro"},
            str(home / "dev-isolator-test"):
                {"bind": "/home/dev/data", "mode": "rw"},
            str(env_host): {"bind": "/env/data", "mode": "rw"},
        }

    def test_unset_env_reference_skips_volume(self, definition, tmp_path: Path,
                                              monkeypatch):
        """A volume spec referencing an unset variable is skipped."""
        config_dir = tmp_path / ".devisolator"
        (config_dir / "config.mine.yaml").write_text(
            "volumes:\n"
            "  - ENV[DEV_ISOLATOR_TEST_UNSET]:/container\n")
        monkeypatch.delenv("DEV_ISOLATOR_TEST_UNSET", raising=False)

        _, volumes = definition.config.addon_run_settings()

        assert volumes == {}

    def test_addons_without_config_contribute_nothing(
            self, definition):
        """Without any addon config file, empty settings are
        returned."""
        assert definition.config.addon_run_settings() == ({}, {})


class TestRunSettingsFromConfig:
    """Test turning a parsed config mapping into run settings."""

    @pytest.fixture
    def settings_from_config(self, tmp_path: Path):
        """Return a callable reproducing the environment/volumes
        processing applied to a config mapping.

        The shared logic the old module level ``run_settings_from_config``
        helper exposed now lives in the (private) ``_get_environment``
        and ``_get_volumes`` methods of :class:`Configuration`; both
        operate on a plain mapping, so a throwaway ``Configuration`` is
        enough to reach them.
        """
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 0.1\n")
        probe = Configuration(config_file, [])

        def _run(config):
            return (probe._get_environment(config),
                    probe._get_volumes(config))

        return _run

    def test_environment_and_volumes_are_processed(self, monkeypatch,
                                                   tmp_path,
                                                   settings_from_config):
        """Environment variables are taken from the host and volumes
        are expanded, exactly as for an addon config. Only existing
        host paths are kept as volume mounts."""
        host_data = tmp_path / "host_data"
        host_data.mkdir()
        env_host = tmp_path / "env-host"
        env_host.mkdir()
        monkeypatch.setenv("DEV_ISOLATOR_TEST_VAR", "test-value")
        monkeypatch.setenv("DEV_ISOLATOR_TEST_HOST", str(env_host))

        environment, volumes = settings_from_config({
            "environment": ["DEV_ISOLATOR_TEST_VAR", "DEV_ISOLATOR_TEST_UNSET"],
            "volumes": [f"{host_data}:/container/data:ro",
                        f"ENV[DEV_ISOLATOR_TEST_HOST]:/env/data"],
        })

        assert environment == {"DEV_ISOLATOR_TEST_VAR": "test-value"}
        assert volumes == {
            str(host_data): {"bind": "/container/data", "mode": "ro"},
            str(env_host): {"bind": "/env/data", "mode": "rw"},
        }

    def test_environment_values_are_used_as_given(self, monkeypatch,
                                                  settings_from_config):
        """A ``NAME=value`` entry is passed through as-is, taking
        precedence over a variable of the same name on the host."""
        monkeypatch.setenv("DEV_ISOLATOR_TEST_VAR", "host-value")

        environment, _ = settings_from_config({
            "environment": [
                "DEV_ISOLATOR_TEST_VAR=given-value",
                "DEV_ISOLATOR_TEST_GIVEN=value3",
            ],
            "volumes": [],
        })

        assert environment == {
            "DEV_ISOLATOR_TEST_VAR": "given-value",
            "DEV_ISOLATOR_TEST_GIVEN": "value3",
        }

    def test_environment_value_may_be_empty(self, settings_from_config):
        """A ``NAME=`` entry sets the variable to the empty string."""

        environment, _ = settings_from_config({
            "environment": ["DEV_ISOLATOR_TEST_EMPTY="],
            "volumes": [],
        })

        assert environment == {"DEV_ISOLATOR_TEST_EMPTY": ""}

    def test_invalid_environment_entries_are_skipped(
            self, settings_from_config):
        """An entry without a name is skipped."""

        environment, _ = settings_from_config({
            "environment": ["=value"],
            "volumes": [],
        })

        assert environment == {}

    def test_empty_config_yields_empty_settings(self, settings_from_config):
        """An empty mapping yields empty environment and volumes."""
        assert settings_from_config(
            {"environment": [], "volumes": []}) == ({}, {})


class TestConfigRunSettings:
    """Test collecting environment variables and volumes from the
    active config.yaml."""

    def test_config_env_and_volumes_are_read(
            self, tmp_path: Path, monkeypatch):
        """The environment and volumes sections of config.yaml are
        processed like an addon config. The Configuration is built
        after the file is written, since it reads and parses it on
        construction."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        host_data = tmp_path / "host_data"
        host_data.mkdir()
        env_host = tmp_path / "env-host"
        env_host.mkdir()
        config_file = config_dir / "config.yaml"
        config_file.write_text(
            "version: 0.1\n"
            "environment:\n"
            "  - DEV_ISOLATOR_TEST_VAR\n"
            "  - DEV_ISOLATOR_TEST_UNSET\n"
            "volumes:\n"
            f"  - {host_data}:/container/data:ro\n"
            f"  - ENV[DEV_ISOLATOR_TEST_HOST]:/env/data\n")
        monkeypatch.setenv("DEV_ISOLATOR_TEST_VAR", "test-value")
        monkeypatch.setenv("DEV_ISOLATOR_TEST_HOST", str(env_host))
        config = Configuration(config_file, [config_dir])

        environment = config.config_environment()
        volumes = config.config_volumes()

        assert environment == {"DEV_ISOLATOR_TEST_VAR": "test-value"}
        assert volumes == {
            str(host_data): {"bind": "/container/data", "mode": "ro"},
            str(env_host): {"bind": "/env/data", "mode": "rw"},
        }

    def test_missing_sections_yield_empty_settings(
            self, definition, tmp_path: Path):
        """A config.yaml without environment/volumes yields empty
        settings."""
        config_dir = tmp_path / ".devisolator"
        (config_dir / "config.yaml").write_text("version: 0.1\naddons:\n  - mine\n")

        assert definition.config.config_environment() == {}
        assert definition.config.config_volumes() == {}

    def test_config_without_env_and_volumes_yields_empty_settings(
            self, definition):
        """A config.yaml declaring no environment/volumes returns
        empty settings."""
        assert definition.config.config_environment() == {}
        assert definition.config.config_volumes() == {}

    def test_invalid_config_file_raises(self, tmp_path: Path):
        """An invalid config.yaml raises a RuntimeError on construction,
        when the file is first read."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("environment: [unclosed\n")

        with pytest.raises(RuntimeError, match="Invalid config"):
            Configuration(config_dir / "config.yaml", [config_dir])


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
    def creation_stub(self, tmp_path: Path, monkeypatch):
        """Stub out the base image and image build steps, so only the
        container creation is exercised."""
        # The base image build is stubbed out; it returns the tuple
        # (image, image_tag, content_hash), of which only the image tag is
        # used (as the base image name in the project Containerfile).
        monkeypatch.setattr(
            BaseImage, "build", lambda self, image: (None, image, None))
        monkeypatch.setattr(
            ContainerDefintion, "build_container",
            lambda self, nocache=False: None)

    def test_environment_and_volumes_are_passed_to_create(
            self, tmp_path: Path, monkeypatch, creation_stub):
        """The environment variables and volumes of the addon
        config files are passed to containers.create()."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        (config_dir / "Containerfile").write_text(
            "FROM ubuntu:26.04\n")
        (config_dir / "addon.mine").write_text("RUN echo custom\n")
        (config_dir / "config.yaml").write_text("version: 0.1\naddons:\n  - mine\n")
        host_data = tmp_path / "host_data"
        host_data.mkdir()
        (config_dir / "config.mine.yaml").write_text(
            "environment:\n  - DEV_ISOLATOR_TEST_VAR\n"
            f"volumes:\n  - {host_data}:/container/data:ro\n")
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
            Configuration(config_dir / "config.yaml", [config_dir]))
        definition.run_container()

        assert containers.created["environment"] == {
            "DEV_ISOLATOR_TEST_VAR": "test-value"}
        assert containers.created["volumes"] == {
            str(host_data): {"bind": "/container/data", "mode": "ro"},
            os.getcwd(): {"bind": "/workspace", "mode": "rw"}}

    def test_without_addon_config_no_settings_are_passed(
            self, tmp_path: Path, monkeypatch, creation_stub):
        """Without addon config files, containers.create() receives
        an empty environment and only the workspace volume."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        (config_dir / "Containerfile").write_text(
            "FROM ubuntu:26.04\n")
        (config_dir / "addon.mine").write_text("RUN echo custom\n")
        (config_dir / "config.yaml").write_text("version: 0.1\naddons:\n  - mine\n")

        containers = FakeContainers()
        monkeypatch.setattr(
            "dev_isolator.container_manager.podman.PodmanClient",
            lambda: FakeRunClient(FakeRunImages(), containers))
        monkeypatch.setattr(
            "dev_isolator.container_manager.subprocess.call",
            lambda cmd: 0)

        definition = ContainerDefintion(
            tmp_path, config_dir / "Containerfile",
            Configuration(config_dir / "config.yaml", [config_dir]))
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
        (config_dir / "Containerfile").write_text(
            "FROM ubuntu:26.04\n")
        host_data = tmp_path / "host_data"
        host_data.mkdir()
        (config_dir / "config.yaml").write_text(
            "version: 0.1\n"
            "environment:\n  - DEV_ISOLATOR_TEST_VAR\n"
            f"volumes:\n  - {host_data}:/container/data:ro\n")
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
            Configuration(config_dir / "config.yaml", [config_dir]))
        definition.run_container()

        assert containers.created["environment"] == {
            "DEV_ISOLATOR_TEST_VAR": "test-value"}
        assert containers.created["volumes"] == {
            str(host_data): {"bind": "/container/data", "mode": "ro"},
            os.getcwd(): {"bind": "/workspace", "mode": "rw"}}

    def test_addon_settings_override_config_yaml_settings(
            self, tmp_path: Path, monkeypatch, creation_stub):
        """Addon settings override those from config.yaml, the
        workspace mount always wins."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        (config_dir / "Containerfile").write_text(
            "FROM ubuntu:26.04\n")
        (config_dir / "addon.mine").write_text("RUN echo custom\n")
        host_data = tmp_path / "host_data"
        host_data.mkdir()
        (config_dir / "config.yaml").write_text(
            "version: 0.1\n"
            "addons:\n  - mine\n"
            "environment:\n  - DEV_ISOLATOR_TEST_VAR\n"
            f"volumes:\n  - {host_data}:/from/config:rw\n")
        (config_dir / "config.mine.yaml").write_text(
            f"volumes:\n  - {host_data}:/from/addon:ro\n")
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
            Configuration(config_dir / "config.yaml", [config_dir]))
        definition.run_container()

        assert containers.created["environment"] == {
            "DEV_ISOLATOR_TEST_VAR": "config-value"}
        assert containers.created["volumes"] == {
            str(host_data): {"bind": "/from/addon", "mode": "ro"},
            os.getcwd(): {"bind": "/workspace", "mode": "rw"}}
