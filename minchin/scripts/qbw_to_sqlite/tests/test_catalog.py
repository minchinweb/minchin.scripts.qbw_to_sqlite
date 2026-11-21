# Copyright (c) 2026 William Minchin
"""Catalog constants: which owners are treated as system tables."""

from __future__ import annotations

from minchin.scripts.qbw_to_sqlite import _catalog


def test_catalog_system_owners_are_excluded() -> None:
    assert "SYS" in _catalog.SYSTEM_OWNERS
    assert _catalog.DEFAULT_OWNER_PREFIX not in _catalog.SYSTEM_OWNERS
