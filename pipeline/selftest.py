#!/usr/bin/env python3
"""Offline self-test for the sourcing pipeline — no network, no writes outside a
temp dir. Run: `python3 pipeline/selftest.py` (from the repo root or from
`pipeline/`).
"""

from __future__ import annotations

import contextlib
import io
import hashlib
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import attestations as at
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
                built, failed = mz.materialize_build_corpus(index, Path(td))
            self.assertEqual(built, [("gpui-box", "1.0.0")])
            self.assertEqual(
                failed,
                [("gpui-box", "1.0.1"), ("gpui-box", "1.0.2")],
                "cksum mismatch + fetch failure are reported, never fatal",
            )
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
            docs = {"tvm": Path(td) / "docs/tvm", "eac": Path(td) / "docs/eac"}
            with mock.patch.object(mz, "run", fake_run):
                mz.compile_passes("gocar-index", Path(td), releases, Path(td), docs)
            # One shared target dir per run, reached through each crate's link.
            self.assertTrue((Path(td) / "cargo-target").is_dir())
        kinds = [(argv[1], argv[2].rsplit("/", 1)[-1], argv[4]) for argv in recorded]
        self.assertIn(("tvm", "1.0.0", str(docs["tvm"] / "gpui-box/1.0.0.json")), kinds)
        self.assertIn(("eac", "1.0.0", str(docs["eac"] / "gpui-box/1.0.0.json")), kinds)
        # A tvm failure on one release never blocks the eac pass (nor later releases).
        self.assertIn(("eac", "1.0.1", str(docs["eac"] / "gpui-box/1.0.1.json")), kinds)


class MeasurementEnvironment(unittest.TestCase):
    """The fingerprint a cached attestation is only valid within."""

    def test_rustc_version_mirrors_the_tools_parsing(self):
        cases = {
            "rustc 1.98.1 (48a229cea 2026-09-01)\n": "1.98.1",
            "rustc 1.98.0-nightly (0e2f9a1b2 2026-09-01)\n": "1.98.0-nightly",
            "1.98.1\n": "1.98.1",
            "not rustc\n": None,
            "\n": None,
        }
        for text, expected in cases.items():
            with mock.patch.object(at, "_probe", lambda *_: text):
                self.assertEqual(at.rustc_version(), expected, repr(text))
                # The rustdoc line is recorded verbatim, not parsed.
                self.assertEqual(at.rustdoc_version(), text.strip() or None)

    def test_an_unknown_probe_leaves_no_environment(self):
        with mock.patch.object(at, "_probe", lambda *_: None):
            self.assertIsNone(at.environment("gocar-index"))

    def test_the_environment_keys_on_the_tool_binary_itself(self):
        with tempfile.TemporaryDirectory() as td:
            tool = Path(td) / "gocar-index"
            tool.write_text("#!/bin/sh\n")
            tool.chmod(0o755)
            with mock.patch.object(at, "rustc_version", lambda: "1.98.1"), mock.patch.object(
                at, "rustdoc_version", lambda: "rustdoc 1.98.1 (48a229cea 2026-09-01)"
            ):
                first = at.environment(str(tool))
                self.assertIsNotNone(first)
                # A rebuild — even of the same version — is a different tool.
                tool.write_text("#!/bin/sh\n# rebuilt\n")
                self.assertNotEqual(at.environment(str(tool))["tool"], first["tool"])


class FloorPolicy(unittest.TestCase):
    """Which floor build a release warrants (T-28, `attestations.floor_for`)."""

    def test_a_declared_msrv_is_attempted_under_its_installed_compiler(self):
        installed = {"1.85.0": "1.85", "1.97.1": "1.97.1"}
        self.assertEqual(at.floor_for("1.85", installed, "1.98.1"), ("1.85", None))
        self.assertEqual(at.floor_for("1.97.1", installed, "1.98.1"), ("1.97.1", None))
        # The installed *name* is what rustup answers to — never a rewrite of it
        # (`cargo +1.85.0` would send rustup after a different channel).
        self.assertEqual(at.floor_for("1.85.0", installed, "1.98.1"), ("1.85", None))
        self.assertEqual(at.floor_for(" 1.85 ", installed, "1.98.1"), ("1.85", None))

    def test_no_attempt_without_a_declaration_or_below_the_active_compiler(self):
        installed = {"1.85.0": "1.85", "1.98.0": "1.98"}
        for declared in (None, "", "not-a-version", "1.98.2-rc.1"):
            self.assertEqual(at.floor_for(declared, installed, "1.98.1"), (None, None), declared)
        # The compiler that already verified the crate proves nothing below itself.
        self.assertEqual(at.floor_for("1.98.1", installed, "1.98.1"), (None, None))
        self.assertEqual(at.floor_for("1.99", installed, "1.98.1"), (None, None))
        # No active compiler at all is no policy at all.
        self.assertEqual(at.floor_for("1.85", installed, None), (None, None))

    def test_a_declared_msrv_without_its_compiler_is_a_named_gap(self):
        # Never attempted — a compiler that is absent was not measured to fail, so
        # it can never be recorded `incompatible` — and never silently the same as
        # "this release declares no floor".
        self.assertEqual(at.floor_for("1.87", {"1.85.0": "1.85"}, "1.98.1"), (None, "1.87.0"))

    def test_installed_toolchains_reads_the_names_rustup_lists(self):
        class Listed:
            returncode = 0
            stdout = (
                "stable-x86_64-unknown-linux-gnu (active, default)\n"
                "nightly-x86_64-unknown-linux-gnu\n"
                "1.85-x86_64-unknown-linux-gnu\n"
                "1.90.0-x86_64-unknown-linux-gnu\n"
                "1.91.0-aarch64-unknown-linux-gnu\n"
            )

        class Failed:
            returncode = 1
            stdout = ""

        def missing(*args, **kwargs):
            raise OSError("rustup is not installed")

        with mock.patch.object(at.subprocess, "run", lambda *a, **k: Listed()):
            # A named channel carries no version to compare and is not a candidate.
            self.assertEqual(
                at.installed_toolchains(),
                {"1.85.0": "1.85", "1.90.0": "1.90.0", "1.91.0": "1.91.0"},
            )
        with mock.patch.object(at.subprocess, "run", lambda *a, **k: Failed()):
            self.assertEqual(at.installed_toolchains(), {})
        with mock.patch.object(at.subprocess, "run", missing):
            self.assertEqual(at.installed_toolchains(), {})

    def test_wanted_toolchains_are_padded_and_below_the_active_compiler(self):
        with mock.patch.object(at, "rustc_version", lambda: "1.98.1"):
            self.assertEqual(
                at.wanted_floor_toolchains(
                    ["", "1.85", "1.87", "1.97", "1.97.1", "1.98.1", "1.99"]
                ),
                ["1.85.0", "1.87.0", "1.97.0", "1.97.1"],
            )


class AttestationReuse(unittest.TestCase):
    """Keeping the T-27/T-28 docs whose inputs have not moved (`attestations.py`)."""

    ENV = {
        "tool": "a" * 64,
        "rustc": "1.98.1",
        "rustdoc": "rustdoc 1.98.1 (48a229cea 2026-09-01)",
    }
    SCHEMAS = {"tvm": "gocar.tvm.v1", "eac": "gocar.eac.v1"}
    NO_FLOOR = at.Floor(None, None)

    def setUp(self):
        """The dependency proof is a real `cargo generate-lockfile`; here it is the
        lock already on disk, so the rule is exercised without a build."""
        patcher = mock.patch.object(
            at, "resolve_lock", lambda crate_dir: at.digest_file(crate_dir / at.LOCK_NAME)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _crate(self, root: Path, vers: str, lock: str = "lock") -> Path:
        """A materialized crate, with the lock `cargo generate-lockfile` left."""
        crate = root / "build/gpui-box" / vers
        crate.mkdir(parents=True, exist_ok=True)
        (crate / at.LOCK_NAME).write_text(lock)
        return crate

    def _measure(self, tree: Path, vers: str) -> None:
        """Write the docs the two passes would have written for a release."""
        for kind, schema in self.SCHEMAS.items():
            path = tree / kind / "gpui-box" / f"{vers}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"schema": schema, "crate_name": "gpui-box"}))

    def _prepare(self, root: Path, tree: Path) -> Path:
        """One release as a completed run leaves it; returns its crate dir."""
        cache = at.Cache(tree, dict(self.ENV))
        self._measure(tree, "1.0.0")
        crate = self._crate(root, "1.0.0")
        cache.record("gpui-box", "1.0.0", "cksum-a", crate, None)
        cache.write()
        return crate

    def _reuse(self, tree: Path, crate: Path, cksum, environment=None, floor=None) -> bool:
        return at.Cache(tree, dict(environment or self.ENV)).reuse(
            "gpui-box", crate.name, cksum, crate, floor or self.NO_FLOOR
        )

    def _index(self, releases) -> dict:
        return {
            "providers": [{
                "id": "gpui-box", "package": "gpui-box", "role": "fork",
                "sources": [{"kind": "crates-io", "releases": [
                    {"id": vers, "meta": {"cksum": f"cksum-{vers}"}} for vers in releases
                ]}],
            }]
        }

    def _fake_run(self, runs: list[tuple[str, str, str]]):
        """Stand in for `gocar-index tvm/eac`: record the call, write the doc."""
        def run(argv):
            package, vers = Path(argv[2]).parts[-2:]
            runs.append((argv[1], package, vers))
            out = Path(argv[4])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps({"schema": self.SCHEMAS[argv[1]], "crate_name": package})
            )
        return run

    def test_a_release_is_reused_when_every_input_it_recorded_still_holds(self):
        with tempfile.TemporaryDirectory() as td:
            root, tree = Path(td), Path(td) / "attestations"
            crate = self._prepare(root, tree)
            first_index = (tree / at.INDEX_NAME).read_bytes()

            second = at.Cache(tree, dict(self.ENV))
            self.assertIn("1 attested release(s)", second.describe())
            self.assertTrue(second.reuse("gpui-box", "1.0.0", "cksum-a", crate, self.NO_FLOOR))
            second.write()
            self.assertTrue((tree / "tvm/gpui-box/1.0.0.json").is_file())
            self.assertTrue((tree / "eac/gpui-box/1.0.0.json").is_file())
            # A run that reuses everything moves nothing on the branch.
            self.assertEqual((tree / at.INDEX_NAME).read_bytes(), first_index)

    def test_any_moved_input_measures_the_release_again(self):
        with tempfile.TemporaryDirectory() as td:
            root, tree = Path(td), Path(td) / "attestations"
            crate = self._prepare(root, tree)
            self.assertTrue(self._reuse(tree, crate, "cksum-a"))
            # A different source blob for the same version.
            self.assertFalse(self._reuse(tree, crate, "cksum-b"))
            self.assertFalse(self._reuse(tree, crate, None), "no identity, no reuse")
            # A moved dependency resolution: the same crate, a different lock.
            (crate / at.LOCK_NAME).write_text("moved")
            self.assertFalse(self._reuse(tree, crate, "cksum-a"))
            (crate / at.LOCK_NAME).write_text("lock")
            self.assertTrue(self._reuse(tree, crate, "cksum-a"))
            # No lock at all is no proof at all.
            (crate / at.LOCK_NAME).unlink()
            self.assertFalse(self._reuse(tree, crate, "cksum-a"))
            (crate / at.LOCK_NAME).write_text("lock")
            # A doc whose bytes moved, then one whose schema moved.
            (tree / "tvm/gpui-box/1.0.0.json").write_text("{}")
            self.assertFalse(self._reuse(tree, crate, "cksum-a"))
            self._measure(tree, "1.0.0")
            (tree / "eac/gpui-box/1.0.0.json").write_text(json.dumps({"schema": "gocar.eac.v2"}))
            self.assertFalse(self._reuse(tree, crate, "cksum-a"))
            self._measure(tree, "1.0.0")
            # A measurement environment that moved drops the whole cache.
            moved = at.Cache(tree, dict(self.ENV, rustdoc="rustdoc 1.98.2 (beef 2026-09-02)"))
            self.assertIn("measurement environment moved", moved.describe())
            self.assertIn("rustdoc changed", moved.describe())
            self.assertFalse(moved.reuse("gpui-box", "1.0.0", "cksum-a", crate, self.NO_FLOOR))

    def test_write_drops_every_doc_its_index_does_not_list(self):
        with tempfile.TemporaryDirectory() as td:
            root, tree = Path(td), Path(td) / "attestations"
            crate = self._prepare(root, tree)
            # A second package and a stray file, both unlisted by the run below.
            for kind, schema in self.SCHEMAS.items():
                path = tree / kind / "gpui-ce/0.1.0.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"schema": schema}))
            stray = tree / "tvm/gpui-box/9.9.9.json"
            stray.write_text(json.dumps({"schema": "gocar.tvm.v1"}))

            later = at.Cache(tree, dict(self.ENV))
            self.assertTrue(later.reuse("gpui-box", "1.0.0", "cksum-a", crate, self.NO_FLOOR))
            later.write()
            self.assertTrue((tree / "tvm/gpui-box/1.0.0.json").is_file())
            self.assertFalse((tree / "tvm/gpui-ce").exists(), "empty package dirs are dropped")
            self.assertFalse(stray.exists())

    def test_a_release_whose_pass_produced_no_doc_is_never_attested(self):
        with tempfile.TemporaryDirectory() as td:
            root, tree = Path(td), Path(td) / "attestations"
            cache = at.Cache(tree, dict(self.ENV))
            # No docs on disk: the pass failed, so there is nothing to attest.
            cache.record("gpui-box", "1.0.0", "cksum-a", self._crate(root, "1.0.0"), None)
            self.assertNotIn("gpui-box/1.0.0", cache.entries)
            cache.write()
            self.assertFalse(self._reuse(tree, self._crate(root, "1.0.0"), "cksum-a"))
            # Half a measurement is not a measurement either.
            self._measure(tree, "1.0.0")
            (tree / "eac/gpui-box/1.0.0.json").unlink()
            half = at.Cache(tree, dict(self.ENV))
            half.record("gpui-box", "1.0.0", "cksum-a", self._crate(root, "1.0.0"), None)
            self.assertIn("gpui-box/1.0.0", half.entries, "the tvm doc is still a receipt")
            self.assertFalse(self._reuse(tree, self._crate(root, "1.0.0"), "cksum-a"))

    def test_the_floor_build_is_part_of_the_reuse_key(self):
        with tempfile.TemporaryDirectory() as td:
            root, tree = Path(td), Path(td) / "attestations"
            crate = self._prepare(root, tree)
            # The entry records no floor, so a run that can attempt one measures again.
            self.assertFalse(self._reuse(tree, crate, "cksum-a", floor=at.Floor("1.85", None)))
            cache = at.Cache(tree, dict(self.ENV))
            cache.record("gpui-box", "1.0.0", "cksum-a", crate, "1.85")
            cache.write()
            self.assertTrue(self._reuse(tree, crate, "cksum-a", floor=at.Floor("1.85", None)))
            # A different compiler is a different certificate.
            self.assertFalse(self._reuse(tree, crate, "cksum-a", floor=at.Floor("1.87", None)))
            self.assertFalse(self._reuse(tree, crate, "cksum-a", floor=self.NO_FLOOR))

    def test_an_attested_floor_survives_a_runner_without_that_compiler(self):
        with tempfile.TemporaryDirectory() as td:
            root, tree = Path(td), Path(td) / "attestations"
            crate = self._prepare(root, tree)
            cache = at.Cache(tree, dict(self.ENV))
            cache.record("gpui-box", "1.0.0", "cksum-a", crate, "1.85")
            cache.write()
            # The compiler is gone from this runner: the floor is a fact about the
            # crate and that compiler, so it is kept — loudly, never as a null.
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertTrue(
                    self._reuse(tree, crate, "cksum-a", floor=at.Floor(None, "1.85.0"))
                )
            self.assertIn("keeps its attested floor 1.85", err.getvalue())
            # A release that declares nothing cannot keep a missing compiler's floor.
            self.assertFalse(self._reuse(tree, crate, "cksum-a", floor=at.Floor(None, None)))

    def test_measure_passes_attempts_the_floor_and_keys_the_reuse_on_it(self):
        runs: list[list[str]] = []

        def fake_run(argv):
            runs.append(argv)
            out = Path(argv[argv.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps({"schema": self.SCHEMAS[argv[1]], "crate_name": "gpui-box"})
            )

        index = {
            "providers": [{
                "id": "gpui-box", "package": "gpui-box", "role": "fork",
                "sources": [{"kind": "crates-io", "releases": [
                    {"id": "1.0.0", "meta": {"cksum": "cksum-1.0.0", "rust_version": "1.85"}}
                ]}],
            }]
        }
        releases = [("gpui-box", "1.0.0")]
        with tempfile.TemporaryDirectory() as td:
            work, out = Path(td) / "work", Path(td) / "measured"
            tree = out / "attestations"
            self._crate(work, "1.0.0")
            with mock.patch.object(mz, "run", fake_run), mock.patch.object(
                mz, "rustc_version", lambda: "1.98.1"
            ), mock.patch.object(mz, "installed_toolchains", lambda: {"1.85.0": "1.85"}):
                first = at.Cache(tree, dict(self.ENV))
                mz.measure_passes(
                    "gocar-index", index, work / "build", releases, [], work, first
                )
            first.write()
            self.assertEqual(
                [argv[argv.index("--floor-toolchain") + 1] for argv in runs if "--floor-toolchain" in argv],
                ["1.85"],
                "only the eac pass takes a floor, under the installed name",
            )
            recorded = json.loads((tree / at.INDEX_NAME).read_text())["entries"]
            self.assertEqual(recorded["gpui-box/1.0.0"]["floor"], "1.85")

            # A second run whose runner lacks that compiler keeps the certificate
            # rather than re-measuring the release without it.
            runs.clear()
            second = at.Cache(tree, dict(self.ENV))
            with mock.patch.object(mz, "run", fake_run), mock.patch.object(
                mz, "rustc_version", lambda: "1.98.1"
            ), mock.patch.object(mz, "installed_toolchains", lambda: {}):
                mz.measure_passes(
                    "gocar-index", index, work / "build", releases, [], work, second
                )
            second.write()
            self.assertEqual(runs, [])
            self.assertEqual(
                json.loads((tree / at.INDEX_NAME).read_text())["entries"]["gpui-box/1.0.0"]["floor"],
                "1.85",
            )

    def test_an_unmaterialized_crate_keeps_its_attestation(self):
        with tempfile.TemporaryDirectory() as td:
            root, tree = Path(td), Path(td) / "attestations"
            self._prepare(root, tree)
            cache = at.Cache(tree, dict(self.ENV))
            self.assertTrue(cache.carry_forward("gpui-box", "1.0.0", "cksum-a"))
            cache.write()
            self.assertTrue((tree / "tvm/gpui-box/1.0.0.json").is_file())
            # The identity digest still has to match: the crate we could not fetch
            # is not necessarily the release we recorded.
            self.assertFalse(
                at.Cache(tree, dict(self.ENV)).carry_forward("gpui-box", "1.0.0", "cksum-b")
            )
            # A moved environment drops it like everything else.
            self.assertFalse(
                at.Cache(tree, dict(self.ENV, rustc="1.99.0")).carry_forward(
                    "gpui-box", "1.0.0", "cksum-a"
                )
            )

    def test_measure_passes_measures_only_the_releases_that_moved(self):
        runs: list[tuple[str, str, str]] = []
        resolved: list[str] = []
        releases = ["1.0.0", "1.0.1"]

        def fake_resolve(crate_dir):
            resolved.append(crate_dir.name)
            return at.digest_file(crate_dir / at.LOCK_NAME)

        with tempfile.TemporaryDirectory() as td:
            work, out = Path(td) / "work", Path(td) / "measured"
            tree = out / "attestations"
            index = self._index(releases)
            for vers in releases:
                self._crate(work, vers)
            with mock.patch.object(mz, "run", self._fake_run(runs)), mock.patch.object(
                at, "resolve_lock", fake_resolve
            ):
                first = at.Cache(tree, dict(self.ENV))
                tvm_dir, eac_dir = mz.measure_passes(
                    "gocar-index", index, work / "build", [("gpui-box", v) for v in releases],
                    [], work, first,
                )
                first.write()
                self.assertEqual(
                    runs,
                    [
                        ("tvm", "gpui-box", "1.0.0"), ("eac", "gpui-box", "1.0.0"),
                        ("tvm", "gpui-box", "1.0.1"), ("eac", "gpui-box", "1.0.1"),
                    ],
                )
                self.assertEqual(resolved, [], "a cold cache probes nothing")
                self.assertEqual((tvm_dir, eac_dir), (tree / "tvm", tree / "eac"))

                # Second run, same environment: only the release whose crate
                # re-resolves to a different lock is measured again.
                runs.clear()
                (work / "build/gpui-box/1.0.1/Cargo.lock").write_text("lock: moved")
                second = at.Cache(tree, dict(self.ENV))
                mz.measure_passes(
                    "gocar-index", index, work / "build", [("gpui-box", v) for v in releases],
                    [], work, second,
                )
                second.write()
                self.assertEqual(
                    runs, [("tvm", "gpui-box", "1.0.1"), ("eac", "gpui-box", "1.0.1")]
                )
                self.assertEqual(resolved, ["1.0.0", "1.0.1"], "one probe per candidate")

    def test_a_disabled_cache_measures_into_the_work_dirs(self):
        runs: list[tuple[str, str, str]] = []

        def no_resolve(crate_dir):
            raise AssertionError("a cache without an environment resolves nothing")

        with tempfile.TemporaryDirectory() as td:
            work, out = Path(td) / "work", Path(td) / "measured"
            self._crate(work, "1.0.0")
            cache = at.Cache(out / "attestations", None)
            self.assertIn("attestations: disabled", cache.describe())
            with mock.patch.object(mz, "run", self._fake_run(runs)), mock.patch.object(
                at, "resolve_lock", no_resolve
            ):
                tvm_dir, eac_dir = mz.measure_passes(
                    "gocar-index", self._index(["1.0.0"]), work / "build",
                    [("gpui-box", "1.0.0")], [], work, cache,
                )
            cache.write()
            self.assertEqual((tvm_dir, eac_dir), (work / "tvm", work / "eac"))
            self.assertEqual(runs, [("tvm", "gpui-box", "1.0.0"), ("eac", "gpui-box", "1.0.0")])
            self.assertFalse((out / "attestations").exists())

    def test_no_reuse_measures_again_and_still_refreshes_the_index(self):
        runs: list[tuple[str, str, str]] = []
        with tempfile.TemporaryDirectory() as td:
            root, tree = Path(td), Path(td) / "attestations"
            crate = self._prepare(root, tree)
            index = self._index(["1.0.0"])
            cache = at.Cache(tree, dict(self.ENV), reuse=False)
            self.assertIn("not reused (--no-reuse)", cache.describe())
            self.assertFalse(cache.reuse("gpui-box", "1.0.0", "cksum-a", crate, self.NO_FLOOR))
            with mock.patch.object(mz, "run", self._fake_run(runs)), mock.patch.object(
                at, "resolve_lock", lambda crate_dir: at.digest_file(crate_dir / at.LOCK_NAME)
            ):
                mz.measure_passes(
                    "gocar-index", index, root / "build", [("gpui-box", "1.0.0")], [], root, cache
                )
            cache.write()
            self.assertEqual(runs, [("tvm", "gpui-box", "1.0.0"), ("eac", "gpui-box", "1.0.0")])
            # The index is current again, so the next run reuses.
            self.assertTrue(
                at.Cache(tree, dict(self.ENV)).reuse(
                    "gpui-box", "1.0.0", "cksum-1.0.0", crate, self.NO_FLOOR
                )
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
