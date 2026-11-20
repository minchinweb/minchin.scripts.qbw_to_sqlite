Changelog
=========

:release:`0.1.0 <unreleased>`
-----------------------------

- Restructured the single script as the ``minchin.scripts.qbw_to_sqlite``
  library. ``convert()`` returns a ``ConversionReport``. Expected failures raise
  ``QBWError``. Logging replaces printing, and importing the package changes
  nothing in the process.
- ``qbw-to-sqlite`` console command, and ``qbw_to_sqlite.py`` for running from a
  checkout. Both use the same command line.
- Copies every base table through a private, read-only SQL Anywhere 17 engine,
  with declared types, primary keys, row-count checks and provenance tables.
- Merge mode, table and row filters, entity views for invoices, customers,
  items and accounts, and strict and lenient handling of bad rows.
- Reads ``.QBB`` backups. Not yet tested against a real backup.
- Experimental ``openqbw`` backend (optional extra). It returns catalog and
  transaction data only, and is not a full copy.
- Requires Python 3.9 or later. Tested on 3.9 and 3.15 in CI.
