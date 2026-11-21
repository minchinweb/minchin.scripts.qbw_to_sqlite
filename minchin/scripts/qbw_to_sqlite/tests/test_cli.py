# Copyright (c) 2026 William Minchin
"""Command line: exit codes, usage errors and the start/done lines."""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from minchin.scripts.qbw_to_sqlite import ConversionReport, cli


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


def _fake_report(tmp_path: Path, **extra) -> ConversionReport:
    return ConversionReport(
        output=tmp_path / "o.sqlite",
        source_name="c.QBW",
        source_kind="qbw",
        backend="openqbw",
        mode="replace",
        **extra,
    )


def test_cli_prints_title_start_and_clock_done(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cli, "convert", lambda *a, **k: _fake_report(tmp_path))
    code = cli.main(["--qbw", "c.QBW", "--out", "o.sqlite", "--backend", "openqbw"])
    assert code == cli.EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    assert "qbw-to-sqlite" in lines[0]
    assert "Convert a QuickBooks .QBW or .QBB company file to SQLite." in lines[1]
    assert lines[2].startswith("[start] c.QBW -> o.sqlite")
    assert re.search(r"\d{2}:\d{2}$", lines[2])
    done = r"^\[done\] openqbw backend -> .*o\.sqlite\s*\d{2}:\d{2}$"
    assert re.search(done, lines[-1])


def test_cli_done_line_reports_problems_and_exit_3(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    report = _fake_report(tmp_path, problems=["QBAdminGroup.abmc_x: FAILED"])
    monkeypatch.setattr(cli, "convert", lambda *a, **k: report)
    code = cli.main(["--qbw", "c.QBW", "--out", "o.sqlite", "--backend", "openqbw"])
    assert code == cli.EXIT_PROBLEMS
    lines = capsys.readouterr().out.splitlines()
    assert "[problem] QBAdminGroup.abmc_x: FAILED" in lines
    assert "(finished with problems, exit status 3)" in lines[-1]
    assert re.search(r"\d{2}:\d{2}$", lines[-1])
