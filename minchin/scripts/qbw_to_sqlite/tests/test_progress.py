# Copyright (c) 2026 William Minchin
"""Per-table progress lines written to the log."""

from __future__ import annotations

import logging

from minchin.scripts.qbw_to_sqlite import TableResult, _convert


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
