# Copyright (c) 2026 William Minchin
"""SQL Anywhere 17: locating it, loading its driver, and running a private engine."""

from __future__ import annotations

from collections.abc import Iterator
import contextlib
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from types import ModuleType
from typing import Any

from minchinweb.scripts.qbw_to_sqlite._errors import QBWError

log = logging.getLogger(__name__)


def find_sqlany_dir(explicit: str | os.PathLike[str] | None) -> Path:
    """Return the SQL Anywhere 17 install root (the folder that holds Bin64).

    The search order is ``explicit``, then the ``SQLANY17`` variable, then
    ``SQL Anywhere 17`` under each Program Files folder.

    Args:
        explicit: A folder given by the caller, or None.

    Returns:
        The install root.

    Raises:
        QBWError: No folder holds ``Bin64/dbeng17.exe``.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("SQLANY17")
    if env:
        candidates.append(Path(env))
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(var)
        if base:
            candidates.append(Path(base) / "SQL Anywhere 17")
    for c in candidates:
        if (c / "Bin64" / "dbeng17.exe").exists():
            return c
    raise QBWError(
        "cannot find SQL Anywhere 17 (Bin64\\dbeng17.exe). Install it, or pass "
        'sa_dir (--sa-dir), e.g. "C:\\Program Files\\SQL Anywhere 17".'
    )


@contextlib.contextmanager
def sqlany_driver(sa_dir: Path) -> Iterator[ModuleType]:
    """Import SQL Anywhere's bundled ``sqlanydb`` driver, with its DLL reachable.

    The driver finds ``dbcapi.dll`` through the ``SQLANY_API_DLL`` variable when
    it first connects, so this block must be active while connecting. The
    variable, ``PATH`` and ``sys.path`` are changed only inside the block and
    restored on exit, so importing this package leaves the process unchanged.
    The loaded DLL stays loaded, so later connections in the same process reuse
    it, even when they run outside this block.

    Args:
        sa_dir: The SQL Anywhere 17 install root.

    Yields:
        The ``sqlanydb`` module.

    Raises:
        QBWError: ``dbcapi.dll`` is missing, or the driver cannot be imported.
    """
    bin64 = sa_dir / "Bin64"
    sdk_py = sa_dir / "SDK" / "Python"
    dbcapi = bin64 / "dbcapi.dll"
    if not dbcapi.exists():
        raise QBWError(f"cannot find dbcapi.dll at {dbcapi}")
    changes = {
        "SQLANY_API_DLL": str(dbcapi),
        "PATH": str(bin64) + os.pathsep + os.environ.get("PATH", ""),
    }
    saved_env = {name: os.environ.get(name) for name in changes}
    saved_path = list(sys.path)
    dll_dir = None
    if hasattr(os, "add_dll_directory"):
        dll_dir = os.add_dll_directory(str(bin64))
    os.environ.update(changes)
    if (sdk_py / "sqlanydb.py").exists():
        sys.path.insert(0, str(sdk_py))
    try:
        try:
            import sqlanydb  # type: ignore[import-not-found]
        except Exception as exc:
            raise QBWError(
                f"cannot import 'sqlanydb' (looked in {sdk_py}): {exc}"
            ) from exc
        yield sqlanydb
    finally:
        for name, old in saved_env.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old
        sys.path[:] = saved_path
        if dll_dir is not None:
            dll_dir.close()


def connect(
    sa_dir: Path, *, uid: str, pwd: str, server: str, dbname: str, port: int
) -> Any:
    """Log in to the running engine and return a connection.

    The connection decodes character data as latin-1 (lossless). Text is fixed
    later, one value at a time, by ``fix_text``.

    Args:
        sa_dir: The SQL Anywhere 17 install root.
        uid: The SQL Anywhere user name as hex.
        pwd: The SQL Anywhere password value.
        server: The engine (server) name.
        dbname: The database name on that engine.
        port: The TCP port the engine listens on.

    Returns:
        An open ``sqlanydb`` connection.

    Raises:
        QBWError: The login was refused, or the driver could not load.
    """
    with sqlany_driver(sa_dir) as sqlanydb:
        try:
            conn = sqlanydb.connect(
                uid=uid,
                pwd=pwd,
                eng=server,
                dbn=dbname,
                links=f"tcpip(host=127.0.0.1;port={port})",
            )
        except Exception as exc:
            raise QBWError(f"cannot log in with this UID and PWD: {exc}") from exc
        try:
            conn.char_set = "iso-8859-1"
            conn.valueof = sqlanydb.mk_valueof((sqlanydb.A_BINARY,), "iso-8859-1")
            conn.assign = sqlanydb.mk_assign("iso-8859-1")
        except Exception as exc:  # noqa: BLE001 - defensive
            log.warning("cannot set latin-1 decoding: %s", exc)
    return conn


class SQLAnywhereEngine:
    """A private, read-only ``dbeng17`` process serving one working copy.

    Use ``start()`` and ``stop()``. Starting waits until the engine accepts
    requests, and raises QBWError if it fails or does not start in time.
    """

    def __init__(
        self,
        sa_dir: Path,
        qbw_copy: Path,
        server: str,
        port: int,
        dbname: str,
        log_path: Path,
    ) -> None:
        self.exe = sa_dir / "Bin64" / "dbeng17.exe"
        self.qbw_copy = qbw_copy
        self.server = server
        self.port = port
        self.dbname = dbname
        self.log_path = log_path
        self.proc: subprocess.Popen[bytes] | None = None

    def start(self, timeout: float = 90.0) -> None:
        """Start the engine and wait until it accepts requests.

        Args:
            timeout: Seconds to wait before giving up.

        Raises:
            QBWError: The engine exits early, reports a fatal error, or times out.
        """
        cmd = [
            str(self.exe),
            "-r",  # whole server read-only (forensic-safe)
            "-o",
            str(self.log_path),  # message log
            "-x",
            f"tcpip(port={self.port})",  # private TCP port
            "-n",
            self.server,  # server (engine) name
            str(self.qbw_copy),
            "-n",
            self.dbname,  # database name
        ]
        log.info(
            "starting engine %s on port %d (server=%s, db=%s)",
            self.exe.name,
            self.port,
            self.server,
            self.dbname,
        )
        if self.log_path.exists():
            self.log_path.unlink()
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            cmd,
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._wait_ready(timeout)

    def _wait_ready(self, timeout: float) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                raise QBWError(
                    f"engine exited early (exit code {self.proc.returncode}). "
                    f"Last lines of {self.log_path}:\n" + self._tail_log()
                )
            if self.log_path.exists():
                txt = self.log_path.read_text(errors="replace")
                if "Now accepting requests" in txt:
                    log.info("engine ready")
                    return
                if "Database server stopped" in txt or "Fatal error" in txt:
                    raise QBWError(
                        f"engine failed to start. Last lines of {self.log_path}:\n"
                        + self._tail_log()
                    )
            time.sleep(0.3)
        raise QBWError(
            f"engine did not start within {timeout:g} seconds. "
            f"Last lines of {self.log_path}:\n" + self._tail_log()
        )

    def _tail_log(self, n: int = 25) -> str:
        try:
            return "\n".join(
                self.log_path.read_text(errors="replace").splitlines()[-n:]
            )
        except OSError:
            return "(no log file)"

    def stop(self) -> None:
        """Stop the engine process. Kills it if a clean stop fails."""
        if self.proc and self.proc.poll() is None:
            log.info("stopping engine")
            try:
                self.proc.terminate()
                self.proc.wait(timeout=20)
            except Exception:  # noqa: BLE001 - kill the engine whatever went wrong
                self.proc.kill()
