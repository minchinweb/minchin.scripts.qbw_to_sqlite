# Copyright (c) 2026 William Minchin
"""The optional openqbw backend. Skipped unless openqbw and the sample exist."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from _support import SAMPLE
import pytest

from minchin.scripts.qbw_to_sqlite import convert


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
