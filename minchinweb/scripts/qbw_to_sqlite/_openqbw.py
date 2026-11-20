# Copyright (c) 2026 William Minchin
"""The openqbw backend (optional, experimental): catalog, indexes and transactions."""

from __future__ import annotations

import decimal
import json
import logging
from pathlib import Path
import sqlite3
import warnings

from minchinweb.scripts.qbw_to_sqlite._errors import QBWError
from minchinweb.scripts.qbw_to_sqlite._text import quote_ident

log = logging.getLogger(__name__)

# Column lists follow openqbw's documented to_dict() fields. They are fixed here,
# so a table keeps its columns even when the scan finds no rows.
OPENQBW_COLUMNS: dict[str, list[str]] = {
    "_openqbw_tables": [
        "table_id",
        "name",
        "col_count",
        "data_root_page",
        "last_page",
        "page_number",
        "stamp_unix_seconds",
        "flags",
    ],
    "_openqbw_indexes": [
        "name",
        "table_id",
        "root_page",
        "is_foreign_key",
        "page_number",
        "flags",
    ],
    "_openqbw_transactions": [
        "qb_id",
        "source_table",
        "txn_type",
        "txn_date_raw",
        "counter",
        "date_source",
        "txn_date_days_since_unix",
        "page_number",
        "page_offset",
        "flags",
    ],
    "_openqbw_line_items": [
        "qb_id_parent",
        "item_qb_id",
        "value",
        "value_undecoded",
        "value_raw",
        "txn_date_raw",
        "counter",
        "date_source",
        "txn_date_days_since_unix",
        "source_table",
        "page_number",
        "page_offset",
        "flags",
    ],
}


def _sql_value(v):
    if v is None or isinstance(v, (int, float, str, bytes)):
        return v
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, (list, dict)):
        return json.dumps(v, default=str)
    return str(v)


def run_openqbw_backend(
    company: Path, dst: sqlite3.Connection, strict: bool
) -> list[str]:
    """Write openqbw's catalog, indexes, transactions and line items.

    Its own warnings go to the ``_openqbw_warnings`` table, not to the log. The
    caller writes the provenance tables.

    Args:
        company: The working copy of the company file.
        dst: The SQLite output connection.
        strict: Passed to openqbw as the inverse of its lenient mode.

    Returns:
        Problem notes, one per scan with records openqbw could not decode.

    Raises:
        QBWError: The openqbw package is not installed.
    """
    try:
        import openqbw  # type: ignore[import-not-found]
    except ImportError as exc:
        raise QBWError(
            "the openqbw backend needs the openqbw package: "
            "pip install openqbw (Python 3.10 or later)"
        ) from exc

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        reader = openqbw.open(str(company), lenient=not strict)
        reports = {
            "_openqbw_tables": reader.tables(),
            "_openqbw_indexes": reader.indexes(),
            "_openqbw_transactions": reader.transactions(),
            "_openqbw_line_items": reader.line_items(),
        }
    log.info("openqbw recorded %d warnings in _openqbw_warnings", len(caught))

    problems: list[str] = []
    dst.execute("BEGIN")
    for name, report in reports.items():
        cols = OPENQBW_COLUMNS[name]
        dst.execute(f"DROP TABLE IF EXISTS {name}")
        dst.execute(f"CREATE TABLE {name} ({', '.join(quote_ident(c) for c in cols)})")
        rows = [item.to_dict() for item in report.items]
        sql = f"INSERT INTO {name} VALUES ({', '.join('?' for _ in cols)})"
        dst.executemany(sql, [[_sql_value(r.get(c)) for c in cols] for r in rows])
        log.info("openqbw: %s: %d rows", name, len(rows))
        if report.rejected:
            problems.append(f"{name}: {len(report.rejected)} records rejected")

    dst.execute("DROP TABLE IF EXISTS _openqbw_rejected")
    dst.execute(
        "CREATE TABLE _openqbw_rejected (scan TEXT, kind TEXT, page_number INTEGER, "
        "page_offset INTEGER, message TEXT)"
    )
    dst.execute("DROP TABLE IF EXISTS _openqbw_warnings")
    dst.execute("CREATE TABLE _openqbw_warnings (scan TEXT, kind TEXT, message TEXT)")
    for name, report in reports.items():
        for rej in report.rejected:
            dst.execute(
                "INSERT INTO _openqbw_rejected VALUES (?,?,?,?,?)",
                (name, rej.kind, rej.page_number, rej.page_offset, rej.message),
            )
        for warn in report.warnings:
            dst.execute(
                "INSERT INTO _openqbw_warnings VALUES (?,?,?)",
                (name, warn.kind, warn.message),
            )
    dst.execute("COMMIT")
    return problems
