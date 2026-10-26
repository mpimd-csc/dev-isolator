""" A small rotating "waiting" animation for dev-isolator

Copyright (C) 2026 Martin Köhler

This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it
under certain conditions; see LICENSE for details.
SPDX-License-Identifier: GPL-3.0-or-later
"""

import itertools
import sys
import threading
import time


class WaitSpinner:
    """Show a small rotating "waiting" animation on a terminal.

    The spinner runs in a background thread and redraws a single
    terminal line with rotating icons (braille animation frames) and
    the elapsed seconds, so the user can see that something is going
    on while a long operation (e.g. a podman image build) runs.

    Intended to be used as a context manager::

        with WaitSpinner("Building image"):
            slow_operation()

    When the operation finishes, the spinning line is replaced by a
    summary line starting with the ``:)`` smiley (operation and total
    duration), so the user sees the operation completed and the cursor
    ends up on a clean line for subsequent output.

    The animation is only drawn when stdout is a terminal; in scripts,
    log files or pipes (``isatty() == False``) nothing is animated -
    the context manager then just measures the elapsed time silently.
    """

    #: Rotating icons (braille spinner frames).
    FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    #: Seconds between icon changes.
    INTERVAL = 0.1

    def __init__(self, message: str = "Waiting"):
        """Prepare, but do not yet start, the animation.

        ``message`` is the text shown (and later summarized) next to
        the rotating icons, e.g. ``"Building image"``.
        """
        self._message = message.strip().rstrip(".")
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._animate = False
        self._line_len = 0

    def _run(self) -> None:
        """Animate the spinner until :meth:`__exit__` stops it."""
        stream = sys.stdout
        frames = itertools.cycle(self.FRAMES)
        start = time.monotonic()
        while not self._stop_event.wait(self.INTERVAL):
            elapsed = time.monotonic() - start
            line = f"{next(frames)} {self._message} ... {elapsed:4.0f}s"
            stream.write("\r" + line)
            stream.flush()
            self._line_len = max(self._line_len, len(line))

    def __enter__(self) -> "WaitSpinner":
        self._started = time.monotonic()
        self._animate = sys.stdout.isatty()
        if self._animate:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join()
        if exc_type is None:
            summary = f":) {self._message} done in " \
                      f"{time.monotonic() - self._started:.1f}s"
            if self._animate:
                # Overwrite the spinning line with the ready emoticon.
                stream = sys.stdout
                stream.write("\r" + summary
                             + " " * max(0, self._line_len - len(summary))
                             + "\n")
            else:
                print(summary)
        elif self._animate:
            stream = sys.stdout
            stream.write("\r" + " " * self._line_len + "\r")
            stream.flush()
