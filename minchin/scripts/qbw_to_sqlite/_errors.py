# Copyright (c) 2026 William Minchin
"""Exceptions raised by the converter."""

from __future__ import annotations


class QBWError(Exception):
    """A conversion could not run, so no output was written.

    Raised for a missing or unreadable company file, an output path that is
    refused, an engine that will not start, a login that fails, or a QBB backup
    with no usable company file. Problems inside a finished conversion, such as
    a table with rejected rows, are reported on the ConversionReport instead.
    """
