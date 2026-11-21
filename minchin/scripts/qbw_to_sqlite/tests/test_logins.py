# Copyright (c) 2026 William Minchin
"""The built-in logins and how the UID and PWD are chosen."""

from __future__ import annotations

import pytest

from minchin.scripts.qbw_to_sqlite._logins import (
    DEFAULT_LOGINS,
    login_candidates,
    uid_hex,
)

BEANCOUNT_PWDS = [pwd for name, _, pwd in DEFAULT_LOGINS if name == "beancount"]


def test_uid_hex_matches_the_known_values() -> None:
    assert uid_hex("Admin") == "41646d696e"
    assert uid_hex("beancount") == "6265616e636f756e74"
    assert uid_hex("minchinweb") == "6d696e6368696e776562"


def test_no_arguments_tries_every_built_in_login_in_order() -> None:
    logins = login_candidates()
    assert [lg.name for lg in logins] == [name for name, _, _ in DEFAULT_LOGINS]
    assert [lg.uid for lg in logins] == [uid_hex(name) for name, _, _ in DEFAULT_LOGINS]
    assert [lg.pwd for lg in logins] == [pwd for _, _, pwd in DEFAULT_LOGINS]


def test_each_user_has_both_file_formats() -> None:
    for name in ("Admin", "beancount", "minchinweb"):
        fmts = [fmt for n, fmt, _ in DEFAULT_LOGINS if n == name]
        assert fmts == ["newer format", "2017 and earlier"]


def test_pwd_without_user_is_used_for_every_built_in_login() -> None:
    logins = login_candidates(pwd="abcd")
    assert [lg.pwd for lg in logins] == ["abcd"] * len(DEFAULT_LOGINS)


def test_user_tries_both_of_its_built_in_pwds() -> None:
    logins = login_candidates(user="beancount")
    assert [lg.uid for lg in logins] == ["6265616e636f756e74"] * 2
    assert [lg.pwd for lg in logins] == BEANCOUNT_PWDS
    assert [lg.fmt for lg in logins] == ["newer format", "2017 and earlier"]


def test_explicit_pwd_overrides_the_built_in_ones() -> None:
    (login,) = login_candidates(user="Admin", pwd="ff")
    assert login.pwd == "ff"
    assert login.fmt == ""


def test_hex_uid_is_used_as_given_with_its_built_in_pwds() -> None:
    logins = login_candidates(uid="6265616E636F756E74")
    assert {lg.name for lg in logins} == {"beancount"}
    assert {lg.uid for lg in logins} == {"6265616e636f756e74"}
    assert [lg.pwd for lg in logins] == BEANCOUNT_PWDS


def test_unknown_user_needs_a_pwd() -> None:
    with pytest.raises(ValueError, match="pass pwd"):
        login_candidates(user="nobody")
    (login,) = login_candidates(user="nobody", pwd="ff")
    assert login.uid == uid_hex("nobody")
