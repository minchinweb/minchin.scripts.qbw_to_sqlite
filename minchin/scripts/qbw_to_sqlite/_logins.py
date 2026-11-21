# Copyright (c) 2026 William Minchin
"""The SQL Anywhere logins to try, and the hex encoding of their user names."""

from __future__ import annotations

from typing import NamedTuple

# The built-in logins, as (user name, format, password value as hex). Each user
# has one password per company-file format. The format of the file decides which
# one works, so every entry is tried until one logs in.
DEFAULT_LOGINS: tuple[tuple[str, str, str], ...] = (
    (
        "Admin",
        "newer format",
        "064e7afebcfbae000b22c7c85e5560f89a2a028000",
    ),
    (
        "Admin",
        "2017 and earlier",
        "074c99f858df3c75f8add53fe5b3413e25cb3c2f98ec3545ae8dbe5bebda83d9fa00",
    ),
    (
        "beancount",
        "newer format",
        "06ea6ee0a1d51a95af38b2e2386eed740163751b00",
    ),
    (
        "beancount",
        "2017 and earlier",
        "07ec23c86e6baf1fee7e918f71512fa00c129a7b708d625e56387ed264c002a2fc00",
    ),
    (
        "minchinweb",
        "newer format",
        "064ef5b0b158e5b9cb08a9395bb70e9fb8c701d800",
    ),
    (
        "minchinweb",
        "2017 and earlier",
        "07577e234440747ab20d3af1b28bfd79146edddf9087494d04fa2575d8b1079c4300",
    ),
)


class Login(NamedTuple):
    """One login to try on the engine.

    Attributes:
        name: The user name, for logs and provenance. If only a UID was given,
            this is its text when it is ASCII, else the hex itself.
        uid: The user name as hex, the form SQL Anywhere expects.
        pwd: The password value, as hex.
        fmt: The file format this password belongs to, or "" if it was given.
    """

    name: str
    uid: str
    pwd: str
    fmt: str = ""

    @property
    def label(self) -> str:
        """The name with its format, for log messages."""
        return f"{self.name} ({self.fmt})" if self.fmt else self.name


def uid_hex(user: str) -> str:
    """Return the hex form of a user name, e.g. ``beancount`` -> ``6265...74``.

    Raises:
        ValueError: The name is not ASCII.
    """
    return user.encode("ascii").hex()


def _name_from_hex(uid: str) -> str | None:
    try:
        return bytes.fromhex(uid).decode("ascii")
    except ValueError:
        return None


def _defaults_for(name: str | None) -> list[tuple[str, str]]:
    """Return the (format, password) pairs built in for a user name."""
    return [(fmt, pwd) for n, fmt, pwd in DEFAULT_LOGINS if n == name]


def login_candidates(
    user: str | None = None, uid: str | None = None, pwd: str | None = None
) -> list[Login]:
    """Return the logins to try, in order.

    The rules:

    - ``uid`` (hex) given: logins for that user name. If ``pwd`` is given, only
      that password is used. Otherwise every built-in password for the name is
      tried, if the name is a built-in one.
    - ``user`` (plain text) given: the same, with the UID generated from the name.
    - Neither given: every built-in login, in ``DEFAULT_LOGINS`` order. A
      supplied ``pwd`` is used for each of them.

    Args:
        user: A user name such as ``beancount``, or None.
        uid: A user name as hex, or None.
        pwd: The password value as hex, or None.

    Returns:
        At least one Login.

    Raises:
        ValueError: A user has no built-in password and none was given, or the
            name is not ASCII.
    """
    if uid is not None:
        name = _name_from_hex(uid)
        label, hex_uid = name or uid, uid.lower()
    elif user is not None:
        name = label = user
        hex_uid = uid_hex(user)
    else:
        return [
            Login(name, uid_hex(name), pwd or password, fmt)
            for name, fmt, password in DEFAULT_LOGINS
        ]

    if pwd:
        return [Login(label, hex_uid, pwd)]
    found = _defaults_for(name)
    if not found:
        what = f"UID {uid}" if uid is not None else f"user {user!r}"
        raise ValueError(f"no built-in PWD for {what}; pass pwd")
    return [Login(label, hex_uid, password, fmt) for fmt, password in found]
