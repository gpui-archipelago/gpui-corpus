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
    3. `gocar-index analyze <corpus> --out analysis.json`,
    4. `gocar-index merge <dataset.json> <analysis.json> merged.json`,
    5. compress the merged dataset (xz preset 9) to `<out>/gpui-contract.json.xz`.

This is the **monolithic** layout (task T-49 tracks switching to a split
`measured/index.json.gz` + immutable `measured/surfaces/…` layout, which the
tools will consume once they have a store loader). Deterministic end to end: a
run with no new releases rewrites byte-identical output, so the `measured`
branch only moves when the measurement does.

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
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

from gz import xz9

DATASET_SCHEMA = "gocar.contract.v0"
OUT_NAME = "gpui-contract.json.xz"
# The artifact used to be gzip-9 (`gpui-contract.json.gz`, ~12 MB); xz is
# ~1.4 MB. Drop the legacy name so the branch carries a single artifact.
LEGACY_NAMES = ("gpui-contract.json.gz",)
SOURCE_KIND = "crates-io"


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
# driver
# --------------------------------------------------------------------------
def run(argv: list[str]) -> None:
    print("+ " + " ".join(argv), flush=True)
    proc = subprocess.run(argv)
    if proc.returncode != 0:
        raise SystemExit(f"command failed with exit code {proc.returncode}: {' '.join(argv)}")


def measure(data_root: Path, out: Path, *, gocar_index: str, work: Path) -> None:
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
    run([gocar_index, "analyze", str(corpus), "--out", str(analysis)])
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
    args = parser.parse_args()

    work = args.work or Path(tempfile.mkdtemp(prefix="gpui-corpus-measure-"))
    try:
        measure(args.data, args.out, gocar_index=args.gocar_index, work=work)
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
