"""Manage container definitions (build/create) for dev-isolator.

Copyright (C) 2026 Martin Köhler

This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it
under certain conditions; see LICENSE for details.
SPDX-License-Identifier: GPL-3.0-or-later
"""

import os
import random
import re
import string
import subprocess
import sys
import time
import podman.errors
from pathlib import Path
from .addons_config import AddonsConfiguration
from .utils import dprint, Verbosity
from .constants import (IMAGE_PREFIX,
                        CONTAINER_NAME_SUFFIX_LENGTH,
                        BUNDLE_DIR)
from .container_file import ContainerFile


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

    When the base image is built (see
    :class:`dev_isolator.base_image.BaseImage`), every
    ``addon.<name>.jinja2`` file whose name is enabled in a
    ``config.yaml`` file is rendered as a jinja2 template and appended
    to the base image's Containerfile. The addon templates receive the
    same variables as the host user template (see
    :func:`dev_isolator.base_image.render_template`). Only the first
    found ``config.yaml`` is used: the ``.devisolator/config.yaml`` in
    the build context when it exists, otherwise the one bundled with
    dev-isolator. Without any ``config.yaml``, every ``addon.*`` file
    is appended.

    When a container is run, the environment variables and volume
    mounts declared directly in the active ``config.yaml`` and, in
    addition, those each enabled addon requests through a
    ``config.<addon_name>.yaml`` file (see
    :class:`dev_isolator.addons_config.AddonsConfiguration`) are passed
    to the container when it is created (see
    :meth:`run_container`). Settings from ``config.yaml`` are applied
    first, addon settings override them and the ``/workspace`` mount of
    the current directory always takes precedence.
    """

    def __init__(self, context_dir, container_file, config_file,
                 addon_search_path, tag=None):
        """Set up the container definition.

        :param container_file: Optional path to a custom Containerfile.
            When ``None``, a ``Containerfile`` inside ``context_dir``
            is used.
        :param context_dir: Optional path to the build context
            directory. When ``None``, the package's bundled
            ``data`` directory is used.
        :raises FileNotFoundError: if the resolved Containerfile does
            not exist or the referenced base image has no matching
            base Containerfile.
        :raises RuntimeError: if the Containerfile's ``FROM``
            instruction does not identify a dev-isolator base image or
            a podman build fails (see :class:`ContainerFile`).
        """
        self.context_dir = Path(context_dir)
        self.container_file = Path(container_file)
        self.config_file = Path(config_file) if config_file else None
        self.addons_search_path = addon_search_path
        if tag:
            self.tag = tag
        else:
            self.tag = "default"

        self.bundled_dir = Path(__file__).parent / BUNDLE_DIR
        self.addons = AddonsConfiguration(self.context_dir, self.bundled_dir,
                                          self.config_file,
                                          self.addons_search_path)

        dprint(Verbosity.DEBUG, f"Containerfile: {self.container_file}")
        dprint(Verbosity.DEBUG, f"Context dir:   {self.context_dir}")
        dprint(Verbosity.DEBUG, f"Config file:   {self.config_file}")
        dprint(Verbosity.DEBUG, f"Bundle dir:    {self.bundled_dir}")
        dprint(Verbosity.DEBUG, f"Addon search:  {self.addons_search_path}")

        self.container_file_obj = ContainerFile(self.context_dir,
                                                self.container_file,
                                                self.config_file,
                                                self.addons_search_path,
                                                self.image_name())

    def build_container(self, nocache=False, keep_container_file=False):
        """Build the image defined by this container definition.

        The merged Containerfile is written as a temporary file
        directly into the build context, so podman-py does not copy it
        itself (its copy, ``.containerfile.<random>``, would be left
        behind as an artifact in the build context). The temporary
        file is removed again after the build.

        If an image with the same tag (see :meth:`image_name`) existed
        before, it is removed after the build - but only when the
        build replaced it, i.e. its image id changed - so no old image
        is left as a dangling artifact in podman.

        :raises RuntimeError: if the podman build fails or the
            superseded image cannot be removed.
        """

        image = self.container_file_obj.build(nocache=nocache,
                                              keep_container_file=keep_container_file)
        return image

    def run_container(self, interactive_shell: str = "bash",
                      command: list[str] | None = None) -> int:
        """Open an interactive terminal/shell inside the container.

        Builds the image (see :meth:`build_container`), creates a
        container from it, ensures it is running and then attaches the
        local terminal to a command inside the container. The container
        is stopped again after the session ends (unless it is already
        gone).

        :param interactive_shell: Shell to start inside the container.
            Ignored when ``command`` is given.
        :param command: Optional command to run inside the container
            instead of the interactive shell, for example
            ``["opencode", "--port", "4096"]``.
        :returns: The exit code of the session.
        :raises RuntimeError: if the container does not reach the
            ``running`` state.

        The container gets a unique name (see :meth:`container_name`),
        so several containers can run in parallel. The interactive
        session itself is attached via the local
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

            # Settings declared directly in the project's config.yaml.
            config_environment, config_volumes = \
                self.addons.config_run_settings()
            environment |= config_environment
            volumes |= config_volumes

            # Settings requested by the enabled addons.
            addon_environment, addon_volumes = self.addons.addon_run_settings()
            environment |= addon_environment
            volumes |= addon_volumes

            # Bind the workspace last, so it always takes precedence.
            volumes[os.getcwd()] = {"bind": "/workspace", "mode": "rw"}

            dprint(Verbosity.INFO,
                   f"Passing environment variables "
                   f"{sorted(environment)} to the container")
            dprint(Verbosity.INFO,
                   f"Mounting volumes {sorted(volumes)} "
                   "into the container")

            # A unique name lets several containers of the same
            # project run in parallel.
            container_name = self.container_name()

            container = client.containers.create(
                image,
                name=container_name,
                # The image's entrypoint may exit immediately; keep
                # the container alive so shells can exec into it.
                entrypoint=["sleep", "infinity"],
                # Run as the same UID/GID as the host user
                # (podman's --userns=keep-id), so files created in
                # the /workspace bind mount stay owned by the user.
                userns_mode="keep-id",
                environment=environment,
                volumes=volumes,
                detach=True,
            )
            dprint(Verbosity.INFO,
                   f"Created container {container_name} "
                   f"from {self.image_name()}")

            container.reload()
            if container.status in ("created", "exited", "stopped"):
                container.start()
                dprint(Verbosity.INFO,
                       f"Started container {container_name}")
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
                        f"Container {container_name} did not reach "
                        f"'running' state (status: {container.status})")

            if command is None:
                exec_argv = [interactive_shell]
            else:
                exec_argv = list(command)
            exec_cmd = [
                "podman", "exec", "-it",
                "--detach-keys", "",
                "-e", f"TERM={os.environ.get('TERM', 'xterm')}",
                container_name,
                *exec_argv,
            ]
            session_command = " ".join(exec_argv)
            dprint(Verbosity.INFO,
                   f"Opening terminal in {container_name} "
                   f"({session_command}; detach with Ctrl-D or 'exit')")
            exit_code = subprocess.call(exec_cmd)
            dprint(Verbosity.INFO,
                   f"Session in {container_name} exited "
                   f"(exit code: {exit_code})")

            # Shut the container down after the terminal session ends.
            try:
                container.stop(timeout=0)
                dprint(Verbosity.INFO,
                       f"Stopped container {container_name}")
                container.remove()
                dprint(Verbosity.INFO,
                       f"Removed container {container_name}")
            except podman.errors.NotFound:
                # Container was removed elsewhere; nothing to shut down.
                dprint(Verbosity.INFO,
                       f"Container {container_name} is already gone")
            except podman.errors.APIError as e:
                dprint(Verbosity.INFO,
                       f"Warning: could not stop container "
                       f"{container_name}: {e}", file=sys.stderr)
        return exit_code

    def clean_image(self):
        """Remove the image built for this container definition.

        Stops (eventually killing) all containers of this container
        definition (by name and by image) and removes them before the
        image is removed; containers are also cleaned when the image
        does not exist anymore.

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
                image = None
            if image is None:
                self._clean_containers(
                    client, [(self.container_name_prefix(), "-")])
                return
            self._clean_images(client, [image],
                               [(self.container_name_prefix(), "-")])

    def clean_local_images(self):
        """Remove all dev-isolator images of the current project.

        Stops (eventually killing) all containers derived from any
        dev-isolator image of the current project — matching by name
        or by image — and removes them before the images are removed.

        When the global verbosity (see :func:`utils.set_verbosity`)
        is at least ``Verbosity.INFO`` (for example ``--verbose``),
        the removed containers and images are printed to stdout.
        :raises RuntimeError: if podman refuses to remove the images.
        """

        name_matchers = [(self.project_container_name_prefix(), "--")]
        with podman.PodmanClient() as client:
            images = [image for image in client.images.list()
                      if self._is_devisolator_local_image(image)]
            if not images:
                dprint(Verbosity.INFO,
                       f"No {IMAGE_PREFIX} images found; nothing to clean")
                self._clean_containers(client, name_matchers)
                return
            self._clean_images(client, images, name_matchers)

    def clean_all_images(self):
        """Remove all dev-isolator images.

        Stops (eventually killing) all containers derived from any
        dev-isolator image — matching by name or by image — and
        removes them before the images are removed.

        When the global verbosity (see :func:`utils.set_verbosity`)
        is at least ``Verbosity.INFO`` (for example ``--verbose``),
        the removed containers and images are printed to stdout.
        :raises RuntimeError: if podman refuses to remove the images.
        """

        name_matchers = [(IMAGE_PREFIX, "-")]
        with podman.PodmanClient() as client:
            images = [image for image in client.images.list()
                      if self._is_devisolator_image(image)]
            if not images:
                dprint(Verbosity.INFO,
                       f"No {IMAGE_PREFIX} images found; nothing to clean")
                self._clean_containers(client, name_matchers)
                return
            self._clean_images(client, images, name_matchers)

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
    def _is_devisolator_local_image(image) -> bool:
        """Return whether ``image`` is a dev-isolator image of the
           current project.

        Like :meth:`_is_devisolator_image`, but limited to the current
        working directory: an image counts when one of its tags names
        the ``image_name_prefix()`` repository (``dev-isolator:<slug>``
        for the current path), for example ``dev-isolator:my-project``
        or ``dev-isolator:my-project--default`` (optionally prefixed by
        a registry such as ``localhost/``). A different project whose
        slug only shares a prefix (e.g. ``my-project-2``) does not
        match.
        """
        prefix = f"{IMAGE_PREFIX}:{ContainerDefintion.current_path_slug()}"
        return any(
            repo_tag == prefix or repo_tag.startswith(prefix + "--")
            for repo_tag in (
                tag.partition("@")[0].rsplit("/", 1)[-1]
                for tag in image.tags or [])
        )

    @staticmethod
    def _clean_images(client, images, name_matchers=()):
        """Stop, kill and remove the containers based on ``images``
        or named after ``name_matchers`` and remove the images
        afterwards.

        :param client: Connected :class:`podman.PodmanClient`.
        :param images: Images to remove.
        :param name_matchers: ``(base, separator)`` pairs describing
            container name prefixes whose containers are removed
            additionally (for example containers left over from a
            previously built image).
        :raises RuntimeError: if podman refuses to remove an image.
        """
        image_ids = {image.id for image in images}
        ContainerDefintion._clean_containers(client, name_matchers,
                                             image_ids)

        for image in images:
            shown_name = ", ".join(image.tags) if image.tags else image.id
            try:
                client.images.remove(image.id, force=True)
                dprint(Verbosity.INFO, f"Removed image {shown_name}")
            except podman.errors.APIError as e:
                raise RuntimeError(
                    f"Failed to remove image {shown_name}: {e}") from e

    @staticmethod
    def _clean_containers(client, name_matchers, image_ids=frozenset()):
        """Stop, kill and remove all containers matching the criteria.

        A container matches when its name equals one of the
        ``name_matcher`` bases or starts with a base followed by its
        separator — ``name_matchers`` is a list of
        ``(base, separator)`` pairs — or when it was created from one
        of the ``image_ids``.

        Running containers are stopped (with a grace period); when
        stopping fails, they are killed. Afterwards they are removed.

        :param client: Connected :class:`podman.PodmanClient`.
        :param name_matchers: ``(base, separator)`` pairs matching
            container names.
        :param image_ids: Ids of images whose containers are removed.
        """
        image_ids = frozenset(image_ids)

        def matches(container) -> bool:
            name = container.name or ""
            if any(name == base or name.startswith(base + separator)
                   for base, separator in name_matchers):
                return True
            # The container report keys "ImageID" and "Image" name
            # the image the container was created from.
            container_image_id = container.attrs.get("ImageID") \
                or container.attrs.get("Image")
            return (container_image_id is not None
                    and container_image_id in image_ids)

        for container in client.containers.list(all=True):
            name = container.name or container.id
            if not matches(container):
                continue
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

    def image_name(self) -> str:
        """Return the image name
           ``dev-isolator:<slug_of_the_current_path>--<tag>``."""
        return f"{IMAGE_PREFIX}:{self.current_path_slug()}--{self.tag}"

    def project_container_name_prefix(self) -> str:
        """Return the container name prefix shared by every tag
           variant of the current project
           ``dev-isolator-<slug_of_the_current_path>``.

        Used by ``clean --local`` to find the containers of all tag
        variants of the project (see :meth:`clean_local_images`).
        """
        return f"{IMAGE_PREFIX}-{self.current_path_slug()}"

    def container_name_prefix(self) -> str:
        """Return the container name prefix shared by all containers
           of this container definition
           ``dev-isolator-<slug_of_the_current_path>--<tag>``.

        Podman container names cannot contain ':', so the tag's
        ``dev-isolator:slug--tag`` form becomes ``dev-isolator-slug--tag``.
        """
        return self.image_name().replace(":", "-")

    def container_name(self) -> str:
        """Return a unique container name for a new run.

        The name is the :meth:`container_name_prefix` followed by a
        random suffix of :const:`CONTAINER_NAME_SUFFIX_LENGTH`
        lowercase alphanumeric characters, for example
        ``dev-isolator-my-project--default-k3x9q2zf``.

        The random suffix lets several containers of the same project
        run in parallel without name clashes. Containers matching the
        prefix are removed again by the ``clean`` command (see
        :meth:`clean_image`).
        """
        alphabet = string.ascii_lowercase + string.digits
        suffix = "".join(random.choices(alphabet,
                                        k=CONTAINER_NAME_SUFFIX_LENGTH))
        return f"{self.container_name_prefix()}-{suffix}"

    @staticmethod
    def current_path_slug() -> str:
        """Return the slug of the current working directory path."""
        return slugify_path(Path.cwd().resolve())
