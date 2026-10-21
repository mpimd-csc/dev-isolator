"""Tests for the demo_function

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

import pytest
from dev_isolator.container_manager import ContainerDefintion, BUNDLE_DIR
from dev_isolator.main import create_parser
from dev_isolator.utils import Verbosity


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
        """addon.* files in .devisolator are appended after the
        bundled ones when enabled in .devisolator/config.yaml."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        container_file = config_dir / "Containerfile"
        container_file.write_text("FROM ubuntu:26.04\n")
        (config_dir / "addon.mine").write_text("RUN echo custom\n")
        (config_dir / "config.yaml").write_text("addons_enabled:\n  - mine\n")

        definition = ContainerDefintion(container_file, tmp_path)
        merged = definition.merged_container_file_content()

        names = [a.name for a in definition.addon_files()]
        assert "addon.mine" in names
        assert names.index("addon.mine") == len(names) - 1
        assert "RUN echo custom" in merged

    def test_disabled_context_addons_are_skipped(self, tmp_path: Path):
        """addon.* files not enabled in any addons.yaml are not
        appended to the Containerfile."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        container_file = config_dir / "Containerfile"
        container_file.write_text("FROM ubuntu:26.04\n")
        (config_dir / "addon.mine").write_text("RUN echo custom\n")

        definition = ContainerDefintion(container_file, tmp_path)
        merged = definition.merged_container_file_content()

        names = [a.name for a in definition.addon_files()]
        assert "addon.mine" not in names
        assert "RUN echo custom" not in merged

    def test_enabled_addons_use_project_config(self, tmp_path: Path):
        """The project's .devisolator/config.yaml overrides the
        bundled one when it exists."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        container_file = config_dir / "Containerfile"
        container_file.write_text("FROM ubuntu:26.04\n")
        (config_dir / "addon.mine").write_text("RUN echo custom\n")
        (config_dir / "config.yaml").write_text("addons_enabled:\n  - mine\n")

        definition = ContainerDefintion(container_file, tmp_path)
        enabled = definition.enabled_addons()

        # Only the project config is honored; the bundled list is
        # ignored entirely.
        assert enabled == {"mine"}
        assert definition.config_file().name == "config.yaml"
        names = [a.name for a in definition.addon_files()]
        assert "addon.mine" in names
        assert "addon.opencode" not in names

    def test_bundled_config_yaml_used_without_project_config(
            self, tmp_path: Path):
        """Without .devisolator/config.yaml the bundled one is used."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        container_file = config_dir / "Containerfile"
        container_file.write_text("FROM ubuntu:26.04\n")

        definition = ContainerDefintion(container_file, tmp_path)

        assert definition.config_file() \
            == (Path(__import__("dev_isolator.container_manager",
                                fromlist=["__file__"]).__file__).parent
                / BUNDLE_DIR / "config.yaml").resolve()
        assert definition.enabled_addons() == {"opencode", "claude"}

    def test_no_config_yaml_enables_everything(self, tmp_path: Path,
                                               monkeypatch):
        """Without any config.yaml file every addon.* file is used."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        container_file = config_dir / "Containerfile"
        container_file.write_text("FROM ubuntu:26.04\n")
        (config_dir / "addon.mine").write_text("RUN echo custom\n")

        # Hide every config.yaml file to simulate "no config at all".
        real_is_file = Path.is_file
        monkeypatch.setattr(
            Path, "is_file",
            lambda self: False if self.name == "config.yaml"
            else real_is_file(self))

        definition = ContainerDefintion(container_file, tmp_path)

        assert not definition.config_file()
        assert definition.enabled_addons() is None
        names = [a.name for a in definition.addon_files()]
        assert "addon.mine" in names

    def test_no_duplicate_addons(self, tmp_path: Path):
        """When the build context is the bundled directory
        itself, each addon is only appended once."""
        bundled = Path(__import__("dev_isolator.container_manager",
                                  fromlist=["__file__"]).__file__).parent \
            / BUNDLE_DIR

        definition = ContainerDefintion(bundled / "Containerfile", bundled)
        addons = definition.addon_files()

        assert len(addons) == len({a for a in addons})
        assert addons  # the bundled directory ships at least one addon


class FakeImage:
    """Minimal stand-in for a podman Image."""

    def __init__(self, tags=None, id="sha256:fake"):
        self.tags = tags
        self.id = id


class FakeImages:
    """Minimal stand-in for the podman images manager."""

    def __init__(self):
        self.built = None

    def build(self, **kwargs):
        self.built = kwargs
        dockerfile = Path(kwargs["dockerfile"])
        if dockerfile.exists():
            self.dockerfile_content = dockerfile.read_text(
                encoding="utf-8")
        else:
            self.dockerfile_content = None
        return FakeImage(id="sha256:abc123"), []


class FakeClient:
    """Minimal stand-in for a podman.PodmanClient.

    Supports the ``with`` usage in ``build_container``.
    """

    def __init__(self, images):
        self.images = images

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestBuildTraceOutput:
    """Test the trace output of build_container."""

    @pytest.fixture
    def project(self, tmp_path: Path):
        """A small project with a custom addon enabled."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        container_file = config_dir / "Containerfile"
        container_file.write_text("FROM ubuntu:26.04\n")
        (config_dir / "addon.mine").write_text("RUN echo custom\n")
        (config_dir / "config.yaml").write_text(
            "addons_enabled:\n  - mine\n")
        return ContainerDefintion(container_file, tmp_path)

    @pytest.fixture
    def patched_images(self, monkeypatch):
        """Patch PodmanClient with a fake using ``FakeImages``."""
        images = FakeImages()
        monkeypatch.setattr(
            "dev_isolator.container_manager.podman.PodmanClient",
            lambda: FakeClient(images))
        return images

    def test_trace_prints_generated_containerfile(
            self, project, patched_images, monkeypatch, capsys):
        """At Verbosity.TRACE the generated Containerfile is printed
        with a "[Containerfile] " line prefix."""
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.TRACE)

        project.build_container()
        output = capsys.readouterr().out

        lines = [line for line in output.splitlines()
                 if line.startswith("[Containerfile] ")]
        assert lines, "no [Containerfile] lines printed"
        plain = [line.removeprefix("[Containerfile] ") for line in lines]
        assert plain[0] == "FROM ubuntu:26.04"
        assert "# --- appended from addon.mine ---" in plain
        assert "RUN echo custom" in plain

    def test_info_does_not_print_generated_containerfile(
            self, project, patched_images, monkeypatch, capsys):
        """Below Verbosity.TRACE no "[Containerfile]" lines appear."""
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.INFO)

        project.build_container()
        output = capsys.readouterr().out

        assert "[Containerfile]" not in output

    def test_build_requests_cleaned_context(
            self, project, patched_images, monkeypatch, capsys):
        """The build request uses the used tag and the merged
        Containerfile of the build context."""
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.ERROR)

        project.build_container()

        built = patched_images.built
        assert built["tag"] == project.image_name()
        assert built["dockerfile"] != "Containerfile"
        # The merged Containerfile is captured by the fake build
        # call, because the temporary file is removed afterwards.
        content = patched_images.dockerfile_content
        assert content.startswith("FROM ubuntu:26.04\n")
        assert "# --- appended from addon.mine ---" in content
        assert "RUN echo custom" in content


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
