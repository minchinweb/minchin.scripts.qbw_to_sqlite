# Copyright (c) 2026 William Minchin
"""Engine integration tests. They need SQL Anywhere 17, the sample file, and
the QBW_TEST_UID and QBW_TEST_PWD environment variables; otherwise they skip.
"""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3

from _support import SAMPLE, _count
import pytest

from minchin.scripts.qbw_to_sqlite import QBWError, _engine, convert


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
