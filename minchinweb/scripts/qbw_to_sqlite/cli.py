# Copyright (c) 2026 William Minchin
"""Command-line interface, installed as ``qbw-to-sqlite``.

This module uses only the public API (``convert``, ``ConversionReport``,
``QBWError``). It is therefore also a worked example of calling the library
from a script.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import logging
import sys

from minchinweb.scripts.qbw_to_sqlite import (
    ConversionReport,
    QBWError,
    __version__,
    convert,
)

EXIT_OK = 0
EXIT_FATAL = 1
EXIT_USAGE = 2
EXIT_PROBLEMS = 3

LOGGER_NAME = "minchinweb.scripts.qbw_to_sqlite"


def parse_where(text: str) -> tuple[str, str]:
    """Split a ``TABLE=PREDICATE`` argument into its table and predicate.

    Raises:
        argparse.ArgumentTypeError: The text has no ``=``.
    """
    if "=" not in text:
        raise argparse.ArgumentTypeError(f"needs TABLE=PREDICATE, got: {text}")
    table, predicate = text.split("=", 1)
    return table.strip().lower(), predicate.strip()


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the command line."""
    p = argparse.ArgumentParser(
        prog="qbw-to-sqlite",
        description="Convert a QuickBooks .QBW or .QBB company file to SQLite.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--qbw", required=True, help="Path to the .QBW or .QBB company file")
    p.add_argument(
        "--out", required=True, help="SQLite file to create (replaced atomically)"
    )
    p.add_argument(
        "--backend",
        choices=["sa", "openqbw"],
        default="sa",
        help="sa: SQL Anywhere engine (needs --uid/--pwd). "
        "openqbw: experimental, no login, partial output",
    )
    p.add_argument(
        "--uid", help="SQL Anywhere UID (hex user name, e.g. 6265616e636f756e74)"
    )
    p.add_argument("--pwd", help="SQL Anywhere PWD (long hex string)")
    p.add_argument(
        "--mode",
        choices=["replace", "merge"],
        default="replace",
        help="replace: fresh output. merge: upsert by primary key",
    )
    p.add_argument(
        "--tables",
        action="append",
        metavar="PATTERN",
        help="Only these tables (glob, matched as table or owner.table; repeatable)",
    )
    p.add_argument(
        "--exclude",
        action="append",
        metavar="PATTERN",
        help="Skip tables matching this glob (repeatable)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Copy at most this many rows per table (no count check)",
    )
    p.add_argument(
        "--where",
        action="append",
        type=parse_where,
        metavar="TABLE=PREDICATE",
        help="SQL WHERE clause for one table, e.g. "
        "abmc_invoice_header=\"transaction_date >= '2024-01-01'\" (repeatable)",
    )
    p.add_argument("--batch-size", type=int, default=2000, help="Rows per insert batch")
    p.add_argument(
        "--strict",
        action="store_true",
        help="Abort a table on its first bad row instead of skipping that row",
    )
    p.add_argument(
        "--sa-dir",
        default=None,
        help="SQL Anywhere 17 install dir (auto-detected if omitted)",
    )
    p.add_argument(
        "--port",
        type=int,
        default=29179,
        help="Private TCP port for the temporary engine",
    )
    p.add_argument(
        "--server", default="QBW2SQLITE", help="Temporary engine (server) name"
    )
    p.add_argument(
        "--work-dir",
        default=None,
        help="Where to put the temp company file copy (default: system temp)",
    )
    p.add_argument(
        "--keep-copy",
        action="store_true",
        help="Keep the temporary company file copy on exit",
    )
    p.add_argument(
        "--version", action="version", version=f"qbw-to-sqlite {__version__}"
    )
    return p


def _print_summary(report: ConversionReport) -> None:
    if report.tables:
        print(
            f"\n[done] {len(report.tables)} tables, {report.rows_written} rows "
            f"-> {report.output}"
        )
    else:
        print(f"[done] {report.backend} backend -> {report.output}")
    for problem in report.problems:
        print(f"[problem] {problem}")
    if report.problems:
        print(f"[done] {len(report.problems)} problem(s). Exit status 3.")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line. Returns the exit status.

    Args:
        argv: The arguments, without the program name. Defaults to ``sys.argv``.

    Returns:
        0 clean, 1 fatal error, 2 usage error, 3 finished with problems.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.backend == "sa" and not (args.uid and args.pwd):
        parser.error("the sa backend needs --uid and --pwd (see --help)")

    # Progress goes to stdout, as the CLI always did. The handler is removed on
    # exit, so calling main() from another program leaves its logging alone.
    logger = logging.getLogger(LOGGER_NAME)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        report = convert(
            args.qbw,
            args.out,
            uid=args.uid,
            pwd=args.pwd,
            backend=args.backend,
            mode=args.mode,
            tables=args.tables or (),
            exclude=args.exclude or (),
            limit=args.limit,
            where=dict(args.where or []),
            batch_size=args.batch_size,
            strict=args.strict,
            sa_dir=args.sa_dir,
            server=args.server,
            port=args.port,
            work_dir=args.work_dir,
            keep_copy=args.keep_copy,
        )
    except QBWError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_FATAL
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_USAGE
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)

    _print_summary(report)
    return EXIT_PROBLEMS if report.problems else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
