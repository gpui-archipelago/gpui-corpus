#!/usr/bin/env python3
"""gpui-corpus — stage 2: measure the sourced corpus into the `measured` branch.

Consumes the `data` branch (`gocar.corpus.v1` index + `sources/<kind>/…` blobs)
and produces the measured contract dataset:

    out/gpui-contract.json.xz   # gocar.contract.v0 with api_hash/versem/surface/tvm/eac
    out/attestations/           # the T-27/T-28 docs + their reuse index

The measurement itself is the published `gocar-index` binary:

    1. materialize a null-attestation `gocar.contract.v0` dataset from the
       corpus index (registry truth only — no fork-specific knowledge),
    2. extract each **measured** entity's source blob into the corpus layout
       the analyzer expects (`<corpus>/<package>/<version>/Cargo.toml` + `src/`)
       — forks, plus the layer crates of a declared facade,
    3. `gocar-index tvm` + `gocar-index eac` over each release's **full
       published crate** (the compiler passes need the whole tarball; the
       pruned `sources/` blobs cannot build — `include_str!` files, README,
       … are missing), best-effort: a release that does not build records
       `null` + a printed reason, never a synthesized matrix,
    4. `gocar-index analyze <corpus> --tvm <dir> --eac <dir> --out analysis.json`,
    5. `gocar-index merge <dataset.json> <analysis.json> merged.json`,
    6. compose each facade's measured layer surfaces into its release rows
       (the dataset stays fork-only; the layers are analysis inputs),
    7. compress the merged dataset (xz preset 9) to `<out>/gpui-contract.json.xz`.

Step 3 is the expensive one — each pass builds a release's whole dependency
graph. All releases therefore share **one cargo target dir**
(`<work>/cargo-target`, reached through a `target/` symlink in each extracted
crate) so cargo reuses a dependency another release already compiled: the
fingerprint is keyed by package id + flags, not by the workspace path.

The passes are also **incremental across runs**. Their docs double as the
`measured` branch's `attestations/` tree — `attestations/{tvm,eac}/<package>/
<vers>.json`, which is the layout `analyze` reads, so reuse copies nothing. A
release is not re-measured when its `cksum`, its freshly resolved dependency
graph (the `Cargo.lock` digest — a new dep release can move an auto-trait
allocation or break the build) and its docs all still match what the previous
run recorded, nor when its `.crate` cannot be materialized at all (it cannot be
re-measured either, and a transient fetch failure must not publish a `null`).
A *failure* is never cached: a release whose pass produced no doc is attempted
again on every run. `attestations/index.json` holds each entry's keys and doc
digests, and `pipeline/attestations.py` documents the rule; the whole cache is
dropped when the measurement environment moves (the tool binary's digest,
`rustc`, `rustdoc`), so a new tool or compiler still re-measures everything.
Pass `--no-reuse` to measure every release again, ignoring the cache.

The EAC pass also attempts a **floor build** (T-28) under each release's
declared `rust-version` where that compiler is installed: the recorded floor is
what `plan`/`verify-env` prune and enforce on, and a declaration that does not
hold is measured and listed `incompatible` rather than promoted to a floor. A
compiler that is not installed is never attempted — it was not measured to fail
— and `--floor-toolchains` prints the specs a runner must install for the policy
to have candidates (the workflow does exactly that).

Pass `--no-compile-passes` to skip steps 3–4's compiler passes and measure
only the syn-level interface (offline, using the pruned blobs — the pre-T-27
behavior). The `attestations/` tree is left alone in that mode: with no passes
run, no doc is measured or verified.

A provider declaring `surface_source` is a **facade**: its own crate is only a
`pub use` layer, so its measured surface is composed from the measured
surfaces of the layer crates the re-exports name (`pipeline/facade.py`). The
composition follows globs and named re-exports, joins cfg gates, keeps
unmeasured targets explicitly unresolved, and records per-release provenance
in `composed_surface`; the row's own `api_hash` still describes the facade
crate itself. See `schema/README.md#facades-surface_source`.

This is the **monolithic** layout (task T-49 tracks switching to a split
`measured/index.json.gz` + immutable `measured/surfaces/…` layout, which the
tools will consume once they have a store loader). Deterministic end to end: a
run with no new releases rewrites byte-identical output — the dataset *and* the
`attestations/` tree it reused — so the `measured` branch only moves when the
measurement does. (With the compiler passes on, the TVM/EAC docs carry the
toolchain provenance string, so a compiler update re-measures and moves the
branch — a real measurement change, recorded, not suppressed.)

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
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

import facade
from attestations import (
    PASSES,
    Cache,
    environment,
    floor_for,
    installed_toolchains,
    rustc_version,
    wanted_floor_toolchains,
)
from fetch_corpus import STATIC_HOST, fetch
from gz import xz9

DATASET_SCHEMA = "gocar.contract.v0"
OUT_NAME = "gpui-contract.json.xz"
# The compiler-pass docs + their reuse index, beside the dataset on `measured`.
ATTESTATIONS_DIR = "attestations"
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
        provider = {
            "id": entity["id"],
            "package": entity["package"],
            "lib_name": entity.get("lib_name") or entity["package"].replace("-", "_"),
            "kind": "registry",
            "repository": entity.get("repository"),
            "description": entity.get("description"),
            "api_epoch": None,
            "note": entity.get("note"),
            "versions": versions,
        }
        if entity.get("surface_source"):
            provider["surface_source"] = entity["surface_source"]
        providers.append(provider)
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


def facade_ids(index: dict) -> set[str]:
    """Providers whose surface is composed from measured layer crates."""
    return {
        entity["id"]
        for entity in index.get("providers", [])
        if entity.get("surface_source")
    }


def measured_entity(entity: dict, facades: set[str]) -> bool:
    """Whether this corpus entity's source is extracted for measurement.

    Forks are measured for the dataset. A dependency is measured only when it
    serves a facade that composes its surface; unrelated dependencies stay
    unmeasured (and out of the derived dataset).
    """
    role = entity.get("role", "fork")
    if role == "fork":
        return True
    return role == "dependency" and entity.get("for") in facades


def extract_corpus(data_root: Path, index: dict, corpus_dir: Path) -> int:
    """Extract every measured entity's source blob; returns the blob count."""
    facades = facade_ids(index)
    extracted = 0
    for entity in index.get("providers", []):
        if not measured_entity(entity, facades):
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


def materialize_build_corpus(
    index: dict, build_dir: Path
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Fetch + extract the full published crate for every fork release.

    Checksum-verified against the index (immutable registry artifacts: the
    tarball a cksum names never changes). A release whose crate cannot be
    fetched/extracted is reported as failed — it has no passes to run, so it
    records `null` unless it still has an attestation to keep — never fatal:
    the syn-level measurement must still complete.
    """
    built: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
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
                    failed.append((package, vers))
    return built, failed


def link_shared_target(crate_dir: Path, shared: Path) -> None:
    """Point a crate's `target/` at one shared dir.

    Cargo fingerprints compiled artifacts by package id + flags, **not** by the
    workspace path, so a shared target dir lets every release reuse the
    dependencies another release already compiled (the corpus shares ~90% of
    its direct dep edges across releases, and only ~167 distinct dep names are
    involved). Without this, each of the ~67 releases rebuilds its whole
    dependency graph from scratch — the dominant cost of the TVM/EAC passes.

    `gocar-index` reads `<crate-dir>/target/doc/<lib>.json`, so the symlink
    keeps that path valid while cargo writes through to the shared dir.
    """
    link = crate_dir / "target"
    if link.is_symlink():
        link.unlink()
    elif link.is_dir():
        shutil.rmtree(link)
    elif link.exists():
        link.unlink()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(shared, target_is_directory=True)


def compile_passes(
    gocar_index: str,
    build_dir: Path,
    releases: list[tuple[str, str]],
    work: Path,
    docs: dict[str, Path],
    floors: dict[tuple[str, str], str] | None = None,
) -> None:
    """Run `gocar-index tvm`/`eac` per release, writing the docs into `docs`.

    `docs[kind]` is the directory the analyzer reads back from
    (`<kind>/<package>/<vers>.json`): the `attestations/` tree, or the scratch
    work dir when attestations are disabled. `gocar-index` creates the parents.
    `floors` names the toolchain to attempt a floor build under, per release
    (T-28); a release absent from it gets no floor attempt — the EAC still
    records the compiler that verified it.

    All releases share one cargo target dir (`<work>/cargo-target`, reached via
    each crate's `target/` symlink), so dependency compilation is paid once per
    distinct dep version rather than once per release. That sharing is per
    *compiler*: a floor build under a distinct toolchain pays its own dep graph.

    Best-effort by design: a release that fails to build is left without a
    doc, so `analyze --tvm/--eac` leaves it `null` — the honest unknown,
    never a fabricated matrix. A doc that was not written is also never
    indexed as an attestation (`attestations.Cache.record`).
    """
    shared_target = work / "cargo-target"
    shared_target.mkdir(parents=True, exist_ok=True)
    tvm_ok = eac_ok = floor_ok = 0
    for package, vers in releases:
        crate_dir = build_dir / package / vers
        link_shared_target(crate_dir, shared_target)
        tvm_out = docs["tvm"] / package / f"{vers}.json"
        try:
            run([gocar_index, "tvm", str(crate_dir), "--out", str(tvm_out)])
            tvm_ok += 1
        except SystemExit as exc:
            print(f"  warning: tvm unmeasured for {package} {vers}: {exc}", file=sys.stderr)
        eac_out = docs["eac"] / package / f"{vers}.json"
        eac_argv = [gocar_index, "eac", str(crate_dir), "--out", str(eac_out)]
        floor = (floors or {}).get((package, vers))
        if floor:
            eac_argv += ["--floor-toolchain", floor]
        try:
            run(eac_argv)
            eac_ok += 1
            if floor:
                floor_ok += 1
        except SystemExit as exc:
            print(f"  warning: eac unmeasured for {package} {vers}: {exc}", file=sys.stderr)
    print(
        f"compiler passes: {tvm_ok} tvm · {eac_ok} eac measured "
        f"(of {len(releases)} built crate(s); {floor_ok} with a floor build)"
    )


# --------------------------------------------------------------------------
# facade composition: measured layer surfaces -> the facade's surface
# --------------------------------------------------------------------------
def analysis_surfaces(analysis: dict) -> dict[tuple[str, str], list[dict]]:
    """Every analyzed release's measured surface, keyed (package, version)."""
    return {
        (row["package"], row["vers"]): row.get("surface") or []
        for row in analysis.get("rows", [])
    }


def facade_graph(index: dict, facade_id: str) -> dict[str, str]:
    """The facade's measured layer crates: extern lib name -> package."""
    graph: dict[str, str] = {}
    for entity in index.get("providers", []):
        if entity.get("for") != facade_id:
            continue
        if entity.get("role", "fork") != "dependency":
            continue
        lib = entity.get("lib_name") or entity["package"].replace("-", "_")
        graph[lib] = entity["package"]
    return graph


def dependency_aliases(index: dict) -> dict[tuple[str, str], set[str]]:
    """Each release's normal-dependency extern names (for opaque-leaf reports)."""
    aliases: dict[tuple[str, str], set[str]] = {}
    for entity in index.get("providers", []):
        for source in entity.get("sources", []):
            for release in source.get("releases", []):
                deps = (release.get("meta") or {}).get("deps") or []
                aliases[(entity["package"], release["id"])] = {
                    dep["name"] for dep in deps if dep.get("kind", "normal") == "normal"
                }
    return aliases


def composed_api_hash(surface: list[dict]) -> str:
    """A deterministic digest of a composed surface.

    `gocar-index`'s H_API is a BLAKE3 over canonical item *texts* the analysis
    doc does not persist, so a composed surface cannot be re-hashed with the
    canonical algorithm here. This projection digest is over the identity
    space the dataset actually carries — sorted ``key``/``digest``/``cfg`` —
    and is used only for equality (lineage/auto-selection), never claimed as
    the canonical H_API. Within a facade it is exact: equal surfaces hash
    equal, a changed item moves it. Cross-provider equality is deliberately
    impossible (different algorithm), which is the conservative direction.
    """
    ordered = sorted(
        surface,
        key=lambda item: (
            item.get("key", ""),
            item.get("digest", ""),
            item.get("cfg", []),
        ),
    )
    payload = "\u0000".join(
        f"{item.get('key', '')}\u0000{item.get('digest', '')}\u0000{'|'.join(item.get('cfg', []))}"
        for item in ordered
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compose_facades(dataset: dict, merged: dict, index: dict, analysis: dict) -> int:
    """Replace each facade release's own surface with its composed surface.

    Runs on the merged dataset, which the pinned `gocar-index` writes without
    the `surface_source`/`composed_surface` provenance (it does not know those
    fields), so the declaration is re-attached here from the materialized
    dataset. The composed release gets a projection `api_hash` over the
    composed surface (`composed_api_hash`), so hash equality keeps meaning
    surface equality; `composed_surface` carries the per-layer provenance.
    The measured `versem` vector is left as `gocar-index` computed it for the
    facade crate's own lineage — the map never renders it, and the CLI treats
    the composed surface as the measured one.
    """
    declarations = {
        provider["id"]: provider["surface_source"]
        for provider in dataset.get("providers", [])
        if provider.get("surface_source")
    }
    if not declarations:
        return 0
    surfaces = analysis_surfaces(analysis)
    aliases = dependency_aliases(index)
    composed = 0
    for provider in merged.get("providers", []):
        source = declarations.get(provider["id"])
        if source is None:
            continue
        provider["surface_source"] = source
        graph = facade_graph(index, provider["id"])
        for record in provider.get("versions", []):
            surface = record.get("surface")
            if surface is None:  # never measured: no own surface to compose onto
                continue
            projection = facade.project_surface(
                surface,
                package=provider["package"],
                vers=record["vers"],
                surfaces=surfaces,
                graph=graph,
                dependencies=aliases,
                direct=source["from"],
            )
            if projection.added == 0:
                continue
            record["surface"] = projection.surface
            # The published surface is now the composed one, so its API hash
            # must describe that surface. The canonical H_API is unrecoverable
            # here (the analysis does not persist canonical texts); the
            # projection digest is exact within the facade and conservative
            # across providers (see `composed_api_hash`).
            record["api_hash"] = composed_api_hash(projection.surface)
            record["composed_surface"] = {
                "kind": source["kind"],
                "sources": projection.sources,
                "items_added": projection.added,
                "unresolved": projection.unresolved,
            }
            composed += 1
            described = ", ".join(
                f"{entry['package']}@{entry['vers']} ({entry['mode']})"
                for entry in projection.sources
            )
            print(
                f"  facade {provider['id']} {record['vers']}: "
                f"{len(surface)} own + {projection.added} composed item(s) "
                f"from {described}"
            )
            for boundary in projection.unresolved:
                print(f"    outside the measured item model: {boundary}")
    if composed:
        print(f"composed {composed} facade release surface(s)")
    return composed


# --------------------------------------------------------------------------
# attestation reuse: keep the docs whose inputs have not moved
# --------------------------------------------------------------------------
def release_cksums(index: dict) -> dict[tuple[str, str], str | None]:
    """Every fork release's registry identity digest, keyed by (package, version).

    Mirrors `materialize_build_corpus`'s walk of the index, so every release a
    pass could run for has a key here. A miss resolves to `None`, which only
    ever refuses a reuse — the safe direction.
    """
    cksums: dict[tuple[str, str], str | None] = {}
    for entity in index.get("providers", []):
        if entity.get("role", "fork") != "fork":
            continue
        for source in entity.get("sources", []):
            for release in source.get("releases", []):
                meta = release.get("meta") or {}
                cksums[(entity["package"], release["id"])] = meta.get("cksum")
    return cksums


def declared_floors(index: dict) -> dict[tuple[str, str], str]:
    """Each release's declared `rust-version` (registry truth), where it declares one.

    The declared floor is the crate's own claim — not an attestation (T-22) —
    which is exactly why the EAC pass attempts it: `gocar-index eac
    --floor-toolchain <declared>` records the compiler that actually built the
    crate, or lists it `incompatible` where the claim does not hold. Mirrors
    `release_cksums`'s walk of the index, so the keys always line up.
    """
    declared: dict[tuple[str, str], str] = {}
    for entity in index.get("providers", []):
        if entity.get("role", "fork") != "fork":
            continue
        for source in entity.get("sources", []):
            for release in source.get("releases", []):
                value = (release.get("meta") or {}).get("rust_version")
                if value:
                    declared[(entity["package"], release["id"])] = value
    return declared


def measure_passes(
    gocar_index: str,
    index: dict,
    build_dir: Path,
    built: list[tuple[str, str]],
    failed: list[tuple[str, str]],
    work: Path,
    cache: Cache,
) -> tuple[Path, Path]:
    """The T-27/T-28 passes, minus the releases whose attestations still hold.

    Per release: keep the cached docs when every input they recorded still
    matches (the cache re-resolves the dependency graph to prove it, and a floor
    build is an input like any other — a release the run can attempt one for is
    measured again); otherwise measure the release. The passes write into the
    cache's own tree (which is what `analyze` reads), and `record` indexes
    exactly what they wrote. Returns the (tvm, eac) doc directories — the
    `attestations/` tree, or the scratch work dirs when attestations are
    disabled.

    The floor candidate is the release's declared MSRV and is attempted only
    where that compiler is installed (see `attestations.floor_for`); a corpus
    row that declares none keeps `toolchain_floor` `null` — unmeasured, never a
    copy of the declaration.
    """
    docs = {kind: cache.dir_for(kind) if cache.enabled else work / kind for kind in PASSES}
    cksums = release_cksums(index)
    declared = declared_floors(index)
    installed = installed_toolchains()
    active = rustc_version()
    floors = {key: floor_for(declared.get(key), installed, active) for key in built}
    to_measure: list[tuple[str, str]] = []
    reused = 0
    for package, vers in built:
        cksum = cksums.get((package, vers))
        if cache.reuse(
            package, vers, cksum, build_dir / package / vers, floors[(package, vers)]
        ):
            print(f"= {package} {vers} (attestation reused)")
            reused += 1
        else:
            to_measure.append((package, vers))
    for package, vers in failed:
        if cache.carry_forward(package, vers, cksums.get((package, vers))):
            print(f"= {package} {vers} (attestation kept: crate not materialized)")
            reused += 1
    if to_measure:
        attempts = {key: floors[key].attempt for key in to_measure if floors[key].attempt}
        compile_passes(gocar_index, build_dir, to_measure, work, docs, attempts)
        for package, vers in to_measure:
            cache.record(
                package,
                vers,
                cksums.get((package, vers)),
                build_dir / package / vers,
                floors[(package, vers)].attempt,
            )
    missing = sorted({floor.missing for floor in floors.values() if floor.missing})
    candidates = sum(1 for floor in floors.values() if floor.attempt)
    print(
        f"attestations: {reused} reused, {len(to_measure)} measured "
        f"(of {len(built) + len(failed)} release(s))"
    )
    # What the floor policy offers (the passes report how many of those actually
    # ran), plus the declared MSRVs whose compiler this runner lacks.
    print(
        f"floors: {candidates} declared-MSRV candidate(s)"
        + (f" · declared but no compiler installed: {', '.join(missing)}" if missing else "")
    )
    return docs["tvm"], docs["eac"]


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def run(argv: list[str]) -> None:
    print("+ " + " ".join(argv), flush=True)
    proc = subprocess.run(argv)
    if proc.returncode != 0:
        raise SystemExit(f"command failed with exit code {proc.returncode}: {' '.join(argv)}")


def measure(
    data_root: Path,
    out: Path,
    *,
    gocar_index: str,
    work: Path,
    with_compile_passes: bool = True,
    reuse: bool = True,
) -> None:
    index_bytes = (data_root / "index.json.gz").read_bytes()
    index = json.loads(gzip.decompress(index_bytes))

    dataset, skipped = dataset_from_index(index)
    versions = sum(len(p["versions"]) for p in dataset["providers"])
    print(f"materialized {versions} version(s) across {len(dataset['providers'])} fork(s)")
    if skipped:
        print(f"non-fork entities outside the dataset: {', '.join(skipped)}")

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
        built, failed = materialize_build_corpus(index, build_dir)
        print(
            f"materialized {len(built)} full crate(s) for the compiler passes "
            f"({len(failed)} skipped)"
        )
        cache = Cache(out / ATTESTATIONS_DIR, environment(gocar_index), reuse=reuse)
        print(cache.describe())
        tvm_dir, eac_dir = measure_passes(
            gocar_index, index, build_dir, built, failed, work, cache
        )
        cache.write()
        analyze_argv += ["--tvm", str(tvm_dir), "--eac", str(eac_dir)]
    run(analyze_argv)
    run([gocar_index, "merge", str(dataset_path), str(analysis), str(merged)])

    merged_text = merged.read_text(encoding="utf-8")
    merged_data = json.loads(merged_text)
    analysis_data = json.loads(analysis.read_text(encoding="utf-8"))
    if compose_facades(dataset, merged_data, index, analysis_data):
        # Keep the merge writer's layout and field order: re-serialize only
        # when a facade actually composed (an unaffected corpus stays
        # byte-identical), and only the composed objects change. The pinned
        # `gocar-index` writes pretty JSON; a newer local build may be compact.
        if merged_text.startswith("{\n"):
            merged.write_text(
                json.dumps(merged_data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        else:
            merged.write_text(
                json.dumps(merged_data, separators=(",", ":"), ensure_ascii=False),
                encoding="utf-8",
            )

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
    parser.add_argument("--out", type=Path, default=None, help="the measured-branch checkout")
    parser.add_argument("--gocar-index", default="gocar-index", help="the gocar-index binary (default: PATH)")
    parser.add_argument("--work", type=Path, default=None, help="scratch dir (default: a fresh temp dir)")
    parser.add_argument(
        "--no-compile-passes",
        action="store_true",
        help="skip the TVM/EAC compiler passes (syn-level only; offline, uses the pruned blobs)",
    )
    parser.add_argument(
        "--no-reuse",
        action="store_true",
        help="measure every release again, ignoring the cached attestations",
    )
    parser.add_argument(
        "--floor-toolchains",
        action="store_true",
        help="print the declared-MSRV toolchains the floor policy needs installed, then exit",
    )
    args = parser.parse_args()

    try:
        if args.floor_toolchains:
            # A runner installs exactly this list (see measure.yml), so at measure
            # time the names the policy asks for are the installed ones.
            index = json.loads(gzip.decompress((args.data / "index.json.gz").read_bytes()))
            wanted = wanted_floor_toolchains(sorted(set(declared_floors(index).values())))
            print(" ".join(wanted))
            return 0
        if args.out is None:
            raise RuntimeError("--out is required (the measured-branch checkout)")
        work = args.work or Path(tempfile.mkdtemp(prefix="gpui-corpus-measure-"))
        measure(
            args.data,
            args.out,
            gocar_index=args.gocar_index,
            work=work,
            with_compile_passes=not args.no_compile_passes,
            reuse=not args.no_reuse,
        )
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
