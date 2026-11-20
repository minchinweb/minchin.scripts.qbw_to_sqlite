# Copyright (c) 2026 William Minchin
"""Hashing, and extraction of the company file from a .QBB backup."""

from __future__ import annotations

import hashlib
from pathlib import Path
import zlib

from minchin.scripts.qbw_to_sqlite._errors import QBWError

QBB_MARKER = b"Compression_Format=Deflate_ZLIB;"
SA_MAGIC = b"\x5e\xba\x7a\xda"  # superblock magic of a SQL Anywhere file
SA_MAGIC_OFFSET = 20  # byte offset of the magic in the company file
MAX_INFLATED_BYTES = 1 << 30  # same cap as openqbw's default


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 digest of a file, read in 1 MiB chunks.

    Args:
        path: The file to hash.

    Returns:
        The 64-character hex digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_qbb(path: Path) -> bool:
    """Return True if the file looks like a QuickBooks backup (.QBB) container."""
    with path.open("rb") as f:
        return QBB_MARKER in f.read(65536)


def _try_inflate(data: bytes, start: int, dest: Path) -> bool:
    """Inflate the zlib stream at ``start`` into ``dest``.

    Returns:
        True if the stream holds a SQL Anywhere file. False otherwise.

    Raises:
        QBWError: The stream inflates past the 1 GiB safety cap.
    """
    decomp = zlib.decompressobj()
    feed = data[start:]
    total = 0
    try:
        with dest.open("wb") as out:
            while True:
                piece = decomp.decompress(feed, 1 << 20)
                if total == 0 and (
                    len(piece) < SA_MAGIC_OFFSET + 4
                    or piece[SA_MAGIC_OFFSET : SA_MAGIC_OFFSET + 4] != SA_MAGIC
                ):
                    return False
                total += len(piece)
                if total > MAX_INFLATED_BYTES:
                    raise QBWError(
                        "company file would exceed the 1 GiB extraction limit"
                    )
                out.write(piece)
                feed = decomp.unconsumed_tail
                if decomp.eof:
                    return True
                if not piece and not feed:
                    return False  # stream ended without its end marker
    except zlib.error:
        return False


def extract_company_file(src: Path, dest: Path) -> None:
    """Extract the company file from a .QBB backup into ``dest``.

    A QBB holds a text header, then a zlib-compressed company file. The header
    layout beyond the marker is not documented, so the zlib stream is found by
    scanning for a zlib header after the marker. A candidate is accepted only if
    it inflates to a file with the SQL Anywhere magic.

    Args:
        src: The .QBB backup.
        dest: Where to write the extracted company file.

    Raises:
        QBWError: The marker is missing, or no stream inflates to a SQL Anywhere
            file.

    Note:
        Not yet tested against a real QuickBooks backup. Covered by a synthetic
        container test.
    """
    data = src.read_bytes()
    marker_at = data.find(QBB_MARKER)
    if marker_at < 0:
        raise QBWError(f"{src.name}: no {QBB_MARKER.decode()} marker")
    begin = marker_at + len(QBB_MARKER)
    end = min(len(data) - 1, begin + 65536)
    for pos in range(begin, end):
        cmf, flg = data[pos], data[pos + 1]
        if cmf & 0x0F != 8 or ((cmf << 8) | flg) % 31 != 0:
            continue  # not a zlib header
        if _try_inflate(data, pos, dest):
            return
    raise QBWError(f"{src.name}: no zlib stream inflates to a SQL Anywhere file")
