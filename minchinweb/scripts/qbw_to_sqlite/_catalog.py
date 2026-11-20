# Copyright (c) 2026 William Minchin
"""Catalog queries: which tables exist, and each column's type and key flag."""

from __future__ import annotations

from typing import Any

from minchinweb.scripts.qbw_to_sqlite._models import ColumnInfo

# SQL Anywhere system schema owners that are never exported.
SYSTEM_OWNERS = frozenset(
    {
        "SYS",
        "dbo",
        "rs_systabgroup",
        "SA_DEBUG",
        "DIAGNOSTICS",
        "extenv",
        "ml_server",
        "SNAPSHOT_ISOLATION",
        "console",
    }
)
# Owner of the QuickBooks business tables. Its tables get no owner prefix.
DEFAULT_OWNER_PREFIX = "QBAdminGroup"

TABLE_QUERY = """
SELECT u.user_name, t.table_name
FROM SYS.SYSTAB  t
JOIN SYS.SYSUSER u ON t.creator = u.user_id
WHERE t.table_type = 1          -- 1 = BASE table
ORDER BY u.user_name, t.table_name
"""

COLUMN_QUERY = """
SELECT u.user_name, t.table_name, c.column_name, d.domain_name,
       c.width, c.scale, c."nulls", c.pkey
FROM SYS.SYSCOLUMN c
JOIN SYS.SYSTAB   t ON c.table_id  = t.table_id
JOIN SYS.SYSUSER  u ON t.creator   = u.user_id
JOIN SYS.SYSDOMAIN d ON c.domain_id = d.domain_id
WHERE t.table_type = 1
ORDER BY u.user_name, t.table_name, c.column_id
"""


def list_user_tables(cur: Any) -> list[tuple[str, str]]:
    """Return ``(owner, table_name)`` for every base table, minus system owners.

    Args:
        cur: An open cursor on the SQL Anywhere connection.
    """
    cur.execute(TABLE_QUERY)
    return [
        (owner, name) for owner, name in cur.fetchall() if owner not in SYSTEM_OWNERS
    ]


def load_column_info(cur: Any) -> dict[tuple[str, str], list[ColumnInfo]]:
    """Map ``(owner, table)`` to its columns, in column order, with types and keys.

    Args:
        cur: An open cursor on the SQL Anywhere connection.
    """
    cur.execute(COLUMN_QUERY)
    out: dict[tuple[str, str], list[ColumnInfo]] = {}
    for owner, table, col, domain, width, scale, nulls, pkey in cur.fetchall():
        if owner in SYSTEM_OWNERS:
            continue
        out.setdefault((owner, table), []).append(
            ColumnInfo(
                name=col,
                source_type=domain or "",
                width=width,
                scale=scale,
                nullable=str(nulls).upper() == "Y",
                primary_key=str(pkey).upper() == "Y",
            )
        )
    return out
