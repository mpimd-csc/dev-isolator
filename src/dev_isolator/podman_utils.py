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
