# Copyright (c) 2026 William Minchin
"""Smoke test: confirms that pytest finds and runs tests in this package."""

from __future__ import annotations


def test_always_pass() -> None:
    """Always passes. Fails only if pytest cannot run this package's tests."""
    assert True
