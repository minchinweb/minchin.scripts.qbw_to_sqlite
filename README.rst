qbw-to-sqlite
=============

Convert a QuickBooks Desktop company file (``.QBW``, or a ``.QBB`` backup) into a
SQLite database that any SQLite tool, pandas, Metabase or ``sqlite3`` can read.
Every user table is copied, with declared column types, primary keys, row-count
checks, entity views and provenance tables. It needs no QuickBooks application
and no Intuit SDK.

    **Scope.** Use this on your own company files, for data portability,
    analysis, archival, or forensic review you are authorized to perform. It does
    not crack or recover unknown passwords. It relies on a QuickBooks user *you*
    create with no password.

The package is ``minchinweb.scripts.qbw_to_sqlite``. It is a library first, and
the ``qbw-to-sqlite`` command is a thin layer over the same API.

Install
-------

.. code-block:: console

    pip install minchinweb.scripts.qbw-to-sqlite
    pip install "minchinweb.scripts.qbw-to-sqlite[openqbw]"   # optional backend

The default ``sa`` backend needs **SQL Anywhere 17** installed (Windows). The
package cannot install it. The Developer Edition is free from SAP.

Library usage
-------------

.. code-block:: python

    from minchinweb.scripts.qbw_to_sqlite import QBWError, convert

    try:
        report = convert(
            r"C:\path\to\Your Company.QBW",
            r"C:\path\to\your_company.sqlite",
            uid="6265616e636f756e74",  # "beancount", as hex
            pwd="07ec23...2fc00",
            tables=["abmc_*"],  # optional: only these tables
        )
    except QBWError as exc:
        raise SystemExit(f"conversion did not run: {exc}")

    print(report.tables[0].status, report.rows_written)
    if not report.ok:
        print("finished with problems:", report.problems)

``convert()`` returns a ``ConversionReport``. Its fields are ``output``,
``tables`` (one ``TableResult`` per table, with its status and counts), ``views``,
``views_skipped``, ``problems``, ``source_sha256`` and ``meta``. The properties
``ok`` and ``rows_written`` summarize them.

``convert()`` raises ``QBWError`` when the conversion cannot run at all: a
missing file, a refused output path, an engine that will not start, or a login
that fails. No output is written in those cases. It raises ``ValueError`` for an
argument that is not valid, for example ``mode="append"``. A report with problems
is still a finished conversion, so check ``report.ok``.

Logging goes through the standard :mod:`logging` module, under the logger
``minchinweb.scripts.qbw_to_sqlite``. The library attaches no handler and
configures no logging. Importing it changes nothing in the process.

The full list of arguments is in the docstring of ``convert``.

Command line
------------

.. code-block:: console

    qbw-to-sqlite --qbw "Your Company.QBW" --out your_company.sqlite ^
        --uid 6265616e636f756e74 --pwd 07ec23...2fc00

From a checkout, without installing, run ``python qbw_to_sqlite.py`` with the
same arguments. The command line is built on the public API, so
``minchinweb/scripts/qbw_to_sqlite/cli.py`` is a working example of calling the
library.

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Option
     - Meaning
   * - ``--qbw``
     - ``.QBW`` or ``.QBB`` company file. Read only, copied, never modified.
   * - ``--out``
     - SQLite file to create. Refused if it is a QuickBooks file. Replaced atomically.
   * - ``--backend``
     - ``sa`` (default) or ``openqbw`` (experimental).
   * - ``--uid`` / ``--pwd``
     - SQL Anywhere credential (``sa`` backend).
   * - ``--mode``
     - ``replace`` (default): fresh output. ``merge``: upsert by primary key.
   * - ``--tables PATTERN``
     - Only tables matching a glob. Matches ``table`` or ``owner.table``. Repeatable.
   * - ``--exclude PATTERN``
     - Skip tables matching a glob. Repeatable.
   * - ``--limit N``
     - At most N rows per table. Skips the count check.
   * - ``--where TABLE=PREDICATE``
     - SQL ``WHERE`` clause for one table. Passed to SQL Anywhere unchanged, so use it only with trusted input.
   * - ``--batch-size``
     - Rows per insert batch (default 2000).
   * - ``--strict``
     - Abort a table on its first bad row, instead of skipping that row.
   * - ``--sa-dir``
     - SQL Anywhere 17 install folder (found automatically if omitted).
   * - ``--port`` / ``--server``
     - Private TCP port and engine name for the temporary engine.
   * - ``--work-dir``
     - Where to put the temporary working copy (default: system temp).
   * - ``--keep-copy``
     - Keep the working copy and engine log.

Exit status
~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 10 90

   * - Code
     - Meaning
   * - ``0``
     - Clean. Every selected table is ``OK``, or ``FILTERED`` or ``SKIPPED`` by request.
   * - ``1``
     - Fatal. No output was written. Examples: wrong password, engine did not start, output path is a QuickBooks file.
   * - ``2``
     - Usage error.
   * - ``3``
     - Finished with problems: at least one table is ``REJECTED_ROWS``, ``PARTIAL``, ``FAILED`` or ``MISMATCH``.

Backends
--------

.. list-table::
   :header-rows: 1
   :widths: 15 35 50

   * - Backend
     - Needs
     - Output and status
   * - ``sa`` (default)
     - SQL Anywhere 17 install, and a no-password QuickBooks user
     - Every base table, typed, with views and provenance. Tested on a sample company file.
   * - ``openqbw``
     - ``pip install openqbw`` (Python 3.10+), no login
     - Catalog, indexes, transactions and line items only. **Experimental.** On the sample file it found 643 catalog entries and **0 transactions and 0 line items**, so it is not a substitute for ``sa``.

How the sa backend works
------------------------

::

    .QBW or .QBB --> working copy --dbeng17 -r--> private read-only engine
                     (SHA-256 of                    |  (TCP on a private port)
                      source and copy)              v
                                          sqlanydb (SQL Anywhere's own driver)
                                                    |  catalog + SELECT *
                                                    v
                                      SQLite  <out>.partial --> atomic rename to <out>

1. **Hash the source** (SHA-256), then **copy it** to a working file. A ``.QBB``
   backup is extracted first (see `QBB backups`_). The original is never opened
   for writing.
2. **Start a private engine, read-only:** ``dbeng17.exe -r -x tcpip(port=...)``.
   ``-r`` makes the whole server read-only. The library waits for ``Now accepting
   requests`` before it connects.
3. **Connect** with ``sqlanydb``, the driver bundled with the SQL Anywhere install.
   The library sets the driver's environment variables only for the duration of
   the import and the connection, then restores them.
4. **Read the catalog.** ``SYS.SYSTAB`` lists the base tables. ``SYS.SYSCOLUMN``
   and ``SYS.SYSDOMAIN`` give each column's type, width, scale, nullability and
   key flag. System owners (``SYS``, ``dbo``, ...) are skipped. Real data is owned
   by **QBAdminGroup**.
5. **Copy each table** in batches. Declared types and primary keys come from the
   catalog. Each table's row count is checked against ``COUNT(*)`` on the source.
6. **Create entity views** when their source tables exist.
7. **Write provenance tables**, then rename the output into place. A failed run
   leaves any existing output untouched.

The credential
--------------

QuickBooks stores each user's database password as a derived value::

    PWD = "07" + SHA-256( prepared(password) ) + "00"        (SQL Anywhere 17 form)
    UID = the QuickBooks user name encoded as ASCII hex
          e.g. "Admin"     -> 41646d696e
               "beancount" -> 6265616e636f756e74

The ``prepared(...)`` step mixes in an Intuit secret that cannot be reproduced
offline, **except for the empty password**, whose value depends only on the user
name. So the recipe is: make a QuickBooks user with **no password**, and use its
blank-password credential.

1. Make the file writable: ``attrib -r "Your Company.QBW"``.
2. In QuickBooks, log in as **Admin** and open the company.
3. **Company > Set Up Users and Passwords > Set Up Users > Add User.** Name it,
   e.g. ``beancount``, **leave the password blank**, and give it access to all areas.
4. **Exit QuickBooks completely**, so the user is written to the file on disk.
5. Get the credential. ``UID`` is ``python -c "print('beancount'.encode().hex())"``.
   ``PWD`` is the blank-password output of the *SQL Anywhere database UID & PWD
   Calculator* in Thegrideon QuickBooks Forensics. It is about 68 hex characters
   of the form ``07...00``.

The same user name gives the same ``UID`` and ``PWD`` in every company file. If a
file's **Admin** user has no password, use Admin directly.

QBB backups
-----------

A ``.QBB`` holds a text header, then a zlib-compressed company file. The library
finds the zlib stream after the ``Compression_Format=Deflate_ZLIB;`` marker, and
accepts only a stream that inflates to a SQL Anywhere file (magic bytes at
offset 20). The inflated file is then converted like a ``.QBW``.

**Not yet tested against a real backup.** No ``.QBB`` sample was available. The
code path has a synthetic container test, and the extracted company file is
checked to be identical to the one it was built from.

Output
------

Data tables
~~~~~~~~~~~

Every selected base table, owned by ``QBAdminGroup`` (typically **297 tables**).
Names mirror the source. Tables from other owners are prefixed, for example
``QBAdminGroup_abmc_invoice_lineitem``. The business data is in the ``abmc_*``
tables. Tables prefixed ``I_``, ``lm_``, ``tq_``, ``perf_`` and ``patch_`` are
QuickBooks internals and are usually small.

Types and keys
~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 50 50

   * - SQL Anywhere domain
     - SQLite declared type
   * - ``integer``, ``smallint``, ``bigint``, ``bit``, ...
     - ``INTEGER``
   * - ``float``, ``double``
     - ``REAL``
   * - ``decimal``, ``numeric``, dates, times, ``char``, ``varchar``, ...
     - ``TEXT``
   * - ``binary``, ``varbinary``, ``long binary``, ...
     - ``BLOB``

``DECIMAL`` and ``NUMERIC`` are declared ``TEXT`` on purpose. SQLite's
``NUMERIC`` affinity would turn ``"12.50"`` into a float and lose the exact value.

Primary keys are recorded in ``_qbw_columns`` and enforced by a unique index
``<table>__pk`` when the copy is clean. The index is not a table constraint, so a
bad row cannot abort a table load.

Provenance tables
~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Table
     - Contents
   * - ``_qbw_meta``
     - Tool version, UTC time, source name and size, **SHA-256 of the source and of the working copy**, backend, mode, filters, views created and skipped, problem list, table and row totals.
   * - ``_qbw_tables``
     - One row per table: owner, SQLite name, **status**, source row count, rows read, rows written, rejected rows, primary key, error, note.
   * - ``_qbw_columns``
     - Column position, source domain, width, scale, nullability, key flag and declared SQLite type.
   * - ``_qbw_rejected``
     - Rows skipped in lenient mode: table, row number, SQLite error (capped at 10,000 rows).

Table statuses
~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 20 65 15

   * - Status
     - Meaning
     - Problem
   * - ``OK``
     - All rows read and written, and the count matches the source.
     - no
   * - ``FILTERED``
     - Copied with ``--limit`` or ``--where``, so the count check was skipped.
     - no
   * - ``SKIPPED``
     - Not selected by ``--tables`` or ``--exclude``.
     - no
   * - ``REJECTED_ROWS``
     - Some rows could not be written. They are logged in ``_qbw_rejected``.
     - **yes**
   * - ``PARTIAL``
     - The read failed partway. Rows read before the failure are kept.
     - **yes**
   * - ``FAILED``
     - The table could not be queried, or nothing could be written.
     - **yes**
   * - ``MISMATCH``
     - Rows read or written do not match the source ``COUNT(*)``.
     - **yes**

Entity views
~~~~~~~~~~~~

When their source tables exist, these views are created. Each is validated after
creation, and is dropped and reported as skipped if it does not query.

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * - View
     - Built from
     - Notes
   * - ``v_invoice_lines``
     - ``abmc_invoice_header`` joined to ``abmc_invoice_lineitem`` on ``transaction_id``, with customer and item names
     - One row per invoice line. Lines with no item have ``item_name`` NULL.
   * - ``v_customers``
     - ``abmc_customer_user``
     - Names with the deleted and hidden flags.
   * - ``v_items``
     - ``abmc_item_user``
     - Names, tax code and asset and COGS accounts.
   * - ``v_accounts``
     - ``abmc_account_user``
     - Names with the deleted flag.

Only invoices are covered so far. Bills, checks, sales receipts, payments and
journal entries need the same kind of join, verified against data first.

Merge mode
~~~~~~~~~~

``mode="merge"`` (``--mode merge``) starts from the existing output and upserts
each table by its primary key. A run repeated on an unchanged file gives the same
counts. Rows **deleted** in QuickBooks are **not** removed from the output,
because the tool reads full tables and does not detect deletions.

Text encoding
-------------

The database declares ``windows-1252``, but QuickBooks stores a **mix of UTF-8
and cp1252** bytes in the same database. Currency symbols such as ``Kč`` and ``€``
are UTF-8, and accented names such as ``Québec`` are cp1252. The driver decodes
as latin-1, which maps every byte and never raises. Each string is then re-read
as UTF-8 when valid, and as cp1252 otherwise. Without this, tables with such
bytes silently export zero rows.

Notes and caveats
-----------------

- **The original file is never modified.** The engine runs on a read-only copy.
  The only change to the company file is the QuickBooks user you created. Delete
  that user afterwards if you do not want it.
- **Logging in runs QuickBooks' login procedure** on the copy, which may touch a
  last-login timestamp. The copy, not the evidence file, is what the engine touches.
- **Different user names give different credentials.** Keep ``beancount`` to reuse one credential.
- **The ``.DSN`` and ``.ND`` files** beside a ``.QBW`` belong to QuickBooks. This
  library does not produce them.
- **SQL Anywhere version.** This targets the SQL Anywhere **17** format used by
  QuickBooks 2018 and later. Older files need the matching engine and password form.
- **Output is written to a ``.<name>.<id>.partial`` file beside ``out``**, renamed
  only on completion. A fatal error leaves any existing output untouched.
- **Engine process.** Only one engine serves a conversion at a time. The driver
  stays loaded for the rest of the process, so the first conversion fixes which
  SQL Anywhere install the process uses.

Not implemented
---------------

- **Page CRC verification.** SQL Anywhere's page checksum is not documented, so
  it could not be implemented and verified.
- **Deletion detection in merge mode** (see above).
- **Incremental reads from a change cursor.** Merge re-reads full tables.
- **AI-agent redaction and audit logging.** Out of scope for an extractor.

Prior art
---------

- **Thegrideon QuickBooks Forensics**: a commercial read-only tool that parses the
  ``.QBW`` and ``.QBB`` format directly. This library uses only its "SQL Anywhere
  database UID & PWD Calculator", to get the blank-password credential.
- **OpenQBW** (Sigilweaver): an open-source reader for the same format. Its
  Python package backs the experimental ``openqbw`` backend.
- **OpenSQLAnywhere**: a clean-room reader for the SQL Anywhere page format.
- **QBExtract** (MIT): uses the official QuickBooks SDK on a running company file,
  and exports curated entities as JSON.
- **Intuit QuickBooks ODBC and Custom Reporting**: the supported route, but only
  for Enterprise, and it needs QuickBooks running.

See ``docs/literature-review.md`` for the comparison that informed these features.

Development
-----------

.. code-block:: console

    uv sync
    uv run pytest
    uv run ruff check . && uv run ruff format --check .

The unit tests use fake source cursors and in-memory SQLite. They need no
QuickBooks file and no SQL Anywhere install. The integration tests run the real
engine on a copy of the sample file in ``qb-sandbox/``. They run only when SQL
Anywhere 17 is installed and both ``QBW_TEST_UID`` and ``QBW_TEST_PWD`` are set.
The openqbw test runs only when ``openqbw`` is importable.

Requirements
------------

- **Windows** with **SQL Anywhere 17** for the ``sa`` backend.
- **Python 3.9 or later.** The CI tests 3.9 and 3.15. Versions in between are
  best effort. The core uses only the standard library.
- **Optional:** ``openqbw`` (Python 3.10+) for ``--backend openqbw``.

The install folder is found from ``%ProgramFiles%``, ``%ProgramFiles(x86)%`` or
``SQLANY17``. Override it with ``sa_dir`` or ``--sa-dir``.

Changes
-------

See ``CHANGELOG.rst``.

License
-------

MIT. See ``LICENSE.txt``.
