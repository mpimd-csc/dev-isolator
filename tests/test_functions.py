"""Tests for the demo_function

SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

import json

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
        bundled ones when enabled in .devisolator/addons.yaml."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        container_file = config_dir / "Containerfile"
        container_file.write_text("FROM ubuntu:26.04\n")
        (config_dir / "addon.mine").write_text("RUN echo custom\n")
        (config_dir / "addons.yaml").write_text("enabled:\n  - mine\n")

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
        """The project's .devisolator/addons.yaml overrides the
        bundled one when it exists."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        container_file = config_dir / "Containerfile"
        container_file.write_text("FROM ubuntu:26.04\n")
        (config_dir / "addon.mine").write_text("RUN echo custom\n")
        (config_dir / "addons.yaml").write_text("enabled:\n  - mine\n")

        definition = ContainerDefintion(container_file, tmp_path)
        enabled = definition.enabled_addons()

        # Only the project config is honored; the bundled list is
        # ignored entirely.
        assert enabled == {"mine"}
        assert definition.addons_config_file().name == "addons.yaml"
        names = [a.name for a in definition.addon_files()]
        assert "addon.mine" in names
        assert "addon.opencode" not in names

    def test_bundled_addons_yaml_used_without_project_config(
            self, tmp_path: Path):
        """Without .devisolator/addons.yaml the bundled one is used."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        container_file = config_dir / "Containerfile"
        container_file.write_text("FROM ubuntu:26.04\n")

        definition = ContainerDefintion(container_file, tmp_path)

        assert definition.addons_config_file() \
            == (Path(__import__("dev_isolator.container_manager",
                                fromlist=["__file__"]).__file__).parent
                / BUNDLE_DIR / "addons.yaml").resolve()
        assert definition.enabled_addons() == {"opencode"}

    def test_no_addons_yaml_enables_everything(self, tmp_path: Path,
                                               monkeypatch):
        """Without any addons.yaml file every addon.* file is used."""
        config_dir = tmp_path / ".devisolator"
        config_dir.mkdir()
        container_file = config_dir / "Containerfile"
        container_file.write_text("FROM ubuntu:26.04\n")
        (config_dir / "addon.mine").write_text("RUN echo custom\n")

        # Hide every addons.yaml file to simulate "no config at all".
        real_is_file = Path.is_file
        monkeypatch.setattr(
            Path, "is_file",
            lambda self: False if self.name == "addons.yaml"
            else real_is_file(self))

        definition = ContainerDefintion(container_file, tmp_path)

        assert not definition.addons_config_file()
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


IMAGE_ID = "abc123def4567890fedcba9876543210"
"""Hex string mimicking a podman build log image id marker."""


class FakeImage:
    """Minimal stand-in for a podman Image."""

    def __init__(self, tags=None, id="sha256:fake"):
        self.tags = tags
        self.id = id


def build_log_lines() -> list[bytes]:
    """Return canned build log lines mimicking the podman stream."""
    return [
        json.dumps({"stream": "STEP 1/3: FROM ubuntu:26.04\n"}).encode(),
        json.dumps({"stream": "STEP 2/3: RUN echo custom\n"}).encode(),
        json.dumps({"stream": f"{IMAGE_ID}\n"}).encode(),
        json.dumps({"stream": "COMMIT dev-isolator:test\n"}).encode(),
    ]


class FakeAPIResponse:
    """Minimal stand-in for a podman APIResponse.

    Replays canned build log lines via ``iter_lines()``.
    """

    def __init__(self, lines: list[bytes]):
        self._lines = lines

    def iter_lines(self):
        yield from self._lines

    def raise_for_status(self, not_found=None):
        return None


class FakeAPI:
    """Minimal stand-in for the podman APIClient.

    Records the ``post`` call for a build request.
    """

    def __init__(self, lines: list[bytes]):
        self.lines = lines
        self.build_post = None

    def post(self, path, **kwargs):
        # Copy streamed payload data, so it can be inspected after
        # the (fake) request "finished".
        data = kwargs.get("data")
        if hasattr(data, "read") and not isinstance(data, bytes):
            kwargs["data"] = data.read()
        self.build_post = {"path": path, **kwargs}
        return FakeAPIResponse(self.lines)


class FakeImages:
    """Minimal stand-in for the podman images manager."""

    def __init__(self):
        self.fetched = None

    def get(self, image_id):
        self.fetched = image_id
        return FakeImage(id=f"sha256:{image_id}")


class FakeClient:
    """Minimal stand-in for a podman.PodmanClient.

    Supports the ``with`` usage in ``build_container``.
    """

    def __init__(self, images, api):
        self.images = images
        self.api = api

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
        (config_dir / "addons.yaml").write_text("enabled:\n  - mine\n")
        return ContainerDefintion(container_file, tmp_path)

    @pytest.fixture
    def api(self):
        """A fake API client replaying a successful build."""
        return FakeAPI(build_log_lines())

    @pytest.fixture
    def patched_client(self, monkeypatch, api):
        """Patch PodmanClient with a fake returning ``api``."""
        images = FakeImages()
        monkeypatch.setattr(
            "dev_isolator.container_manager.podman.PodmanClient",
            lambda: FakeClient(images, api))
        return images

    def test_trace_prints_generated_containerfile(
            self, project, patched_client, api, monkeypatch, capsys):
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
            self, project, patched_client, api, monkeypatch, capsys):
        """Below Verbosity.TRACE no "[Containerfile]" lines appear."""
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.INFO)

        project.build_container()
        output = capsys.readouterr().out

        assert "[Containerfile]" not in output

    def test_info_streams_build_output(
            self, project, patched_client, api, monkeypatch, capsys):
        """At Verbosity.INFO the build log is streamed live, every
        line prefixed by "[build] "."""
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.INFO)

        project.build_container()
        output = capsys.readouterr().out

        lines = [line for line in output.splitlines()
                 if line.startswith("[build] ")]
        assert lines, "no [build] lines printed"
        plain = [line.removeprefix("[build] ") for line in lines]
        assert plain[0] == "STEP 1/3: FROM ubuntu:26.04"
        assert plain[1] == "STEP 2/3: RUN echo custom"
        # The image id marker and the last log line are streamed, too.
        assert IMAGE_ID in plain
        assert plain[-1] == "COMMIT dev-isolator:test"

    def test_error_does_not_stream_build_output(
            self, project, patched_client, api, monkeypatch, capsys):
        """Below Verbosity.INFO no "[build]" lines appear."""
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.ERROR)

        project.build_container()
        output = capsys.readouterr().out

        assert "[build]" not in output

    def test_build_requests_streamed_tarball(
            self, project, patched_client, api, monkeypatch, capsys):
        """The build request streams a context tarball holding the
        merged Containerfile and matches the used tag."""
        monkeypatch.setattr(
            "dev_isolator.utils.VERBOSITY", Verbosity.ERROR)

        project.build_container()

        assert api.build_post["path"] == "/build"
        assert api.build_post["stream"] is True
        params = api.build_post["params"]
        assert params["dockerfile"] == "Containerfile"
        assert params["t"] == project.image_name()
        assert params["nocache"] is False

        # The streamed payload is a tar with the merged Containerfile.
        import io
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(api.build_post["data"]),
                          mode="r:") as tar:
            member = tar.extractfile("Containerfile")
            content = member.read().decode("utf-8")
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
