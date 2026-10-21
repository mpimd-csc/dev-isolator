""" Utility functions for dev-isolator

Copyright (C) 2026 Captain Hook

This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it
under certain conditions; see LICENSE for details.
SPDX-License-Identifier: GPL-3.0-or-later
"""

from pathlib import Path

# Global verbosity level, controlled via :func:`set_verbosity`.
VERBOSITY = 0


def set_verbosity(level: int) -> None:
    """Set the global verbosity level used by :func:`dprint`."""
    global VERBOSITY
    VERBOSITY = level


def dprint(level: int, *args, **kwargs) -> None:
    """Print the message if the global verbosity is >= ``level``.

    ``args`` and ``kwargs`` are forwarded to :func:`print`, so e.g.
    ``file=sys.stderr`` works as usual.
    """
    if VERBOSITY >= level:
        print(*args, **kwargs)


def config_path(pattern):
    """Search for :param:`pattern` upwards from the current directory.

    Walks up the directory tree starting at the current working directory
    until the home directory (inclusive) and returns the path of the first
    ``pattern`` found in one of those directories.

    Returns None when the home directory is reached and the pattern
    was not found.
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
