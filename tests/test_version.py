"""Smoke tests for the installed package metadata."""

from __future__ import annotations

import importlib.metadata
import re

import longpole


def test_version_matches_installed_metadata() -> None:
    """The version in code is the version the package declares to installers."""
    assert longpole.__version__ == importlib.metadata.version("longpole")


def test_version_is_pep440() -> None:
    """Keep the version string in the shape packaging tools can parse."""
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:\.dev\d+)?", longpole.__version__)
