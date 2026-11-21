# Copyright (c) 2026 William Minchin
"""Library entry point: side effects, argument checks and the report object."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from _support import _sa_company_bytes
import pytest

from minchin.scripts.qbw_to_sqlite import (
    ConversionReport,
    QBWError,
    TableResult,
    _engine,
    convert,
)


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
        ({"user": "nobody"}, "no built-in PWD"),
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
