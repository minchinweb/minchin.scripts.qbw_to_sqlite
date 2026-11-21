# Copyright (c) 2026 William Minchin
"""Shared pytest fixtures for the qbw_to_sqlite tests."""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def dst():
    """An in-memory SQLite output, closed after the test."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    yield conn
    conn.close()
