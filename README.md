# qbw_to_sqlite — Convert a QuickBooks `.QBW` to SQLite

Converts a QuickBooks Desktop company file (`.QBW`) into a plain SQLite database
you can query with any SQLite tool, pandas, Metabase, `sqlite3`, etc. It copies
every user table out of the file faithfully — no QuickBooks application, no
Intuit SDK, and no paid forensic tool required at conversion time.

> **Scope / authorization.** This is for working with **your own** company files
> (data portability, analysis, archival, forensic review you're authorized to
> perform). It does not crack or recover unknown passwords — it relies on a
> QuickBooks user *you* create with no password. Only use it on files you own or
> are authorized to access.

---

## Background: what a `.QBW` actually is

A modern QuickBooks Desktop company file is **not** a proprietary blob. Since
QuickBooks 2018 it is a **SAP SQL Anywhere 17 database** (earlier years used
SQL Anywhere 11/12/16). QuickBooks ships and runs the SQL Anywhere engine itself
— that's the `QBDBMgrN.exe` "Database Server Manager" process and the
`QB SQL Anywhere` files under `C:\Program Files (x86)\Common Files\Intuit`.

Two facts make direct conversion possible:

1. **The database file is not encrypted.** You can start a standalone SQL
   Anywhere engine (`dbeng17.exe`) directly on a copy of the `.QBW` with no
   encryption key. It mounts and starts like any ordinary SQL Anywhere database.

2. **Access is gated only by a normal database login.** QuickBooks protects the
   data with SQL Anywhere user accounts, not file encryption. So the entire
   problem reduces to: *get a valid username/password, then run SQL.*

The only genuinely hard part is that login — see
[The credential](#the-credential-the-only-hard-part).

---

## How the script works

```
.QBW  ──copy──►  temp copy  ──dbeng17 -r──►  private read-only engine
                                                   │  (TCP on a private port)
                                                   ▼
                                       sqlanydb (SQL Anywhere's own driver)
                                                   │  enumerate + SELECT *
                                                   ▼
                                            SQLite output file
```

Step by step (`main()` in `qbw_to_sqlite.py`):

1. **Copy the file.** The original `.QBW` is never opened for writing. We copy it
   to a temp file and work on the copy.
2. **Start a private engine, read-only.** We launch
   `dbeng17.exe -r -x tcpip(port=…) -n <server> <copy> -n <db>`:
   - `-r` starts the **whole server read-only** — nothing can modify the data
     (forensically safe).
   - `-x tcpip(port=…)` listens on a private, non-default TCP port so it never
     collides with QuickBooks' own running engine.
   - A unique server/database name keeps it isolated.
   The script waits for `Now accepting requests` in the engine log before
   connecting, and tears the engine down (and deletes the copy) on exit.
3. **Connect** with `sqlanydb`, the pure-Python driver that **ships inside the
   SQL Anywhere install** (`SDK\Python\sqlanydb.py` + `dbcapi.dll`). No `pip`
   install is needed; the script locates and wires it up automatically.
4. **Enumerate user tables** from the catalog:
   ```sql
   SELECT u.user_name, t.table_name
   FROM SYS.SYSTAB t JOIN SYS.SYSUSER u ON t.creator = u.user_id
   WHERE t.table_type = 1        -- base tables only
   ```
   System owners (`SYS`, `dbo`, `rs_systabgroup`, …) are skipped. In practice all
   real QuickBooks data is owned by **`QBAdminGroup`**.
5. **Copy each table** into SQLite: `SELECT *`, create a matching SQLite table,
   and `executemany` the rows in batches. Values are coerced sensibly (see
   [Data types & encoding](#data-types--encoding)).

### Why it works

QuickBooks relies on **application-level** access control (the QuickBooks UI
enforces roles/permissions) layered on top of a few SQL Anywhere database users.
At the raw database level those users can read the tables directly. Because the
file is unencrypted, any SQL Anywhere 17 engine can open it — we just supply it a
valid login and read. QuickBooks itself doesn't need to be running.

---

## The credential (the only hard part)

You cannot type a human QuickBooks password here. QuickBooks stores each user's
database password as a derived value:

```
PWD = "07" + SHA-256( prepared(password) ) + "00"        (SQL Anywhere 17 form)
UID = the QuickBooks user name encoded as ASCII hex
      e.g. "Admin"     -> 41646d696e
           "beancount" -> 6265616e636f756e74    ( "beancount".encode().hex() )
```

The `prepared(...)` step mixes in an **Intuit-specific secret** that is not
public and cannot be reproduced offline — **except for the empty password**,
whose result is a fixed value that depends only on the user name. That is the
loophole this whole approach rests on.

So the recipe is: **make a QuickBooks user with no password**, and use its
blank-password database credential.

### Creating the no-password user

1. **Make the file writable if needed.** These files are often marked read-only.
   A read-only `.QBW` can't be checkpointed, so a newly created user never gets
   written to disk. Clear it: `attrib -r "Your Company.QBW"` (or Properties →
   uncheck Read-only).
2. In QuickBooks, log in as **Admin** and open the company.
3. **Company → Set Up Users and Passwords → Set Up Users → Add User.**
   - Name it e.g. `beancount`.
   - **Leave the password blank.** (Non-admin users can be passwordless even when
     "Customer Credit Card Protection" forbids a blank *Admin* password.)
   - Give it access to **all areas** so it can read every table.
4. **Exit QuickBooks completely** (not just *Close Company*). This checkpoints the
   new user into the `.QBW` on disk — confirm the file's modified-time changed.
5. Get the credential:
   - `UID` = `python -c "print('beancount'.encode().hex())"` → `6265616e636f756e74`
   - `PWD` = the **blank-password** output of the *"SQL Anywhere database UID & PWD
     Calculator"* in Thegrideon QuickBooks Forensics (leave the password field
     empty). It is a ~68-hex-char string of the form `07…00`.

Because the blank-password value depends only on the **user name**, the *same*
`beancount` user gives the *same* `UID`/`PWD` in **every** company file — create
it once per file, reuse one credential for all.

> If a company file's **Admin** user genuinely has no password, you can skip the
> new user and use Admin directly: `--uid 41646d696e --pwd <Admin blank value>`.

---

## Requirements

- **Windows** with **SQL Anywhere 17** installed. It ships with the SQL Anywhere
  Developer Edition (free) and with some QuickBooks installs. The script needs
  `…\SQL Anywhere 17\Bin64\dbeng17.exe` and `dbcapi.dll`, plus
  `…\SQL Anywhere 17\SDK\Python\sqlanydb.py`.
- **Python 3.8+** — standard library only (the SQL Anywhere driver is loaded from
  the install, not from PyPI).

The install dir is auto-detected from `%ProgramFiles%` / `%ProgramFiles(x86)%` /
`$SQLANY17`; override with `--sa-dir` if needed.

### Installing on a new machine

**The one thing you must install is SQL Anywhere 17 — it is not pip-installable.**
The script needs two native pieces from it:

- `…\SQL Anywhere 17\Bin64\dbeng17.exe` — the engine that mounts the `.QBW`.
- `…\SQL Anywhere 17\Bin64\dbcapi.dll` — the C client the Python driver calls.

Get SQL Anywhere 17 by installing **SAP SQL Anywhere 17** (the **Developer
Edition is free**, via an SAP account; the "Client/runtime" install is enough —
you need the engine, the client DLL, and `SDK\Python\sqlanydb.py`). A machine that
already runs QuickBooks Desktop 2018+ *has* a SQL Anywhere 17 runtime, but it may
not expose the standalone `dbeng17.exe`/SDK, so installing the SAP package is the
reliable route.

**Can you get the Python driver from PyPI?** Yes — `pip install sqlanydb` works —
**but it does not help here.** `sqlanydb` is a pure-Python *ctypes wrapper*; it
still loads the native `dbcapi.dll` at runtime (without it you get
`Could not load dbcapi`). Since SQL Anywhere 17 already bundles `sqlanydb.py` in
`SDK\Python`, **this script uses the bundled copy automatically and you don't need
pip at all.** Installing from PyPI would still require the SQL Anywhere native
client, so it buys you nothing.

So, the full shopping list for a fresh Windows box:

| Component | How | pip-able? |
|---|---|---|
| SQL Anywhere 17 (engine `dbeng17.exe` + `dbcapi.dll` + `sqlanydb.py`) | SAP SQL Anywhere 17 Developer Edition (free) | **No** |
| Python 3.8+ | python.org | — |
| `sqlanydb` driver | comes with SQL Anywhere (used automatically); `pip install sqlanydb` optional but still needs the DLL above | wrapper only |
| Thegrideon QuickBooks Forensics | only to read the **blank-password PWD** from its calculator, **once** per new user name — not needed at conversion time, and not needed at all if you reuse a user name whose PWD you already have (e.g. `beancount`, `Admin`) | n/a |

Nothing else — no ODBC DSN setup, no QuickBooks running, no other Python packages.

---

## Usage

```bat
python qbw_to_sqlite.py ^
    --qbw "C:\path\to\Your Company.QBW" ^
    --out "C:\path\to\your_company.sqlite" ^
    --uid 6265616e636f756e74 ^
    --pwd 07ec23...2fc00
```

Options (`--help` for all):

| Option | Meaning |
|---|---|
| `--qbw` | path to the `.QBW` company file (read-only; copied, never modified) |
| `--out` | SQLite file to create |
| `--uid` / `--pwd` | the SQL-Anywhere credential (see above) |
| `--sa-dir` | SQL Anywhere 17 install dir (auto-detected if omitted) |
| `--port` | private TCP port for the temp engine (default 29179) |
| `--server` | temp engine/server name |
| `--work-dir` | where to put the temp copy (default: system temp) |
| `--keep-copy` | keep the temp `.QBW` copy instead of deleting it |

---

## Output

- A standalone **SQLite** database containing every base table owned by
  `QBAdminGroup` (typically **297 tables**). System catalog tables are excluded.
- SQLite table names mirror the source; non-`DBA` owners are prefixed with the
  owner, e.g. `QBAdminGroup_abmc_invoice_lineitem`.
- The real business data lives in the **`abmc_*`** tables (accounts, transactions,
  invoices, vendors, currencies, audit trail, …). Tables prefixed `I_`, `lm_`,
  `tq_`, `perf_`, `patch_`, etc. are QuickBooks internals and are usually small or
  empty — they're exported too, for completeness.
- The script prints each table and its row count, then a final
  `[done] N tables, M rows -> <file>` line. Example runs ranged from ~5k rows
  (small company) to ~150k rows.

### Data types & encoding

- Numbers come across as integers/floats; **`DECIMAL`/`NUMERIC` are stored as
  text** to preserve exact precision (money). Dates/times are ISO strings; binary
  columns are stored as SQLite BLOBs.
- **Text encoding is deliberately defensive.** The database declares the
  `windows-1252` character set, but QuickBooks actually stores a **mix of UTF-8
  and cp1252 bytes** in the same database (e.g. currency symbols like `Kč`, `€`
  are UTF-8; accented names like `Québec`, `Ministère` are cp1252). No single
  charset decodes all of it. The script tells the driver to decode as **latin-1**
  (which maps all 256 byte values and never raises), then re-interprets each
  string per value: decode as UTF-8 when valid, else cp1252 (`fix_text()`).
  Without this, any table containing such bytes would silently export **zero
  rows**.

---

## Prior art of note

- **Thegrideon QuickBooks Forensics** (`thegrideon.com`) — a commercial,
  read-only forensic tool that parses the `.QBW`/`.QBB` file format **directly**,
  with no SQL Anywhere engine and no login. That's a different (and much larger)
  approach than this script; we use its **"SQL Anywhere database UID & PWD
  Calculator"** only to obtain the blank-password credential. Its "QuickBooks
  Internals" pages document the hex-username / `07…00` password scheme.
- **Intuit QuickBooks ODBC / "Custom Reporting"** — the *supported* path, but it's
  a QuickBooks **Enterprise** feature, requires QuickBooks running, and authorizes
  through the app rather than giving direct database access.
- **QODBC (FLEXquarters)** — a popular third-party ODBC driver, but it talks to
  QuickBooks via the **SDK / qbXML**, not the raw database.
- **Data-recovery vendors** (e.g. Gillware) describe opening damaged `.QBW` files
  with "direct Sybase tools that read the database independent of QuickBooks" —
  the same underlying fact this script exploits.
- **Full-disclosure, 2016 (seclists)** — a report that QuickBooks runs startup SQL
  with `DBA` privileges and that a modified file can alter the login procedure;
  useful background on the database's internal users (`metadata_user`,
  `backup_user`) and the `-sbx` sandbox hardening Intuit later added.

---

## Notes, caveats & cleanup

- **The conversion never modifies the original file** — it works on a read-only
  copy mounted by a read-only engine. The only change to your data is the
  QuickBooks **user you created** in QuickBooks itself. You can delete that user
  (and re-apply read-only / Customer Credit Card Protection) once you're done.
- **Connecting does run QuickBooks' login procedure** server-side (it may touch a
  last-login timestamp). If strict forensic non-alteration matters, note that the
  copy — not your evidence file — is what the engine touches.
- **Per-file credential:** different user *names* produce different `UID`/`PWD`.
  Keep the user name consistent (`beancount`) to reuse one credential everywhere.
- **The `.DSN` / `.ND` files** that appear next to a `.QBW` are QuickBooks' own
  (written by `QBDBMgrN`), not produced by this script. The live hosted engine
  unloads databases when the QuickBooks UI closes, which is why the script mounts
  its own copy rather than connecting to QuickBooks' engine.
- **SQL Anywhere version:** this targets the SQL Anywhere **17** format used by
  QuickBooks 2018+. Older company files use earlier SQL Anywhere versions and
  would need the matching engine (`dbeng11`/`dbeng16`) and the corresponding
  password form.
