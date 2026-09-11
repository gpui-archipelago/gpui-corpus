#!/usr/bin/env python3
"""gpui-corpus — stage 2: measure the sourced corpus into the `measured` branch.

Consumes the `data` branch (`gocar.corpus.v1` index + `sources/<kind>/…` blobs)
and produces the measured contract dataset:

    out/gpui-contract.json.gz   # gocar.contract.v0 with api_hash/versem/surface

The measurement itself is the published `gocar-index` binary:

    1. materialize a null-attestation `gocar.contract.v0` dataset from the
       corpus index (registry truth only — no fork-specific knowledge),
    2. extract each release's source blob into the corpus layout the analyzer
       expects (`<corpus>/<package>/<version>/Cargo.toml` + `src/`),
    3. `gocar-index tvm` + `gocar-index eac` over each release's **full
       published crate** (the compiler passes need the whole tarball; the
       pruned `sources/` blobs cannot build — `include_str!` files, README,
       … are missing), best-effort: a release that does not build records
       `null` + a printed reason, never a synthesized matrix,
    4. `gocar-index analyze <corpus> --tvm <dir> --eac <dir> --out analysis.json`,
    5. `gocar-index merge <dataset.json> <analysis.json> merged.json`,
    6. compress the merged dataset (xz preset 9) to `<out>/gpui-contract.json.xz`.

Pass `--no-compile-passes` to skip steps 3–4's compiler passes and measure
only the syn-level interface (offline, using the pruned blobs — the pre-T-27
behavior).

This is the **monolithic** layout (task T-49 tracks switching to a split
`measured/index.json.gz` + immutable `measured/surfaces/…` layout, which the
tools will consume once they have a store loader). Deterministic end to end: a
run with no new releases rewrites byte-identical output, so the `measured`
branch only moves when the measurement does. (With the compiler passes on, the
TVM/EAC docs carry the toolchain provenance string, so a compiler update
re-measures and moves the branch — a real measurement change, recorded, not
suppressed.)

The dataset's `synced_at` — the fork map's "data as of" line — is the newest
release `created_at` in the corpus index (registry truth), not a wall clock, so
it stays deterministic while still stating how far the coverage reaches.

Stdlib + the published `gocar-index`. Run (from the repo root):

    python3 pipeline/measure.py --data in --out out
    python3 pipeline/measure.py --data in --out out --work /tmp/measure
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

from fetch_corpus import STATIC_HOST, fetch
from gz import xz9

DATASET_SCHEMA = "gocar.contract.v0"
OUT_NAME = "gpui-contract.json.xz"
# The artifact used to be gzip-9 (`gpui-contract.json.gz`, ~12 MB); xz is
# ~1.4 MB. Drop the legacy name so the branch carries a single artifact.
LEGACY_NAMES = ("gpui-contract.json.gz",)
SOURCE_KIND = "crates-io"

# The compiler passes (T-27 TVM, T-28 EAC) measure a release's auto-trait
# matrix and toolchain span. They need the crate's *dependency graph* to build,
# so they run over the full published `.crate` (the pruned `sources/` blob —
# Cargo.toml/build.rs/src only — cannot build: e.g. gpui's `src/gpui.rs`
# `include_str!`s `../README.md`, which the blob drops).


# --------------------------------------------------------------------------
# materialize: corpus index -> gocar.contract.v0 (registry truth, nulls)
# --------------------------------------------------------------------------
def corpus_synced_at(index: dict) -> str | None:
    """The corpus's coverage timestamp: the newest release `created_at`.

    The measured dataset's `synced_at` is the site's "data as of" line. It is
    derived from registry truth (the publication time the crates.io API records
    per release) rather than a wall clock, so a no-change re-measure stays
    byte-identical while the dataset still states how far its coverage reaches.
    Missing timestamps (an API failure is best-effort in stage 1) are skipped;
    None only when no release carries one.
    """
    newest: datetime | None = None
    for entity in index.get("providers", []):
        for source in entity.get("sources", []):
            for release in source.get("releases", []):
                stamp = (release.get("meta") or {}).get("created_at")
                if not stamp:
                    continue
                try:
                    parsed = datetime.fromisoformat(stamp)
                except (TypeError, ValueError):
                    continue
                if newest is None or parsed > newest:
                    newest = parsed
    # Whole seconds: the line is user-facing, sub-second precision is noise.
    return newest.replace(microsecond=0).isoformat() if newest is not None else None


def dataset_from_index(index: dict) -> tuple[dict, list[str]]:
    """A `gocar.contract.v0` dataset from the corpus index, attestations null.

    Returns the dataset and the non-fork entities skipped (the dataset model is
    fork-oriented; companions/dependencies are not bindable providers).
    """
    providers: list[dict] = []
    skipped: list[str] = []
    for entity in index.get("providers", []):
        role = entity.get("role", "fork")
        if role != "fork":
            skipped.append(f"{entity['id']} ({role})")
            continue
        versions: list[dict] = []
        for source in entity.get("sources", []):
            if source.get("kind") != SOURCE_KIND:
                continue
            for release in source.get("releases", []):
                meta = release.get("meta", {})
                versions.append({
                    "vers": release["id"],
                    "yanked": bool(meta.get("yanked", False)),
                    "cksum": meta.get("cksum"),
                    "rust_version": meta.get("rust_version"),
                    "toolchain_floor": None,
                    "versem": None,
                    "api_hash": None,
                    "tvm": None,
                    "eac": None,
                    "feature_names": list(meta.get("feature_names", [])),
                    "deps": list(meta.get("deps", [])),
                })
        providers.append({
            "id": entity["id"],
            "package": entity["package"],
            "lib_name": entity.get("lib_name") or entity["package"].replace("-", "_"),
            "kind": "registry",
            "repository": entity.get("repository"),
            "description": entity.get("description"),
            "api_epoch": None,
            "note": entity.get("note"),
            "versions": versions,
        })
    dataset = {
        "schema": DATASET_SCHEMA,
        "synced_at": corpus_synced_at(index),
        "recommended_provider": index.get("recommended_provider"),
        "contract": index["contract"],
        "providers": providers,
    }
    return dataset, skipped


# --------------------------------------------------------------------------
# extract: source blobs -> the corpus layout gocar-index reads
# --------------------------------------------------------------------------
def extract_member(tar: tarfile.TarFile, member: tarfile.TarInfo, dest: Path) -> None:
    root = dest.resolve()
    target = (root / member.name).resolve()
    if target != root and root not in target.parents:
        raise RuntimeError(f"unsafe path in source blob: {member.name}")
    handle = tar.extractfile(member)
    if handle is None:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(handle.read())


def extract_corpus(data_root: Path, index: dict, corpus_dir: Path) -> int:
    """Extract every fork release's source blob; returns the blob count."""
    extracted = 0
    for entity in index.get("providers", []):
        if entity.get("role", "fork") != "fork":
            continue
        for source in entity.get("sources", []):
            for release in source.get("releases", []):
                blob = (release.get("artifact") or {}).get("blob")
                if not blob:
                    continue
                dest = corpus_dir / entity["package"] / release["id"]
                with tarfile.open(data_root / blob, "r:gz") as tar:
                    for member in tar.getmembers():
                        if member.isfile():
                            extract_member(tar, member, dest)
                extracted += 1
    return extracted


# --------------------------------------------------------------------------
# compile passes: full published crate -> tvm/eac docs (T-27, T-28)
# --------------------------------------------------------------------------
def extract_crate_tarball(crate: bytes, dest: Path) -> None:
    """Extract a full `.crate` tarball, stripping its top-level directory."""
    root = dest.resolve()
    with tarfile.open(fileobj=io.BytesIO(crate), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            parts = member.name.split("/", 1)
            if len(parts) != 2 or not parts[1]:
                continue
            target = (root / parts[1]).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError(f"unsafe path in crate tarball: {member.name}")
            handle = tar.extractfile(member)
            if handle is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(handle.read())


def ensure_workspace_table(manifest: Path) -> None:
    """Give the extracted crate its own empty `[workspace]`.

    The build runs where the crate is checked out, potentially under a parent
    cargo workspace; without this cargo refuses with "believes it's in a
    workspace when it's not". An already-declared workspace table is left
    alone.
    """
    text = manifest.read_text(encoding="utf-8")
    if "[workspace]" in text:
        return
    manifest.write_text(text.rstrip() + "\n\n[workspace]\n", encoding="utf-8")


def materialize_build_corpus(index: dict, build_dir: Path) -> tuple[list[tuple[str, str]], int]:
    """Fetch + extract the full published crate for every fork release.

    Checksum-verified against the index (immutable registry artifacts: the
    tarball a cksum names never changes). A release whose crate cannot be
    fetched/extracted is skipped (its passes record `null` later), never
    fatal — the syn-level measurement must still complete.
    """
    built: list[tuple[str, str]] = []
    skipped = 0
    for entity in index.get("providers", []):
        if entity.get("role", "fork") != "fork":
            continue
        for source in entity.get("sources", []):
            for release in source.get("releases", []):
                package, vers = entity["package"], release["id"]
                dest = build_dir / package / vers
                try:
                    crate = fetch(f"{STATIC_HOST}/{package}/{package}-{vers}.crate")
                    cksum = (release.get("meta") or {}).get("cksum")
                    if cksum and hashlib.sha256(crate).hexdigest() != cksum:
                        raise RuntimeError("cksum mismatch")
                    extract_crate_tarball(crate, dest)
                    ensure_workspace_table(dest / "Cargo.toml")
                    built.append((package, vers))
                except (OSError, RuntimeError, tarfile.TarError) as exc:
                    print(f"  warning: cannot materialize {package} {vers}: {exc}", file=sys.stderr)
                    skipped += 1
    return built, skipped


def compile_passes(
    gocar_index: str, build_dir: Path, releases: list[tuple[str, str]], work: Path
) -> tuple[Path, Path]:
    """Run `gocar-index tvm`/`eac` per release; return (tvm_dir, eac_dir).

    Best-effort by design: a release that fails to build is left without a
    doc, so `analyze --tvm/--eac` leaves it `null` — the honest unknown,
    never a fabricated matrix.
    """
    tvm_dir = work / "tvm"
    eac_dir = work / "eac"
    tvm_ok = eac_ok = 0
    for package, vers in releases:
        crate_dir = build_dir / package / vers
        tvm_out = tvm_dir / package / f"{vers}.json"
        try:
            run([gocar_index, "tvm", str(crate_dir), "--out", str(tvm_out)])
            tvm_ok += 1
        except SystemExit as exc:
            print(f"  warning: tvm unmeasured for {package} {vers}: {exc}", file=sys.stderr)
        eac_out = eac_dir / package / f"{vers}.json"
        try:
            run([gocar_index, "eac", str(crate_dir), "--out", str(eac_out)])
            eac_ok += 1
        except SystemExit as exc:
            print(f"  warning: eac unmeasured for {package} {vers}: {exc}", file=sys.stderr)
    print(
        f"compiler passes: {tvm_ok} tvm · {eac_ok} eac measured (of {len(releases)} built crate(s))"
    )
    return tvm_dir, eac_dir


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def run(argv: list[str]) -> None:
    print("+ " + " ".join(argv), flush=True)
    proc = subprocess.run(argv)
    if proc.returncode != 0:
        raise SystemExit(f"command failed with exit code {proc.returncode}: {' '.join(argv)}")


def measure(
    data_root: Path, out: Path, *, gocar_index: str, work: Path, with_compile_passes: bool = True
) -> None:
    index_bytes = (data_root / "index.json.gz").read_bytes()
    index = json.loads(gzip.decompress(index_bytes))

    dataset, skipped = dataset_from_index(index)
    versions = sum(len(p["versions"]) for p in dataset["providers"])
    print(f"materialized {versions} version(s) across {len(dataset['providers'])} fork(s)")
    if skipped:
        print(f"skipped non-fork entities: {', '.join(skipped)}")

    work.mkdir(parents=True, exist_ok=True)
    corpus = work / "corpus"
    dataset_path = work / "dataset.json"
    dataset_path.write_text(json.dumps(dataset, sort_keys=True), encoding="utf-8")

    extracted = extract_corpus(data_root, index, corpus)
    print(f"extracted {extracted} source blob(s) into {corpus}")

    analysis = work / "analysis.json"
    merged = work / "merged.json"
    analyze_argv = [gocar_index, "analyze", str(corpus), "--out", str(analysis)]
    if with_compile_passes:
        build_dir = work / "build"
        build_dir.mkdir(parents=True, exist_ok=True)
        built, skipped = materialize_build_corpus(index, build_dir)
        print(f"materialized {len(built)} full crate(s) for the compiler passes ({skipped} skipped)")
        tvm_dir, eac_dir = compile_passes(gocar_index, build_dir, built, work)
        analyze_argv += ["--tvm", str(tvm_dir), "--eac", str(eac_dir)]
    run(analyze_argv)
    run([gocar_index, "merge", str(dataset_path), str(analysis), str(merged)])

    payload = xz9(merged.read_bytes())
    out_path = out / OUT_NAME
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(payload)
    for legacy in LEGACY_NAMES:
        (out / legacy).unlink(missing_ok=True)
    print(f"wrote {out_path} ({len(payload):,} bytes xz)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure the sourced corpus into the measured dataset.")
    parser.add_argument("--data", type=Path, required=True, help="the data-branch checkout (index + sources)")
    parser.add_argument("--out", type=Path, required=True, help="the measured-branch checkout")
    parser.add_argument("--gocar-index", default="gocar-index", help="the gocar-index binary (default: PATH)")
    parser.add_argument("--work", type=Path, default=None, help="scratch dir (default: a fresh temp dir)")
    parser.add_argument(
        "--no-compile-passes",
        action="store_true",
        help="skip the TVM/EAC compiler passes (syn-level only; offline, uses the pruned blobs)",
    )
    args = parser.parse_args()

    work = args.work or Path(tempfile.mkdtemp(prefix="gpui-corpus-measure-"))
    try:
        measure(
            args.data,
            args.out,
            gocar_index=args.gocar_index,
            work=work,
            with_compile_passes=not args.no_compile_passes,
        )
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
