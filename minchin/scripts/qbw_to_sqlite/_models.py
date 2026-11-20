# Copyright (c) 2026 William Minchin
"""Data types shared by the converter, and the SQL Anywhere to SQLite type map."""

from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
from pathlib import Path
from typing import Any

# Statuses that count as problems. FILTERED and SKIPPED are deliberate choices
# by the caller, so they are not problems.
PROBLEM_STATUSES = frozenset({"REJECTED_ROWS", "PARTIAL", "FAILED", "MISMATCH"})

# SQLite picks column affinity from the declared type. NUMERIC affinity would
# turn exact decimal text such as "12.50" into a float, so DECIMAL/NUMERIC are
# declared TEXT and stored exactly as text.
INTEGER_DOMAINS = frozenset(
    {
        "smallint",
        "integer",
        "tinyint",
        "bigint",
        "unsigned int",
        "unsigned smallint",
        "unsigned bigint",
        "bit",
    }
)
REAL_DOMAINS = frozenset({"float", "double"})
TEXT_DOMAINS = frozenset(
    {
        "char",
        "varchar",
        "long varchar",
        "nchar",
        "nvarchar",
        "long nvarchar",
        "date",
        "time",
        "timestamp",
        "timestamp with time zone",
        "uniqueidentifier",
        "xml",
        "numeric",
        "decimal",
    }
)
BLOB_DOMAINS = frozenset(
    {"binary", "varbinary", "long binary", "varbit", "long varbit"}
)


def sqlite_affinity(domain: str | None) -> str:
    """Return the SQLite declared type for a SQL Anywhere domain name.

    Args:
        domain: The SQL Anywhere domain, such as ``"decimal"``, or None.

    Returns:
        ``"INTEGER"``, ``"REAL"``, ``"TEXT"`` or ``"BLOB"``, or ``""`` when the
        domain is unknown, which leaves the column untyped.
    """
    if domain is None:
        return ""
    d = domain.lower()
    if d in INTEGER_DOMAINS:
        return "INTEGER"
    if d in REAL_DOMAINS:
        return "REAL"
    if d in TEXT_DOMAINS:
        return "TEXT"
    if d in BLOB_DOMAINS:
        return "BLOB"
    return ""


def dst_name_for(owner: str, table: str) -> str:
    """Return the SQLite table name: plain for DBA-owned tables, else owner_table."""
    return table if owner in ("DBA", "") else f"{owner}_{table}"


@dataclass
class ColumnInfo:
    """One column of a source table, as the catalog describes it."""

    name: str
    source_type: str
    width: int | None
    scale: int | None
    nullable: bool
    primary_key: bool

    @property
    def declared_type(self) -> str:
        """The SQLite declared type for this column."""
        return sqlite_affinity(self.source_type)


@dataclass
class Options:
    """Settings that control which tables are copied and how."""

    mode: str = "replace"  # "replace" or "merge"
    includes: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    limit: int | None = None
    where: dict[str, str] = field(default_factory=dict)
    batch_size: int = 2000
    strict: bool = False

    def where_for(self, owner: str, table: str) -> str | None:
        """Return the WHERE predicate for a table, or None if it has none."""
        for key in (f"{owner}.{table}".lower(), table.lower()):
            if key in self.where:
                return self.where[key]
        return None

    def selected(self, owner: str, table: str) -> bool:
        """Return True if a table passes the include and exclude globs."""
        keys = [f"{owner}.{table}".lower(), table.lower()]

        def hit(patterns: list[str]) -> bool:
            return any(
                fnmatch.fnmatchcase(k, p.lower()) for k in keys for p in patterns
            )

        if self.includes and not hit(self.includes):
            return False
        return not (self.excludes and hit(self.excludes))


@dataclass
class TableResult:
    """What happened to one source table during a conversion."""

    owner: str
    table: str
    dst_name: str
    status: str = "OK"
    source_rows: int | None = None
    rows_read: int = 0
    rows_written: int = 0
    rejects: list[tuple[int, str]] = field(default_factory=list)
    error: str | None = None
    primary_key: list[str] = field(default_factory=list)
    note: str | None = None


@dataclass
class ConversionReport:
    """The result of a conversion. Returned by ``convert()``.

    A report with problems is still a finished conversion: the output exists
    and holds what could be read. Check ``ok`` and ``problems``.

    Attributes:
        output: Path of the SQLite file written.
        source_name: File name of the company file that was read.
        source_kind: ``"qbw"`` or ``"qbb"``.
        backend: ``"sa"`` or ``"openqbw"``.
        mode: ``"replace"`` or ``"merge"``.
        tables: One TableResult per table the sa backend considered. Empty for
            the openqbw backend, which writes its own tables.
        views: Names of the entity views created.
        views_skipped: Views not created, each with its reason.
        problems: One line per problem, such as ``"QBAdminGroup.abmc_x: FAILED"``.
        source_sha256: SHA-256 of the company file as given.
        meta: The provenance values written to the ``_qbw_meta`` table.
    """

    output: Path
    source_name: str
    source_kind: str
    backend: str
    mode: str
    tables: list[TableResult] = field(default_factory=list)
    views: list[str] = field(default_factory=list)
    views_skipped: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    source_sha256: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when the conversion finished with no problems."""
        return not self.problems

    @property
    def rows_written(self) -> int:
        """Total rows written across all tables from the sa backend."""
        return sum(t.rows_written for t in self.tables)
