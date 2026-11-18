"""Tests for dev_isolator.utils

SPDX-License-Identifier: GPL-3.0-or-later
"""

import sys
from pathlib import Path

import pytest

from dev_isolator import utils
from dev_isolator.utils import (Verbosity, dprint, get_verbosity,
                                search_path, set_verbosity)


@pytest.fixture(autouse=True)
def restore_verbosity():
    """Keep the global verbosity level isolated between tests."""
    original = utils.VERBOSITY
    yield
    utils.VERBOSITY = original


class TestVerbosity:
    """Tests for the Verbosity IntEnum"""

    def test_member_values(self):
        """Members have their documented integer values."""
        assert Verbosity.ERROR == 0
        assert Verbosity.INFO == 1
        assert Verbosity.DEBUG == 2
        assert Verbosity.TRACE == 3

    def test_members_compare_with_plain_integers(self):
        """As an IntEnum, members compare against plain integers."""
        assert Verbosity.DEBUG >= 1
        assert Verbosity.INFO < Verbosity.TRACE
        assert Verbosity.INFO == Verbosity(1)

    def test_members_are_ordered(self):
        """ERROR < INFO < DEBUG < TRACE."""
        assert Verbosity.ERROR < Verbosity.INFO \
            < Verbosity.DEBUG < Verbosity.TRACE


class TestSetGetVerbosity:
    """Tests for set_verbosity and get_verbosity"""

    def test_set_and_get_integer(self):
        """set_verbosity stores the level that get_verbosity returns."""
        set_verbosity(2)
        assert get_verbosity() == Verbosity.DEBUG

    def test_set_with_member(self):
        """A Verbosity member is accepted as level."""
        set_verbosity(Verbosity.TRACE)
        assert get_verbosity() == Verbosity.TRACE

    def test_default_verbosity_is_error(self):
        """Without any prior set_verbosity call the level is ERROR."""
        assert get_verbosity() == Verbosity.ERROR

    def test_invalid_level_raises(self):
        """Unknown integer levels are rejected."""
        with pytest.raises(ValueError):
            set_verbosity(42)


class TestDprint:
    """Tests for dprint"""

    def test_prints_when_verbosity_is_high_enough(self, capsys):
        """The message is printed when VERBOSITY >= level."""
        set_verbosity(Verbosity.DEBUG)
        dprint(Verbosity.DEBUG, "hello")
        assert "hello" in capsys.readouterr().out

    def test_prints_for_lower_level(self, capsys):
        """Messages below the current level are printed as well."""
        set_verbosity(Verbosity.TRACE)
        dprint(Verbosity.ERROR, "error message")
        assert "error message" in capsys.readouterr().out

    def test_silent_when_verbosity_is_lower(self, capsys):
        """Nothing is printed when VERBOSITY < level."""
        set_verbosity(Verbosity.ERROR)
        dprint(Verbosity.DEBUG, "hidden")
        assert capsys.readouterr().out == ""

    def test_multiple_args_and_kwargs_are_forwarded(self, capsys):
        """args and kwargs are passed through to print."""
        set_verbosity(Verbosity.DEBUG)
        dprint(Verbosity.DEBUG, "a", "b", sep="-", end="!\n")
        assert capsys.readouterr().out == "[DEBUG] a-b!\n"

    def test_file_kwarg_is_forwarded(self, capsys):
        """Output goes to the requested stream."""
        set_verbosity(Verbosity.TRACE)
        dprint(Verbosity.TRACE, "to stderr", file=sys.stderr)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "to stderr" in captured.err

    def test_level_may_be_plain_integer(self, capsys):
        """dprint accepts a plain integer as level."""
        set_verbosity(1)
        dprint(1, "plain int")
        assert "plain int" in capsys.readouterr().out


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the home directory into tmp_path."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


class TestConfigPath:
    """Tests for search_path"""

    def test_found_in_current_directory(self, tmp_path: Path,
                                        fake_home: Path,
                                        monkeypatch: pytest.MonkeyPatch):
        """The pattern is found in the current directory itself."""
        marker = tmp_path / "config.yaml"
        marker.touch()
        monkeypatch.chdir(tmp_path)

        assert search_path("config.yaml") == marker

    def test_found_above_current_directory(self, tmp_path: Path,
                                           fake_home: Path,
                                           monkeypatch: pytest.MonkeyPatch):
        """The pattern is found in a parent directory."""
        project = fake_home / "project"
        deep = project / "sub" / "deep"
        deep.mkdir(parents=True)

        marker = project / ".devisolator"
        marker.touch()
        monkeypatch.chdir(deep)

        assert search_path(".devisolator") == marker

    def test_found_in_home(self, tmp_path: Path, fake_home: Path,
                           monkeypatch: pytest.MonkeyPatch):
        """The home directory is included in the search."""
        deep = fake_home / "tools" / "deep"
        deep.mkdir(parents=True)

        marker = fake_home / "settings.toml"
        marker.touch()
        monkeypatch.chdir(deep)

        assert search_path("settings.toml") == marker

    def test_nearest_match_wins(self, tmp_path: Path, fake_home: Path,
                                monkeypatch: pytest.MonkeyPatch):
        """The first match on the way up is returned, not a deeper
        nested or higher one."""
        project = fake_home / "project"
        deep = project / "sub" / "deep"
        deep.mkdir(parents=True)
        (project / "marker").touch()
        (deep / "marker").touch()
        (fake_home / "marker").touch()
        monkeypatch.chdir(deep)

        assert search_path("marker") == deep / "marker"

    def test_returns_none_when_not_found_in_home_tree(
            self, tmp_path: Path, fake_home: Path,
            monkeypatch: pytest.MonkeyPatch):
        """None is returned when the walk finds nothing."""
        deep = fake_home / "elsewhere" / "deep"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)

        assert search_path("missing.txt") is None

    def test_works_outside_home(self, tmp_path: Path, fake_home: Path,
                                monkeypatch: pytest.MonkeyPatch):
        """For a working directory outside the home tree the search
        continues up to the filesystem root."""
        outside = tmp_path / "outside"
        deep = outside / "deep"
        deep.mkdir(parents=True)

        marker = outside / "marker.conf"
        marker.touch()
        monkeypatch.chdir(deep)

        assert search_path("marker.conf") == marker

    def test_returns_none_outside_home_without_match(
            self, tmp_path: Path, fake_home: Path,
            monkeypatch: pytest.MonkeyPatch):
        """None is returned for a working directory outside the home
        tree when the walk reaches the filesystem root."""
        outside = tmp_path / "outside2"
        deep = outside / "deep"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)

        assert search_path("missing.conf") is None
