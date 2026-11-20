#!/usr/bin/env python
r"""
qbw_to_sqlite.py  --  Convert a QuickBooks .QBW company file to a SQLite database.

HOW IT WORKS
------------
A modern QuickBooks .QBW file is just a (non-encrypted) Sybase/SAP SQL Anywhere 17
database.  This script:

    1. Copies the .QBW to a temporary working file (the original is never touched).
    2. Starts a private, READ-ONLY SQL Anywhere 17 engine on that copy.
    3. Connects over TCP/IP using the SQL-Anywhere-level UID/PWD for the file.
    4. Copies every user table into a fresh SQLite database.
    5. Shuts the engine down and deletes the working copy.

You must supply the SQL-Anywhere-level UID and PWD for the file.  These are NOT
your normal QuickBooks login -- QuickBooks derives an SA password from each
QuickBooks user's password using an Intuit-specific transform, so you cannot
type a human password here.  The reliable way to get a usable login:

    1. In QuickBooks, create a NEW user with NO password (e.g. "beancount"),
       giving it access to all areas.  A blank password is required because the
       transform for a non-blank password cannot be reproduced offline.
    2. EXIT QuickBooks completely so the new user is checkpointed into the .QBW
       on disk.  If the .QBW is marked read-only, clear that first (attrib -r),
       or the new user is never written and the login below will fail.
    3. UID = the user name as ASCII hex.  "beancount" -> 6265616e636f756e74
       (python:  "beancount".encode().hex() ).
    4. PWD = the blank-password value from the "SQL Anywhere database UID & PWD
       Calculator" in Thegrideon QuickBooks Forensics (leave the password field
       empty).  It is "07" + SHA-256 + "00" (~68 hex chars) and depends only on
       the user name, so the SAME "beancount"/no-password user yields the SAME
       UID/PWD in every company file.

Text encoding note: QuickBooks mixes UTF-8 and windows-1252 bytes in the same
database; this script decodes defensively (see fix_text) so no table is dropped.

REQUIREMENTS
------------
    * Windows with "SQL Anywhere 17" installed (ships with QuickBooks /
      the SQL Anywhere developer edition).  Needs dbeng17.exe + dbcapi.dll.
    * Python 3.8+  (only the standard library, plus SQL Anywhere's own
      sqlanydb.py which is located automatically).

USAGE
-----
    python qbw_to_sqlite.py --qbw "Sample Company.QBW" --out sample.sqlite ^
        --uid 6265616e636f756e74 --pwd 07ec23...2fc00

    # see all options
    python qbw_to_sqlite.py --help
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# --------------------------------------------------------------------------- #
# Locating SQL Anywhere 17
# --------------------------------------------------------------------------- #

def find_sqlany_dir(explicit: str | None) -> Path:
    """Return the SQL Anywhere 17 install root (the folder containing Bin64)."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("SQLANY17")
    if env:
        candidates.append(Path(env))
    for pf in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if pf:
            candidates.append(Path(pf) / "SQL Anywhere 17")
    for c in candidates:
        if c and (c / "Bin64" / "dbeng17.exe").exists():
            return c
    raise SystemExit(
        "Could not find 'SQL Anywhere 17' (Bin64\\dbeng17.exe).\n"
        "Install it, or pass --sa-dir \"C:\\Program Files\\SQL Anywhere 17\"."
    )


def load_sqlanydb(sa_dir: Path):
    """Import SQL Anywhere's bundled sqlanydb driver, wiring up dbcapi.dll."""
    bin64 = sa_dir / "Bin64"
    sdk_py = sa_dir / "SDK" / "Python"
    dbcapi = bin64 / "dbcapi.dll"
    if not dbcapi.exists():
        raise SystemExit(f"dbcapi.dll not found at {dbcapi}")
    # Make the native DLL and its dependencies loadable.
    os.environ["SQLANY_API_DLL"] = str(dbcapi)
    os.environ["PATH"] = str(bin64) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(bin64))
    # Prefer the sqlanydb.py that ships with the install.
    if (sdk_py / "sqlanydb.py").exists():
        sys.path.insert(0, str(sdk_py))
    try:
        import sqlanydb  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise SystemExit(
            "Could not import 'sqlanydb'.\n"
            f"Looked in: {sdk_py}\n"
            f"Underlying error: {exc!r}"
        )
    return sqlanydb


# --------------------------------------------------------------------------- #
# Engine lifecycle
# --------------------------------------------------------------------------- #

class SQLAnywhereEngine:
    """Starts a private read-only dbeng17 on a working copy of the .QBW."""

    def __init__(self, sa_dir: Path, qbw_copy: Path, server: str, port: int,
                 dbname: str, log_path: Path):
        self.exe = sa_dir / "Bin64" / "dbeng17.exe"
        self.qbw_copy = qbw_copy
        self.server = server
        self.port = port
        self.dbname = dbname
        self.log_path = log_path
        self.proc: subprocess.Popen | None = None

    def start(self, timeout: float = 90.0) -> None:
        cmd = [
            str(self.exe),
            "-r",                                   # whole server read-only (forensic-safe)
            "-o", str(self.log_path),               # message log
            "-x", f"tcpip(port={self.port})",       # listen on a private TCP port
            "-n", self.server,                      # server (engine) name
            str(self.qbw_copy),
            "-n", self.dbname,                      # database name
        ]
        print(f"[engine] starting: {self.exe.name} on port {self.port} "
              f"(server={self.server}, db={self.dbname})")
        if self.log_path.exists():
            self.log_path.unlink()
        # -qi/-qw would hide the window; keep a hidden console on Windows.
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NO_WINDOW
        self.proc = subprocess.Popen(cmd, creationflags=flags,
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        self._wait_ready(timeout)

    def _wait_ready(self, timeout: float) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                raise SystemExit(
                    f"[engine] exited early (code {self.proc.returncode}). "
                    f"See log: {self.log_path}\n" + self._tail_log())
            if self.log_path.exists():
                txt = self.log_path.read_text(errors="replace")
                if "Now accepting requests" in txt:
                    print("[engine] ready.")
                    return
                if "Database server stopped" in txt or "Fatal error" in txt:
                    raise SystemExit("[engine] failed to start:\n" + self._tail_log())
            time.sleep(0.3)
        raise SystemExit(f"[engine] timed out after {timeout}s. Log:\n" + self._tail_log())

    def _tail_log(self, n: int = 25) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-n:])
        except OSError:
            return "(no log)"

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            print("[engine] stopping.")
            try:
                self.proc.terminate()
                self.proc.wait(timeout=20)
            except Exception:
                self.proc.kill()


# --------------------------------------------------------------------------- #
# Schema discovery + copy
# --------------------------------------------------------------------------- #

# SQL Anywhere system schema owners we never want to export.
SYSTEM_OWNERS = {
    "SYS", "dbo", "rs_systabgroup", "SA_DEBUG", "DIAGNOSTICS", "extenv",
    "ml_server", "SNAPSHOT_ISOLATION", "console",
}


def list_user_tables(cur) -> list[tuple[str, str]]:
    """Return [(owner, table_name), ...] for base tables, excluding system ones."""
    cur.execute(
        """
        SELECT u.user_name, t.table_name
        FROM SYS.SYSTAB  t
        JOIN SYS.SYSUSER u ON t.creator = u.user_id
        WHERE t.table_type = 1          -- 1 = BASE table
        ORDER BY u.user_name, t.table_name
        """
    )
    out = []
    for owner, name in cur.fetchall():
        if owner in SYSTEM_OWNERS:
            continue
        out.append((owner, name))
    return out


def fix_text(s: str) -> str:
    """Reinterpret a latin-1-decoded string using its real encoding.

    The driver is configured to decode character data as latin-1 (lossless,
    never raises).  QuickBooks text is a mix of UTF-8 and cp1252, so we recover
    the original bytes and decode as UTF-8 when valid, else fall back to cp1252.
    """
    b = s.encode("latin-1")
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp1252", errors="replace")


def sqlite_value(v):
    """Coerce a SQL Anywhere value into something sqlite3 accepts."""
    import datetime
    import decimal
    if isinstance(v, str):
        return fix_text(v)
    if v is None or isinstance(v, (int, float, bytes)):
        return v
    if isinstance(v, decimal.Decimal):
        # keep full precision as text; change to float(v) if you prefer numbers
        return str(v)
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat(sep=" ")
    if isinstance(v, bool):
        return int(v)
    return str(v)


def copy_table(src_cur, dst: sqlite3.Connection, owner: str, table: str,
               batch: int = 2000) -> int:
    """Copy one table from SQL Anywhere into SQLite. Returns row count."""
    qualified = f'"{owner}"."{table}"'
    src_cur.execute(f"SELECT * FROM {qualified}")
    cols = [d[0] for d in src_cur.description]
    col_defs = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join("?" for _ in cols)
    # Flat SQLite name: owner is almost always one schema, but keep it unique.
    dst_name = table if owner in ("DBA", "") else f"{owner}_{table}"
    dst.execute(f'DROP TABLE IF EXISTS "{dst_name}"')
    dst.execute(f'CREATE TABLE "{dst_name}" ({col_defs})')
    insert = f'INSERT INTO "{dst_name}" VALUES ({placeholders})'

    total = 0
    while True:
        rows = src_cur.fetchmany(batch)
        if not rows:
            break
        dst.executemany(insert, [[sqlite_value(v) for v in row] for row in rows])
        total += len(rows)
    dst.commit()
    return total


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    p = argparse.ArgumentParser(
        description="Convert a QuickBooks .QBW to a SQLite database.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--qbw", required=True, help="Path to the .QBW company file")
    p.add_argument("--out", required=True, help="Path to the SQLite file to create")
    p.add_argument("--uid", required=True,
                   help="SQL Anywhere UID (hex user name, e.g. 41646d696e)")
    p.add_argument("--pwd", required=True,
                   help="SQL Anywhere PWD (long hex string)")
    p.add_argument("--sa-dir", default=None,
                   help="SQL Anywhere 17 install dir (auto-detected if omitted)")
    p.add_argument("--port", type=int, default=29179,
                   help="Private TCP port for the temporary engine")
    p.add_argument("--server", default="QBW2SQLITE",
                   help="Temporary engine (server) name")
    p.add_argument("--work-dir", default=None,
                   help="Where to put the temp .QBW copy (default: system temp)")
    p.add_argument("--keep-copy", action="store_true",
                   help="Do not delete the temporary .QBW copy on exit")
    args = p.parse_args()

    qbw = Path(args.qbw).resolve()
    if not qbw.exists():
        raise SystemExit(f"QBW not found: {qbw}")
    out = Path(args.out).resolve()

    sa_dir = find_sqlany_dir(args.sa_dir)
    sqlanydb = load_sqlanydb(sa_dir)
    print(f"[info] SQL Anywhere 17: {sa_dir}")

    work_dir = Path(args.work_dir).resolve() if args.work_dir else Path(tempfile.gettempdir())
    work_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="qbw_", suffix=".QBW", dir=str(work_dir))
    os.close(fd)
    qbw_copy = Path(tmp)
    print(f"[info] copying company file -> {qbw_copy}")
    shutil.copy2(qbw, qbw_copy)

    log_path = qbw_copy.with_suffix(".engine.log")
    engine = SQLAnywhereEngine(sa_dir, qbw_copy, args.server, args.port,
                               dbname=args.server + "_DB", log_path=log_path)
    conn = None
    try:
        engine.start()
        print("[db] connecting...")
        conn = sqlanydb.connect(
            uid=args.uid,
            pwd=args.pwd,
            eng=args.server,
            dbn=args.server + "_DB",
            links=f"tcpip(host=127.0.0.1;port={args.port})",
        )
        # QuickBooks's text encoding is inconsistent: the database declares
        # windows-1252, yet some columns hold UTF-8 bytes (e.g. currency symbols
        # like "Kc"/EUR) while others hold raw cp1252 bytes (e.g. "Quebec",
        # "Ministere").  No single charset decodes all of it, so we tell sqlanydb
        # to decode as latin-1 -- which maps all 256 byte values and never raises
        # -- and then reinterpret each string per-value in sqlite_value().
        try:
            conn.char_set = "iso-8859-1"
            conn.valueof = sqlanydb.mk_valueof((sqlanydb.A_BINARY,), "iso-8859-1")
            conn.assign = sqlanydb.mk_assign("iso-8859-1")
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[warn] could not set latin-1 decoding: {exc}")
        cur = conn.cursor()

        tables = list_user_tables(cur)
        print(f"[db] found {len(tables)} user tables.")

        dst = sqlite3.connect(str(out))
        dst.execute("PRAGMA journal_mode=OFF")
        dst.execute("PRAGMA synchronous=OFF")

        grand = 0
        for i, (owner, table) in enumerate(tables, 1):
            try:
                n = copy_table(cur, dst, owner, table)
                grand += n
                print(f"  [{i:>4}/{len(tables)}] {owner}.{table}: {n} rows")
            except Exception as exc:
                print(f"  [{i:>4}/{len(tables)}] {owner}.{table}: SKIPPED ({exc})")
        dst.execute("VACUUM")
        dst.close()
        cur.close()
        print(f"\n[done] {len(tables)} tables, {grand} rows -> {out}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        engine.stop()
        if not args.keep_copy:
            for f in (qbw_copy, log_path):
                try:
                    f.unlink()
                except OSError:
                    pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
