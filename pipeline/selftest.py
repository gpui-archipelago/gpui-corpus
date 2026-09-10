#!/usr/bin/env python3
"""Offline self-test for the sourcing pipeline — no network, no writes outside a
temp dir. Run: `python3 pipeline/selftest.py` (from the repo root or from
`pipeline/`).
"""

from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

import fetch_corpus as fc


def crate_bytes(name: str, vers: str, *, with_lib: bool = True, with_manifest: bool = True) -> bytes:
    """A synthetic `.crate` tarball: <name>-<vers>/{Cargo.toml,src/…,tests/…}."""
    buf = io.BytesIO()
    members = []
    if with_manifest:
        members.append((f"{name}-{vers}/Cargo.toml", b'[package]\nname = "x"\nversion = "0.0.0"\n'))
    if with_lib:
        members.append((f"{name}-{vers}/src/lib.rs", b"pub fn f() {}\n"))
    members.append((f"{name}-{vers}/tests/it.rs", b"#[test]\nfn t() {}\n"))
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for member, data in members:
            info = tarfile.TarInfo(member)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class Determinism(unittest.TestCase):
    def test_gzip_is_byte_stable_and_os_agnostic(self):
        a, b = fc.gz9(b"x" * 1000), fc.gz9(b"x" * 1000)
        self.assertEqual(a, b)
        self.assertEqual(a[9], 255, "OS byte forced to unknown")

    def test_tar_and_json_are_byte_stable(self):
        entries = [("Cargo.toml", b"x", 0o644), ("src/lib.rs", b"y", 0o644)]
        self.assertEqual(fc.tar_gz9(entries), fc.tar_gz9(entries))
        self.assertEqual(fc.json_gz9({"b": 1, "a": [2]}), fc.json_gz9({"a": [2], "b": 1}))

    def test_source_selection_keeps_only_the_measurement_input(self):
        names = [n for n, _, _ in fc.crate_source_entries(crate_bytes("demo", "1.0.0"))]
        self.assertEqual(names, ["Cargo.toml", "src/lib.rs"])

    def test_a_crate_without_a_manifest_is_rejected(self):
        with self.assertRaises(RuntimeError):
            fc.crate_source_entries(crate_bytes("demo", "1.0.0", with_manifest=False))


class SparsePaths(unittest.TestCase):
    def test_the_documented_scheme(self):
        self.assertEqual(fc._sparse_path("a"), "1/a")
        self.assertEqual(fc._sparse_path("ab"), "2/ab")
        self.assertEqual(fc._sparse_path("abc"), "3/a/abc")
        self.assertEqual(fc._sparse_path("gpui-box"), "gp/ui/gpui-box")


class CratesIoAdapter(unittest.TestCase):
    ROWS = [
        {"vers": "1.0.0", "cksum": "aaa", "yanked": False, "features": {"f": []},
         "deps": [{"name": "serde", "req": "^1", "kind": "normal"}]},
        {"vers": "1.0.1", "cksum": "bbb", "yanked": True, "features": {}, "deps": []},
    ]
    PROVIDER = {"id": "gpui-box", "package": "gpui-box", "lib_name": "gpui"}
    SOURCE = {"kind": "crates-io", "index_path": "gp/ui/gpui-box"}

    def setUp(self):
        self._sparse, self._api, self._fetch = fc.sparse_rows, fc.api_meta, fc.fetch
        fc.sparse_rows = lambda path: list(self.ROWS)
        fc.api_meta = lambda pkg: ({"repository": "https://example/x", "description": "d"}, {})
        fc.fetch = lambda url, **kw: crate_bytes("gpui-box", "1.0.0")
        self.addCleanup(self._restore)

    def _restore(self):
        fc.sparse_rows, fc.api_meta, fc.fetch = self._sparse, self._api, self._fetch

    def test_sources_blobs_and_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            record, meta, new, reused = fc.sync_crates_io(
                self.PROVIDER, self.SOURCE, {}, out, only=set(), dry_run=False)
            self.assertEqual((new, reused), (2, 0))
            self.assertEqual(meta["repository"], "https://example/x")

            release = record["releases"][0]
            self.assertEqual(record["kind"], "crates-io")
            self.assertEqual(release["id"], "1.0.0")
            self.assertEqual(release["meta"]["cksum"], "aaa")
            self.assertEqual(release["meta"]["deps"][0]["name"], "serde")
            self.assertEqual(release["artifact"]["blob"], "sources/crates-io/gpui-box/1.0.0.tar.gz")

            blob = out / release["artifact"]["blob"]
            self.assertTrue(blob.is_file())
            with tarfile.open(blob, mode="r:gz") as tar:
                self.assertEqual(tar.getnames(), ["Cargo.toml", "src/lib.rs"])

    def test_incremental_reuse_skips_downloads(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            record, _, _, _ = fc.sync_crates_io(self.PROVIDER, self.SOURCE, {}, out,
                                                only=set(), dry_run=False)
            index = fc.build_index({"contract": {"id": "gpui"}},
                                   [{"id": "gpui-box", "sources": [record]}])
            (out / "index.json.gz").write_bytes(fc.json_gz9(index))

            prior = fc.prior_releases(fc.load_index(out))
            again, _, new, reused = fc.sync_crates_io(self.PROVIDER, self.SOURCE, prior, out,
                                                      only=set(), dry_run=False)
            self.assertEqual((new, reused), (0, 2))
            self.assertEqual(again["releases"][0]["artifact"], record["releases"][0]["artifact"])

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            record, _, new, reused = fc.sync_crates_io(self.PROVIDER, self.SOURCE, {}, out,
                                                       only=set(), dry_run=True)
            self.assertEqual((new, reused), (0, 0))
            self.assertIsNone(record["releases"][0]["artifact"])
            self.assertEqual(list(out.iterdir()), [])


class AdapterGuard(unittest.TestCase):
    def test_an_unknown_kind_fails_loudly(self):
        entry = {"id": "p", "package": "p", "lib_name": "g", "sources": [{"kind": "github"}]}
        with self.assertRaisesRegex(RuntimeError, "no adapter for source kind 'github'"):
            fc.sync_provider(entry, {}, Path("/tmp"), only=set(), dry_run=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
