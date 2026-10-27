"""Manage container definitions (build/create) for dev-isolator.

Copyright (C) 2026 Martin Köhler

This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it
under certain conditions; see LICENSE for details.
SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path
from .podman_utils import get_image_id, remove_superseeded_image
from .utils import dprint, Verbosity, get_verbosity
from .constants import BUNDLE_DIR, TEMPLATES_DIR
from .waitspinner import WaitSpinner
from .addons_config import AddonsConfiguration

import os
import pwd
import re
import podman
import json
import jinja2
from functools import lru_cache
from tempfile import mkstemp


def render_template(template: jinja2.Template) -> str:
    """Render a Containerfile jinja2 template with the host
       context.

    :param template: The template to render.
    :returns: The rendered template text.
    """
    return template.render(
        uid=os.getuid(),
        gid=os.getgid(),
        username=host_username(),
        home_dir=Path.home()
    )


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
def _add_user_template() -> jinja2.Template:
    """Return the (cached) jinja2 template for the add user block."""
    return _jinja_environment().get_template(
        "add_user.containerfile.jinja2")


class BaseImage:
    def __init__(self, context_dir, config_file,
                 addon_search_path):

        self.context_dir = Path(context_dir)
        self.config_file = Path(config_file) if config_file else None
        self.addons_search_path = addon_search_path
        self.bundled_dir = Path(__file__).parent / BUNDLE_DIR

        self.base_image_dir = self.bundled_dir / "base"
        dprint(Verbosity.INFO, f"Base image dir: {self.base_image_dir}")
        self.base_images = {}
        for path in sorted(self.base_image_dir.glob("Containerfile.*")):
            value = self._parse_di_base(path)
            if value is not None:
                dprint(Verbosity.DEBUG, f"Found base image {value} in {path}")
                self.base_images[value] = path

        self.addons = AddonsConfiguration(self.context_dir, self.bundled_dir,
                                          self.config_file,
                                          self.addons_search_path)

    def _parse_di_base(self, path):
        """Return the DI-BASE value from the file's first comment block."""
        # Matches "DI-BASE = VALUE" or "DI_BASE = VALUE" in a comment line.
        _DI_BASE_RE = re.compile(
            r"#\s*DI[-_]BASE\s*=\s*(\S+)")
        value = None
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                if not line.startswith("#"):
                    break
                match = _DI_BASE_RE.search(line)
                if match:
                    value = match.group(1)
                    break
        return value

    def write_container_file(self):
        container_file = self.base_images.get(self.base_image, None)
        base_content = container_file.read_text(encoding="utf-8")

        file_content = self.merged_container_file_content(base_content)
        if get_verbosity() >= Verbosity.TRACE:
            for line in file_content.splitlines():
                print(f"[Containerfile] {line}")

        fd, temp_name = mkstemp(dir=self.context_dir,
                                prefix=".containerfile.")
        temp_container_file = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                fp.write(file_content)
                # Make sure the content reaches disk before podman
                # reads the file during the build.
                fp.flush()
                os.fsync(fp.fileno())
        except BaseException:
            temp_container_file.unlink(missing_ok=True)
            raise
        return fd, temp_container_file

    def build(self, image):
        """Build a base image.

        ``name`` identifies the base Containerfile, i.e. the suffix
        after ``Containerfile.`` (e.g. ``ubuntu-24.04``). The resulting
        image is tagged ``dev-isolator-<name>:<tag>``, where the tag is
        taken from the name (``ubuntu-24.04`` -> ``24.04``); a name
        without a dash is used as the tag itself.

        :returns: the built image.
        :raises FileNotFoundError: if no Containerfile exists for
            ``name``.
        :raises RuntimeError: if the tag cannot be derived or the
            podman build fails.
        """
        for prefix in ("localhost/dev-isolator-", "dev-isolator-"):
            if image.startswith(prefix):
                image = image.removeprefix(prefix)
                break

        if not self.base_images.get(image, None):
            raise FileNotFoundError("Could not find the base image for "
                                    f"{image}")
        self.base_image = image

        try:
            self.tag = image.split(":")[1]
        except Exception as e:
            raise RuntimeError(f"The base image {image} does not contain a "
                               "tag") from e
        self.name = image.split(":")[0]

        image_tag = f"dev-isolator-{self.name}:{self.tag}"

        try:
            fd, temp_container_file = self.write_container_file()
            dprint(Verbosity.INFO, "Created temporary Containerfile "
                                   f"{temp_container_file}")

            with podman.PodmanClient() as client:
                previous_id = get_image_id(client, image_tag)
                dprint(Verbosity.INFO, f"Building base image "
                       f"{image_tag} ...")
                with WaitSpinner(f"Building base image {image_tag}"):
                    image, build_logs = client.images.build(
                        path=str(self.base_image_dir),
                        dockerfile=str(temp_container_file),
                        tag=image_tag,
                        rm=True,
                        forcerm=True,
                        pull=True,
                        )
                remove_superseeded_image(client, previous_id, image.id,
                                         image_tag)

        except podman.errors.BuildError as e:
            raise RuntimeError(
                f"Failed to build base image {image_tag}: {e}") from e
        except podman.errors.APIError as e:
            raise RuntimeError(
                f"Podman API error while building base image "
                f"{image_tag}: {e}") from e
        finally:
            if temp_container_file is not None:
                temp_container_file.unlink(missing_ok=True)

        if get_verbosity() >= Verbosity.TRACE:
            for entry in build_logs:
                if isinstance(entry, bytes):
                    entry = json.loads(
                        entry.decode(errors='replace').strip())
                for stream in entry.get("stream", "").splitlines():
                    dprint(Verbosity.TRACE, f"[build] {stream}")

        dprint(Verbosity.INFO, f"Built base image {image.id} "
               f"tagged {image_tag}")
        return image

    def add_user_content(self) -> str:
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
        return render_template(_add_user_template())

    def merged_container_file_content(self, base_content) -> str:
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
        text = str(base_content)
        text += "\n\n" + self.add_user_content()
        for addon in self.addons.addon_files():
            dprint(Verbosity.INFO, f"Appending addon: {addon.name}")
            text += f"\n\n# --- appended from {addon.name} ---\n"
            template = _jinja_environment().from_string(
                addon.read_text(encoding="utf-8"))
            text += render_template(template).rstrip("\n") + "\n"
        return text
