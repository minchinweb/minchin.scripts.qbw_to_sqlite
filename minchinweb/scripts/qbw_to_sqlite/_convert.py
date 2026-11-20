# Copyright (c) 2026 William Minchin
"""The conversion pipeline behind ``convert()``.

Steps: hash the source, copy it (or extract it from a .QBB), copy the tables
through the SQL Anywhere engine (or run the openqbw backend), add views and
provenance, then rename the output into place.
"""

from __future__ import annotations

import contextlib
import datetime
import logging
import os
from os import PathLike
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import TYPE_CHECKING, Any
import uuid

from minchinweb.scripts.qbw_to_sqlite._catalog import list_user_tables, load_column_info
from minchinweb.scripts.qbw_to_sqlite._copy import copy_table
from minchinweb.scripts.qbw_to_sqlite._engine import (
    SQLAnywhereEngine,
    connect,
    find_sqlany_dir,
)
from minchinweb.scripts.qbw_to_sqlite._errors import QBWError
from minchinweb.scripts.qbw_to_sqlite._models import (
    PROBLEM_STATUSES,
    ColumnInfo,
    ConversionReport,
    Options,
    TableResult,
)
from minchinweb.scripts.qbw_to_sqlite._openqbw import run_openqbw_backend
from minchinweb.scripts.qbw_to_sqlite._provenance import write_provenance
from minchinweb.scripts.qbw_to_sqlite._qbb import (
    extract_company_file,
    is_qbb,
    sha256_file,
)
from minchinweb.scripts.qbw_to_sqlite._version import __version__
from minchinweb.scripts.qbw_to_sqlite._views import create_views

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

log = logging.getLogger(__name__)

BACKENDS = ("sa", "openqbw")
MODES = ("replace", "merge")
DEFAULT_PORT = 29179
DEFAULT_SERVER = "QBW2SQLITE"


def convert(
    source: str | PathLike[str],
    output: str | PathLike[str],
    *,
    uid: str | None = None,
    pwd: str | None = None,
    backend: str = "sa",
    mode: str = "replace",
    tables: Iterable[str] = (),
    exclude: Iterable[str] = (),
    limit: int | None = None,
    where: Mapping[str, str] | None = None,
    batch_size: int = 2000,
    strict: bool = False,
    sa_dir: str | PathLike[str] | None = None,
    server: str = DEFAULT_SERVER,
    port: int = DEFAULT_PORT,
    work_dir: str | PathLike[str] | None = None,
    keep_copy: bool = False,
) -> ConversionReport:
    """Convert a QuickBooks company file to a SQLite database.

    The source is never written to. The sa backend starts a private, read-only
    SQL Anywhere 17 engine on a copy of it. The output is written beside
    ``output`` under a temporary name and renamed into place only when the run
    finishes, so an existing output survives a fatal error.

    Args:
        source: Path to a ``.QBW`` company file or a ``.QBB`` backup.
        output: The SQLite file to create. Refused if it is a QuickBooks file.
        uid: The SQL Anywhere user name as hex. Required for the sa backend.
        pwd: The SQL Anywhere password value. Required for the sa backend.
        backend: ``"sa"`` (the SQL Anywhere engine, the default) or
            ``"openqbw"`` (experimental, no login, catalog and transactions only).
        mode: ``"replace"`` writes a fresh output. ``"merge"`` starts from the
            existing output and upserts each table by its primary key.
        tables: Glob patterns for the tables to copy. Each matches ``table`` or
            ``owner.table``. An empty sequence means all tables.
        exclude: Glob patterns for tables to skip.
        limit: Copy at most this many rows per table. Skips the count check.
        where: A SQL predicate for each table, keyed by table name. The predicate
            is passed to SQL Anywhere unchanged, so use it only with trusted input.
        batch_size: Rows per insert batch.
        strict: Abort a table on its first bad row instead of skipping that row.
        sa_dir: The SQL Anywhere 17 install folder. Found automatically if None.
        server: Name of the temporary engine.
        port: Private TCP port for the temporary engine.
        work_dir: Where to put the temporary working copy. The system temp
            folder if None.
        keep_copy: Keep the working copy and the engine log on exit.

    Returns:
        A ConversionReport. A report with problems is still a finished
        conversion: check ``report.ok``.

    Raises:
        QBWError: The conversion could not run. Examples: the source is missing,
            the output path is refused, the engine will not start, or the login
            fails. No output is written in these cases.
        ValueError: An argument is not valid, for example an unknown ``mode``,
            or the sa backend without ``uid`` and ``pwd``.
    """
    _check_arguments(backend, mode, uid, pwd, limit, batch_size)

    qbw = Path(source).resolve()
    if not qbw.exists():
        raise QBWError(f"company file not found: {qbw}")
    out = Path(output).resolve()
    if out == qbw or out.suffix.lower() in (".qbw", ".qbb"):
        raise QBWError(f"refusing to write output over a QuickBooks file: {out}")

    opt = Options(
        mode=mode,
        includes=list(tables),
        excludes=list(exclude),
        limit=limit,
        where={k.lower(): v for k, v in (where or {}).items()},
        batch_size=batch_size,
        strict=strict,
    )
    if opt.mode == "merge" and (opt.limit is not None or opt.where):
        log.warning("--limit/--where with merge mode only upserts the rows selected")

    kind = "qbb" if is_qbb(qbw) else "qbw"
    meta: dict[str, Any] = {
        "tool": "qbw_to_sqlite",
        "tool_version": __version__,
        "extracted_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "source_name": qbw.name,
        "source_kind": kind,
        "source_bytes": qbw.stat().st_size,
        "source_sha256": sha256_file(qbw),
        "backend": backend,
        "mode": opt.mode,
        "filters": {
            "tables": opt.includes,
            "exclude": opt.excludes,
            "limit": opt.limit,
            "where": opt.where,
        },
    }

    work = Path(work_dir).resolve() if work_dir else Path(tempfile.gettempdir())
    work.mkdir(parents=True, exist_ok=True)
    fd, tmp_company = tempfile.mkstemp(prefix="qbw_", suffix=".QBW", dir=str(work))
    os.close(fd)
    company = Path(tmp_company)
    tmp_out = out.with_name(f".{out.name}.{uuid.uuid4().hex[:8]}.partial")
    dst: sqlite3.Connection | None = None

    try:
        log.info("reading %s -> working copy %s", kind.upper(), company)
        if kind == "qbb":
            extract_company_file(qbw, company)
        else:
            shutil.copy2(qbw, company)
        meta["company_file_sha256"] = sha256_file(company)

        # Merge starts from the existing output; replace starts empty.
        if opt.mode == "merge" and out.exists():
            shutil.copy2(out, tmp_out)

        dst = sqlite3.connect(str(tmp_out), isolation_level=None)
        dst.execute("PRAGMA journal_mode=OFF")
        dst.execute("PRAGMA synchronous=OFF")
        results: list[TableResult]
        columns: dict[tuple[str, str], list[ColumnInfo]]
        if backend == "openqbw":
            results, columns = [], {}
            problems = run_openqbw_backend(company, dst, strict)
        else:
            assert uid is not None and pwd is not None  # checked above
            results, columns = run_sa_backend(
                company,
                dst,
                opt,
                meta,
                uid=uid,
                pwd=pwd,
                sa_dir=sa_dir,
                server=server,
                port=port,
            )
            problems = []

        for r in results:
            if r.status in PROBLEM_STATUSES:
                problems.append(f"{r.owner}.{r.table}: {r.status}")
        meta["problems"] = problems
        meta["tables_total"] = len(results)
        meta["tables_ok"] = sum(1 for r in results if r.status == "OK")
        meta["rows_written"] = sum(r.rows_written for r in results)

        write_provenance(dst, meta, results, columns)
        dst.execute("VACUUM")
        dst.close()
        dst = None
        Path(tmp_out).replace(out)  # atomic on the same volume

        return ConversionReport(
            output=out,
            source_name=qbw.name,
            source_kind=kind,
            backend=backend,
            mode=opt.mode,
            tables=results,
            views=list(meta.get("views", [])),
            views_skipped=list(meta.get("views_skipped", [])),
            problems=problems,
            source_sha256=meta["source_sha256"],
            meta=meta,
        )
    except sqlite3.Error as exc:
        raise QBWError(f"SQLite: {exc}") from exc
    finally:
        if dst is not None:
            dst.close()
        if tmp_out.exists():
            with contextlib.suppress(OSError):
                tmp_out.unlink()
        if not keep_copy:
            for f in (company, company.with_suffix(".engine.log")):
                with contextlib.suppress(OSError):
                    f.unlink()


def _check_arguments(
    backend: str,
    mode: str,
    uid: str | None,
    pwd: str | None,
    limit: int | None,
    batch_size: int,
) -> None:
    if backend not in BACKENDS:
        raise ValueError(f"backend must be one of {BACKENDS}, not {backend!r}")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
    if backend == "sa" and not (uid and pwd):
        raise ValueError("the sa backend needs uid and pwd (see the README)")
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")


def _log_table(i: int, total: int, res: TableResult) -> None:
    label = f"{res.owner}.{res.table}"
    detail = f"{res.rows_written} rows" if res.rows_written else "0 rows"
    if res.status != "OK":
        detail += f", {res.status}"
    if res.error:
        detail += f": {res.error.splitlines()[0]}"
    log.info("[%4d/%d] %s: %s", i, total, label, detail)


def run_sa_backend(
    company: Path,
    dst: sqlite3.Connection,
    opt: Options,
    meta: dict[str, Any],
    *,
    uid: str,
    pwd: str,
    sa_dir: str | PathLike[str] | None,
    server: str,
    port: int,
) -> tuple[list[TableResult], dict[tuple[str, str], list[ColumnInfo]]]:
    """Copy every selected table through the SQL Anywhere engine.

    Args:
        company: The working copy to serve.
        dst: The SQLite output connection.
        opt: Selection and batching settings.
        meta: Run-level values. Filled with the install folder and the views.
        uid: The SQL Anywhere user name as hex.
        pwd: The SQL Anywhere password value.
        sa_dir: The install folder, or None to find it.
        server: Name of the temporary engine.
        port: Private TCP port for the temporary engine.

    Returns:
        The per-table results, and the catalog columns of the selected tables.

    Raises:
        QBWError: The engine or the login failed.
    """
    sa = find_sqlany_dir(sa_dir)
    meta["sa_dir"] = str(sa)
    log.info("SQL Anywhere 17: %s", sa)

    dbname = server + "_DB"
    engine = SQLAnywhereEngine(
        sa,
        company,
        server,
        port,
        dbname=dbname,
        log_path=company.with_suffix(".engine.log"),
    )
    conn = None
    try:
        engine.start()
        log.info("connecting")
        conn = connect(sa, uid=uid, pwd=pwd, server=server, dbname=dbname, port=port)
        cur = conn.cursor()

        selected_tables = [
            (o, t) for o, t in list_user_tables(cur) if opt.selected(o, t)
        ]
        columns = load_column_info(cur)
        log.info("%d user tables selected", len(selected_tables))

        results: list[TableResult] = []
        for i, (owner, table) in enumerate(selected_tables, 1):
            res = copy_table(
                cur, dst, owner, table, columns.get((owner, table), []), opt
            )
            results.append(res)
            _log_table(i, len(selected_tables), res)

        views, views_skipped = create_views(dst)
        if views:
            log.info("views created: %s", ", ".join(views))
        for skip in views_skipped:
            log.info("views skipped %s", skip)
        meta["views"] = views
        meta["views_skipped"] = views_skipped

        cur.close()
        selected = set(selected_tables)
        return results, {k: v for k, v in columns.items() if k in selected}
    finally:
        if conn is not None:
            with contextlib.suppress(Exception):  # closing is best effort
                conn.close()
        engine.stop()
