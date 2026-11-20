# Copyright (c) 2026 William Minchin
"""Run the qbw-to-sqlite command from a source checkout, without installing.

This is the same command as the installed ``qbw-to-sqlite`` entry point. Its
logic lives in ``minchinweb.scripts.qbw_to_sqlite.cli``, which is written
against the public API, so it doubles as a worked example of using the library
from a script.

    python qbw_to_sqlite.py --help
"""

from __future__ import annotations

from minchinweb.scripts.qbw_to_sqlite.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
