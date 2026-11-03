"""Build the dev-isolator base image (BaseImage).

Before a project's image can be built, its base image must exist: a
base image is built from one of the Containerfiles bundled with
dev-isolator (``data/base/Containerfile.<name>``, identified by the
``DI_BASE`` comment in its first comment block). The resulting image is
tagged ``dev-isolator-<name>:<tag>``, e.g.
``dev-isolator-ubuntu:26.04``.

The used Containerfile of a base image merges three sources: the base
Containerfile itself, the addons enabled in the active ``config.yaml``
(each ``addon.<name>.jinja2`` file rendered as a jinja2 template with
the host context, see :class:`Configuration`) and the host user
block (a user with the invoking host user's UID, GID and username,
template ``add_user.containerfile.jinja2``).

Project image builds (see
:class:`dev_isolator.container_image.ContainerImage`) then base their
``FROM`` instruction on this base image.

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
from .config import Configuration

import os
import pwd
import re
import podman
import json
import jinja2
import hashlib
from functools import lru_cache
from tempfile import mkstemp, TemporaryDirectory
from .config import Configuration

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
    """Build the base image a project image is based upon.

    A base image is built from one of the Containerfiles bundled with
    dev-isolator (``data/base/Containerfile.<name>``), with the addons
    enabled in the active ``config.yaml`` and the host user appended
    (see :meth:`merged_container_file_content`). The resulting image
    is tagged ``dev-isolator-<name>:<tag>``.

    :attr base_image: Name (without registry prefix and tag) of the
        selected base Containerfile, e.g. ``ubuntu``.
    :attr tag: The tag of the selected base Containerfile, e.g.
        ``26.04``.
    :attr name: Like ``base_image``; the part of the selected base
        image before its ``:`` separator.
    :attr base_images: Mapping from the ``DI_BASE`` value of every
        bundled base Containerfile to its path, e.g.
        ``ubuntu:26.04`` to ``data/base/Containerfile.ubuntu-26.04``.
    """

    def __init__(self, context_dir=None, config: Configuration = None):
        """Set up the base image for a build context.

        :param context_dir: Path to the project's build context
            directory; the temporary merged base Containerfile is
            written here during the build.
        :param config: Path to the project's ``config.yaml``,
            determining the enabled addons. May be ``None``, in which
            case (as with a missing file) every ``addon.*`` file is
            used.
        """
        self.config = config
        self.bundled_dir = BUNDLE_DIR

        self.base_image_dir = self.bundled_dir / "base"
        dprint(Verbosity.INFO, f"Base image dir: {self.base_image_dir}")

        self.base_images = {}
        for path in sorted(self.base_image_dir.glob("Containerfile.*")):
            value = self._parse_di_base(path)
            if value is not None:
                dprint(Verbosity.DEBUG, f"Found base image {value} in {path}")
                self.base_images[value] = path

    def list_base_images(self):
        """Return the sorted list of bundled base image names.

        :returns: Every ``DI_BASE`` value found in the bundled base
            Containerfiles (see :attr:`base_images`), as strings,
            sorted alphabetically, e.g.
            ``["ubuntu:24.04", "ubuntu:26.04"]``. Used by the
            ``list-base`` sub command (see
            :func:`dev_isolator.main.list_base`).
        """
        bil = []
        for value in self.base_images.keys():
            bil.append(str(value))
        bil.sort()
        return bil

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

    def write_container_file(self, directory):
        """Write the used Containerfile for the base image build.

        The used Containerfile is the merged content of the selected
        base Containerfile (see :meth:`merged_container_file_content`).
        At ``Verbosity.TRACE`` the content is printed to stdout.

        The file is created inside ``directory`` and fsynced,
        so its content reaches disk before podman reads the file
        during the build.

        :returns: The tuple ``(fd, temp_container_file, content_hash)``
            of the open file descriptor of the written file, its path
            and the SHA-256 hash of the Containerfile content (used
            as part of the built image's tag).
        """
        container_file = self.base_images.get(self.base_image, None)
        base_content = container_file.read_text(encoding="utf-8")

        file_content = self.merged_container_file_content(base_content)
        m = hashlib.sha256()
        m.update(file_content.encode("utf-8"))
        content_hash = m.hexdigest()

        if get_verbosity() >= Verbosity.TRACE:
            for line in file_content.splitlines():
                print(f"[Containerfile] {line}")

        fd, temp_name = mkstemp(dir=directory,
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
        return fd, temp_container_file, content_hash

    def build(self, image):
        """Build a base image.

        ``image`` identifies the base image the project's Containerfile
        is based on, for example
        ``localhost/dev-isolator-ubuntu:26.04`` (as written in a
        project Containerfile) or plain ``ubuntu:26.04``. An optional
        ``localhost/dev-isolator-`` or ``dev-isolator-`` prefix is
        stripped, and the remainder must match the ``DI_BASE`` value of
        one of the bundled base Containerfiles (see
        :meth:`_parse_di_base` and :attr:`base_images`).

        The resulting image is tagged
        ``dev-isolator-<name>:<tag>``, where ``name`` and ``tag`` are
        the parts of the identified base image around its ``:``
        separator (``ubuntu:26.04`` -> ``dev-isolator-ubuntu:26.04``).

        The built Containerfile is the merged base Containerfile with
        the enabled addons and the host user appended (see
        :meth:`merged_container_file_content`). It requires a
        Debian-style base image (``useradd`` and ``getent`` must
        exist).

        :param image: The base image to build the project image from,
            e.g. ``localhost/dev-isolator-ubuntu:26.04``.
        :returns: The built image.
        :raises FileNotFoundError: if no bundled base Containerfile
            matches ``image``.
        :raises RuntimeError: if the tag cannot be derived from
            ``image`` or the podman build fails.
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

        temp_dir_object = TemporaryDirectory(ignore_cleanup_errors=True)
        temp_dir = temp_dir_object.name

        fd, temp_container_file, content_hash = (self.write_container_file(
                                                 temp_dir))
        image_tag = f"dev-isolator-{self.name}:{self.tag}-{content_hash}"
        dprint(Verbosity.INFO, "Created temporary Containerfile "
                               f"{temp_container_file}")

        try:
            with podman.PodmanClient() as client:
                previous_id = get_image_id(client, image_tag)
                dprint(Verbosity.INFO, f"Building base image "
                       f"{image_tag} ...")
                with WaitSpinner(f"Building base image {image_tag}"):
                    image, build_logs = client.images.build(
                        path=str(temp_dir),
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
            if temp_dir_object is not None:
                temp_dir_object.cleanup()

        if get_verbosity() >= Verbosity.TRACE:
            for entry in build_logs:
                if isinstance(entry, bytes):
                    entry = json.loads(
                        entry.decode(errors='replace').strip())
                for stream in entry.get("stream", "").splitlines():
                    dprint(Verbosity.TRACE, f"[build] {stream}")

        dprint(Verbosity.INFO, f"Built base image {image.id} "
               f"tagged {image_tag} with hash {content_hash}")
        return image, image_tag, content_hash

    def add_user_content(self) -> str:
        """Return the Containerfile instructions adding the host user.

        Renders the ``templates/add_user.containerfile.jinja2``
        template shipped with dev-isolator (see :const:`TEMPLATES_DIR`)
        with the host context (see :func:`render_template`), as do the
        ``addon.*`` files in :meth:`merged_container_file_content`.

        The template removes any pre-existing user/group occupying
        those properties from the image and then creates a user whose
        UID, GID and username match those of the invoking host user,
        making it the image's default user.

        It requires a Debian-style base image (``useradd`` and
        ``getent`` must exist).
        """
        return render_template(_add_user_template())

    def merged_container_file_content(self, base_content) -> str:
        """Return the base image's used Containerfile content.

        Starts with the content of the base ``Containerfile``
        ``base_content`` and appends the host user block (same UID,
        GID and username as the invoking user, see :meth:`add_user_content`),
        followed by every addon file from
        :meth:`Configuration.addon_files` rendered as a jinja2
        template (see :func:`render_template` for the template
        variables), separated by a comment naming the addon file.

        The host user makes shells and exec sessions run as that
        user; addons that need to install packages switch to root and
        must re-assert the host user again themselves.

        :param base_content: The content of the base Containerfile to
            append the host user and the addons to.
        :returns: The merged Containerfile content.
        :raises RuntimeError: if an addon config file is invalid (via
            :meth:`Configuration.addon_files`).
        """
        text = str(base_content)
        text += "\n\n" + self.add_user_content()
        for addon in self.config.list_addon_files():
            dprint(Verbosity.INFO, f"Appending addon: {addon.name}")
            text += f"\n\n# --- appended from {addon.name} ---\n"
            template = _jinja_environment().from_string(
                addon.read_text(encoding="utf-8"))
            text += render_template(template).rstrip("\n") + "\n"
        return text
