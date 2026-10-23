""" Utility functions for dev-isolator

Copyright (C) 2026 Martin Köhler

This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it
under certain conditions; see LICENSE for details.
SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path
from enum import IntEnum


class Verbosity(IntEnum):
    """Enum-like verbosity levels used by :func:`set_verbosity`,
    :func:`get_verbosity` and :func:`dprint`.

    Being an :class:`enum.IntEnum`, members compare against plain
    integers (e.g. the ``--verbose`` repeat count) as usual:

    >>> Verbosity.DEBUG >= 1
    True
    """

    ERROR = 0
    INFO = 1
    DEBUG = 2
    TRACE = 3


# Global verbosity level, controlled via :func:`set_verbosity`.
VERBOSITY = Verbosity.ERROR


def set_verbosity(level: Verbosity | int) -> None:
    """Set the global verbosity level used by :func:`dprint`."""
    global VERBOSITY
    VERBOSITY = Verbosity(level)


def get_verbosity() -> int:
    """Return the current global verbosity level."""
    global VERBOSITY
    return VERBOSITY


def dprint(level: Verbosity | int, *args, **kwargs) -> None:
    """Print the message if the global verbosity is >= ``level``.

    ``level`` may be a :class:`Verbosity` member (e.g.
    ``Verbosity.DEBUG``) or a plain integer.

    ``args`` and ``kwargs`` are forwarded to :func:`print`, so e.g.
    ``file=sys.stderr`` works as usual.

    The message is prefixed with the level name in brackets, e.g.
    ``[ERROR]``, ``[INFO]``, ``[DEBUG]`` or ``[TRACE]``.
    """
    if VERBOSITY >= level:
        prefix = f"[{Verbosity(level).name}]"
        args = (f"{prefix} {args[0]}", *args[1:]) if args else (prefix,)
        print(*args, **kwargs)


def search_path(pattern):
    """Search for ``pattern`` upwards from the current directory.

    Walks up the directory tree starting at the current working
    directory until the home or the filesystem root directory
    (inclusive) and returns the path of the first ``pattern`` found
    in one of those directories.

    Returns None when the walk reaches the home directory (or the
    filesystem root, for a working directory outside the home tree)
    and the pattern was not found.
    """
    home = Path.home()
    directory = Path.cwd().resolve()

    while True:
        candidate = directory / pattern
        if candidate.exists():
            return candidate
        if directory == home or directory.parent == directory:
            return None
        directory = directory.parent
