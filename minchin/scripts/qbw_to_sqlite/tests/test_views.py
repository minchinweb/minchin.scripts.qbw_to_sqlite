# Copyright (c) 2026 William Minchin
"""Entity views built over the copied tables."""

from __future__ import annotations

from minchin.scripts.qbw_to_sqlite import _views


def _make_table(dst, table: str, columns: list[str]) -> None:
    dst.execute(f'CREATE TABLE "QBAdminGroup_{table}" ({", ".join(columns)})')


def test_views_created_when_sources_present(dst) -> None:
    _make_table(
        dst,
        "abmc_customer_user",
        [
            "customer_id",
            "name",
            "is_hidden_bool",
            "is_deleted_bool",
            "is_delete_pending_bool",
        ],
    )
    created, skipped = _views.create_views(dst)
    assert "v_customers" in created
    assert any(s.startswith("v_items") for s in skipped)


def test_view_with_missing_column_is_skipped(dst) -> None:
    # Regression: SQLite accepts CREATE VIEW with unknown columns; the view must
    # be validated and reported as skipped.
    _make_table(
        dst,
        "abmc_invoice_header",
        ["transaction_id", "customer_id", "transaction_date"],
    )
    _make_table(
        dst,
        "abmc_invoice_lineitem",
        [
            "transaction_id",
            "item_id",
            "account_id",
            "quantity_qnty",
            "unit_price_amt",
            "amount_amt",
            "sales_tax_code_id",
            "class_id",
            "memo",
        ],
    )
    _make_table(dst, "abmc_customer_user", ["customer_id", "name"])
    _make_table(dst, "abmc_item_user", ["item_id", "name"])
    created, skipped = _views.create_views(dst)
    assert "v_invoice_lines" not in created
    assert any(s.startswith("v_invoice_lines: ") for s in skipped), skipped
    names = {
        r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='view'")
    }
    assert "v_invoice_lines" not in names
