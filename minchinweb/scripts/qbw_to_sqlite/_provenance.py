# Copyright (c) 2026 William Minchin
"""Provenance tables: what was read, how, and what each table's status was."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from minchinweb.scripts.qbw_to_sqlite._models import ColumnInfo, TableResult

MAX_REJECT_ROWS_LOGGED = 10000


def write_provenance(
    dst: sqlite3.Connection,
    meta: dict[str, Any],
    tables: list[TableResult],
    columns: dict[tuple[str, str], list[ColumnInfo]],
) -> None:
    """Write the provenance tables: ``_qbw_meta`` and the three others.

    Any existing provenance tables are replaced.

    Args:
        dst: The SQLite output connection.
        meta: Run-level values. Non-string values are stored as JSON.
        tables: One result per table copied.
        columns: The catalog columns, keyed by ``(owner, table)``.
    """
    for name in ("_qbw_meta", "_qbw_tables", "_qbw_columns", "_qbw_rejected"):
        dst.execute(f"DROP TABLE IF EXISTS {name}")
    dst.execute("CREATE TABLE _qbw_meta (key TEXT PRIMARY KEY, value TEXT)")
    dst.executemany(
        "INSERT INTO _qbw_meta VALUES (?, ?)",
        [(k, v if isinstance(v, str) else json.dumps(v)) for k, v in meta.items()],
    )

    dst.execute("""CREATE TABLE _qbw_tables (
        owner TEXT, table_name TEXT, sqlite_table TEXT, status TEXT,
        source_rows INTEGER, rows_read INTEGER, rows_written INTEGER,
        rejected_rows INTEGER, primary_key TEXT, error TEXT, note TEXT)""")
    dst.executemany(
        "INSERT INTO _qbw_tables VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                r.owner,
                r.table,
                r.dst_name,
                r.status,
                r.source_rows,
                r.rows_read,
                r.rows_written,
                len(r.rejects),
                ",".join(r.primary_key) or None,
                r.error,
                r.note,
            )
            for r in tables
        ],
    )

    dst.execute("""CREATE TABLE _qbw_columns (
        sqlite_table TEXT, position INTEGER, column_name TEXT, source_type TEXT,
        width INTEGER, scale INTEGER, nullable INTEGER, primary_key INTEGER,
        declared_type TEXT)""")
    col_rows = []
    for r in tables:
        for pos, c in enumerate(columns.get((r.owner, r.table), []), 1):
            col_rows.append(
                (
                    r.dst_name,
                    pos,
                    c.name,
                    c.source_type,
                    c.width,
                    c.scale,
                    int(c.nullable),
                    int(c.primary_key),
                    c.declared_type,
                )
            )
    dst.executemany("INSERT INTO _qbw_columns VALUES (?,?,?,?,?,?,?,?,?)", col_rows)

    dst.execute(
        "CREATE TABLE _qbw_rejected (sqlite_table TEXT, row_number INTEGER, error TEXT)"
    )
    logged = 0
    for r in tables:
        for row_no, message in r.rejects:
            if logged >= MAX_REJECT_ROWS_LOGGED:
                break
            dst.execute(
                "INSERT INTO _qbw_rejected VALUES (?,?,?)",
                (r.dst_name, row_no, message),
            )
            logged += 1
