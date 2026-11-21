# Copyright (c) 2026 William Minchin
"""Column type mapping, text recovery and identifier quoting."""

from __future__ import annotations

import decimal

from minchin.scripts.qbw_to_sqlite import _models, _text


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
