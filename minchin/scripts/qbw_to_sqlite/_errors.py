# Copyright (c) 2026 William Minchin
"""Exceptions raised by the converter.

Every error derives from ``QBWError``, which derives from ``Exception``, so
``except QBWError`` catches all of them. Each subclass names one kind of
failure, so a caller can react to it specifically.
"""

from __future__ import annotations

import sqlite3


class QBWError(Exception):
    """A conversion could not run, so no output was written.

    Base class for every error this package raises on purpose. Catch this to
    handle any of them. Problems inside a finished conversion, such as a table
    with rejected rows, are reported on the ConversionReport instead.
    """


class InputError(QBWError, OSError):
    """The company file is missing, not a QuickBooks file, or too large.

    Also an ``OSError``, so ``except OSError`` catches it.

    Fix: pass a different or valid company file.
    """


class OutputPathError(QBWError, ValueError):
    """The output path is refused: it is the company file, or it has a
    QuickBooks extension (.QBW or .QBB). Also a ``ValueError``, since it
    rejects an argument.

    Fix: choose a different output path.
    """


class EngineNotFoundError(QBWError):
    """A required dependency is not installed or not found: the SQL Anywhere 17
    engine or its Python driver, or the openqbw package.

    Fix: install the dependency, or pass ``sa_dir`` to point at SQL Anywhere 17.
    """


class EngineError(QBWError, RuntimeError):
    """The SQL Anywhere engine failed to start, stopped early, or rejected the
    login. The message includes the tail of the engine log where available.

    Also a ``RuntimeError``.

    Fix: check the engine log, or retry with a different UID and PWD.
    """


class DatabaseError(QBWError, sqlite3.Error):
    """Writing the SQLite output failed. The output may be partly written.

    Also a ``sqlite3.Error``.

    Fix: check disk space and permissions on the output path; report a bug if
    the error persists.
    """
