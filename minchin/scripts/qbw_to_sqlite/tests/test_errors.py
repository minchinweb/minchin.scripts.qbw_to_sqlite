# Copyright (c) 2026 William Minchin
"""The error classes derive from QBWError, which derives from Exception."""

from __future__ import annotations

import sqlite3

import pytest

from minchin.scripts.qbw_to_sqlite import (
    DatabaseError,
    EngineError,
    EngineNotFoundError,
    InputError,
    OutputPathError,
    QBWError,
)

SUBCLASSES = [
    InputError,
    OutputPathError,
    EngineNotFoundError,
    EngineError,
    DatabaseError,
]


def test_base_is_an_exception() -> None:
    assert issubclass(QBWError, Exception)


@pytest.mark.parametrize("cls", SUBCLASSES)
def test_subclasses_derive_from_qbwerror(cls: type[QBWError]) -> None:
    assert issubclass(cls, QBWError)
    assert issubclass(cls, Exception)


@pytest.mark.parametrize("cls", SUBCLASSES)
def test_subclass_caught_by_base(cls: type[QBWError]) -> None:
    with pytest.raises(QBWError):
        raise cls("boom")


@pytest.mark.parametrize(
    ("cls", "builtin"),
    [
        (InputError, OSError),
        (OutputPathError, ValueError),
        (EngineError, RuntimeError),
        (DatabaseError, sqlite3.Error),
    ],
)
def test_builtin_base(cls: type[QBWError], builtin: type[BaseException]) -> None:
    assert issubclass(cls, builtin)
    with pytest.raises(builtin):
        raise cls("boom")
