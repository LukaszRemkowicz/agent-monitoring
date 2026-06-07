from __future__ import annotations


def format_byte_size(byte_count: int) -> str:
    """Return a compact human-readable byte size."""

    if byte_count >= 1024 * 1024:
        return f"{byte_count / 1024 / 1024:.1f} MB"
    return f"{byte_count / 1024:.1f} KB"
