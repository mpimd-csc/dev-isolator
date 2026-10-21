"""Manage container definitions (build/create) for dev-environment-isolator.

Copyright (C) 2026 Captain Hook

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
import podman.errors
from pathlib import Path

IMAGE_PREFIX = "isolator"


def slugify_path(path: str | os.PathLike) -> str:
    """Return a slug (e.g. ``my-project``) suitable
       for a container name/tag."""
    text = str(path)
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()


class ContainerDefintion:

    def build_container(self, verbose=False):
        """Build an image from the Containerfile in the current path.

        The image is tagged ``isolator:<slug_of_the_current_path>``.
        """

        context_dir = Path(__file__).parent / "container" / "opencode"
        containerfile = context_dir / "Containerfile"
        if not containerfile.is_file():
            raise FileNotFoundError(f"No Containerfile found in {context_dir}")

        tag = self.image_name()
        try:
            with podman.PodmanClient() as client:
                image, build_logs = client.images.build(
                    path=str(context_dir),
                    dockerfile=str(containerfile),
                    tag=tag,
                    rm=True,
                )
        except podman.errors.BuildError as e:
            raise RuntimeError(f"Failed to build image {tag}: {e}") from e
        except podman.errors.APIError as e:
            raise RuntimeError(f"Podman API error while building {tag}: {e}")

        if verbose:
            for entry in build_logs:
                if isinstance(entry, bytes):
                    print(f"[build] {entry.decode(errors='replace').strip()}")
                else:
                    for stream in entry.get("stream", "").splitlines():
                        print(f"[build] {stream}")

        print(f"Built image {image.id} tagged {tag}")
        return image

    def run_container(self, interactive_shell: str = "bash"):
        """Open an interactive terminal/shell inside the container.

        Creates the container from the image (or reuses one created
        earlier), ensures it is running and then attaches the local
        terminal to a shell inside the container.

        The interactive session itself is attached via the local
        ``podman exec -it`` CLI, because the podman Python bindings
        cannot bridge stdin/TTY to the podman service (their
        ``attach``/``attach_socket``/``exec_run	socket=True`` are not
        implemented).
        """

        self.build_container()

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
                  "(exit code: {exit_code})")

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

    def image_name(self) -> str:
        """Return the image name ``isolator:<slug_of_the_current_path>``."""
        return f"{IMAGE_PREFIX}:{self.current_path_slug()}"

    def container_name(self) -> str:
        """Return a valid container name derived from the current path tag.

        Podman container names cannot contain ':', so the tag's
        ``isolator:slug`` form becomes ``isolator-slug``.
        """
        return self.image_name().replace(":", "-")

    @staticmethod
    def current_path_slug() -> str:
        """Return the slug of the current working directory path."""
        return slugify_path(Path.cwd().resolve())
