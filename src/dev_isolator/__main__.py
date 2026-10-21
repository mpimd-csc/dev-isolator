"""Allow running as `python -m dev_isolator`.

Copyright (C) 2026 Martin Köhler

This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it
under certain conditions; see LICENSE for details.
SPDX-License-Identifier: GPL-3.0-or-later
"""

from dev_isolator import main
import sys

if __name__ == "__main__":
    sys.exit(main())
