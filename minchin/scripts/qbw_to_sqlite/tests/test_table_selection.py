# Copyright (c) 2026 William Minchin
"""Table selection: --include, --exclude and --where matching."""

from __future__ import annotations

from _support import _opts


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
