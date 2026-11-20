# Copyright (c) 2026 William Minchin
"""Tests for the qbw_to_sqlite library and its command line.

The unit tests use fake source cursors and in-memory SQLite, so they need no
QuickBooks file and no SQL Anywhere install. The engine integration tests run
the real engine on a copy of the sample company file. They are skipped unless
SQL Anywhere 17 is installed, the sample is present, and the environment
variables QBW_TEST_UID and QBW_TEST_PWD are set.
"""

from __future__ import annotations

import decimal
import hashlib
import logging
import os
from pathlib import Path
import sqlite3
import sys
import zlib

import pytest

from minchinweb.scripts.qbw_to_sqlite import (
    ConversionReport,
    QBWError,
    TableResult,
    _catalog,
    _convert,
    _copy,
    _engine,
    _models,
    _qbb,
    _text,
    _views,
    cli,
    convert,
)

# tests/ -> qbw_to_sqlite/ -> scripts/ -> minchinweb/ -> repository root
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


@pytest.fixture
def dst():
    """An in-memory SQLite output, closed after the test."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    yield conn
    conn.close()


def _opts(**kw) -> _models.Options:
    base = {"batch_size": 2}
    base.update(kw)
    return _models.Options(**base)


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


# --------------------------------------------------------------------------- #
# Column types and text decoding
# --------------------------------------------------------------------------- #


def test_integer_domains_map_to_integer() -> None:
    for d in ("integer", "smallint", "bigint", "bit", "unsigned int"):
        assert _models.sqlite_affinity(d) == "INTEGER", d


def test_decimal_and_numeric_map_to_text_so_money_stays_exact() -> None:
    # NUMERIC affinity would turn "12.50" into a float.
    assert _models.sqlite_affinity("decimal") == "TEXT"
    assert _models.sqlite_affinity("numeric") == "TEXT"


def test_float_binary_and_unknown_domains() -> None:
    assert _models.sqlite_affinity("double") == "REAL"
    assert _models.sqlite_affinity("long binary") == "BLOB"
    assert _models.sqlite_affinity("st_geometry") == ""
    assert _models.sqlite_affinity(None) == ""


def test_utf8_bytes_are_recovered() -> None:
    raw = "Kč €".encode()
    assert _text.fix_text(raw.decode("latin-1")) == "Kč €"


def test_cp1252_bytes_are_recovered() -> None:
    raw = "Québec".encode("cp1252")
    assert _text.fix_text(raw.decode("latin-1")) == "Québec"


def test_decimal_value_keeps_exact_text() -> None:
    assert _text.sqlite_value(decimal.Decimal("12.50")) == "12.50"


def test_quote_ident_doubles_quotes() -> None:
    assert _text.quote_ident('a"b') == '"a""b"'


# --------------------------------------------------------------------------- #
# Table selection
# --------------------------------------------------------------------------- #


def test_include_matches_table_or_owner_table() -> None:
    o = _opts(includes=["abmc_invoice_*"])
    assert o.selected("QBAdminGroup", "abmc_invoice_header")
    assert not o.selected("QBAdminGroup", "abmc_bill_header")


def test_owner_qualified_pattern() -> None:
    o = _opts(includes=["qbadmingroup.abmc_*"])
    assert o.selected("QBAdminGroup", "abmc_terms")
    assert not o.selected("OTHER", "abmc_terms")


def test_exclude_skips_matching_tables() -> None:
    o = _opts(excludes=["perf_*"])
    assert not o.selected("QBAdminGroup", "perf_x")
    assert o.selected("QBAdminGroup", "abmc_x")


def test_where_is_looked_up_by_table_name() -> None:
    o = _opts(where={"abmc_invoice_header": "transaction_id < 5"})
    assert o.where_for("QBAdminGroup", "abmc_invoice_header") == "transaction_id < 5"
    assert o.where_for("QBAdminGroup", "abmc_terms") is None


# --------------------------------------------------------------------------- #
# Copying one table
# --------------------------------------------------------------------------- #


def test_ok_when_counts_match(dst) -> None:
    src = FakeCursor(rows=[(1, "a"), (2, "b"), (3, "c")])
    res = _copy.copy_table(src, dst, "QBAdminGroup", "abmc_t", [], _opts())
    assert res.status == "OK"
    assert (res.source_rows, res.rows_read, res.rows_written) == (3, 3, 3)
    assert _count(dst, "QBAdminGroup_abmc_t") == 3


def test_partial_keeps_rows_read_before_failure(dst) -> None:
    src = FakeCursor(rows=[(i, "x") for i in range(6)], fail_after_batches=2)
    res = _copy.copy_table(src, dst, "QBAdminGroup", "abmc_t", [], _opts())
    assert res.status == "PARTIAL"
    assert res.error is not None
    assert _count(dst, "QBAdminGroup_abmc_t") == 4


def test_failed_when_source_query_fails(dst) -> None:
    src = FakeCursor(rows=[(1, "a")], fail_on_execute="COUNT")
    res = _copy.copy_table(src, dst, "QBAdminGroup", "abmc_t", [], _opts())
    assert res.status == "FAILED"
    assert (
        dst.execute(
            "SELECT name FROM sqlite_master WHERE name='QBAdminGroup_abmc_t'"
        ).fetchone()
        is None
    )


def test_mismatch_when_read_is_short_of_source_count(dst) -> None:
    src = FakeCursor(rows=[(1, "a"), (2, "b")], count=5)
    res = _copy.copy_table(src, dst, "QBAdminGroup", "abmc_t", [], _opts())
    assert res.status == "MISMATCH"
    assert "read 2 of 5" in res.note


def test_filtered_when_limit_applied(dst) -> None:
    src = FakeCursor(rows=[(i, "x") for i in range(10)])
    res = _copy.copy_table(src, dst, "QBAdminGroup", "abmc_t", [], _opts(limit=3))
    assert res.status == "FILTERED"
    assert res.rows_written == 3
    assert res.source_rows is None


def test_declared_types_and_primary_key_index(dst) -> None:
    cols = [
        _models.ColumnInfo("id", "integer", 4, 0, False, True),
        _models.ColumnInfo("amt", "decimal", 19, 4, True, False),
    ]
    src = FakeCursor(rows=[(1, "1.0000")], names=("id", "amt"))
    res = _copy.copy_table(src, dst, "QBAdminGroup", "abmc_t", cols, _opts())
    assert res.status == "OK"
    decl = {r[1]: r[2] for r in dst.execute("PRAGMA table_info(QBAdminGroup_abmc_t)")}
    assert decl == {"id": "INTEGER", "amt": "TEXT"}
    idx = dst.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name='QBAdminGroup_abmc_t__pk'"
    ).fetchone()
    assert idx is not None


def test_merge_upserts_by_key_and_keeps_other_rows(dst) -> None:
    cols = [
        _models.ColumnInfo("id", "integer", 4, 0, False, True),
        _models.ColumnInfo("name", "varchar", 20, 0, True, False),
    ]
    first = FakeCursor(rows=[(1, "old"), (2, "keep")])
    _copy.copy_table(first, dst, "QBAdminGroup", "abmc_t", cols, _opts(mode="merge"))
    second = FakeCursor(rows=[(1, "new"), (3, "added")])
    res = _copy.copy_table(
        second, dst, "QBAdminGroup", "abmc_t", cols, _opts(mode="merge")
    )
    assert res.note == "merged into existing table"
    rows = dst.execute(
        "SELECT id, name FROM QBAdminGroup_abmc_t ORDER BY id"
    ).fetchall()
    assert rows == [(1, "new"), (2, "keep"), (3, "added")]


def _merge_table_with_reject_trigger(dst) -> list[_models.ColumnInfo]:
    """Seed a merge table, then reject any later row whose name is 'bad'."""
    cols = [
        _models.ColumnInfo("id", "integer", 4, 0, False, True),
        _models.ColumnInfo("name", "varchar", 20, 0, True, False),
    ]
    _copy.copy_table(
        FakeCursor(rows=[(1, "seed")]),
        dst,
        "QBAdminGroup",
        "abmc_t",
        cols,
        _opts(mode="merge"),
    )
    dst.execute(
        "CREATE TRIGGER reject_bad BEFORE INSERT ON QBAdminGroup_abmc_t "
        "WHEN NEW.name = 'bad' BEGIN SELECT RAISE(ABORT, 'bad row'); END"
    )
    return cols


def test_rejected_rows_are_reported_and_others_kept(dst) -> None:
    cols = _merge_table_with_reject_trigger(dst)
    src = FakeCursor(rows=[(2, "good"), (3, "bad"), (4, "good2")])
    res = _copy.copy_table(
        src, dst, "QBAdminGroup", "abmc_t", cols, _opts(mode="merge")
    )
    assert res.status == "REJECTED_ROWS"
    assert [n for n, _ in res.rejects] == [2]
    names = [
        r[0] for r in dst.execute("SELECT name FROM QBAdminGroup_abmc_t ORDER BY id")
    ]
    assert names == ["seed", "good", "good2"]


def test_strict_aborts_table_on_first_bad_row(dst) -> None:
    cols = _merge_table_with_reject_trigger(dst)
    src = FakeCursor(rows=[(2, "good"), (3, "bad"), (4, "good2")])
    res = _copy.copy_table(
        src, dst, "QBAdminGroup", "abmc_t", cols, _opts(mode="merge", strict=True)
    )
    # The bad row sits in the first batch, which rolls back as a whole.
    assert res.status == "FAILED"
    assert res.rows_written == 0
    assert res.error is not None


# --------------------------------------------------------------------------- #
# Entity views
# --------------------------------------------------------------------------- #


def _make_table(dst, table: str, columns: list[str]) -> None:
    dst.execute(f'CREATE TABLE "QBAdminGroup_{table}" ({", ".join(columns)})')


def test_views_created_when_sources_present(dst) -> None:
    _make_table(
        dst,
        "abmc_customer_user",
        [
            "customer_id",
            "name",
            "is_hidden_bool",
            "is_deleted_bool",
            "is_delete_pending_bool",
        ],
    )
    created, skipped = _views.create_views(dst)
    assert "v_customers" in created
    assert any(s.startswith("v_items") for s in skipped)


def test_view_with_missing_column_is_skipped(dst) -> None:
    # Regression: SQLite accepts CREATE VIEW with unknown columns; the view must
    # be validated and reported as skipped.
    _make_table(
        dst,
        "abmc_invoice_header",
        ["transaction_id", "customer_id", "transaction_date"],
    )
    _make_table(
        dst,
        "abmc_invoice_lineitem",
        [
            "transaction_id",
            "item_id",
            "account_id",
            "quantity_qnty",
            "unit_price_amt",
            "amount_amt",
            "sales_tax_code_id",
            "class_id",
            "memo",
        ],
    )
    _make_table(dst, "abmc_customer_user", ["customer_id", "name"])
    _make_table(dst, "abmc_item_user", ["item_id", "name"])
    created, skipped = _views.create_views(dst)
    assert "v_invoice_lines" not in created
    assert any(s.startswith("v_invoice_lines: ") for s in skipped), skipped
    names = {
        r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='view'")
    }
    assert "v_invoice_lines" not in names


# --------------------------------------------------------------------------- #
# QBB backups and file hashing
# --------------------------------------------------------------------------- #


def test_extracts_company_file_from_container(tmp_path: Path) -> None:
    company = _sa_company_bytes(4096)
    qbb = tmp_path / "x.qbb"
    qbb.write_bytes(
        b"Compression_Format=Deflate_ZLIB;\r\nVersion=1;\r\n"
        + zlib.compress(company, 6)
    )
    out = tmp_path / "company.QBW"
    _qbb.extract_company_file(qbb, out)
    assert out.read_bytes() == company


def test_missing_marker_is_a_qbw_error(tmp_path: Path) -> None:
    qbb = tmp_path / "x.qbb"
    qbb.write_bytes(b"not a backup")
    with pytest.raises(QBWError):
        _qbb.extract_company_file(qbb, tmp_path / "c.QBW")


def test_stream_without_sa_magic_is_rejected(tmp_path: Path) -> None:
    qbb = tmp_path / "x.qbb"
    qbb.write_bytes(
        b"Compression_Format=Deflate_ZLIB;\r\n" + zlib.compress(b"x" * 300, 6)
    )
    with pytest.raises(QBWError):
        _qbb.extract_company_file(qbb, tmp_path / "c.QBW")


def test_is_qbb_detects_marker(tmp_path: Path) -> None:
    qbb = tmp_path / "x.qbb"
    qbb.write_bytes(b"Compression_Format=Deflate_ZLIB;")
    assert _qbb.is_qbb(qbb)
    qbw = tmp_path / "x.QBW"
    qbw.write_bytes(_sa_company_bytes())
    assert not _qbb.is_qbb(qbw)


def test_sha256_matches_hashlib(tmp_path: Path) -> None:
    f = tmp_path / "f.bin"
    f.write_bytes(b"abc" * 1000)
    assert _qbb.sha256_file(f) == hashlib.sha256(b"abc" * 1000).hexdigest()


# --------------------------------------------------------------------------- #
# Library: side effects and error handling
# --------------------------------------------------------------------------- #


def test_driver_context_restores_environment_and_path(tmp_path: Path) -> None:
    # A fake install with dbcapi.dll but no sqlanydb.py: the import fails, and
    # the process environment must come back exactly as it was.
    (tmp_path / "Bin64").mkdir()
    (tmp_path / "Bin64" / "dbcapi.dll").write_bytes(b"")
    env_before = dict(os.environ)
    path_before = list(sys.path)
    with pytest.raises(QBWError, match="sqlanydb"), _engine.sqlany_driver(tmp_path):
        pass  # pragma: no cover - the import fails first
    assert dict(os.environ) == env_before
    assert list(sys.path) == path_before


def test_missing_sa_install_is_a_qbw_error(tmp_path: Path, monkeypatch) -> None:
    for var in ("SQLANY17", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(QBWError, match="SQL Anywhere 17"):
        _engine.find_sqlany_dir(tmp_path / "nowhere")


def test_missing_company_file_is_a_qbw_error(tmp_path: Path) -> None:
    with pytest.raises(QBWError, match="not found"):
        convert(tmp_path / "nope.QBW", tmp_path / "o.sqlite", uid="x", pwd="y")


def test_refuses_to_overwrite_the_company_file(tmp_path: Path) -> None:
    src = tmp_path / "co.QBW"
    src.write_bytes(_sa_company_bytes())
    with pytest.raises(QBWError, match="refusing"):
        convert(src, src, uid="x", pwd="y")
    assert src.read_bytes() == _sa_company_bytes()


def test_refuses_output_with_a_quickbooks_extension(tmp_path: Path) -> None:
    src = tmp_path / "co.QBW"
    src.write_bytes(_sa_company_bytes())
    with pytest.raises(QBWError, match="QuickBooks extension"):
        convert(src, tmp_path / "other.qbb", uid="x", pwd="y")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"backend": "nope"}, "backend"),
        ({"mode": "append"}, "mode"),
        ({"uid": None, "pwd": "y"}, "uid and pwd"),
        ({"uid": "x", "pwd": "y", "limit": 0}, "limit"),
        ({"uid": "x", "pwd": "y", "batch_size": 0}, "batch_size"),
    ],
)
def test_bad_arguments_are_value_errors(tmp_path: Path, kwargs, message) -> None:
    src = tmp_path / "co.QBW"
    src.write_bytes(_sa_company_bytes())
    with pytest.raises(ValueError, match=message):
        convert(src, tmp_path / "o.sqlite", **kwargs)


def test_report_ok_follows_problems() -> None:
    base = {
        "output": Path("o.sqlite"),
        "source_name": "c.QBW",
        "source_kind": "qbw",
        "backend": "sa",
        "mode": "replace",
    }
    clean = ConversionReport(**base)
    assert clean.ok and clean.rows_written == 0
    bad = ConversionReport(
        **base,
        problems=["QBAdminGroup.abmc_x: FAILED"],
        tables=[
            TableResult("QBAdminGroup", "abmc_x", "QBAdminGroup_abmc_x", rows_written=4)
        ],
    )
    assert not bad.ok
    assert bad.rows_written == 4


# --------------------------------------------------------------------------- #
# Progress lines
# --------------------------------------------------------------------------- #


def _progress_line(caplog, res: TableResult) -> str:
    with caplog.at_level(logging.INFO, logger=_convert.log.name):
        _convert._log_table(1, 1, res)
    return caplog.messages[-1]


def test_progress_line_for_clean_table(caplog) -> None:
    res = TableResult("QBAdminGroup", "abmc_x", "QBAdminGroup_abmc_x", rows_written=5)
    res.status = "OK"
    assert _progress_line(caplog, res).endswith("QBAdminGroup.abmc_x: 5 rows")


def test_progress_line_gives_the_reason_for_a_mismatch(caplog) -> None:
    res = TableResult("QBAdminGroup", "abmc_y", "QBAdminGroup_abmc_y", rows_written=10)
    res.status = "MISMATCH"
    res.note = "read 10 of 12 source rows; SQLite has 10"
    assert _progress_line(caplog, res).endswith(
        "QBAdminGroup.abmc_y: 10 rows, MISMATCH: read 10 of 12 source rows; "
        "SQLite has 10"
    )


def test_progress_line_counts_rejected_rows(caplog) -> None:
    res = TableResult("QBAdminGroup", "abmc_r", "QBAdminGroup_abmc_r", rows_written=3)
    res.status = "REJECTED_ROWS"
    res.rejects = [(1, "bad"), (2, "bad")]
    assert _progress_line(caplog, res).endswith(
        "QBAdminGroup.abmc_r: 3 rows, REJECTED_ROWS (2 rejected)"
    )


def test_progress_line_survives_an_empty_error(caplog) -> None:
    # An empty error message once raised IndexError and aborted the run.
    res = TableResult("QBAdminGroup", "abmc_z", "QBAdminGroup_abmc_z")
    res.status = "FAILED"
    res.error = ""
    assert _progress_line(caplog, res).endswith(
        "QBAdminGroup.abmc_z: 0 rows, FAILED: unknown error"
    )


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #


def test_cli_missing_company_file_is_exit_1(tmp_path: Path, capsys) -> None:
    code = cli.main(
        [
            "--qbw",
            str(tmp_path / "nope.QBW"),
            "--out",
            str(tmp_path / "o.sqlite"),
            "--uid",
            "x",
            "--pwd",
            "y",
        ]
    )
    assert code == cli.EXIT_FATAL
    assert "company file not found" in capsys.readouterr().err


def test_cli_bad_limit_is_a_usage_error(capsys) -> None:
    # The library rejects the value with ValueError; the CLI maps it to exit 2.
    code = cli.main(
        ["--qbw", "x", "--out", "y", "--uid", "x", "--pwd", "y", "--limit", "0"]
    )
    assert code == cli.EXIT_USAGE
    assert "limit must be at least 1" in capsys.readouterr().err


def test_cli_where_without_equals_is_a_usage_error(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(
            [
                "--qbw",
                "x",
                "--out",
                "y",
                "--uid",
                "x",
                "--pwd",
                "y",
                "--where",
                "abmc_terms",
            ]
        )
    assert exc.value.code == cli.EXIT_USAGE
    assert "expected TABLE=PREDICATE, got 'abmc_terms'" in capsys.readouterr().err


def test_cli_sa_backend_without_credentials_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--qbw", "x", "--out", "y"])
    assert exc.value.code == cli.EXIT_USAGE


def test_parse_where_lowercases_table() -> None:
    assert cli.parse_where("ABMC_Terms = x = 1") == ("abmc_terms", "x = 1")


# --------------------------------------------------------------------------- #
# openqbw backend (optional)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not SAMPLE.exists(), reason="needs the sample company file")
def test_openqbw_backend_writes_catalog_and_provenance(tmp_path: Path) -> None:
    pytest.importorskip("openqbw")
    out = tmp_path / "ob.sqlite"
    report = convert(SAMPLE, out, backend="openqbw", work_dir=tmp_path)
    assert report.tables == []
    c = sqlite3.connect(str(out))
    try:
        assert c.execute("SELECT COUNT(*) FROM _openqbw_tables").fetchone()[0] > 0
        # Empty result sets keep their columns, so this query must not fail.
        c.execute("SELECT COUNT(*) FROM _openqbw_transactions").fetchone()
        assert (
            c.execute(
                "SELECT value FROM _qbw_meta WHERE key='source_sha256'"
            ).fetchone()
            is not None
        )
    finally:
        c.close()


# --------------------------------------------------------------------------- #
# Catalog
# --------------------------------------------------------------------------- #


def test_catalog_system_owners_are_excluded() -> None:
    assert "SYS" in _catalog.SYSTEM_OWNERS
    assert _catalog.DEFAULT_OWNER_PREFIX not in _catalog.SYSTEM_OWNERS


# --------------------------------------------------------------------------- #
# Engine integration tests (need SQL Anywhere 17 and the sample file)
# --------------------------------------------------------------------------- #


def _sa_available() -> bool:
    try:
        _engine.find_sqlany_dir(None)
    except QBWError:
        return False
    return True


needs_engine = pytest.mark.skipif(
    not (
        _sa_available()
        and SAMPLE.exists()
        and os.environ.get("QBW_TEST_UID")
        and os.environ.get("QBW_TEST_PWD")
    ),
    reason="needs SQL Anywhere 17, the sample file, and QBW_TEST_UID/QBW_TEST_PWD",
)


@needs_engine
def test_full_run_is_clean_and_matches_expectations(tmp_path: Path) -> None:
    out = tmp_path / "out.sqlite"
    report = convert(
        SAMPLE,
        out,
        uid=os.environ["QBW_TEST_UID"],
        pwd=os.environ["QBW_TEST_PWD"],
        port=29197,
        work_dir=tmp_path,
    )
    assert report.ok, report.problems
    assert report.output == out.resolve()
    c = sqlite3.connect(str(out))
    try:
        statuses = dict(
            c.execute("SELECT status, COUNT(*) FROM _qbw_tables GROUP BY status")
        )
        assert statuses == {"OK": 297}
        assert _count(c, "QBAdminGroup_abmc_invoice_lineitem") == 205
        assert _count(c, "v_invoice_lines") == 205
        assert (
            c.execute("SELECT value FROM _qbw_meta WHERE key='problems'").fetchone()[0]
            == "[]"
        )
    finally:
        c.close()
    assert not list(tmp_path.glob(".out.sqlite.*.partial"))


@needs_engine
def test_engine_run_leaves_process_environment_unchanged(tmp_path: Path) -> None:
    env_before = dict(os.environ)
    convert(
        SAMPLE,
        tmp_path / "out.sqlite",
        uid=os.environ["QBW_TEST_UID"],
        pwd=os.environ["QBW_TEST_PWD"],
        port=29198,
        work_dir=tmp_path,
    )
    assert os.environ.get("SQLANY_API_DLL") == env_before.get("SQLANY_API_DLL")
    assert os.environ.get("PATH") == env_before.get("PATH")
