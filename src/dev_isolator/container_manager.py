"""Manage container definitions (build/create) for dev-isolator.

Copyright (C) 2026 Martin Köhler

This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it
under certain conditions; see LICENSE for details.
SPDX-License-Identifier: GPL-3.0-or-later
"""

import os
import pwd
import re
import subprocess
import sys
import time
import json
from tempfile import NamedTemporaryFile, mkstemp
from functools import lru_cache

import podman.errors
import jinja2
from pathlib import Path
from .addons_config import AddonsConfiguration, BUNDLE_DIR
from .utils import dprint, Verbosity, get_verbosity

IMAGE_PREFIX = "dev-isolator"
OCI_IMAGE_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
TEMPLATES_DIR = Path(__file__).parent / "templates"


def slugify_path(path: str | os.PathLike) -> str:
    """Return a slug (e.g. ``my-project``) suitable
       for a container name/tag.

    Replaces every character outside ``[a-zA-Z0-9]`` with ``-``,
    collapses runs of such characters, strips leading/trailing ``-``
    and lowercases the result.
    """
    text = str(path)
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()


@lru_cache(maxsize=1)
def _jinja_environment() -> jinja2.Environment:
    """Return the (cached) shared jinja2 environment."""
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATES_DIR),
        # Containerfile instructions, not HTML: no escaping.
        autoescape=False,
        keep_trailing_newline=True,
    )


@lru_cache(maxsize=1)
def _host_user_template() -> jinja2.Template:
    """Return the (cached) jinja2 template for the host user block."""
    return _jinja_environment().get_template(
        "host_user.containerfile.jinja2")


class ContainerDefintion:
    """Define, build and run the dev-isolator container for a project.

    By default the ``Containerfile`` and build context shipped with
    dev-isolator are used; both can be overridden through the
    constructor. The image name and container name are derived from
    the slug of the current working directory (see
    :func:`slugify_path`).

    When the image is built, every ``addon.<name>.jinja2`` file whose
    name is enabled in a ``config.yaml`` file is rendered as a jinja2
    template and appended to the used ``Containerfile``. The addon
    templates receive the same variables as the host user template
    (see :meth:`host_user_containerfile_content`). Only the first
    found ``config.yaml`` is used:
    the ``.devisolator/config.yaml`` in the build context when it
    exists, otherwise the one bundled with dev-isolator. Without any
    ``config.yaml``, every ``addon.*`` file is appended.

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
            not exist.
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

    def merged_container_file_content(self) -> str:
        """Return the used Containerfile with all addons appended.

        Starts with the content of ``self.container_file`` and appends
        every addon file from :meth:`AddonsConfiguration.addon_files`
        rendered as a jinja2 template (see
        :meth:`host_user_containerfile_content` for the template
        variables), separated by a comment naming the addon file.

        Afterwards a host user (same UID, GID and username as the
        invoking user, see :meth:`host_username`) is created and made
        the image's default user, so shells and exec sessions run as
        that user. It requires a Debian-style base image (``useradd``
        and ``getent`` must exist).
        """
        text = self.container_file.read_text(encoding="utf-8")
        text += "\n\n" + self.host_user_containerfile_content()
        for addon in self.addons.addon_files():
            text += f"\n\n# --- appended from {addon.name} ---\n"
            template = _jinja_environment().from_string(
                addon.read_text(encoding="utf-8"))
            text += self.render_template(template).rstrip("\n") + "\n"
        text += "\n\n# --- workdir ---\n"
        text += f"USER {ContainerDefintion.host_username()}\n"
        text += "WORKDIR /workspace\n"
        return text

    @staticmethod
    def render_template(template: jinja2.Template) -> str:
        """Render a Containerfile jinja2 template with the host
           context.

        :param template: The template to render.
        :returns: The rendered template text.
        """
        return template.render(
            uid=os.getuid(),
            gid=os.getgid(),
            username=ContainerDefintion.host_username(),
            home_dir=Path.home()
        )

    @staticmethod
    def host_username() -> str:
        """Return a username usable in the container for the host user.

        Uses the username of the invoking host user (via
        :func:`os.getuid`); invalid characters are replaced by ``_``.
        When no matching host user is known or the name is not usable
        for :command:`useradd`, a name starting with a letter is
        derived from the UID.
        """
        uid = os.getuid()
        try:
            name = pwd.getpwuid(uid).pw_name
        except KeyError:
            name = ""
        name = re.sub(r"[^a-zA-Z0-9_-]", "_", name).strip("-")
        if not name or not name[0].isalpha():
            name = f"user{uid}"
        return name

    def host_user_containerfile_content(self) -> str:
        """Return the Containerfile instructions adding the host user.

        Renders the ``templates/host_user.containerfile.jinja2``
        template shipped with dev-isolator (see :const:`TEMPLATES_DIR`)
        with the host context (see :meth:`render_template`), as do the
        ``addon.*`` files in :meth:`merged_container_file_content`.

        The template removes any pre-existing user/group occupying
        those properties from the image and then creates a user whose
        UID, GID and username match those of the invoking host user,
        making it the image's default user.
        """
        return self.render_template(_host_user_template())

    def _current_image_id(self, client, tag):
        """Return the id of the image tagged ``tag``, or ``None``.

        :param client: Connected :class:`podman.PodmanClient`.
        :param tag: Name (tag) of the image to look up.
        """
        try:
            return client.images.get(tag).id
        except (podman.errors.NotFound, podman.errors.ImageNotFound):
            dprint(Verbosity.INFO, f"No existing image {tag}")
            return None

    def _remove_superseded_image(self, client, tag, previous_id, new_id):
        """Remove the previous image with ``tag``, if the build
           replaced it.

        When podman applies a build's tag to a new image, the previous
        image loses the tag and remains as a dangling artifact. To
        avoid this, it is removed (by id) after the build - but only
        when the build actually produced a new image id.

        :param client: Connected :class:`podman.PodmanClient`.
        :param tag: Name (tag) of the image that was built.
        :param previous_id: Id of the image tagged ``tag`` before the
            build, or ``None`` when there was none.
        :param new_id: Id of the image the build produced.
        :raises RuntimeError: if podman refuses to remove the image.
        """
        if previous_id is None:
            dprint(Verbosity.INFO, f"No previous image {tag} to remove")
            return
        if previous_id == new_id:
            dprint(Verbosity.INFO, f"Image {tag} unchanged "
                   f"({previous_id}); not removing")
            return
        dprint(Verbosity.INFO, f"Removing superseded image {tag} "
               f"({previous_id})")
        try:
            client.images.remove(previous_id, force=True)
            dprint(Verbosity.INFO, f"Removed image {tag}")
        except podman.errors.APIError as e:
            raise RuntimeError(f"Failed to remove image {tag}: {e}") from e

    def _safe_container_file(self):
        merged_content = self.merged_container_file_content()
        with NamedTemporaryFile("w", prefix="Containerfile-fail-",
                                dir=os.getcwd(),
                                delete=False,
                                delete_on_close=False) as fp:
            filename = fp.name
            fp.write(merged_content)
        dprint(Verbosity.ERROR, f"Kept generated Containerfile as {filename}")

    def build_container(self, nocache=False, keep_container_file=False):
        """Build the image defined by this container definition.

        If an image with the same tag (see :meth:`image_name`) existed
        before, it is removed after the build - but only when the
        build replaced it, i.e. its image id changed - so no old image
        is left as a dangling artifact in podman.

        :raises RuntimeError: if the podman build fails or the
            superseded image cannot be removed.
        """

        tag = self.image_name()
        merged_content = self.merged_container_file_content()

        try:
            with podman.PodmanClient() as client:
                previous_id = self._current_image_id(client, tag)
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
                    self._remove_superseded_image(
                        client, tag, previous_id, image.id)
        except podman.errors.BuildError as e:
            if keep_container_file:
                self._safe_container_file()
            raise RuntimeError(f"Failed to build image {tag}: {e}") from e
        except podman.errors.APIError as e:
            if keep_container_file:
                self._safe_container_file()
            raise RuntimeError(f"Podman API error while building {tag}: {e}") \
                from e

        if get_verbosity() >= Verbosity.TRACE:
            for entry in build_logs:
                if isinstance(entry, bytes):
                    entry = json.loads(
                        entry.decode(errors='replace').strip())
                for stream in entry.get("stream", "").splitlines():
                    dprint(Verbosity.TRACE, f"[build] {stream}")

        dprint(Verbosity.INFO, f"Built image {image.id} tagged {tag}")
        return image

    def run_container(self, interactive_shell: str = "bash",
                      command: list[str] | None = None) -> int:
        """Open an interactive terminal/shell inside the container.

        Builds the image (see :meth:`build_container`), creates the
        container from it (or reuses one created earlier), ensures
        it is running and then attaches the local terminal to a
        command inside the container. The container is stopped again
        after the session ends (unless it is already gone).

        :param interactive_shell: Shell to start inside the container.
            Ignored when ``command`` is given.
        :param command: Optional command to run inside the container
            instead of the interactive shell, for example
            ``["opencode", "--port", "4096"]``.
        :returns: The exit code of the session.
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

            container = client.containers.create(
                image,
                name=self.container_name(),
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
                   f"Created container {self.container_name()} "
                   f"from {self.image_name()}")

            container.reload()
            if container.status in ("created", "exited", "stopped"):
                container.start()
                dprint(Verbosity.INFO,
                       f"Started container {self.container_name()}")
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

            if command is None:
                exec_argv = [interactive_shell]
            else:
                exec_argv = list(command)
            exec_cmd = [
                "podman", "exec", "-it",
                "--detach-keys", "",
                "-e", f"TERM={os.environ.get('TERM', 'xterm')}",
                self.container_name(),
                *exec_argv,
            ]
            session_command = " ".join(exec_argv)
            dprint(Verbosity.INFO,
                   f"Opening terminal in {self.container_name()} "
                   f"({session_command}; detach with Ctrl-D or 'exit')")
            exit_code = subprocess.call(exec_cmd)
            dprint(Verbosity.INFO,
                   f"Session in {self.container_name()} exited "
                   f"(exit code: {exit_code})")

            # Shut the container down after the terminal session ends.
            try:
                container.stop(timeout=0)
                dprint(Verbosity.INFO,
                       f"Stopped container {self.container_name()}")
                container.remove()
                dprint(Verbosity.INFO,
                       f"Removed container {self.container_name()}")
            except podman.errors.NotFound:
                # Container was removed elsewhere; nothing to shut down.
                dprint(Verbosity.INFO,
                       f"Container {self.container_name()} is already gone")
            except podman.errors.APIError as e:
                dprint(Verbosity.INFO,
                       f"Warning: could not stop container "
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

    def clean_local_images(self):
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
                      if self._is_devisolator_local_image(image)]
            if not images:
                dprint(Verbosity.INFO,
                       f"No {IMAGE_PREFIX} images found; nothing to clean")
                return
            self._clean_images(client, images)

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
           ``dev-isolator:<slug_of_the_current_path>--<tag>``."""
        return f"{IMAGE_PREFIX}:{self.current_path_slug()}--{self.tag}"

    def image_name_prefix(self) -> str:
        """Return the image name prefix
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
