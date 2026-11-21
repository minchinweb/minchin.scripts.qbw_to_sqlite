# Copyright (c) 2026 William Minchin
"""Plain helpers shared by the qbw_to_sqlite test modules.

Fixtures live in conftest.py; this module holds the fake cursor, option
builders and paths that several test files import.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

from minchin.scripts.qbw_to_sqlite import _models, _qbb

# tests/ -> qbw_to_sqlite/ -> scripts/ -> minchin/ -> repository root
ROOT = Path(__file__).resolve().parents[4]
SAMPLE = ROOT / "qb-sandbox" / "Sample Company.QBW"


def _sa_company_bytes(size: int = 256) -> bytes:
    """Fake company bytes with the SQL Anywhere magic at offset 20."""
    return b"\x00" * 20 + _qbb.SA_MAGIC + bytes(range(256))[: size - 24]


class FakeCursor:
    """Stands in for a SQL Anywhere cursor: rows from ``rows``, COUNT from ``count``."""

    def __init__(
        self,
        rows=(),
        count=None,
        names=("id", "name"),
        fail_on_execute: str | None = None,
        fail_after_batches: int | None = None,
    ):
        self.rows = [tuple(r) for r in rows]
        self.count = len(self.rows) if count is None else count
        self.names = list(names)
        self.fail_on_execute = fail_on_execute
        self.fail_after_batches = fail_after_batches
        self.batches = 0
        self.position = 0
        self.description: list[tuple] = []

    def execute(self, sql: str):
        if self.fail_on_execute and self.fail_on_execute in sql:
            raise RuntimeError("source says no")
        if sql.lstrip().upper().startswith("SELECT COUNT"):
            self._mode = "count"
        else:
            self._mode = "rows"
            self.position = 0
            self.description = [(n,) for n in self.names]
        return self

    def fetchone(self):
        return (self.count,)

    def fetchmany(self, n: int):
        if (
            self.fail_after_batches is not None
            and self.batches >= self.fail_after_batches
        ):
            raise RuntimeError("connection dropped mid-table")
        self.batches += 1
        chunk = self.rows[self.position : self.position + n]
        self.position += len(chunk)
        return chunk


def _opts(**kw) -> _models.Options:
    base = {"batch_size": 2}
    base.update(kw)
    return _models.Options(**base)


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
