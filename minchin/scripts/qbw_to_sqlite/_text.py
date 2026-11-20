# Copyright (c) 2026 William Minchin
"""Text and value helpers: QuickBooks text decoding and SQLite identifiers."""

from __future__ import annotations

import datetime
import decimal


def fix_text(s: str) -> str:
    """Reinterpret a latin-1-decoded string using its real encoding.

    The driver decodes character data as latin-1 (lossless, never raises).
    QuickBooks text is a mix of UTF-8 and cp1252, so this recovers the original
    bytes, decodes them as UTF-8 when valid, and falls back to cp1252.

    Args:
        s: A string decoded as latin-1.

    Returns:
        The text with its original characters restored.
    """
    b = s.encode("latin-1")
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp1252", errors="replace")


def sqlite_value(v):
    """Coerce a SQL Anywhere value into something sqlite3 accepts.

    Args:
        v: A value as returned by the driver.

    Returns:
        A value sqlite3 can bind. Decimals become exact text, and dates become
        ISO 8601 text.
    """
    if isinstance(v, str):
        return fix_text(v)
    if v is None or isinstance(v, (int, float, bytes)):
        return v
    if isinstance(v, decimal.Decimal):
        return str(v)  # keep full precision as text
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat(sep=" ")
    if isinstance(v, bool):
        return int(v)
    return str(v)


def quote_ident(name: str) -> str:
    """Quote a SQLite identifier, doubling any embedded double quotes."""
    return '"' + name.replace('"', '""') + '"'
