"""Durations and sizes read the way build output does."""

from __future__ import annotations

import pytest

from longpole import units


@pytest.mark.parametrize(
    ("milliseconds", "text"),
    [
        (0, "0 ms"),
        (999, "999 ms"),
        (1_000, "1.0 s"),
        (1_649, "1.6 s"),
        (59_949, "59.9 s"),
        (60_000, "1m 00s"),
        (125_000, "2m 05s"),
        (3_600_000, "1h 00m"),
        (3_720_000, "1h 02m"),
    ],
)
def test_duration_picks_the_unit_by_magnitude(milliseconds: int, text: str) -> None:
    """Milliseconds under a second, tenths of seconds under a minute, then m:ss and h:mm."""
    assert units.duration(milliseconds) == text


@pytest.mark.parametrize(
    ("byte_count", "text"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (1023, "1023 B"),
        (1024, "1.0 KiB"),
        (3_481, "3.4 KiB"),
        (12 * 1024 * 1024, "12.0 MiB"),
        (int(1.2 * 1024**3), "1.2 GiB"),
    ],
)
def test_size_uses_binary_units(byte_count: int, text: str) -> None:
    """Bytes are exact; larger sizes get one decimal and a binary prefix."""
    assert units.size(byte_count) == text
