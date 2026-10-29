"""Write and build the project image's Containerfile (ContainerFile).

The project's ``Containerfile`` inside the build context must be based
on a base image built from the Containerfiles bundled with dev-isolator
(see :class:`dev_isolator.base_image.BaseImage`): the single ``FROM``
instruction of the project's Containerfile must reference a base image
such as ``localhost/dev-isolator-ubuntu:26.04``.

When the project image is built, the used Containerfile is written as a
temporary file into the build context, containing the project
Containerfile plus the trailing workdir block (``USER`` of the host
user and ``WORKDIR /workspace``), and passed to ``podman build`` with
the project's :const:`image_name` tag. A previous image replaced by
the build is removed afterwards (see
:func:`dev_isolator.podman_utils.remove_superseeded_image`).

Copyright (C) 2026 Martin Köhler

This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it
under certain conditions; see LICENSE for details.
SPDX-License-Identifier: GPL-3.0-or-later
"""

import os
import shutil
from pathlib import Path
from .base_image import BaseImage, host_username
from .podman_utils import get_image_id, remove_superseeded_image
from .utils import dprint, Verbosity, get_verbosity
from .waitspinner import WaitSpinner
from tempfile import mkstemp, TemporaryDirectory
from tree_sitter import Language, Parser
import tree_sitter_containerfile
import podman.errors
import podman
import json
from .constants import BUNDLE_DIR


def replace_from_images(content: str, new_image: str) -> str:
    """Replace the image of every ``FROM`` instruction in a Containerfile.

    Parses ``content`` with the tree-sitter Containerfile grammar (the
    same grammar used by :meth:`ContainerFile.get_from_image`), finds
    every ``FROM`` instruction's image spec and replaces it with
    ``new_image``; tags, digests and ``AS <alias>`` parts of the
    instruction are preserved (only the image itself is replaced, so
    ``FROM ubuntu:26.04`` becomes e.g. ``FROM
    localhost/dev-isolator-ubuntu:26.04-something``).

    :param content: The Containerfile content to modify.
    :param new_image: The image spec to put in place of each
        ``FROM`` instruction's current image.
    :returns: The modified Containerfile content.
    """
    parser = Parser(
        Language(tree_sitter_containerfile.language())
    )

    source = content.encode("utf-8")
    tree = parser.parse(source)

    replacements = []

    def visit(node):
        """Collect the byte range of each ``FROM`` image spec."""
        if node.type == "from_instruction":
            for child in node.named_children:
                if child.type == "image_spec":
                    replacements.append(
                        (child.start_byte, child.end_byte)
                    )
                    break

        for child in node.named_children:
            visit(child)

    visit(tree.root_node)

    for start, end in reversed(replacements):
        source = (
            source[:start]
            + new_image.encode("utf-8")
            + source[end:]
        )

    return source.decode("utf-8")


class ContainerFile:
    """Write and build the project image's Containerfile.

    A ContainerFile is created for a build context together with the
    paths of the project's own ``Containerfile``, the project's
    ``config.yaml`` and the addon search paths. Before anything else,
    the ``FROM`` instruction of the project Containerfile identifies
    the dev-isolator base image to use, and that base image is built
    (see :class:`dev_isolator.base_image.BaseImage`).

    :attr base_image: The
        :class:`dev_isolator.base_image.BaseImage` this Containerfile
        is based on, built on initialization.
    """

    def __init__(self, context_dir, container_file, config_file,
                 addon_search_path, image_name):
        """Set up the container file and build its base image.

        :param context_dir: Path to the build context directory; the
            temporary merged Containerfile is written here during the
            build.
        :param container_file: Path to the project's ``Containerfile``
            inside the build context.
        :param config_file: Path to the project's ``config.yaml``,
            determining the enabled addons of the base image build.
        :param addon_search_path: List of directories searched for
            ``addon.*`` files that are appended to the base image's
            Containerfile.
        :param image_name: Tag used for the built project image (see
            :meth:`dev_isolator.container_manager.ContainerDefintion.
            image_name`).
        :raises FileNotFoundError: if ``container_file`` does not
            exist or the referenced base image has no matching base
            Containerfile.
        :raises RuntimeError: if the Containerfile does not contain
            exactly one ``FROM`` instruction or does not use a
            dev-isolator image as base, or if a podman build fails.
        """
        self.context_dir = Path(context_dir)
        self.container_file = Path(container_file)
        self.config_file = Path(config_file) if config_file else None
        self.addons_search_path = addon_search_path
        self.image_name = image_name

        if not self.container_file.is_file():
            raise FileNotFoundError(f"Containterfile {self.container_file}"
                                    " not found.")

        result = self.get_from_image(self.container_file)
        if len(result) != 1:
            raise RuntimeError("The supplied Containerfile contains more than"
                               " one FROM statement.")
        result = result[0]
        base_image_name = result.get("image", "xxx")
        base_image_tag = result.get("tag", None)
        if not (base_image_name.startswith("localhost/dev-isolator-") or
                base_image_name.startswith("dev-isolator-")):
            raise RuntimeError(f"The setup in {self.container_file} does not "
                               "use a dev-isolator image as base. Got "
                               f"{base_image_name}")
        self.from_image_name = f"{base_image_name}{base_image_tag}"

        self.base_image = BaseImage(self.context_dir, self.config_file,
                                    self.addons_search_path)

    def _write_container_file(self, directory):
        """Write the used Containerfile for the image build.

        The used Containerfile starts with the content of the
        project's ``Containerfile`` and appends the trailing workdir
        block: a comment marker, the host user as default user (same
        username as on the host, see
        :func:`dev_isolator.base_image.host_username`) and
        ``WORKDIR /workspace``. At ``Verbosity.TRACE`` the content is
        printed to stdout.

        The file is created directly inside the build context, so
        podman-py does not copy it itself (its own copy,
        ``.containerfile.<random>``, would be left behind as an
        artifact in the build context). The content is fsynced, so it
        reaches disk before podman reads the file during the build.

        :returns: The tuple ``(fd, temp_container_file)`` of the
            open file descriptor of the written file and its path.
        """
        # Setup final Containerfile
        file_content = self.container_file.read_text(encoding="utf-8")
        file_content = replace_from_images(file_content,
                                           self.base_image_name)
        file_content = str(file_content)
        file_content += "\n\n# --- workdir ---\n"
        file_content += f"USER {host_username()}\n"
        file_content += "WORKDIR /workspace\n"

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
        return fd, temp_container_file

    def _safe_container_file(self, temp_file):
        """Keep a generated Containerfile for debugging a failure.

        Copies the temporary file ``temp_file`` to the current
        working directory as ``Containerfile-fail-<random>`` and
        reports the kept name in an error message. Used when the
        image build fails and the build was requested with
        ``keep_container_file=True`` (see
        :meth:`dev_isolator.container_manager.ContainerDefintion.
        build_container`).
        """
        fd, temp_name = mkstemp(dir=os.getcwd(),
                                prefix="Containerfile-fail-")
        fd.close()
        shutil.copyfile(temp_file, temp_name)
        dprint(Verbosity.ERROR, "Kept generated Containerfile as "
                                f"{temp_name}")

    def build(self, nocache=False, keep_container_file=False):
        """Build the image defined by this container file.

        The used Containerfile is written by
        :meth:`_write_container_file` directly into the build
        context, passed to ``podman build`` with the
        ``self.image_name`` tag, and removed again after the build.

        If an image with the same tag existed before, it is removed
        after the build - but only when the build replaced it, i.e.
        its image id changed - so no old image is left as a dangling
        artifact in podman.

        :param nocache: Run ``podman build`` with ``--nocache``.
        :param keep_container_file: Keep the generated Containerfile
            in the current directory when the build fails (see
            :meth:`_safe_container_file`).
        :returns: The built podman image.
        :raises RuntimeError: if the podman build fails.
        """
        tag = self.image_name
        _, self.base_image_name, _ = self.base_image.build(self.from_image_name)

        if self.context_dir == BUNDLE_DIR:
            dprint(Verbosity.INFO, "Context dir is the data directory, using "
                                   "a temporary one.")
            temp_dir_object = TemporaryDirectory(ignore_cleanup_errors=True)
            build_image_dir = temp_dir_object.name
        else:
            build_image_dir = self.context_dir
            temp_dir_object = None

        fd, temp_container_file = self._write_container_file(build_image_dir)

        try:
            with podman.PodmanClient() as client:
                previous_id = get_image_id(client, tag)
                dprint(Verbosity.INFO, "Created temporary Containerfile "
                       f"{temp_container_file}")

                dprint(Verbosity.INFO, "Building image ...")
                with WaitSpinner(f"Building image {tag}"):
                    image, build_logs = client.images.build(
                        path=str(build_image_dir),
                        dockerfile=str(temp_container_file),
                        tag=tag,
                        rm=True,
                        forcerm=True,
                        pull=True,
                        nocache=nocache
                        )
                remove_superseeded_image(client, previous_id, image.id, tag)
        except podman.errors.BuildError as e:
            if keep_container_file:
                self._safe_container_file(temp_container_file.name)
            raise RuntimeError(f"Failed to build image {tag}: {e}") from e
        except podman.errors.APIError as e:
            if keep_container_file:
                self._safe_container_file(temp_container_file.name)
            raise RuntimeError(f"Podman API error while building {tag}: {e}") \
                from e
        finally:
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

        dprint(Verbosity.INFO, f"Built image {image.id} tagged {tag}")
        return image

    def get_from_image(self, filename):
        """Return the ``FROM`` instructions of a Containerfile.

        Parses ``filename`` with the tree-sitter Containerfile
        grammar and returns one entry per ``FROM`` instruction.

        :param filename: Path to the Containerfile to parse.
        :returns: A list of dicts with the keys ``image`` (the image
            name without tag), ``tag`` (the ``:tag`` part including
            the colon, or ``None``), ``digest`` (the ``@digest``
            part, or ``None``) and ``alias`` (the name after ``AS``,
            or ``None``).
        """
        source = Path(filename).read_bytes()
        language = Language(tree_sitter_containerfile.language())
        parser = Parser(language)
        tree = parser.parse(source)

        results = []

        def visit(node):
            """Append the data of one ``FROM`` instruction (if any)
               and recurse into its children."""
            if node.type == "from_instruction":
                image_spec = next(
                    (
                        child
                        for child in node.named_children
                        if child.type == "image_spec"
                    ),
                    None,
                )

                if image_spec is not None:

                    def field_text(field):
                        """Return the raw text of an ``image_spec``
                           child field, or ``None``."""
                        child = image_spec.child_by_field_name(field)
                        if child is None:
                            return None
                        return source[child.start_byte:child.end_byte].decode()

                    alias_node = node.child_by_field_name("as")

                    results.append({
                        "image": field_text("name"),
                        "tag": field_text("tag"),
                        "digest": field_text("digest"),
                        "alias": (
                            source[alias_node.start_byte:alias_node.end_byte]
                            .decode()
                            if alias_node is not None
                            else None
                        ),
                    })

            for child in node.named_children:
                visit(child)

        visit(tree.root_node)
        return results


