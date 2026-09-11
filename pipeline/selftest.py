#!/usr/bin/env python3
"""Offline self-test for the sourcing pipeline — no network, no writes outside a
temp dir. Run: `python3 pipeline/selftest.py` (from the repo root or from
`pipeline/`).
"""

from __future__ import annotations

import io
import hashlib
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fetch_corpus as fc
import measure as mz


def crate_bytes(name: str, vers: str, *, with_lib: bool = True, with_manifest: bool = True) -> bytes:
    """A synthetic `.crate` tarball: <name>-<vers>/{Cargo.toml,README.md,src/…,tests/…}."""
    buf = io.BytesIO()
    members = []
    if with_manifest:
        members.append((f"{name}-{vers}/Cargo.toml", b'[package]\nname = "x"\nversion = "0.0.0"\n'))
    if with_lib:
        members.append((f"{name}-{vers}/src/lib.rs", b"pub fn f() {}\n"))
    members.append((f"{name}-{vers}/README.md", b"# demo\n"))
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

    def test_registry_meta_keeps_the_registry_creation_time(self):
        meta = fc.registry_meta(
            self.ROWS[0], {"rust_version": "1.82", "created_at": "2026-01-02T03:04:05+00:00"}
        )
        self.assertEqual(meta["created_at"], "2026-01-02T03:04:05+00:00")
        self.assertIsNone(fc.registry_meta(self.ROWS[1], {}).get("created_at"))

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


class Roles(unittest.TestCase):
    def test_lib_name_defaults_to_the_package_with_underscores(self):
        self.assertEqual(fc.lib_name("gpui-platform-gpui-unofficial"), "gpui_platform_gpui_unofficial")
        self.assertEqual(fc.lib_name("kael"), "kael")

    def test_validate_config_accepts_a_companion_entry(self):
        fc.validate_config({"providers": [
            {"id": "gpui-unofficial", "package": "gpui-unofficial", "sources": [{"kind": "crates-io"}]},
            {"id": "gpui-platform-gpui-unofficial", "package": "gpui-platform-gpui-unofficial",
             "role": "companion", "for": "gpui-unofficial", "sources": [{"kind": "crates-io"}]},
        ]})  # does not raise

    def test_validate_config_rejects_bad_entries(self):
        io_ = [{"kind": "crates-io"}]
        cases = [
            ([{"id": "a", "package": "a", "sources": []}], "no sources"),
            ([{"id": "a", "package": "a", "role": "wat", "sources": io_}], "unknown role"),
            ([{"id": "a", "package": "a", "for": "ghost", "sources": io_}], "unknown provider"),
            ([{"id": "a", "package": "a", "sources": io_},
              {"id": "a", "package": "b", "sources": io_}], "duplicate"),
        ]
        for providers, needle in cases:
            with self.assertRaisesRegex(RuntimeError, needle):
                fc.validate_config({"providers": providers})


class CompanionSourcing(unittest.TestCase):
    """A companion is sourced exactly like a fork — no companion-specific code."""

    ENTRY = {
        "id": "gpui-platform-gpui-unofficial",
        "package": "gpui-platform-gpui-unofficial",
        "role": "companion",
        "for": "gpui-unofficial",
        "sources": [{"kind": "crates-io", "index_path": "gp/ui/gpui-platform-gpui-unofficial"}],
    }

    def setUp(self):
        self._sparse, self._api, self._fetch = fc.sparse_rows, fc.api_meta, fc.fetch
        fc.sparse_rows = lambda path: [{
            "vers": "0.1.0", "cksum": "ccc", "yanked": False, "features": {},
            "deps": [{"name": "gpui-unofficial", "req": "1", "kind": "normal"}],
        }]
        fc.api_meta = lambda pkg: ({}, {})
        fc.fetch = lambda url, **kw: crate_bytes("gpui-platform-gpui-unofficial", "0.1.0")
        self.addCleanup(self._restore)

    def _restore(self):
        fc.sparse_rows, fc.api_meta, fc.fetch = self._sparse, self._api, self._fetch

    def test_it_is_sourced_and_carries_its_role_and_dependencies(self):
        with tempfile.TemporaryDirectory() as td:
            provider, new, reused = fc.sync_provider(self.ENTRY, {}, Path(td), only=set(), dry_run=False)
        self.assertEqual((new, reused), (1, 0))
        self.assertEqual(provider["role"], "companion")
        self.assertEqual(provider["for"], "gpui-unofficial")
        self.assertEqual(provider["lib_name"], "gpui_platform_gpui_unofficial")
        release = provider["sources"][0]["releases"][0]
        self.assertEqual(
            release["artifact"]["blob"],
            "sources/crates-io/gpui-platform-gpui-unofficial/0.1.0.tar.gz",
        )
        self.assertEqual(release["meta"]["deps"][0]["name"], "gpui-unofficial")


class MeasureStage(unittest.TestCase):
    """Stage 2 adapters: corpus index -> dataset, blobs -> analyzer layout."""

    INDEX = {
        "schema": "gocar.corpus.v1",
        "contract": {"id": "gpui", "description": "d"},
        "recommended_provider": "gpui-unofficial",
        "providers": [
            {
                "id": "gpui-unofficial", "package": "gpui-unofficial", "lib_name": "gpui",
                "role": "fork", "repository": "r", "description": "d", "note": "n",
                "sources": [{
                    "kind": "crates-io", "spec": {"package": "gpui-unofficial"},
                    "releases": [{
                        "id": "1.0.0",
                        "meta": {"yanked": False, "cksum": "aaa", "rust_version": "1.82",
                                 "feature_names": ["f"],
                                 "deps": [{"name": "serde", "req": "^1", "optional": False,
                                           "default_features": True, "kind": "normal"}]},
                        "artifact": {"blob": "sources/crates-io/gpui-unofficial/1.0.0.tar.gz",
                                     "sha256": "x", "format": "tar.gz"},
                    }],
                }],
            },
            {
                "id": "gpui-platform-gpui-unofficial",
                "package": "gpui-platform-gpui-unofficial",
                "role": "companion", "for": "gpui-unofficial",
                "sources": [{"kind": "crates-io", "releases": []}],
            },
        ],
    }

    def test_materialize_keeps_forks_and_nulls_the_attestations(self):
        dataset, skipped = mz.dataset_from_index(self.INDEX)
        self.assertEqual(dataset["schema"], "gocar.contract.v0")
        self.assertIsNone(dataset["synced_at"], "no creation times in this fixture")
        self.assertEqual([p["id"] for p in dataset["providers"]], ["gpui-unofficial"])
        self.assertEqual(skipped, ["gpui-platform-gpui-unofficial (companion)"])
        version = dataset["providers"][0]["versions"][0]
        self.assertEqual(version["vers"], "1.0.0")
        self.assertEqual(version["cksum"], "aaa")
        self.assertEqual(version["deps"][0]["name"], "serde")
        for field in ("versem", "api_hash", "tvm", "eac", "toolchain_floor"):
            self.assertIsNone(version[field], field)

    def test_synced_at_is_the_newest_release_creation_time(self):
        index = json.loads(json.dumps(self.INDEX))
        index["providers"][0]["sources"][0]["releases"][0]["meta"]["created_at"] = (
            "2026-09-05T05:05:17+00:00"
        )
        index["providers"][1]["sources"][0]["releases"] = [
            # Microseconds are dropped: the value is a user-facing "data as of".
            {"id": "0.1.0", "meta": {"created_at": "2026-09-09T01:02:03.876818+00:00"}, "artifact": None}
        ]
        self.assertEqual(mz.corpus_synced_at(index), "2026-09-09T01:02:03+00:00")
        dataset, _ = mz.dataset_from_index(index)
        self.assertEqual(dataset["synced_at"], "2026-09-09T01:02:03+00:00")

    def test_synced_at_skips_missing_and_malformed_stamps(self):
        index = json.loads(json.dumps(self.INDEX))
        releases = index["providers"][0]["sources"][0]["releases"]
        releases[0]["meta"]["created_at"] = "not-a-date"
        releases.append({"id": "1.0.1", "meta": {}, "artifact": None})
        self.assertIsNone(mz.corpus_synced_at(index))

    def test_extract_corpus_writes_the_analyzer_layout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            blob = root / "sources/crates-io/gpui-unofficial/1.0.0.tar.gz"
            blob.parent.mkdir(parents=True)
            blob.write_bytes(fc.tar_gz9([
                ("Cargo.toml", b'[package]\nname = "gpui-unofficial"\n', 0o644),
                ("src/lib.rs", b"pub fn f() {}\n", 0o644),
            ]))
            index = json.loads(json.dumps(self.INDEX))
            index["providers"] = index["providers"][:1]
            count = mz.extract_corpus(root, index, root / "corpus")
            self.assertEqual(count, 1)
            self.assertTrue((root / "corpus/gpui-unofficial/1.0.0/Cargo.toml").is_file())
            self.assertTrue((root / "corpus/gpui-unofficial/1.0.0/src/lib.rs").is_file())

    def test_extract_member_rejects_path_traversal(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo("../evil")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        with tempfile.TemporaryDirectory() as td:
            with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:") as tar:
                with self.assertRaises(RuntimeError):
                    mz.extract_member(tar, tar.getmembers()[0], Path(td) / "dest")


class CompilePasses(unittest.TestCase):
    """T-27/T-28: the full-crate materialization + best-effort compiler passes."""

    def _index(self, releases):
        return {"providers": [{
            "id": "gpui-box", "package": "gpui-box", "role": "fork",
            "sources": [{"kind": "crates-io", "releases": releases}],
        }]}

    def _release(self, vers, cksum, blob=True):
        return {
            "id": vers,
            "meta": {"cksum": cksum},
            "artifact": {"blob": f"sources/crates-io/gpui-box/{vers}.tar.gz"} if blob else None,
        }

    def test_extract_full_crate_strips_the_top_dir_and_keeps_extras(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "gpui-box/1.0.0"
            mz.extract_crate_tarball(crate_bytes("gpui-box", "1.0.0"), dest)
            self.assertTrue((dest / "Cargo.toml").is_file())
            self.assertTrue((dest / "src/lib.rs").is_file())
            # The pruned blob drops these; the full crate must keep them.
            self.assertTrue((dest / "README.md").is_file())
            self.assertTrue((dest / "tests/it.rs").is_file())

    def test_materialize_verifies_the_cksum_and_skips_failures(self):
        good = crate_bytes("gpui-box", "1.0.0")
        index = self._index([
            self._release("1.0.0", hashlib.sha256(good).hexdigest()),
            self._release("1.0.1", "deadbeef"),
            self._release("1.0.2", hashlib.sha256(good).hexdigest()),
        ])

        def fake_fetch(url, **kw):
            if url.endswith("gpui-box-1.0.2.crate"):
                raise RuntimeError("offline")
            return good

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(mz, "fetch", fake_fetch):
                built, skipped = mz.materialize_build_corpus(index, Path(td))
            self.assertEqual(built, [("gpui-box", "1.0.0")])
            self.assertEqual(skipped, 2, "cksum mismatch + fetch failure are skipped, never fatal")
            manifest = (Path(td) / "gpui-box/1.0.0/Cargo.toml").read_text()
            self.assertIn("[workspace]", manifest)

    def test_ensure_workspace_table_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "Cargo.toml"
            manifest.write_text('[package]\nname = "x"\n')
            mz.ensure_workspace_table(manifest)
            mz.ensure_workspace_table(manifest)
            self.assertEqual(manifest.read_text().count("[workspace]"), 1)

    def test_link_shared_target_points_target_at_the_shared_dir(self):
        with tempfile.TemporaryDirectory() as td:
            crate = Path(td) / "gpui-box/1.0.0"
            crate.mkdir(parents=True)
            (crate / "target").mkdir()  # a pre-existing dir is replaced
            shared = Path(td) / "cargo-target"
            shared.mkdir()
            mz.link_shared_target(crate, shared)
            mz.link_shared_target(crate, shared)  # idempotent
            self.assertTrue((crate / "target").is_symlink())
            self.assertEqual((crate / "target").resolve(), shared.resolve())

    def test_compile_passes_run_both_tools_and_survive_failures(self):
        recorded: list[list[str]] = []

        def fake_run(argv):
            recorded.append(argv)
            if argv[1] == "tvm" and argv[2].endswith("1.0.1"):
                raise SystemExit("tvm failed")

        releases = [("gpui-box", "1.0.0"), ("gpui-box", "1.0.1")]
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(mz, "run", fake_run):
                tvm_dir, eac_dir = mz.compile_passes("gocar-index", Path(td), releases, Path(td))
        kinds = [(argv[1], argv[2].rsplit("/", 1)[-1]) for argv in recorded]
        self.assertIn(("tvm", "1.0.0"), kinds)
        self.assertIn(("eac", "1.0.0"), kinds)
        # A tvm failure on one release never blocks the eac pass (nor later releases).
        self.assertIn(("eac", "1.0.1"), kinds)
        self.assertEqual(tvm_dir, Path(td) / "tvm")
        self.assertEqual(eac_dir, Path(td) / "eac")


if __name__ == "__main__":
    unittest.main(verbosity=2)
