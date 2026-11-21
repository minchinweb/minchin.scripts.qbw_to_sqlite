# Copyright (c) 2026 William Minchin
"""Copying one table: status, batching, types, merge and strict mode."""

from __future__ import annotations

from _support import FakeCursor, _count, _opts

from minchin.scripts.qbw_to_sqlite import _copy, _models


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
