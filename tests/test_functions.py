"""Tests for the container definition and base image handling of
dev-isolator and main's argument parsing.

SPDX-License-Identifier: GPL-3.0-or-later
"""

import importlib
import string
import sys
import tarfile
from pathlib import Path

from types import SimpleNamespace

import podman.errors
import pytest
import dev_isolator.container_manager as container_manager
from dev_isolator.base_image import BaseImage, host_username
from dev_isolator.config import Configuration
from dev_isolator.constants import (
    BUNDLE_DIR, ADDON_DIR, CONTAINER_NAME_SUFFIX_LENGTH)
from dev_isolator.container_manager import ContainerDefintion
from dev_isolator.main import DEFAULT_SHELL, create_parser, parse_args, \
    init_project, render_target_lines, target, list_targets
from dev_isolator.utils import Verbosity

# The directory bundled with dev-isolator (Containerfile, addons,
# config.yaml).
BUNDLED_DIR = Path(container_manager.__file__).resolve().parent / BUNDLE_DIR


def make_configuration(config_file, addon_search_paths):
    """Return a :class:`dev_isolator.config.Configuration` for the given
    active ``config.yaml`` and addon search paths."""
    return Configuration(config_file, addon_search_paths)


def empty_configuration(tmp_path: Path, addon_search_paths):
    """Return a configuration that enables no addons.

    The current ``Configuration`` requires an existing ``config.yaml``
    (it reads it on construction); an empty file enables no addons, so
    this stands in for the old "no config.yaml" case in the tests that
    only need the host user block or a plain build.
    """
    config_file = tmp_path / "config.yaml"
    config_file.write_text("version: 0.1\n")
    return Configuration(config_file, addon_search_paths)


class TestMergedContainerfile:
    """Test the merged Containerfile of the base image"""

    def test_bundled_addons_are_appended(self, tmp_path: Path):
        """Addon.* files enabled in the bundled config.yaml are
        appended to the base image's Containerfile."""
        config = make_configuration(BUNDLED_DIR / "config.yaml",
                                    [ADDON_DIR])
        base = BaseImage(config)
        merged = base.merged_container_file_content("FROM ubuntu:26.04\n")

        assert merged.startswith("FROM ubuntu:26.04\n")
        for addon in base.config.list_addon_files():
            assert addon.name in merged
        # The bundled opencode addon is enabled in the bundled
        # config.yaml and part of the package (as a jinja2 template).
        assert any(a.name == "addon.opencode.jinja2"
                   for a in base.config.list_addon_files())
        assert "opencode" in merged

    def test_host_user_is_appended(self, tmp_path: Path):
        """A user matching the host's UID, GID and username is
        appended to the base image's Containerfile and made its
        default user."""
        import os

        base = BaseImage(empty_configuration(tmp_path, []))
        merged = base.merged_container_file_content("FROM ubuntu:26.04\n")

        uid = os.getuid()
        gid = os.getgid()
        username = host_username()
        # Existing users/groups occupying the host UID/GID/username
        # are removed before the user is created.
        assert f"getent passwd {uid}" in merged
        assert "userdel --force --remove" in merged
        assert f"userdel --force --remove {username}" in merged
        assert "groupdel" in merged
        assert f"groupadd --gid {gid} {username}" in merged
        assert f"useradd --uid {uid} --gid {gid}" in merged
        assert f"USER {username}" in merged
        # The user made the default user is created after the useradd;
        # the workdir switch to /workspace happens later, in the
        # project image's Containerfile.
        assert merged.index(f"USER {username}") \
            > merged.index(f"useradd --uid {uid}")

    def test_enabled_context_addons_are_appended(self, tmp_path: Path):
        """addon.* files in .devisolator are appended after the
        bundled ones when enabled in .devisolator/config.yaml."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        (config_dir / "addon.mine").write_text("RUN echo custom\n")
        (config_dir / "config.yaml").write_text(
            "version: 0.1\naddons:\n  - mine\n")

        config = make_configuration(config_dir / "config.yaml",
                                    [config_dir, BUNDLED_DIR])
        base = BaseImage(config)
        merged = base.merged_container_file_content("FROM ubuntu:26.04\n")

        names = [a.name for a in base.config.list_addon_files()]
        assert "addon.mine" in names
        assert names.index("addon.mine") == len(names) - 1
        assert "RUN echo custom" in merged

    def test_disabled_context_addons_are_skipped(self, tmp_path: Path):
        """addon.* files not enabled in any config.yaml are not
        appended to the base image's Containerfile."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        (config_dir / "addon.mine").write_text("RUN echo custom\n")

        # Without a project config.yaml the bundled one is active,
        # and it does not enable the "mine" addon.
        config = make_configuration(BUNDLED_DIR / "config.yaml",
                                    [config_dir, BUNDLED_DIR])
        base = BaseImage(config)
        merged = base.merged_container_file_content("FROM ubuntu:26.04\n")

        names = [a.name for a in base.config.list_addon_files()]
        assert "addon.mine" not in names
        assert "RUN echo custom" not in merged

    def test_enabled_addons_use_project_config(self, tmp_path: Path):
        """The project's .devisolator/config.yaml overrides the
        bundled one when it exists."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        (config_dir / "addon.mine").write_text("RUN echo custom\n")
        (config_dir / "config.yaml").write_text(
            "version: 0.1\naddons:\n  - mine\n")

        config = make_configuration(config_dir / "config.yaml",
                                    [config_dir, BUNDLED_DIR])
        base = BaseImage(config)
        enabled = base.config.enabled_addons()

        # Only the project config is honored; the bundled list is
        # ignored entirely.
        assert enabled == {"mine"}
        assert base.config.config_file \
            == (config_dir / "config.yaml").resolve()
        names = [a.name for a in base.config.list_addon_files()]
        assert "addon.mine" in names
        assert "addon.opencode.jinja2" not in names

    def test_bundled_config_yaml_used_without_project_config(
            self, tmp_path: Path):
        """Without .devisolator/config.yaml the bundled one is used."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()

        config = make_configuration(BUNDLED_DIR / "config.yaml",
                                    [config_dir, BUNDLED_DIR])
        base = BaseImage(config)

        assert base.config.config_file \
            == (BUNDLED_DIR / "config.yaml").resolve()
        assert base.config.enabled_addons() == {"opencode", "claude"}

    def test_config_yaml_without_addons_key_enables_nothing(
            self, tmp_path: Path):
        """A config.yaml without an 'addons' key enables no addon, so
        no addon.* file is appended to the Containerfile."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        (config_dir / "addon.mine").write_text("RUN echo custom\n")
        (config_dir / "config.yaml").write_text("version: 0.1\n")

        config = make_configuration(config_dir / "config.yaml",
                                    [config_dir, BUNDLED_DIR])
        base = BaseImage(config)

        assert base.config.config_file \
            == (config_dir / "config.yaml").resolve()
        assert base.config.enabled_addons() == set()
        names = [a.name for a in base.config.list_addon_files()]
        assert "addon.mine" not in names

    def test_no_duplicate_addons(self, tmp_path: Path):
        """When the build context is the bundled directory
        itself, each addon is only appended once."""
        bundled = Path(container_manager.__file__).resolve().parent \
            / BUNDLE_DIR

        config = make_configuration(bundled / "config.yaml", [ADDON_DIR])
        base = BaseImage(config)
        addons = base.config.list_addon_files()

        assert len(addons) == len({a for a in addons})
        assert addons  # the bundled directory ships at least one addon

    def test_jinja2_addons_are_rendered(self, tmp_path: Path):
        """addon.*.jinja2 files are rendered with the host context
        before being appended to the Containerfile."""
        import os

        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        (config_dir / "addon.rendered.jinja2").write_text(
            "RUN echo user={{ username }} uid={{ uid }} "
            "home={{ home_dir }}\n")
        (config_dir / "config.yaml").write_text(
            "version: 0.1\n"
            "addons:\n  - rendered\n")

        config = make_configuration(config_dir / "config.yaml",
                                    [config_dir])
        base = BaseImage(config)
        merged = base.merged_container_file_content("FROM ubuntu:26.04\n")

        username = host_username()
        assert f"user={username}" in merged
        assert f"uid={os.getuid()}" in merged
        # No unrendered template markers remain.
        assert "{{" not in merged
        assert "{%" not in merged


class FakeImage:
    """Minimal stand-in for a podman Image."""

    def __init__(self, tags=None, id="sha256:fake"):
        self.tags = tags
        self.id = id


class FakeImages:
    """Minimal stand-in for the podman images manager."""

    def __init__(self):
        self.built = None
        # Existing podman images by tag; returned by ``get``.
        self.existing = {}
        # Image ids passed to ``remove``.
        self.removed = []
        # ("get", tag)/("remove", image_id)/("build", tag) events
        # in the order they happened.
        self.events = []

    def get(self, name):
        self.events.append(("get", name))
        try:
            return self.existing[name]
        except KeyError:
            raise podman.errors.NotFound(
                f"No image with tag {name}") from None

    def list(self):
        return list(self.existing.values())

    def remove(self, image_id, force=False):
        self.events.append(("remove", image_id))
        self.removed.append((image_id, force))
        for tag, image in list(self.existing.items()):
            if image.id == image_id:
                del self.existing[tag]

    def build(self, **kwargs):
        self.events.append(("build", kwargs.get("tag")))
        self.built = kwargs
        self.dockerfile_content = None
        if kwargs.get("custom_context") and "fileobj" in kwargs:
            # The merged Containerfile lives inside the context tarball
            # handed to the build as a custom context.
            fileobj = kwargs["fileobj"]
            try:
                fileobj.seek(0)
                with tarfile.open(fileobj=fileobj, mode="r:*") as tar:
                    member = tar.extractfile(kwargs["dockerfile"])
                    if member is not None:
                        self.dockerfile_content = \
                            member.read().decode("utf-8")
            except (tarfile.TarError, KeyError, OSError):
                self.dockerfile_content = None
        else:
            dockerfile = Path(kwargs["dockerfile"])
            if dockerfile.exists():
                self.dockerfile_content = dockerfile.read_text(
                    encoding="utf-8")
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
    def project(self, tmp_path: Path, monkeypatch):
        """A small project with a custom addon enabled."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        container_file = config_dir / "Containerfile"
        container_file.write_text(
            "FROM ubuntu:26.04\n")
        (config_dir / "addon.mine").write_text("RUN echo custom\n")
        (config_dir / "config.yaml").write_text(
            "version: 0.1\naddons:\n  - mine\n")
        # The base image build is not part of these tests; it is stubbed
        # out to return the (image, image_tag, content_hash) tuple the
        # real build produces, of which only the image tag is used (as
        # the base image name in the project Containerfile).
        monkeypatch.setattr(
            BaseImage, "build", lambda self, image: (None, image, None))
        config = make_configuration(config_dir / "config.yaml",
                                    [config_dir])
        return ContainerDefintion(tmp_path, container_file, config)

    @pytest.fixture
    def patched_images(self, monkeypatch):
        """Patch PodmanClient with a fake using ``FakeImages``."""
        images = FakeImages()
        monkeypatch.setattr(
            "dev_isolator.container_image.podman.PodmanClient",
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
        assert "# --- workdir ---" in plain
        # The default user is set before the workdir is switched.
        username = host_username()
        assert plain.index(f"USER {username}") \
            < plain.index("WORKDIR /workspace")

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
        assert content.startswith(
            "FROM ubuntu:26.04\n")
        assert "WORKDIR /workspace" in content

    def test_superseded_image_is_removed_after_build(
            self, project, patched_images, monkeypatch):
        """When the build produces a new image id for the same tag,
        the previous image is removed with ``force=True`` afterwards."""
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.ERROR)

        existing = FakeImage(id="sha256:old", tags=[project.image_name()])
        patched_images.existing[project.image_name()] = existing

        project.build_container()

        assert patched_images.removed == [("sha256:old", True)]
        # The lookup happens before and the removal after the build.
        assert patched_images.events == [
            ("get", project.image_name()),
            ("build", project.image_name()),
            ("remove", "sha256:old"),
        ]

    def test_unchanged_image_is_not_removed(
            self, project, patched_images, monkeypatch):
        """When the build returns the existing image id, nothing is
        removed."""
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.ERROR)

        existing = FakeImage(id="sha256:abc123", tags=[project.image_name()])
        patched_images.existing[project.image_name()] = existing

        project.build_container()

        assert patched_images.removed == []
        assert ("remove", "sha256:abc123") not in patched_images.events

    def test_build_without_existing_image_skips_removal(
            self, project, patched_images, monkeypatch):
        """When no image with the build's tag exists, nothing is
        removed and the build proceeds."""
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.ERROR)

        project.build_container()

        assert patched_images.removed == []
        assert patched_images.built["tag"] == project.image_name()


class TestBaseImageBuild:
    """Test that the base image build uses the merged Containerfile."""

    @pytest.fixture
    def base(self, tmp_path: Path):
        """A base image with a custom addon enabled."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        (config_dir / "addon.mine").write_text("RUN echo custom\n")
        (config_dir / "config.yaml").write_text(
            "version: 0.1\naddons:\n  - mine\n")
        config = make_configuration(config_dir / "config.yaml",
                                    [config_dir])
        return BaseImage(config)

    def test_build_appends_addons_to_base_containerfile(
            self, base, monkeypatch):
        """The built base image Containerfile starts with the base
        image content and has the enabled addons and the host user
        appended."""
        images = FakeImages()
        monkeypatch.setattr(
            "dev_isolator.base_image.podman.PodmanClient",
            lambda: FakeClient(images))

        base.build("ubuntu:26.04")

        # The tag carries a content hash of the merged Containerfile
        # (dev-isolator-<name>:<tag>-<hash>).
        assert images.built["tag"].startswith("dev-isolator-ubuntu:26.04-")
        content = images.dockerfile_content
        # The base Containerfile (including its DI_BASE comment)
        # comes first.
        assert "FROM ubuntu:26.04" in content
        assert "# --- appended from addon.mine ---" in content
        assert "RUN echo custom" in content
        assert f"USER {host_username()}" in content

    def test_build_with_unknown_base_image_raises(
            self, base, monkeypatch):
        """A base image without a matching Containerfile and without
        a supported package manager raises and nothing is built."""
        images = FakeImages()
        monkeypatch.setattr(
            "dev_isolator.base_image.podman.PodmanClient",
            lambda: FakeClient(images))
        # No package manager can be detected for the unknown image.
        monkeypatch.setattr(
            "dev_isolator.base_image.detect_package_manager",
            lambda image: None)

        with pytest.raises(RuntimeError):
            base.build("unknown:1.0")

        assert images.built is None


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


class TestHelpParser:
    """Test the CLI arguments of the help subcommand."""

    def test_help_is_recognized(self):
        parser = create_parser()
        args = parser.parse_args(["help"])
        assert args.command == "help"


class TestOpencodeParser:
    """Test the CLI arguments of the opencode subcommand."""

    def test_opencode_without_args(self):
        args = parse_args(["opencode"])
        assert args.command == "opencode"
        assert args.opencode_args == []

    def test_opencode_passes_plain_arguments(self):
        args = parse_args(["opencode", "auth", "login"])
        assert args.command == "opencode"
        assert args.opencode_args == ["auth", "login"]

    def test_opencode_passes_leading_options_verbatim(self):
        """Options starting with '-' are passed to opencode,
        not parsed by the dev-isolator parser."""
        args = parse_args(
            ["opencode", "--port", "4096", "-m", "some.model"])
        assert args.command == "opencode"
        assert args.opencode_args == ["--port", "4096",
                                      "-m", "some.model"]

    def test_opencode_after_verbosity_flags(self):
        """Global verbosity flags before the subcommand are honored
        and do not stop the verbatim arguments from being passed."""
        args = parse_args(["-vvv", "opencode", "--port", "4096"])
        assert args.verbose == 3
        assert args.command == "opencode"
        assert args.opencode_args == ["--port", "4096"]

    def test_opencode_token_after_other_subcommand_is_not_split(self):
        """``opencode`` used as the value of the global ``--shell``
        option does not hijack the parsing of the following
        subcommand."""
        args = parse_args(["--shell", "opencode", "run"])
        assert args.command == "run"
        assert args.shell == "opencode"

    def test_opencode_uses_cli_arguments(self, monkeypatch):
        """main's argument parsing takes the arguments after
        ``opencode`` from the CLI verbatim."""
        import sys

        monkeypatch.setattr(sys, "argv",
                            ["dev-isolator", "opencode", "--port", "4096"])

        args = parse_args()

        assert args.command == "opencode"
        assert args.opencode_args == ["--port", "4096"]


class TestTargetParser:
    """Test the CLI arguments of the target subcommand."""

    def test_target_with_name_only(self):
        args = parse_args(["target", "test"])
        assert args.command == "target"
        assert args.target_args == ["test"]

    def test_target_passes_extra_arguments(self):
        """The arguments following the target name are passed to the
        target's command."""
        args = parse_args(["target", "test", "-k", "foo"])
        assert args.command == "target"
        assert args.target_args == ["test", "-k", "foo"]

    def test_target_passes_leading_options_verbatim(self):
        """Options starting with '-' are passed through, not parsed by
        the dev-isolator parser (target takes only positional
        arguments)."""
        args = parse_args(["target", "--port", "4096", "test"])
        assert args.command == "target"
        assert args.target_args == ["--port", "4096", "test"]

    def test_target_after_verbosity_flags(self):
        """Global verbosity flags before the subcommand are honored
        and do not stop the verbatim arguments from being passed."""
        args = parse_args(["-vvv", "target", "test"])
        assert args.verbose == 3
        assert args.command == "target"
        assert args.target_args == ["test"]

    def test_target_without_arguments(self):
        args = parse_args(["target"])
        assert args.command == "target"
        assert args.target_args == []


class TestConfigTargets:
    """Test the ``targets`` section of the active ``config.yaml``."""

    def write_config(self, tmp_path: Path, content: str) -> Configuration:
        config_file = tmp_path / "config.yaml"
        config_file.write_text(content)
        return Configuration(config_file, [tmp_path])

    def test_missing_section_yields_no_targets(self, tmp_path: Path):
        config = self.write_config(tmp_path, "version: 0.1\n")
        assert config.config_targets() == {}

    def test_string_and_list_commands_are_read(self, tmp_path: Path):
        config = self.write_config(tmp_path, (
            "version: 0.1\n"
            "targets:\n"
            "  test: pytest -v\n"
            "  lint:\n    - ruff\n    - check\n"))

        assert config.config_targets() == {
            "test": "pytest -v",
            "lint": ["ruff", "check"],
        }

    def test_non_mapping_section_is_rejected(self, tmp_path: Path):
        config = self.write_config(tmp_path,
                                   "version: 0.1\ntargets: []\n")
        with pytest.raises(RuntimeError):
            config.config_targets()

    def test_invalid_command_is_rejected(self, tmp_path: Path):
        config = self.write_config(
            tmp_path, "version: 0.1\ntargets:\n  test: [1, 2]\n")
        with pytest.raises(RuntimeError):
            config.config_targets()


class TestTargetCommand:
    """Test rendering the command template of a target into lines."""

    def test_string_form_renders_all_args(self):
        lines = render_target_lines("pytest -v {{ all_args }}",
                                    ["-k", "foo"])
        assert lines == [["pytest", "-v", "-k", "foo"]]

    def test_string_form_without_extra_args(self):
        lines = render_target_lines("pytest -v {{ all_args }}", [])
        assert lines == [["pytest", "-v"]]

    def test_list_entries_are_whole_lines(self):
        """Every list entry is one line of a script; a line may hold
        several tokens."""
        lines = render_target_lines(
            ["pytest --cov={{ args[0] }}", "ruff check src"], ["src"])
        assert lines == [["pytest", "--cov=src"],
                         ["ruff", "check", "src"]]

    def test_single_line_list_is_one_line(self):
        lines = render_target_lines(["pytest -v"], [])
        assert lines == [["pytest", "-v"]]

    def test_optional_argument_is_rendered_when_set(self):
        lines = render_target_lines(
            ["pytest {{ args[0] | optional }} -x"], ["-k"])
        assert lines == [["pytest", "-k", "-x"]]

    def test_optional_argument_value_is_rendered_when_set(self):
        lines = render_target_lines(
            ["pytest {{ args[0] | optional }} -x"], ["foo"])
        assert lines == [["pytest", "foo", "-x"]]

    def test_optional_argument_token_is_dropped_when_unset(self):
        lines = render_target_lines(
            ["pytest {{ args[0] | optional }} -x"], [])
        assert lines == [["pytest", "-x"]]

    def test_optional_argument_drops_preceding_option(self):
        """An unset optional value drops its preceding option token, so
        an optional ``-k <value>`` pair disappears as a whole."""
        lines = render_target_lines(
            ["pytest -k {{ args[0] | optional }}"], [])
        assert lines == [["pytest"]]

    def test_optional_flag_with_embedded_value_is_dropped_alone(self):
        """An option token holding the unset placeholder itself is
        dropped without also dropping the preceding option."""
        lines = render_target_lines(
            ["pytest -v -k={{ args[0] | optional }}"], [])
        assert lines == [["pytest", "-v"]]

    def test_string_form_optional_is_dropped_when_unset(self):
        lines = render_target_lines(
            "pytest {{ args[0] | optional }} -x", [])
        assert lines == [["pytest", "-x"]]

    def test_line_becoming_empty_is_skipped(self):
        """A line whose tokens are all dropped disappears from the
        script."""
        lines = render_target_lines(
            ["{{ args[0] | optional }}", "echo done"], [])
        assert lines == [["echo", "done"]]

    def test_mandatory_argument_is_required(self):
        """A ``{{ args[n] }}`` placeholder without the ``optional``
        filter aborts when the argument was not given."""
        with pytest.raises(RuntimeError):
            render_target_lines(["pytest --cov={{ args[0] }}"], [])


class TestTargetDispatch:
    """Test that main_intern() dispatches the ``target`` subcommand.

    A single-line target runs its command directly; a multi-line
    target runs its script in one shell session that stops at the
    first failing line.
    """

    def write_config(self, tmp_path: Path, targets_yaml: str):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(targets_yaml)
        return Configuration(config_file, [tmp_path])

    def test_single_line_target_runs_directly(self, tmp_path, monkeypatch):
        """A single-line target's command is executed as is, without a
        wrapping shell."""
        di_main = importlib.import_module("dev_isolator.main")
        definition = SimpleNamespace(
            config=self.write_config(
                tmp_path, "version: 0.1\ntargets:\n  test: pytest -v\n"))

        called = {}

        def fake_in_container(definition_, command, remainder):
            called["call"] = (command, remainder)
            return 0

        monkeypatch.setattr(di_main, "in_container_command",
                            fake_in_container)
        monkeypatch.setattr(di_main, "ContainerDefintion",
                            lambda *args, **kwargs: definition)
        monkeypatch.setattr(sys, "argv", ["di", "target", "test"])

        assert di_main.main_intern() == 0
        assert called["call"] == ("pytest", ["-v"])

    def test_multi_line_target_runs_one_shell_script(
            self, tmp_path, monkeypatch):
        """A multi-line target runs its script in one shell session
        (``<shell> -ec script``), stopping at the first failing line."""
        di_main = importlib.import_module("dev_isolator.main")
        definition = SimpleNamespace(
            config=self.write_config(
                tmp_path,
                "version: 0.1\ntargets:\n"
                "  full:\n    - pytest -v\n    - ruff check\n"))

        called = {}

        def fake_in_container(definition_, command, remainder):
            called["call"] = (command, remainder)
            return 0

        monkeypatch.setattr(di_main, "in_container_command",
                            fake_in_container)
        monkeypatch.setattr(di_main, "ContainerDefintion",
                            lambda *args, **kwargs: definition)
        monkeypatch.setattr(sys, "argv", ["di", "target", "full"])

        assert di_main.main_intern() == 0
        command, remainder = called["call"]
        assert command == DEFAULT_SHELL
        assert remainder == ["-ec", "pytest -v\nruff check"]

    def test_multi_line_target_uses_selected_shell(
            self, tmp_path, monkeypatch):
        """The shell selected with the global ``--shell/-s`` option
        runs the script."""
        di_main = importlib.import_module("dev_isolator.main")
        definition = SimpleNamespace(
            config=self.write_config(
                tmp_path,
                "version: 0.1\ntargets:\n"
                "  full:\n    - echo one\n    - echo two\n"))

        called = {}

        def fake_in_container(definition_, command, remainder):
            called["call"] = (command, remainder)
            return 0

        monkeypatch.setattr(di_main, "in_container_command",
                            fake_in_container)
        monkeypatch.setattr(di_main, "ContainerDefintion",
                            lambda *args, **kwargs: definition)
        monkeypatch.setattr(sys, "argv",
                            ["di", "--shell", "zsh", "target", "full"])

        assert di_main.main_intern() == 0
        command, remainder = called["call"]
        assert command == "zsh"
        assert remainder == ["-ec", "echo one\necho two"]

    def test_script_tokens_are_quoted(self, tmp_path, monkeypatch):
        """Tokens with spaces are quoted safely when the script lines
        are re-assembled."""
        di_main = importlib.import_module("dev_isolator.main")
        definition = SimpleNamespace(
            config=self.write_config(
                tmp_path,
                "version: 0.1\ntargets:\n"
                "  full:\n"
                "    - echo \"hello world\"\n"
                "    - echo done\n"))

        called = {}

        def fake_in_container(definition_, command, remainder):
            called["call"] = (command, remainder)
            return 0

        monkeypatch.setattr(di_main, "in_container_command",
                            fake_in_container)
        monkeypatch.setattr(di_main, "ContainerDefintion",
                            lambda *args, **kwargs: definition)
        monkeypatch.setattr(sys, "argv", ["di", "target", "full"])

        assert di_main.main_intern() == 0
        _, remainder = called["call"]
        assert remainder == ["-ec", "echo 'hello world'\necho done"]


class TestListTargets:
    """Test the ``list-targets`` subcommand."""

    def test_list_targets_is_parsed(self):
        args = parse_args(["list-targets"])
        assert args.command == "list-targets"

    def test_targets_are_printed_sorted(self, tmp_path: Path, capsys):
        """The target names are printed one per line, sorted
        alphabetically."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "version: 0.1\ntargets:\n"
            "  test: pytest\n"
            "  cover: pytest --cov=.\n"
            "  lint: ruff check\n")
        definition = SimpleNamespace(
            config=Configuration(config_file, [tmp_path]))

        assert list_targets(definition, parse_args(["list-targets"])) == 0
        assert capsys.readouterr().out == "cover\nlint\ntest\n"

    def test_no_targets_prints_nothing(self, tmp_path: Path, capsys):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("version: 0.1\n")
        definition = SimpleNamespace(
            config=Configuration(config_file, [tmp_path]))

        assert list_targets(definition, parse_args(["list-targets"])) == 0
        assert capsys.readouterr().out == ""

    def test_main_intern_dispatches_list_targets(
            self, tmp_path, monkeypatch, capsys):
        """main_intern() runs the list-targets handler of the active
        config.yaml without touching podman."""
        di_main = importlib.import_module("dev_isolator.main")
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "version: 0.1\ntargets:\n  test: pytest\n")
        definition = SimpleNamespace(
            config=Configuration(config_file, [tmp_path]))

        monkeypatch.setattr(di_main, "ContainerDefintion",
                            lambda *args, **kwargs: definition)
        monkeypatch.setattr(sys, "argv", ["di", "list-targets"])

        assert di_main.main_intern() == 0
        assert capsys.readouterr().out == "test\n"


class TestTargetHandler:
    """Test the error paths of the ``target`` sub command handler."""

    def make_definition(self, tmp_path: Path, targets_yaml: str):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(targets_yaml)
        return SimpleNamespace(
            config=Configuration(config_file, [tmp_path]))

    def test_no_target_given_prints_available_targets(
            self, tmp_path: Path, capsys):
        definition = self.make_definition(
            tmp_path, "version: 0.1\ntargets:\n  test: pytest\n")
        args = parse_args(["target"])

        assert target(definition, args) == 1
        err = capsys.readouterr().err
        assert "No target given" in err
        assert "test" in err

    def test_unknown_target_prints_available_targets(
            self, tmp_path: Path, capsys):
        definition = self.make_definition(
            tmp_path, "version: 0.1\ntargets:\n  test: pytest\n")
        args = parse_args(["target", "bogus"])

        assert target(definition, args) == 1
        err = capsys.readouterr().err
        assert "Unknown target 'bogus'" in err
        assert "test" in err

    def test_mandatory_argument_error_returns_one(
            self, tmp_path: Path, capsys):
        definition = self.make_definition(
            tmp_path,
            "version: 0.1\ntargets:\n"
            "  cover:\n    - pytest\n    - --cov={{ args[0] }}\n")
        args = parse_args(["target", "cover"])

        assert target(definition, args) == 1
        assert "error" in capsys.readouterr().err.lower()


class TestGlobalShellOption:
    """Test the global ``--shell``/``-s`` option.

    The option lives on the top level parser, so it precedes the sub
    command and applies to ``shell`` as well as to the pass-through
    commands that fall back to an interactive shell.
    """

    def test_default_shell_is_bash(self):
        args = parse_args([])
        assert args.command == "shell"
        assert args.shell == DEFAULT_SHELL

    def test_shell_long_option(self):
        args = parse_args(["--shell", "zsh"])
        assert args.command == "shell"
        assert args.shell == "zsh"

    def test_shell_short_option(self):
        args = parse_args(["-s", "fish"])
        assert args.command == "shell"
        assert args.shell == "fish"

    def test_shell_before_explicit_shell_subcommand(self):
        args = parse_args(["--shell", "zsh", "shell"])
        assert args.command == "shell"
        assert args.shell == "zsh"

    def test_shell_before_run(self):
        args = parse_args(["--shell", "zsh", "run", "make", "test"])
        assert args.command == "run"
        assert args.shell == "zsh"
        assert args.run_args == ["make", "test"]

    def test_shell_before_opencode(self):
        args = parse_args(["--shell", "zsh", "opencode", "--port", "4096"])
        assert args.command == "opencode"
        assert args.shell == "zsh"
        assert args.opencode_args == ["--port", "4096"]

    def test_shell_value_does_not_hijack_subcommand(self):
        """A sub command name given as the value of ``--shell`` is not
        mistaken for the sub command itself."""
        args = parse_args(["--shell", "opencode", "run"])
        assert args.command == "run"
        assert args.shell == "opencode"
        assert args.run_args == []


class TestPrecommandOptions:
    """Unknown options before the sub command must be an error.

    The global options live on the top level parser and precede the sub
    command; options following ``opencode``/``claude``/``run`` belong to
    the command inside the container and are still passed through.
    """

    def test_unknown_option_before_build_is_error(self):
        with pytest.raises(SystemExit) as excinfo:
            parse_args(["--bogus", "build"])
        assert excinfo.value.code == 2

    def test_unknown_option_before_shell_is_error(self):
        with pytest.raises(SystemExit) as excinfo:
            parse_args(["-x", "shell"])
        assert excinfo.value.code == 2

    def test_unknown_option_only_is_error(self):
        with pytest.raises(SystemExit) as excinfo:
            parse_args(["--bogus"])
        assert excinfo.value.code == 2

    def test_multiple_unknown_options_before_command_are_error(self):
        with pytest.raises(SystemExit) as excinfo:
            parse_args(["--bogus", "-x", "build"])
        assert excinfo.value.code == 2

    def test_unknown_option_before_pass_through_command_is_error(
            self):
        """An unknown option before ``opencode`` is rejected instead of
        being passed through to opencode."""
        with pytest.raises(SystemExit) as excinfo:
            parse_args(["--unknown", "opencode", "--port", "4096"])
        assert excinfo.value.code == 2

    def test_global_options_before_command_still_work(self):
        """Recognized global options before the command are honored and
        do not turn into an error."""
        args = parse_args(["-vvv", "--shell", "zsh", "opencode"])
        assert args.verbose == 3
        assert args.shell == "zsh"
        assert args.command == "opencode"
        assert args.opencode_args == []


class FakeRunContainer:
    """Minimal stand-in for a running podman Container."""

    def __init__(self, name="fake"):
        self.name = name
        self.status = "running"

    def reload(self):
        pass

    def start(self):
        pass

    def stop(self, timeout=None):
        pass

    def remove(self):
        pass


class FakeRunContainers:
    """Minimal stand-in for the podman containers manager."""

    def __init__(self):
        self.created = []

    def create(self, *args, **kwargs):
        self.created.append(kwargs)
        return FakeRunContainer(kwargs.get("name", "fake"))


class FakeRunClient:
    """Stand-in for podman.PodmanClient in ``run_container``."""

    def __init__(self, images, containers):
        self.images = images
        self.containers = containers

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestRunContainerCommand:
    """Test that run_container execs the requested command."""

    @pytest.fixture
    def project(self, tmp_path: Path, monkeypatch):
        """A small project without custom addons."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        container_file = config_dir / "Containerfile"
        container_file.write_text(
            "FROM ubuntu:26.04\n")
        # The image build is not part of these tests; only the exec
        # behavior of run_container is exercised, so build_container is
        # stubbed out.
        monkeypatch.setattr(
            ContainerDefintion, "build_container",
            lambda self, keep_container_file=False: None)
        config_file = config_dir / "config.yaml"
        config_file.write_text("version: 0.1\n")
        config = make_configuration(config_file, [config_dir])
        return ContainerDefintion(tmp_path, container_file, config)

    @pytest.fixture
    def exec_cmd(self, monkeypatch):
        """Capture the ``podman exec`` command built by
        run_container instead of calling the real CLI."""
        commands = []
        monkeypatch.setattr(
            "dev_isolator.container_manager.subprocess.call",
            lambda cmd: commands.append(cmd) or 0)
        return commands

    def test_execs_given_command(self, project, monkeypatch, exec_cmd):
        """run_container runs the given command instead of a shell
        inside the container."""
        import os

        expected_term = os.environ.get("TERM", "xterm")
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.ERROR)
        images = FakeImages()
        images.existing[project.image_name()] = FakeImage(
            id="sha256:abc123", tags=[project.image_name()])
        containers = FakeRunContainers()
        monkeypatch.setattr(
            "dev_isolator.container_manager.podman.PodmanClient",
            lambda: FakeRunClient(images, containers))

        exit_code = project.run_container(
            command=["opencode", "auth", "login"])

        assert exit_code == 0
        assert len(exec_cmd) == 1
        cmd = exec_cmd[0]
        assert cmd[:7] == ["podman", "exec", "-it", "--detach-keys", "",
                           "-e", f"TERM={expected_term}"]
        # The session runs in the created container ...
        assert cmd[-4] == containers.created[0]["name"]
        # ... and the command with its arguments.
        assert cmd[-3:] == ["opencode", "auth", "login"]

    def test_defaults_to_interactive_shell(self, project, monkeypatch,
                                           exec_cmd):
        """Without a command, run_container still opens the
        interactive shell."""
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.ERROR)
        images = FakeImages()
        images.existing[project.image_name()] = FakeImage(
            id="sha256:abc123", tags=[project.image_name()])
        containers = FakeRunContainers()
        monkeypatch.setattr(
            "dev_isolator.container_manager.podman.PodmanClient",
            lambda: FakeRunClient(images, containers))

        project.run_container("zsh")

        assert len(exec_cmd) == 1
        assert exec_cmd[0][-1] == "zsh"


class TestMainRunDispatch:
    """Test that main_intern() dispatches the ``run`` subcommand.

    ``run`` without a command must fall back to ``shell``; with a
    command it must pass the command and its arguments through to
    ``in_container_command``.

    ``main_intern`` is exercised directly: it holds the dispatch logic
    and returns the handler's exit code; the ``main`` wrapper reports
    that code unchanged (or 255 for an unhandled exception).
    """

    def test_run_without_command_dispatches_to_shell(self, monkeypatch):
        """``di run`` with no command opens the interactive shell
        instead of raising a TypeError."""
        di_main = importlib.import_module("dev_isolator.main")

        called = {}

        def fake_shell(definition, args):
            called["shell"] = args.shell
            return 0

        monkeypatch.setattr(di_main, "shell", fake_shell)
        monkeypatch.setattr(di_main, "ContainerDefintion",
                            lambda *args, **kwargs: object())
        monkeypatch.setattr(sys, "argv", ["di", "run"])

        assert di_main.main_intern() == 0
        # run without a command falls back to the shell handler, which
        # starts the default shell.
        assert called == {"shell": DEFAULT_SHELL}

    def test_run_with_command_is_passed_through(self, monkeypatch):
        """``di run make test`` passes the command and its arguments
        through to in_container_command."""
        di_main = importlib.import_module("dev_isolator.main")

        called = {}

        def fake_in_container(definition, command, remainder):
            called["call"] = (command, remainder)
            return 0

        monkeypatch.setattr(di_main, "in_container_command",
                            fake_in_container)
        monkeypatch.setattr(di_main, "ContainerDefintion",
                            lambda *args, **kwargs: object())
        monkeypatch.setattr(sys, "argv", ["di", "run", "make", "test"])

        assert di_main.main_intern() == 0
        assert called["call"] == ("make", ["test"])


class TestMainExitCodes:
    """Test that main() returns the handler's exit code and turns an
    unhandled exception into the exit code 255.

    The wrapper must not discard the handler's return value: its exit
    code is passed straight through to the process exit status.
    """

    def test_returns_handler_exit_code(self, monkeypatch):
        """The exit code of the dispatched handler is returned as-is."""
        di_main = importlib.import_module("dev_isolator.main")
        monkeypatch.setattr(di_main, "main_intern", lambda: 1)

        assert di_main.main() == 1

    def test_unhandled_exception_returns_255(self, monkeypatch):
        """An unhandled exception is reported and turned into 255
        instead of propagating."""
        di_main = importlib.import_module("dev_isolator.main")

        def boom():
            raise RuntimeError("boom")

        monkeypatch.setattr(di_main, "main_intern", boom)

        assert di_main.main() == 255


class TestContainerName:
    """Test the naming of the containers created by run_container."""

    @pytest.fixture
    def project(self, tmp_path: Path, monkeypatch):
        """A small project without custom addons."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        container_file = config_dir / "Containerfile"
        container_file.write_text(
            "FROM ubuntu:26.04\n")
        # The image build is not part of these tests; build_container is
        # stubbed so only the container naming is exercised.
        monkeypatch.setattr(
            ContainerDefintion, "build_container",
            lambda self, keep_container_file=False: None)
        config_file = config_dir / "config.yaml"
        config_file.write_text("version: 0.1\n")
        config = make_configuration(config_file, [config_dir])
        return ContainerDefintion(tmp_path, container_file, config)

    def test_prefix_is_image_name_without_colon(self, project):
        prefix = project.container_name_prefix()
        assert prefix == project.image_name().replace(":", "-")

    def test_name_has_random_suffix(self, project):
        """The container name extends the prefix by 8 random
        lowercase alphanumeric characters, so several containers
        can run in parallel."""
        prefix = project.container_name_prefix()
        name = project.container_name()

        assert name.startswith(prefix + "-")
        suffix = name[len(prefix) + 1:]
        assert len(suffix) == CONTAINER_NAME_SUFFIX_LENGTH
        assert all(c in string.ascii_lowercase + string.digits
                   for c in suffix)

    def test_names_of_consecutive_calls_differ(self, project):
        names = {project.container_name() for _ in range(64)}
        assert len(names) == 64

    @pytest.fixture
    def exec_cmd(self, monkeypatch):
        """Capture the ``podman exec`` command built by
        run_container instead of calling the real CLI."""
        commands = []
        monkeypatch.setattr(
            "dev_isolator.container_manager.subprocess.call",
            lambda cmd: commands.append(cmd) or 0)
        return commands

    def test_created_container_is_named_with_suffix(
            self, project, monkeypatch, exec_cmd):
        """run_container creates a container whose name carries the
        random suffix (see TestContainerName)."""
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.ERROR)
        images = FakeImages()
        images.existing[project.image_name()] = FakeImage(
            id="sha256:abc123", tags=[project.image_name()])
        containers = FakeRunContainers()
        monkeypatch.setattr(
            "dev_isolator.container_manager.podman.PodmanClient",
            lambda: FakeRunClient(images, containers))

        project.run_container(command=["true"])

        prefix = project.container_name_prefix()
        name = containers.created[0]["name"]
        assert name.startswith(prefix + "-")
        assert len(name) == len(prefix) + 1 + CONTAINER_NAME_SUFFIX_LENGTH
        assert exec_cmd[0][-2] == name


class FakeCleanContainer:
    """Minimal stand-in for a podman Container in ``clean``."""

    def __init__(self, name, image_id=None, status="running"):
        self.name = name
        self.id = f"container-{name}"
        self.status = status
        self.attrs = {}
        if image_id is not None:
            self.attrs["ImageID"] = image_id
        self.events = []

    def reload(self):
        pass

    def stop(self, timeout=None):
        self.events.append(("stop", timeout))

    def kill(self, *args):
        self.events.append(("kill",))

    def remove(self):
        self.events.append(("remove",))


class StrictStopContainer(FakeCleanContainer):
    """A container whose stop always fails, falling back to kill."""

    def stop(self, timeout=None):
        raise podman.errors.APIError("stop failed")


class FakeCleanContainers:
    """Minimal stand-in for the podman containers manager."""

    def __init__(self, containers=None):
        self.containers = containers or []

    def list(self, all=False):
        return list(self.containers)


class TestCleanRemovesMatchingContainers:
    """Test that clean stops, kills and removes matching containers.

    Containers are matched by name (a project prefix followed by the
    random suffix) and by the id of the image they were created
    from.
    """

    @pytest.fixture
    def project(self, tmp_path: Path, monkeypatch):
        """A small project without custom addons."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        container_file = config_dir / "Containerfile"
        container_file.write_text(
            "FROM ubuntu:26.04\n")
        # The image build is not part of these tests; only the container
        # cleanup is exercised.
        config_file = config_dir / "config.yaml"
        config_file.write_text("version: 0.1\n")
        config = make_configuration(config_file, [config_dir])
        return ContainerDefintion(tmp_path, container_file, config)

    @pytest.fixture
    def containers(self):
        """The fake containers manager capturing the removals."""
        return FakeCleanContainers()

    @pytest.fixture
    def install_client(self, containers, monkeypatch):
        """Return a function patching PodmanClient to serve the fake
        containers together with given fake images."""

        def install(images):
            monkeypatch.setattr(
                "dev_isolator.container_manager.podman.PodmanClient",
                lambda: FakeRunClient(images, containers))
            return images

        return install

    def test_running_container_is_stopped_and_removed(
            self, project, containers, install_client, monkeypatch):
        prefix = project.container_name_prefix()
        container = FakeCleanContainer(f"{prefix}-abcd1234",
                                       image_id="sha256:abc123")
        containers.containers = [container]
        images = install_client(FakeImages())
        images.existing[project.image_name()] = FakeImage(
            id="sha256:abc123", tags=[project.image_name()])
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.ERROR)

        project.clean_image()

        assert container.events == [("stop", 10), ("remove",)]
        assert images.removed == [("sha256:abc123", True)]

    def test_stopped_container_is_removed_without_stop(
            self, project, containers, install_client, monkeypatch):
        prefix = project.container_name_prefix()
        container = FakeCleanContainer(f"{prefix}-abcd1234",
                                       image_id="sha256:abc123",
                                       status="exited")
        containers.containers = [container]
        images = install_client(FakeImages())
        images.existing[project.image_name()] = FakeImage(
            id="sha256:abc123", tags=[project.image_name()])
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.ERROR)

        project.clean_image()

        assert container.events == [("remove",)]

    def test_container_is_killed_when_stop_fails(
            self, project, containers, install_client, monkeypatch):
        prefix = project.container_name_prefix()
        container = StrictStopContainer(f"{prefix}-abcd1234",
                                        image_id="sha256:abc123")
        containers.containers = [container]
        images = install_client(FakeImages())
        images.existing[project.image_name()] = FakeImage(
            id="sha256:abc123", tags=[project.image_name()])
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.ERROR)

        project.clean_image()

        assert container.events == [("kill",), ("remove",)]

    def test_foreign_containers_are_left_alone(
            self, project, containers, install_client, monkeypatch):
        prefix = project.container_name_prefix()
        own_container = FakeCleanContainer(f"{prefix}-abcd1234",
                                           image_id="sha256:abc123")
        other_project = FakeCleanContainer(
            prefix + "2-other--default-abcd1234",
            image_id="sha256:otherimage")
        unrelated = FakeCleanContainer("unrelated-container",
                                       image_id="sha256:otherimage")
        containers.containers = [own_container, other_project, unrelated]
        images = install_client(FakeImages())
        images.existing[project.image_name()] = FakeImage(
            id="sha256:abc123", tags=[project.image_name()])
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.ERROR)

        project.clean_image()

        assert own_container.events == [("stop", 10), ("remove",)]
        assert other_project.events == []
        assert unrelated.events == []

    def test_clean_without_image_still_removes_containers(
            self, project, containers, install_client, monkeypatch):
        """Containers of the project are removed even when the image
        was already deleted."""
        prefix = project.container_name_prefix()
        container = FakeCleanContainer(f"{prefix}-abcd1234",
                                       image_id="sha256:abc123")
        containers.containers = [container]
        install_client(FakeImages())
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.ERROR)

        project.clean_image()

        assert container.events == [("stop", 10), ("remove",)]

    def test_clean_local_removes_containers_by_prefix(
            self, project, containers, install_client, monkeypatch):
        """clean --local removes containers of every tag variant of
        the current project, matched by name."""
        prefix = project.project_container_name_prefix()
        default_container = FakeCleanContainer(f"{prefix}--default-ab12",
                                               image_id="sha256:def456")
        gpu_container = FakeCleanContainer(f"{prefix}--gpu-cd34",
                                           image_id="sha256:ghi789")
        other_project = FakeCleanContainer(f"{prefix}-2--default-ab12",
                                           image_id="sha256:def456")
        containers.containers = [default_container, gpu_container,
                                 other_project]
        install_client(FakeImages())
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.ERROR)

        project.clean_local_images()

        assert default_container.events == [("stop", 10), ("remove",)]
        assert gpu_container.events == [("stop", 10), ("remove",)]
        # A project whose slug only shares a prefix does not match.
        assert other_project.events == []

    def test_clean_all_removes_containers_without_image(
            self, project, containers, install_client, monkeypatch):
        """clean --all removes dev-isolator containers even when no
        dev-isolator image exists anymore."""
        container = FakeCleanContainer(
            "dev-isolator-some-project--default-abcd1234",
            image_id="sha256:xyz000")
        containers.containers = [container]
        install_client(FakeImages())
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.ERROR)

        project.clean_all_images()

        assert container.events == [("stop", 10), ("remove",)]


class TestInitBase:
    """di init matches the ``--base`` value exactly against the
    bundled base images (the ``DI_BASE`` values of the bundled base
    Containerfiles)."""

    def _init_from(self, monkeypatch, tmp_path: Path, base_args):
        """Run ``di init`` with the given ``--base`` arguments in
        ``tmp_path`` and return the first (FROM) line of the generated
        Containerfile."""
        monkeypatch.chdir(tmp_path)
        assert init_project(parse_args(["init", *base_args])) == 0
        containerfile = (tmp_path / ".devisolator" / "Containerfile")
        return containerfile.read_text(encoding="utf-8").splitlines()[0]

    def test_default_base(self, monkeypatch, tmp_path: Path):
        """Without ``--base`` the default base ubuntu:26.04 is used."""
        assert self._init_from(monkeypatch, tmp_path, []) \
            == "FROM ubuntu:26.04"

    def test_plain_name_is_kept(self, monkeypatch, tmp_path: Path):
        """A plain base name is used as-is."""
        assert self._init_from(
            monkeypatch, tmp_path, ["--base", "ubuntu:26.04"]) \
            == "FROM ubuntu:26.04"

    def test_prefixed_base_is_unknown(self, monkeypatch, tmp_path: Path):
        """A base name with a ``dev-isolator-`` or
        ``localhost/dev-isolator-`` prefix matches no bundled base
        image and aborts with an error."""
        monkeypatch.chdir(tmp_path)
        # The unknown-base path consults the podman daemon; mock it so
        # the test does not depend on a running daemon or registry
        # configuration. Fetch the module from ``sys.modules``: the
        # package re-exports the ``main()`` function, so the attribute
        # ``dev_isolator.main`` (and thus ``monkeypatch``'s string
        # target) resolves to the function instead of the module.
        monkeypatch.setattr(sys.modules["dev_isolator.main"],
                            "detect_package_manager", lambda image: None)
        for base in ("dev-isolator-ubuntu:26.04",
                     "localhost/dev-isolator-ubuntu:24.04"):
            with pytest.raises(RuntimeError, match="unknown"):
                init_project(parse_args(["init", "--base", base]))

    def test_unknown_base_raises(self, monkeypatch, tmp_path: Path):
        """An unknown base image aborts with an error and nothing is
        written."""
        monkeypatch.chdir(tmp_path)
        # The unknown-base path consults the podman daemon; mock it so
        # the test does not depend on a running daemon or registry
        # configuration. Fetch the module from ``sys.modules``: the
        # package re-exports the ``main()`` function, so the attribute
        # ``dev_isolator.main`` (and thus ``monkeypatch``'s string
        # target) resolves to the function instead of the module.
        monkeypatch.setattr(sys.modules["dev_isolator.main"],
                            "detect_package_manager", lambda image: None)
        with pytest.raises(RuntimeError, match="unknown"):
            init_project(parse_args(["init", "--base", "bogus:1.0"]))
