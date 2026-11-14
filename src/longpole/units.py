"""Human-readable numbers for durations and sizes.

Every report shows the same quantities, so the formatting lives in one place
and the tests pin it once.
"""

from __future__ import annotations


def duration(milliseconds: int) -> str:
    """Format a duration the way a person reads build output."""
    if milliseconds < 1_000:
        return f"{milliseconds} ms"
    seconds = milliseconds / 1_000
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, seconds = divmod(round(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def size(byte_count: int) -> str:
    """Format a byte count with a binary unit."""
    value = float(byte_count)
    for unit in ("B", "KiB", "MiB"):
        if value < 1024:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"
