# Copyright (c) 2026 William Minchin
"""Copying one source table into SQLite, batch by batch, with a count check."""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from typing import Any

from minchinweb.scripts.qbw_to_sqlite._models import (
    ColumnInfo,
    Options,
    TableResult,
    dst_name_for,
)
from minchinweb.scripts.qbw_to_sqlite._text import quote_ident, sqlite_value

log = logging.getLogger(__name__)


def _write_batch(
    dst: sqlite3.Connection,
    sql: str,
    rows: list,
    first_row_number: int,
    strict: bool,
    rejects: list[tuple[int, str]],
) -> int:
    """Insert a batch. On failure, retry row by row and log bad rows (unless strict).

    Returns:
        The number of rows written.

    Raises:
        sqlite3.Error: A batch failed and ``strict`` is set.
    """
    payload = [[sqlite_value(v) for v in row] for row in rows]
    dst.execute("SAVEPOINT batch")
    try:
        dst.executemany(sql, payload)
        dst.execute("RELEASE batch")
        return len(payload)
    except sqlite3.Error:
        dst.execute("ROLLBACK TO batch")
        dst.execute("RELEASE batch")
        if strict:
            raise
        written = 0
        for offset, row in enumerate(payload):
            try:
                dst.execute(sql, row)
                written += 1
            except sqlite3.Error as row_exc:
                rejects.append((first_row_number + offset, str(row_exc)))
        return written


def copy_table(
    src_cur: Any,
    dst: sqlite3.Connection,
    owner: str,
    table: str,
    columns: list[ColumnInfo],
    opt: Options,
) -> TableResult:
    """Copy one table from SQL Anywhere into SQLite and record what happened.

    Failures are recorded in the returned TableResult rather than raised, so one
    bad table does not stop the others.

    Args:
        src_cur: An open cursor on the source connection.
        dst: The SQLite output connection.
        owner: The source owner, such as ``QBAdminGroup``.
        table: The source table name.
        columns: The table's catalog columns, used for declared types and keys.
        opt: Selection and batching settings.

    Returns:
        The table's result, including its status.
    """
    res = TableResult(owner=owner, table=table, dst_name=dst_name_for(owner, table))
    qualified = f"{quote_ident(owner)}.{quote_ident(table)}"
    where = opt.where_for(owner, table)
    filtered = opt.limit is not None or where is not None
    res.primary_key = [c.name for c in columns if c.primary_key]

    # 1. Query the source. Failures here leave nothing in the output.
    try:
        if not filtered:
            src_cur.execute(f"SELECT COUNT(*) FROM {qualified}")
            res.source_rows = src_cur.fetchone()[0]
        select = f"SELECT * FROM {qualified}" + (f" WHERE ({where})" if where else "")
        src_cur.execute(select)
        names = [d[0] for d in src_cur.description]
    except Exception as exc:  # noqa: BLE001 - the driver raises its own error classes
        res.status = "FAILED"
        res.error = f"cannot query source: {exc}"
        return res

    by_name = {c.name: c for c in columns}
    col_defs = ", ".join(
        quote_ident(n)
        + (
            f" {by_name[n].declared_type}"
            if n in by_name and by_name[n].declared_type
            else ""
        )
        for n in names
    )
    placeholders = ", ".join("?" for _ in names)
    dst_q = quote_ident(res.dst_name)
    pk_cols = ", ".join(quote_ident(c) for c in res.primary_key)
    pk_index = quote_ident(res.dst_name + "__pk")
    insert_verb = "INSERT OR REPLACE" if opt.mode == "merge" else "INSERT"
    insert = f"{insert_verb} INTO {dst_q} VALUES ({placeholders})"

    dst.execute("BEGIN")
    try:
        # 2. Create or keep the destination table.
        existing = [r[1] for r in dst.execute(f"PRAGMA table_info({dst_q})")]
        if opt.mode == "merge" and existing == names:
            res.note = "merged into existing table"
        else:
            if opt.mode == "merge" and existing:
                res.note = "recreated: columns changed since last run"
            dst.execute(f"DROP TABLE IF EXISTS {dst_q}")
            dst.execute(f"CREATE TABLE {dst_q} ({col_defs})")
        if res.primary_key and opt.mode == "merge":
            # Upsert needs a unique key, created before the load for INSERT OR REPLACE.
            dst.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {pk_index} ON {dst_q} ({pk_cols})"
            )

        # 3. Stream rows in batches.
        while True:
            want = opt.batch_size
            if opt.limit is not None:
                want = min(want, opt.limit - res.rows_read)
                if want <= 0:
                    break
            rows = src_cur.fetchmany(want)
            if not rows:
                break
            written = _write_batch(
                dst, insert, rows, res.rows_read + 1, opt.strict, res.rejects
            )
            res.rows_read += len(rows)
            res.rows_written += written
        dst.execute("COMMIT")
    except Exception as exc:  # noqa: BLE001 - keep the rows read so far
        # Report the table as partial; its rows stay in the output.
        res.error = str(exc)
        with contextlib.suppress(sqlite3.Error):
            dst.execute("COMMIT")

    # 4. A primary-key index for replace mode, created after the load.
    if res.primary_key and opt.mode != "merge" and res.error is None:
        try:
            dst.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {pk_index} ON {dst_q} ({pk_cols})"
            )
        except sqlite3.Error as exc:
            res.note = f"primary key not enforced: {exc}"

    # 5. Status, and a read-back count check when nothing was filtered.
    if res.error is not None:
        res.status = "PARTIAL" if res.rows_written else "FAILED"
    elif not filtered and res.source_rows is not None:
        dst_count = dst.execute(f"SELECT COUNT(*) FROM {dst_q}").fetchone()[0]
        expected = res.source_rows - len(res.rejects)
        read_ok = res.rows_read == res.source_rows
        if (
            not read_ok
            or (opt.mode == "replace" and dst_count != expected)
            or dst_count < expected
        ):
            res.status = "MISMATCH"
            res.note = (
                f"read {res.rows_read} of {res.source_rows}; sqlite has {dst_count}"
            )
        elif res.rejects:
            res.status = "REJECTED_ROWS"
        else:
            res.status = "OK"
    elif res.rejects:
        res.status = "REJECTED_ROWS"
    else:
        res.status = "FILTERED" if filtered else "OK"
    log.debug("%s.%s: %s", owner, table, res.status)
    return res
