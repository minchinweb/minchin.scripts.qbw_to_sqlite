# Copyright (c) 2026 William Minchin
"""QBB backup extraction, QBB detection and file hashing."""

from __future__ import annotations

import hashlib
from pathlib import Path
import zlib

from _support import _sa_company_bytes
import pytest

from minchin.scripts.qbw_to_sqlite import QBWError, _qbb


def test_extracts_company_file_from_container(tmp_path: Path) -> None:
    company = _sa_company_bytes(4096)
    qbb = tmp_path / "x.qbb"
    qbb.write_bytes(
        b"Compression_Format=Deflate_ZLIB;\r\nVersion=1;\r\n"
        + zlib.compress(company, 6)
    )
    out = tmp_path / "company.QBW"
    _qbb.extract_company_file(qbb, out)
    assert out.read_bytes() == company


def test_missing_marker_is_a_qbw_error(tmp_path: Path) -> None:
    qbb = tmp_path / "x.qbb"
    qbb.write_bytes(b"not a backup")
    with pytest.raises(QBWError):
        _qbb.extract_company_file(qbb, tmp_path / "c.QBW")


def test_stream_without_sa_magic_is_rejected(tmp_path: Path) -> None:
    qbb = tmp_path / "x.qbb"
    qbb.write_bytes(
        b"Compression_Format=Deflate_ZLIB;\r\n" + zlib.compress(b"x" * 300, 6)
    )
    with pytest.raises(QBWError):
        _qbb.extract_company_file(qbb, tmp_path / "c.QBW")


def test_is_qbb_detects_marker(tmp_path: Path) -> None:
    qbb = tmp_path / "x.qbb"
    qbb.write_bytes(b"Compression_Format=Deflate_ZLIB;")
    assert _qbb.is_qbb(qbb)
    qbw = tmp_path / "x.QBW"
    qbw.write_bytes(_sa_company_bytes())
    assert not _qbb.is_qbb(qbw)


def test_sha256_matches_hashlib(tmp_path: Path) -> None:
    f = tmp_path / "f.bin"
    f.write_bytes(b"abc" * 1000)
    assert _qbb.sha256_file(f) == hashlib.sha256(b"abc" * 1000).hexdigest()
