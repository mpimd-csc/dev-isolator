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
import tarfile
import time
import json
from tempfile import NamedTemporaryFile
import podman.errors
from pathlib import Path
from .addons_config import AddonsConfiguration, BUNDLE_DIR
from .utils import dprint, Verbosity, get_verbosity

IMAGE_PREFIX = "dev-isolator"
OCI_IMAGE_MANIFEST = "application/vnd.oci.image.manifest.v1+json"


def slugify_path(path: str | os.PathLike) -> str:
    """Return a slug (e.g. ``my-project``) suitable
       for a container name/tag.

    Replaces every character outside ``[a-zA-Z0-9]`` with ``-``,
    collapses runs of such characters, strips leading/trailing ``-``
    and lowercases the result.
    """
    text = str(path)
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()


class ContainerDefintion:
    """Define, build and run the dev-isolator container for a project.

    By default the ``Containerfile`` and build context shipped with
    dev-isolator are used; both can be overridden through the
    constructor. The image name and container name are derived from
    the slug of the current working directory (see
    :func:`slugify_path`).

    When the image is built, every ``addon.*`` file whose name is
    enabled in a ``config.yaml`` file is appended to the used
    ``Containerfile``. Only the first found ``config.yaml`` is used:
    the ``.devisolator/config.yaml`` in the build context when it
    exists, otherwise the one bundled with dev-isolator. Without any
    ``config.yaml``, every ``addon.*`` file is appended.

    When a container is run, each enabled addon may additionally
    contribute environment variables and volume mounts through a
    ``config.<addon_name>.yaml`` file (see
    :class:`dev_isolator.addons_config.AddonsConfiguration`). Those
    are passed to the container when it is created (see
    :meth:`run_container`).
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
        self.addons = AddonsConfiguration(self.context_dir, self.bundled_dir)

        dprint(Verbosity.INFO, f"Container File: {self.container_file}")
        dprint(Verbosity.INFO, f"Context Dir:    {self.context_dir}")
        dprint(Verbosity.INFO, f"Bundle Dir:     {self.bundled_dir}")

    def merged_container_file_content(self) -> str:
        """Return the used Containerfile with all addons appended.

        Starts with the content of ``self.container_file`` and appends
        the content of every file from
        :meth:`AddonsConfiguration.addon_files`, separated
        by a comment naming the addon file.
        """
        text = self.container_file.read_text(encoding="utf-8")
        for addon in self.addons.addon_files():
            text += f"\n\n# --- appended from {addon.name} ---\n"
            text += addon.read_text(encoding="utf-8").rstrip("\n") + "\n"
        text += "\n\n# --- workdir ---\n"
        text += "WORKDIR /workspace\n"
        return text

    def build_container(self, nocache=False):
        """Build the image defined by this container definition.

        :raises RuntimeError: if the podman build fails.
        """

        tag = self.image_name()
        merged_content = self.merged_container_file_content()

        try:
            with podman.PodmanClient() as client:
                for addon in self.addons.addon_files():
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
            raise RuntimeError(f"Podman API error while building {tag}: {e}") \
                from e

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

        self.build_container(nocache=False)

        with podman.PodmanClient() as client:
            image = client.images.get(self.image_name())

            environment: dict[str, str] = {}
            volumes: dict[str, dict] = {}

            addon_environment, addon_volumes = self.addons.addon_run_settings()
            environment |= addon_environment
            volumes |= addon_volumes

            # Bind the workspace
            volumes[os.getcwd()] = {"bind": "/workspace", "mode": "rw"}

            dprint(Verbosity.INFO,
                   f"Passing environment variables "
                   f"{sorted(environment)} to the container")
            dprint(Verbosity.INFO,
                   f"Mounting volumes {sorted(volumes)} "
                   "into the container")

            volumes[os.getcwd()] = {"bind": "/workspace", "mode": "rw"}

            container = client.containers.create(
                image,
                name=self.container_name(),
                # The image's entrypoint may exit immediately; keep
                # the container alive so shells can exec into it.
                entrypoint=["sleep", "infinity"],
                environment=environment,
                volumes=volumes,
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
                    container.kill(9)
                    container.remove()
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
                container.remove()
                print(f"Removed container {self.container_name()}")
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
