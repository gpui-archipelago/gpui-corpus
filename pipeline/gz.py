"""Deterministic gzip helpers shared by the corpus pipeline stages.

Every blob and index this repo publishes is compressed here, so byte-identical
content always compresses to byte-identical bytes: a fixed mtime (0), the gzip
header's OS byte forced to "unknown", and key-sorted compact JSON. That is what
lets a no-change re-run leave a derived branch byte-identical.
"""

from __future__ import annotations

import gzip
import io
import json
import lzma


def gz9(data: bytes) -> bytes:
    """gzip level 9 with mtime=0 and OS=unknown — byte-stable across re-runs."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as gz:
        gz.write(data)
    out = bytearray(buf.getvalue())
    out[9] = 255  # OS byte: "unknown", so the header does not leak the host
    return bytes(out)


def json_gz9(value: object) -> bytes:
    """Compact, key-sorted, gzip-9 JSON — deterministic for equal content."""
    text = json.dumps(value, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
    return gz9(text.encode("utf-8"))


def xz9(data: bytes) -> bytes:
    """xz (LZMA2) preset 9 — used for the dataset, where it is ~8× smaller
    than gzip-9 on the highly repetitive item surfaces."""
    return lzma.compress(data, format=lzma.FORMAT_XZ, preset=9)
