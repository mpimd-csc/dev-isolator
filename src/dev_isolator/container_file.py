"""Manage container definitions (build/create) for dev-isolator.

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
from tempfile import mkstemp
from tree_sitter import Language, Parser
import tree_sitter_containerfile
import podman.errors
import podman
import json


class ContainerFile:
    def __init__(self, context_dir, container_file, config_file,
                 addon_search_path, image_name):

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
        base_image = result.get("image", "xxx")
        base_image_tag = result.get("tag", None)
        if not (base_image.startswith("localhost/dev-isolator-") or
                base_image.startswith("dev-isolatpr-")):
            raise RuntimeError(f"The setup in {self.container_file} does not "
                               "use a dev-isolator image as base. ")
        base_image = f"{base_image}{base_image_tag}"

        self.base_image = BaseImage(self.context_dir, self.config_file,
                                    self.addons_search_path)
        self.base_image.build(base_image)

    def _write_container_file(self):
        # Setup final Containerfile
        file_content = self.container_file.read_text(encoding="utf-8")
        file_content = str(file_content)
        file_content += "\n\n# --- workdir ---\n"
        file_content += f"USER {host_username()}\n"
        file_content += "WORKDIR /workspace\n"

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

    def _safe_container_file(self, temp_file):
        fd, temp_name = mkstemp(dir=os.getcwd(),
                                prefix="Containerfile-fail-")
        fd.close()
        shutil.copyfile(temp_file, temp_name)
        dprint(Verbosity.ERROR, "Kept generated Containerfile as "
                                f"{temp_name}")

    def build(self, nocache=False, keep_container_file=False):
        tag = self.image_name

        fd, temp_container_file = self._write_container_file()

        try:
            with podman.PodmanClient() as client:
                previous_id = get_image_id(client, tag)
                dprint(Verbosity.INFO, "Created temporary Containerfile "
                       f"{temp_container_file}")

                dprint(Verbosity.INFO, "Building image ...")
                with WaitSpinner(f"Building image {tag}"):
                    image, build_logs = client.images.build(
                        path=str(self.context_dir),
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
        source = Path(filename).read_bytes()
        language = Language(tree_sitter_containerfile.language())
        parser = Parser(language)
        tree = parser.parse(source)

        results = []

        def visit(node):
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
