"""Tests for the demo_function

SPDX-License-Identifier: GPL-3.0-or-later
"""

import pytest
from dev_environment_isolator import demo_function

class TestDemoFunction:
    """Test the demo_function"""

    def test_demo_function(self):
        """Test that demo_function returns the correct string"""
        assert demo_function("Captain") == "Captain was here."
