qbw-to-sqlite
=============

Convert a QuickBooks Desktop company file (``.QBW``, or a ``.QBB`` backup) into a
SQLite database that any SQLite tool can read: pandas, Metabase or ``sqlite3``.
The conversion copies every user table with its declared column types and primary
keys. It checks row counts, then adds entity views and provenance tables. It needs
neither the QuickBooks application nor the Intuit SDK.

    **Scope.** Use this only on company files you are authorized to examine: your
    own data, or archival, analysis or forensic review you are authorized to
    perform. It does not crack or recover unknown passwords. It relies on a
    QuickBooks user *you* create with no password.

The package is ``minchinweb.scripts.qbw_to_sqlite``. It is a library first. The
``qbw-to-sqlite`` command wraps the same API.

Install
-------

.. code-block:: console

    pip install minchinweb.scripts.qbw-to-sqlite
    pip install "minchinweb.scripts.qbw-to-sqlite[openqbw]"   # optional backend

The default ``sa`` backend needs **SQL Anywhere 17** on Windows. The package does
not install it. SAP offers the Developer Edition free of charge.

Use as a library
----------------

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

``convert()`` returns a ``ConversionReport`` with the fields ``output``, ``tables``
(one ``TableResult`` per table, with its status and counts), ``views``,
``views_skipped``, ``problems``, ``source_sha256`` and ``meta``. The ``ok`` and
``rows_written`` properties summarize them.

``convert()`` raises ``QBWError`` when the conversion cannot run at all: the file is
missing, the output path is refused, the engine will not start, or the login fails.
It writes no output in those cases. It raises ``ValueError`` for an invalid
argument, such as ``mode="append"``. A report with problems still means the
conversion finished, so check ``report.ok``.

Logging uses the standard ``logging`` module, under the logger
``minchinweb.scripts.qbw_to_sqlite``. The library attaches no handler and configures
no logging, so importing it changes nothing in the process.

The docstring of ``convert`` lists every argument.

Command line
------------

.. code-block:: console

    qbw-to-sqlite --qbw "Your Company.QBW" --out your_company.sqlite ^
        --uid 6265616e636f756e74 --pwd 07ec23...2fc00

From a checkout, run ``python qbw_to_sqlite.py`` with the same arguments, without
installing. The command line uses the public API, so
``minchinweb/scripts/qbw_to_sqlite/cli.py`` shows how to call the library.

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Option
     - Meaning
   * - ``--qbw``
     - ``.QBW`` or ``.QBB`` company file. Read only, copied, never modified.
   * - ``--out``
     - SQLite file to create. Refused if it is a QuickBooks file. Replaced atomically, even when the run finishes with problems.
   * - ``--backend``
     - ``sa`` (default) or ``openqbw`` (experimental).
   * - ``--uid`` / ``--pwd``
     - SQL Anywhere credential (``sa`` backend).
   * - ``--mode``
     - ``replace`` (default) builds a fresh output and overwrites the old one. ``merge`` upserts by primary key into the existing output. Deleted rows are kept, and tables without a primary key gain duplicates. See `Re-running on an updated file`_.
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

Exit codes
~~~~~~~~~~

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
     - Catalog, indexes, transactions and line items only. **Experimental.** On the sample file it found 643 catalog entries and **0 transactions and 0 line items**, so it does not replace ``sa``.

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
   backup is extracted first (see `QBB backups`_). The library never opens the
   original for writing.
2. **Start a private, read-only engine:** ``dbeng17.exe -r -x tcpip(port=...)``.
   ``-r`` makes the whole server read-only. The library waits for ``Now accepting
   requests`` before it connects.
3. **Connect** with ``sqlanydb``, the driver bundled with SQL Anywhere. The library
   sets the driver's environment variables only for the import and the connection,
   then restores them.
4. **Read the catalog.** ``SYS.SYSTAB`` lists the base tables. ``SYS.SYSCOLUMN``
   and ``SYS.SYSDOMAIN`` give each column's type, width, scale, nullability and key
   flag. The library skips system owners (``SYS``, ``dbo``, ...). Real data belongs
   to **QBAdminGroup**.
5. **Copy each table** in batches, using the declared types and primary keys from
   the catalog. It checks each row count against ``COUNT(*)`` on the source.
6. **Create entity views** whose source tables exist.
7. **Write provenance tables**, then rename the output into place. A failed run
   leaves any existing output untouched.

Getting the UID and PWD
-----------------------

QuickBooks stores each user's database password as a derived value::

    PWD = "07" + SHA-256( prepared(password) ) + "00"        (SQL Anywhere 17 form)
    UID = the QuickBooks user name encoded as ASCII hex
          e.g. "Admin"     -> 41646d696e
               "beancount" -> 6265616e636f756e74

The ``prepared(...)`` step mixes in an Intuit secret that cannot be reproduced
offline. The exception is the empty password: its value depends only on the user
name. So make a QuickBooks user with **no password**, and use its blank-password
credential.

1. Make the file writable: ``attrib -r "Your Company.QBW"``.
2. In QuickBooks, log in as **Admin** and open the company.
3. Choose **Company > Set Up Users and Passwords > Set Up Users > Add User.** Name
   the user, e.g. ``beancount``, **leave the password blank**, and grant access to
   all areas.
4. **Exit QuickBooks completely**, so the new user is written to disk.
5. Get the credential. ``UID`` is ``python -c "print('beancount'.encode().hex())"``.
   ``PWD`` is the blank-password output of the *SQL Anywhere database UID & PWD
   Calculator* in Thegrideon QuickBooks Forensics. It is about 68 hex characters of
   the form ``07...00``.

The same user name gives the same ``UID`` and ``PWD`` in every company file. If a
file's **Admin** user has no password, use Admin directly.

QBB backups
-----------

A ``.QBB`` holds a text header followed by a zlib-compressed company file. The
library finds the zlib stream after the ``Compression_Format=Deflate_ZLIB;`` marker.
It accepts only a stream that inflates to a SQL Anywhere file (magic bytes at
offset 20), then converts the result like a ``.QBW``.

**Not yet tested on a real backup.** No ``.QBB`` sample was available. A synthetic
container test covers this code path, and the extracted company file matches the
one the test built it from.

What the output contains
------------------------

Data tables
~~~~~~~~~~~

Every selected base table, owned by ``QBAdminGroup`` (typically **297 tables**).
Names mirror the source. Tables from other owners get a prefix, for example
``QBAdminGroup_abmc_invoice_lineitem``. The business data lives in the ``abmc_*``
tables. Tables prefixed ``I_``, ``lm_``, ``tq_``, ``perf_`` and ``patch_`` hold
QuickBooks internals and are usually small.

Column types and keys
~~~~~~~~~~~~~~~~~~~~~

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

The library maps ``DECIMAL`` and ``NUMERIC`` to ``TEXT`` on purpose. SQLite's
``NUMERIC`` affinity would convert ``"12.50"`` to a float and lose the exact value.

Primary keys go into ``_qbw_columns``. They are enforced by a unique index,
``<table>__pk``, when the copy is clean. The index is not a table constraint, so
one bad row cannot abort a table load.

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

The library creates these views when their source tables exist. It validates each
view after creation. A view that fails to query is dropped and reported as skipped.

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

Only invoices are covered so far. To add bills, checks, sales receipts, payments and
journal entries, write the same kind of join, and verify it against the data first.

Re-running on an updated file
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The result of running the conversion again on an existing ``--out`` file depends on
``--mode``. The default is ``replace``.

``replace`` (default)
    Builds a new output from the current company file, then renames it over the old
    one. The old file is not read, compared or kept. If the run finishes with
    problems (exit status 3), the new output still replaces the old one. A fatal
    error leaves the old file untouched. To keep earlier exports, give each run its
    own name, for example ``--out your_company_2026-11-20.sqlite``, and compare the
    snapshots.

``merge`` (``--mode merge``)
    Starts from the existing output and upserts each selected table by primary key.

    - Changed rows are overwritten, and new rows are added. For tables with a
      primary key, a repeat run on an unchanged file gives the same counts.
    - The tool reads full tables and does not detect deletions. Rows **deleted** in
      QuickBooks are **not** removed.
    - If a table's column names change, the table is dropped and recreated from the
      new file, so its earlier rows are lost. Its note reads ``recreated: columns
      changed since last run``.
    - Unselected tables keep their earlier contents.
    - **Tables with no primary key gain duplicate rows on every run.** Only keyed
      tables are upserted. In one test, two identical merges of a two-row keyless
      table produced four rows. Do not merge a dataset that includes keyless tables
      until this is fixed.
    - The ``_qbw_*`` provenance tables are rebuilt on each run, so they describe only
      the latest run. The output does not record which run wrote which row, and
      nothing checks that the new company file is the same file as before.

Text encoding
-------------

The database declares ``windows-1252``, but QuickBooks stores a **mix of UTF-8 and
cp1252** bytes in the same database. Currency symbols such as ``Kč`` and ``€`` are
UTF-8. Accented names such as ``Québec`` are cp1252. The driver decodes everything
as latin-1, which maps every byte and never raises. The library then re-reads each
string as UTF-8 when the bytes are valid UTF-8, and as cp1252 otherwise. Without
this step, tables with such bytes silently export zero rows.

Caveats
-------

- **The original file is never modified.** The engine runs on a read-only copy. The
  only change to the company file is the QuickBooks user you create. Delete that
  user afterwards if you do not want it.
- **Logging in runs QuickBooks' login procedure on the copy,** which may update a
  last-login timestamp. The engine touches the copy, not the evidence file.
- **Different user names give different credentials.** Use the same name, such as
  ``beancount``, to reuse one credential.
- **The ``.DSN`` and ``.ND`` files** beside a ``.QBW`` belong to QuickBooks. This
  library does not produce them.
- **SQL Anywhere version.** The library targets the SQL Anywhere **17** format that
  QuickBooks 2018 and later use. Older files need the matching engine and password
  form.
- **Output goes to a ``.<name>.<id>.partial`` file beside ``out``.** The library
  renames it only on completion. A fatal error leaves any existing output untouched.
- **One engine serves one conversion at a time.** The driver stays loaded for the
  life of the process, so the first conversion fixes which SQL Anywhere install the
  process uses.

Known gaps
----------

- **Page CRC verification.** SQL Anywhere's page checksum is undocumented, so it has
  not been implemented or verified.
- **Deletion detection in merge mode** (see above).
- **Incremental reads from a change cursor.** Merge re-reads full tables.
- **AI-agent redaction and audit logging.** Out of scope for an extractor.

Related tools
-------------

- **Thegrideon QuickBooks Forensics**: a commercial read-only tool that parses the
  ``.QBW`` and ``.QBB`` formats directly. This library uses only its "SQL Anywhere
  database UID & PWD Calculator" to get the blank-password credential.
- **OpenQBW** (Sigilweaver): an open-source reader for the same format. Its Python
  package backs the experimental ``openqbw`` backend.
- **OpenSQLAnywhere**: a clean-room reader for the SQL Anywhere page format.
- **QBExtract** (MIT): uses the official QuickBooks SDK on a running company file,
  and exports curated entities as JSON.
- **Intuit QuickBooks ODBC and Custom Reporting**: the supported route, but only for
  Enterprise, and it requires QuickBooks to be running.

See ``docs/literature-review.md`` for the comparison that informed these features.

Development and tests
---------------------

.. code-block:: console

    uv sync
    uv run pytest
    uv run ruff check . && uv run ruff format --check .

The unit tests use fake source cursors and in-memory SQLite. They need neither a
QuickBooks file nor a SQL Anywhere install. The integration tests run the real
engine on a copy of the sample file in ``qb-sandbox/``. They run only when SQL
Anywhere 17 is installed and both ``QBW_TEST_UID`` and ``QBW_TEST_PWD`` are set. The
openqbw test runs only when ``openqbw`` is importable.

Requirements
------------

- **Windows** with **SQL Anywhere 17** for the ``sa`` backend.
- **Python 3.9 or later.** CI tests 3.9 and 3.15. Versions in between are best
  effort. The core uses only the standard library.
- **Optional:** ``openqbw`` (Python 3.10+) for ``--backend openqbw``.

The library finds the install folder under ``%ProgramFiles%``,
``%ProgramFiles(x86)%`` or ``SQLANY17``. Override it with ``sa_dir`` or
``--sa-dir``.

Changelog
---------

See ``CHANGELOG.rst``.

License
-------

MIT. See ``LICENSE.txt``.
