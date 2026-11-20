# Copyright (c) 2026 William Minchin
"""Entity views over the QuickBooks tables, validated after creation."""

from __future__ import annotations

import sqlite3

from minchinweb.scripts.qbw_to_sqlite._catalog import DEFAULT_OWNER_PREFIX
from minchinweb.scripts.qbw_to_sqlite._models import dst_name_for
from minchinweb.scripts.qbw_to_sqlite._text import quote_ident

# Each view needs the listed source tables (names without the owner prefix).
VIEW_DEFS: list[tuple[str, tuple[str, ...], str]] = [
    (
        "v_invoice_lines",
        (
            "abmc_invoice_header",
            "abmc_invoice_lineitem",
            "abmc_customer_user",
            "abmc_item_user",
        ),
        """
        CREATE VIEW "{view}" AS
        SELECT h.transaction_id, h.doc_num, h.transaction_date, h.customer_id,
               cu.name AS customer_name, h.memo AS header_memo,
               l.target_id AS line_target_id, l.item_id, iu.name AS item_name,
               l.account_id, l.quantity_qnty, l.unit_price_amt, l.amount_amt,
               l.sales_tax_code_id, l.class_id, l.memo AS line_memo
        FROM {abmc_invoice_header} h
        JOIN {abmc_invoice_lineitem} l ON l.transaction_id = h.transaction_id
        LEFT JOIN {abmc_customer_user} cu ON cu.customer_id = h.customer_id
        LEFT JOIN {abmc_item_user} iu ON iu.item_id = l.item_id
        """,
    ),
    (
        "v_customers",
        ("abmc_customer_user",),
        """
        CREATE VIEW "{view}" AS
        SELECT customer_id, name, is_hidden_bool, is_deleted_bool,
               is_delete_pending_bool
        FROM {abmc_customer_user}
        """,
    ),
    (
        "v_items",
        ("abmc_item_user",),
        """
        CREATE VIEW "{view}" AS
        SELECT item_id, name, is_deleted_bool, sales_tax_code_id,
               asset_account_id, cogs_account_id
        FROM {abmc_item_user}
        """,
    ),
    (
        "v_accounts",
        ("abmc_account_user",),
        """
        CREATE VIEW "{view}" AS
        SELECT account_id, name, is_deleted_bool, reimbursed_account_id
        FROM {abmc_account_user}
        """,
    ),
]


def create_views(dst: sqlite3.Connection) -> tuple[list[str], list[str]]:
    """Create the entity views whose source tables are present.

    SQLite accepts a CREATE VIEW whose columns do not exist and fails only when
    the view is queried. So each view is queried once with LIMIT 0 and dropped
    again if that fails.

    Args:
        dst: The SQLite output connection.

    Returns:
        ``(created, skipped)``. ``skipped`` holds a reason for each view not created.
    """
    existing = {
        r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    created: list[str] = []
    skipped: list[str] = []
    for view, tables, template in VIEW_DEFS:
        names = {t: dst_name_for(DEFAULT_OWNER_PREFIX, t) for t in tables}
        missing = [n for n in names.values() if n not in existing]
        if missing:
            skipped.append(f"{view}: missing {', '.join(missing)}")
            continue
        fmt = {t: quote_ident(n) for t, n in names.items()}
        fmt["view"] = view
        dst.execute(f"DROP VIEW IF EXISTS {quote_ident(view)}")
        dst.execute(template.format(**fmt))
        try:
            dst.execute(f"SELECT * FROM {quote_ident(view)} LIMIT 0").fetchall()
        except sqlite3.Error as exc:
            dst.execute(f"DROP VIEW IF EXISTS {quote_ident(view)}")
            skipped.append(f"{view}: {exc}")
            continue
        created.append(view)
    return created, skipped
