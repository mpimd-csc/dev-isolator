"""Podman helpers shared by the image build and cleanup code.

Provides the image id lookup and the removal of images superseded by a
new build with the same tag, keeping podman free of dangling images.

Copyright (C) 2026 Martin Köhler

This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it
under certain conditions; see LICENSE for details.
SPDX-License-Identifier: GPL-3.0-or-later
"""

import podman
from .utils import dprint, Verbosity


from tree_sitter import Language, Parser
import tree_sitter_containerfile
import podman.errors
import podman


def replace_from_images(content: str, new_image: str) -> str:
    """Replace the image of every ``FROM`` instruction in a Containerfile.

    Parses ``content`` with the tree-sitter Containerfile grammar (the
    same grammar used by :meth:`ContainerImage.get_from_image`), finds
    every ``FROM`` instruction's image spec and replaces it with
    ``new_image``; tags, digests and ``AS <alias>`` parts of the
    instruction are preserved (only the image itself is replaced, so
    ``FROM ubuntu:26.04`` becomes e.g.
    ``FROM dev-isolator-ubuntu:26.04-something``).

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

def get_image_id(client, tag):
    """Return the id of the image tagged ``tag``, or ``None``.

    :param client: Connected :class:`podman.PodmanClient`.
    :param tag: Name (tag) of the image to look up.
    """
    try:
        return client.images.get(tag).id
    except (podman.errors.NotFound, podman.errors.ImageNotFound):
        dprint(Verbosity.INFO, f"No existing image {tag}")
        return None


def remove_superseeded_image(client, previous_id, new_id, tag):
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
