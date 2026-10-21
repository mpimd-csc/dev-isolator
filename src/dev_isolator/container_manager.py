"""Manage container definitions (build/create) for dev-isolator.

Copyright (C) 2026 Martin Köhler

This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it
under certain conditions; see LICENSE for details.
SPDX-License-Identifier: GPL-3.0-or-later
"""

import os
import re
import subprocess
import sys
import time
import json
import yaml
import podman.errors
from tempfile import NamedTemporaryFile
from pathlib import Path
from .utils import dprint, Verbosity, get_verbosity

IMAGE_PREFIX = "dev-isolator"
ADDONS_FILE_NAME = "addons.yaml"
DEV_ISOLATOR_DIR = ".devisolator"
BUNDLE_DIR = "data"

# Sentinel marking a not-yet-computed cached value.
_UNSET = object()

def slugify_path(path: str | os.PathLike) -> str:
    """Return a slug (e.g. ``my-project``) suitable
       for a container name/tag.

    Replaces every character outside ``[a-zA-Z0-9]`` with ``-``,
    collapses runs of such characters, strips leading/trailing ``-``
    and lowercases the result.
    """
    text = str(path)
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()


def read_enabled_addons(addons_file: Path) -> set[str]:
    """Return the addon names enabled in an ``addons.yaml`` file.

    The file is expected to have the shape::

        enabled:
          - opencode
          - my-addon

    :param addons_file: Path to the ``addons.yaml`` file.
    :returns: The set of enabled addon names (possibly empty).
    :raises RuntimeError: if the file is not valid YAML, does not
        contain a mapping, or the ``enabled`` entry is not a list.
    """
    try:
        data = yaml.safe_load(addons_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RuntimeError(
            f"Invalid addons file {addons_file}: {e}") from e
    if data is None:
        return set()
    if not isinstance(data, dict):
        raise RuntimeError(
            f"Invalid addons file {addons_file}: "
            "expected a mapping with an 'enabled' key")
    enabled = data.get("enabled")
    if enabled is None:
        return set()
    if not isinstance(enabled, list):
        raise RuntimeError(
            f"Invalid addons file {addons_file}: "
            "'enabled' must be a list of addon names")
    return {str(name) for name in enabled}


class ContainerDefintion:
    """Define, build and run the dev-isolator container for a project.

    By default the ``Containerfile`` and build context shipped with
    dev-isolator are used; both can be overridden through the
    constructor. The image name and container name are derived from
    the slug of the current working directory (see
    :func:`slugify_path`).

    When the image is built, every ``addon.*`` file whose name is
    enabled in an ``addons.yaml`` file is appended to the used
    ``Containerfile``. Only the first found ``addons.yaml`` is used:
    the ``.devisolator/addons.yaml`` in the build context when it
    exists, otherwise the one bundled with dev-isolator. Without any
    ``addons.yaml``, every ``addon.*`` file is appended.
    """

    def __init__(self, container_file=None, context_dir=None):
        """Set up the container definition.

        :param container_file: Optional path to a custom Containerfile.
            When ``None``, a ``Containerfile`` inside ``context_dir``
            is used.
        :param context_dir: Optional path to the build context
            directory. When ``None``, the package's bundled
            ``data`` directory is used.
        :raises FileNotFoundError: if the resolved Containerfile does
            not exist.
        """
        if context_dir:
            self.context_dir = Path(context_dir)
        else:
            self.context_dir = Path(__file__).parent / BUNDLE_DIR

        if container_file:
            self.container_file = Path(container_file)
        else:
            self.container_file = self.context_dir / "Containerfile"

        if not self.container_file.is_file():
            raise FileNotFoundError("No Containerfile found at"
                                    f"{self.container_file}")
        self.bundled_dir = Path(__file__).parent / BUNDLE_DIR
        self._enabled_addons = _UNSET
        self._addon_files = _UNSET

        dprint(Verbosity.INFO, f"Container File: {self.container_file}")
        dprint(Verbosity.INFO, f"Context Dir:    {self.context_dir}")
        dprint(Verbosity.INFO, f"Bundle Dir:     {self.bundled_dir}")

    def addons_config_file(self) -> Path | None:
        """Return the active ``addons.yaml`` file controlling the addons.

        The ``.devisolator/addons.yaml`` in the build context is used
        when it exists; otherwise the bundled ``addons.yaml`` is used.

        :returns: The resolved (absolute) path of the active
                  ``addons.yaml`` file, or ``None`` when neither
                  exists.
        """
        for directory in (self.context_dir / DEV_ISOLATOR_DIR,
                          self.bundled_dir):
            candidate = directory / ADDONS_FILE_NAME
            dprint(Verbosity.TRACE, f"Try addon file {candidate}")
            resolved = candidate.resolve()
            if resolved.is_file():
                return resolved
        return None

    def enabled_addons(self) -> set[str] | None:
        """Return the names of the enabled addons.

        Only the first ``addons.yaml`` file found by
        :meth:`addons_config_file` is used: the
        ``.devisolator/addons.yaml`` in the build context when it
        exists, otherwise the bundled one.

        The result is computed once and cached for the lifetime of
        this instance, so repeated calls (e.g. from
        :meth:`addon_files` and :meth:`merged_container_file_content`
        during a build) do not print the addon managing messages
        again.

        :returns: The set of enabled addon names, or ``None`` when
            no ``addons.yaml`` file exists at all (in which case
            every ``addon.*`` file is used).
        :raises RuntimeError: if an ``addons.yaml`` file is invalid.
        """
        if self._enabled_addons is _UNSET:
            addon_file = self.addons_config_file()
            if not addon_file:
                enabled = None
            else:
                names = read_enabled_addons(addon_file)
                enabled = set(names)
                dprint(Verbosity.INFO,
                       f"Addons enabled in {addon_file}: {sorted(names)}")
            self._enabled_addons = enabled
        return self._enabled_addons

    def addon_files(self):
        """Return all ``addon.*`` files appended to the Containerfile.

        The addons bundled with dev-isolator come first, followed by
        any ``addon.*`` files in the ``.devisolator`` directory
        inside the build context (sorted by file name). Duplicates
        are removed, so when the build context *is* the bundled
        directory, every file is only returned once.

        Only addons whose name (the part after ``addon.``, e.g.
        ``opencode`` for ``addon.opencode``) is enabled via
        :meth:`enabled_addons` are returned. Without any
        ``addons.yaml`` file, every ``addon.*`` file is returned.

        Like :meth:`enabled_addons`, the result is computed once and
        cached for the lifetime of this instance, so the addon
        managing messages are only printed once per build.

        :returns: List of resolved (absolute) paths to the addon files.
        """
        if self._addon_files is _UNSET:
            enabled = self.enabled_addons()

            addons = []
            seen = set()
            for directory in (self.bundled_dir,
                              self.context_dir / DEV_ISOLATOR_DIR):
                if not directory.is_dir():
                    continue
                for addon in sorted(directory.glob("addon.*")):
                    if not addon.is_file():
                        continue
                    name = addon.name.removeprefix("addon.")
                    if enabled is not None and name not in enabled:
                        dprint(Verbosity.INFO,
                               f"Skipping disabled addon: {addon.name}")
                        continue
                    resolved = addon.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        addons.append(resolved)
            self._addon_files = addons
        return list(self._addon_files)

    def merged_container_file_content(self) -> str:
        """Return the used Containerfile with all addons appended.

        Starts with the content of ``self.container_file`` and appends
        the content of every file from :meth:`addon_files`, separated
        by a comment naming the addon file.
        """
        text = self.container_file.read_text(encoding="utf-8")
        for addon in self.addon_files():
            text += f"\n\n# --- appended from {addon.name} ---\n"
            text += addon.read_text(encoding="utf-8").rstrip("\n") + "\n"
        return text

    def build_container(self, nocache=False):
        """Build the image defined by this container definition.

        Builds from the used Containerfile with all ``addon.*`` files
        appended (see :meth:`merged_container_file_content`) and the
        build context as context. The image is tagged
        ``dev-isolator:<slug_of_the_current_path>``.

        The merged Containerfile is written to a temporary file
        outside the build context, so the original directory is
        not modified.

        When the global verbosity (see :func:`utils.set_verbosity`)
        is greater than ``Verbosity.INFO`` (i.e. ``Verbosity.DEBUG``,
        for example ``--verbose --verbose``), the full
        build log is printed to stdout after the build has finished.

        At ``Verbosity.TRACE`` (``--verbose --verbose --verbose``),
        the generated (merged) Containerfile is printed to stdout
        before the build, with every line prefixed by
        ``[Containerfile] ``.

        :raises RuntimeError: if the podman build fails.
        """

        tag = self.image_name()
        merged_content = self.merged_container_file_content()

        try:
            with podman.PodmanClient() as client:
                for addon in self.addon_files():
                    dprint(Verbosity.INFO, f"Appending addon: {addon}")
                if get_verbosity() >= Verbosity.TRACE:
                    for line in merged_content.splitlines():
                        print(f"[Containerfile] {line}")
                with NamedTemporaryFile(
                        "w",
                        encoding="utf-8",
                        prefix="dev-isolator-containerfile-"
                        ) as merged_container_file:
                    dprint(Verbosity.INFO, "Created temporary Containerfile "
                           f"{merged_container_file.name}")
                    merged_container_file.write(merged_content)
                    # Make sure the content reaches disk before podman
                    # reads the file during the build.
                    merged_container_file.flush()

                    image, build_logs = client.images.build(
                        path=str(self.context_dir),
                        dockerfile=merged_container_file.name,
                        tag=tag,
                        rm=True,
                        forcerm=True,
                        pull=True,
                        nocache=nocache
                        )
        except podman.errors.BuildError as e:
            raise RuntimeError(f"Failed to build image {tag}: {e}") from e
        except podman.errors.APIError as e:
            raise RuntimeError(f"Podman API error while building {tag}: {e}")

        if get_verbosity() > Verbosity.INFO:
            for entry in build_logs:
                if isinstance(entry, bytes):
                    entry = json.loads(entry.decode(errors='replace').strip())
                for stream in entry.get("stream", "").splitlines():
                    print(f"[build] {stream}")

        print(f"Built image {image.id} tagged {tag}")
        return image

    def run_container(self, interactive_shell: str = "bash"):
        """Open an interactive terminal/shell inside the container.

        Builds the image (see :meth:`build_container`), creates the
        container from it (or reuses one created earlier), ensures
        it is running and then attaches the local terminal to a
        shell inside the container. The container is stopped again
        after the shell session ends (unless it is already gone).

        :param interactive_shell: Shell to start inside the container.
        :returns: The exit code of the shell session.
        :raises RuntimeError: if the container does not reach the
            ``running`` state.

        The interactive session itself is attached via the local
        ``podman exec -it`` CLI, because the podman Python bindings
        cannot bridge stdin/TTY to the podman service (their
        ``attach``/``attach_socket``/``exec_run(socket=True)`` are not
        implemented).
        """

        self.build_container( nocache=False)

        with podman.PodmanClient() as client:
            image = client.images.get(self.image_name())
            try:
                container = client.containers.get(self.container_name())
                print(f"Reusing existing container {self.container_name()}")
            except podman.errors.NotFound:
                container = client.containers.create(
                    image,
                    name=self.container_name(),
                    # The image's entrypoint may exit immediately; keep
                    # the container alive so shells can exec into it.
                    entrypoint=["sleep", "infinity"],
                    detach=True,
                )
                print(f"Created container {self.container_name()} "
                      f"from {self.image_name()}")

            container.reload()
            if container.status in ("created", "exited", "stopped"):
                container.start()
                print(f"Started container {self.container_name()}")
                # Wait until the container is actually running before
                # attaching, otherwise podman exec may hit it too early.
                for _ in range(100):
                    container.reload()
                    if container.status == "running":
                        break
                    time.sleep(0.1)
                else:
                    raise RuntimeError(
                        f"Container {self.container_name()} did not reach "
                        f"'running' state (status: {container.status})")

            exec_cmd = [
                "podman", "exec", "-it",
                "--detach-keys", "",
                "-e", f"TERM={os.environ.get('TERM', 'xterm')}",
                self.container_name(),
                interactive_shell,
            ]
            print(f"Opening terminal in {self.container_name()} "
                  f"({interactive_shell}; detach with Ctrl-D or 'exit')")
            exit_code = subprocess.call(exec_cmd)
            print(f"Shell in {self.container_name()} exited "
                  f"(exit code: {exit_code})")

            # Shut the container down after the terminal session ends.
            try:
                container.stop(timeout=0)
                print(f"Stopped container {self.container_name()}")
            except podman.errors.NotFound:
                # Container was removed elsewhere; nothing to shut down.
                print(f"Container {self.container_name()} is already gone")
            except podman.errors.APIError as e:
                print(f"Warning: could not stop container "
                      f"{self.container_name()}: {e}", file=sys.stderr)
        return exit_code

    def clean_image(self):
        """Remove the image built for this container definition.

        Stops (eventually killing) all containers based on the image
        and removes them before the image is removed.

        When the global verbosity (see :func:`utils.set_verbosity`)
        is at least ``Verbosity.INFO`` (for example ``--verbose``),
        the removed containers and image are printed to stdout.
        :raises RuntimeError: if podman refuses to remove the image.
        """

        with podman.PodmanClient() as client:
            try:
                image = client.images.get(self.image_name())
            except (podman.errors.NotFound, podman.errors.ImageNotFound):
                dprint(Verbosity.INFO, "Image "
                       f"{self.image_name()} not found; nothing to clean")
                return
            self._clean_images(client, [image])

    def clean_all_images(self):
        """Remove all dev-isolator images.

        Stops (eventually killing) all containers based on any
        dev-isolator image and removes them before the images are
        removed.

        When the global verbosity (see :func:`utils.set_verbosity`)
        is at least ``Verbosity.INFO`` (for example ``--verbose``),
        the removed containers and images are printed to stdout.
        :raises RuntimeError: if podman refuses to remove the images.
        """

        with podman.PodmanClient() as client:
            images = [image for image in client.images.list()
                      if self._is_devisolator_image(image)]
            if not images:
                dprint(Verbosity.INFO,
                       f"No {IMAGE_PREFIX} images found; nothing to clean")
                return
            self._clean_images(client, images)

    @staticmethod
    def _is_devisolator_image(image) -> bool:
        """Return whether ``image`` is tagged as a dev-isolator image.

        A image counts as dev-isolator image, when one of its tags
        names the ``IMAGE_PREFIX`` repository (the tag with the
        slug), for example ``dev-isolator:my-project`` or
        ``localhost/dev-isolator:my-project``.
        """
        return any(
            tag.partition("@")[0].rsplit("/", 1)[-1].rsplit(":", 1)[0]
            == IMAGE_PREFIX
            for tag in image.tags or [])

    @staticmethod
    def _clean_images(client, images):
        """Stop and remove the containers based on ``images`` and
        remove the images afterwards.

        :param client: Connected :class:`podman.PodmanClient`.
        :param images: Images to remove.
        :raises RuntimeError: if podman refuses to remove an image.
        """
        image_ids = {image.id for image in images}

        for container in client.containers.list(all=True):
            # The container report keys "ImageID" and "Image" name
            # the image the container was created from.
            container_image_id = container.attrs.get("ImageID") \
                or container.attrs.get("Image")
            if container_image_id is None \
                    or container_image_id not in image_ids:
                continue
            name = container.name or container.id
            try:
                container.reload()
                if container.status == "running":
                    try:
                        container.stop(timeout=10)
                        dprint(Verbosity.INFO, f"Stopped container {name}")
                    except podman.errors.APIError:
                        # Fall back to killing the container.
                        container.kill()
                        dprint(Verbosity.INFO, f"Killed container {name}")
                container.remove()
                dprint(Verbosity.INFO, f"Removed container {name}")
            except podman.errors.NotFound:
                dprint(Verbosity.INFO, f"Container {name} is already gone")
            except podman.errors.APIError as e:
                print(f"Warning: could not remove container "
                      f"{name}: {e}", file=sys.stderr)

        for image in images:
            shown_name = ", ".join(image.tags) if image.tags else image.id
            try:
                client.images.remove(image.id, force=True)
                dprint(Verbosity.INFO, f"Removed image {shown_name}")
            except podman.errors.APIError as e:
                raise RuntimeError(
                    f"Failed to remove image {shown_name}: {e}") from e

    def image_name(self) -> str:
        """Return the image name
           ``dev-isolator:<slug_of_the_current_path>``."""
        return f"{IMAGE_PREFIX}:{self.current_path_slug()}"

    def container_name(self) -> str:
        """Return a valid container name derived from the current path tag.

        Podman container names cannot contain ':', so the tag's
        ``dev-isolator:slug`` form becomes ``dev-isolator-slug``.
        """
        return self.image_name().replace(":", "-")

    @staticmethod
    def current_path_slug() -> str:
        """Return the slug of the current working directory path."""
        return slugify_path(Path.cwd().resolve())
