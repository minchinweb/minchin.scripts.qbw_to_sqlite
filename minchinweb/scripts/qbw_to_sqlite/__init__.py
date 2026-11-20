# Copyright (c) 2026 William Minchin
"""Convert QuickBooks Desktop .QBW and .QBB company files to SQLite.

Example::

    from minchinweb.scripts.qbw_to_sqlite import convert

    report = convert("Company.QBW", "company.sqlite", uid="6265...", pwd="07...")
    if not report.ok:
        print(report.problems)

Only ``convert``, ``ConversionReport``, ``TableResult`` and ``QBWError`` are
public. Everything in the underscore-prefixed modules may change.
"""

from __future__ import annotations

import logging

from minchinweb.scripts.qbw_to_sqlite._convert import convert
from minchinweb.scripts.qbw_to_sqlite._errors import QBWError
from minchinweb.scripts.qbw_to_sqlite._models import ConversionReport, TableResult
from minchinweb.scripts.qbw_to_sqlite._version import __version__

# A library must not configure logging. Silence "no handler" warnings unless
# the caller attaches one.
logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = ["ConversionReport", "QBWError", "TableResult", "__version__", "convert"]
