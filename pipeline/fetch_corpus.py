#!/usr/bin/env python3
"""gpui-corpus — stage 1: source version data into incremental blobs.

Each provider in `providers.json` declares one or more **typed sources**
(`crates-io` today; `github` next). The pipeline enumerates each source's
releases and writes one immutable, deterministic gzip-9 blob per release into
the derived-branch checkout, plus a small index:

    out/index.json.gz                            # gocar.corpus.v1
    out/sources/<kind>/<...>/<release>.tar.gz    # one blob per release

The schema is source-agnostic: a release is `{id, meta, artifact}` where `id`
is the source's own release key, `meta` is kind-specific, and `artifact` (the
blob + digest) is common. Adding a source kind means adding one adapter below —
the config, index and blob namespace do not change shape.

An entity is not necessarily a fork: `role` marks a fork lineage, its platform
`companion`, or a `dependency`; `for` links a companion/dependency to the fork
it serves. Nothing about the pipeline is fork-specific — a companion crate and
its dependencies are sourced exactly like a fork is.

Incremental: a release already present with the same identity digest keeps its
blob and is never rewritten, so a re-run only downloads new releases. Blobs are
written with a fixed mtime and the index omits any wall-clock field, so a
no-new-release run leaves the tree byte-identical (a meaningful branch diff).

Stdlib only — no gocar crate needs to be published to source the corpus. Run:

    python3 pipeline/fetch_corpus.py --out /path/to/data-branch-checkout
    python3 pipeline/fetch_corpus.py --out out --only gpui-unofficial@1.18.1
    python3 pipeline/fetch_corpus.py --out out --dry-run

Exit codes: 0 ok (including "nothing new"); 1 on a fetch, config or IO failure.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from gz import gz9, json_gz9

INDEX_HOST = "https://index.crates.io"
API_HOST = "https://crates.io/api/v1/crates"
STATIC_HOST = "https://static.crates.io/crates"
USER_AGENT = "gpui-corpus/0.1 (+https://github.com/gpui-archipelago/gpui-corpus)"

HERE = Path(__file__).resolve().parent
CONFIG = HERE.parent / "providers.json"
CORPUS_SCHEMA = "gocar.corpus.v1"

# Provider roles. A `fork` is a bindable lineage; a `companion` is a fork's
# platform crate; a `dependency` is a library sourced for measurement. Extend
# this tuple to admit new roles — the schema is otherwise role-agnostic.
ROLES = ("fork", "companion", "dependency")

# The measurement input: what `gocar-index analyze` reads. A crate tarball's
# remaining files (tests/, examples/, benches/, assets/, …) never reach the
# analyzer, so they are dropped to keep the blobs lean and the diff stable.
KEEP_EXACT = {"Cargo.toml", "build.rs"}
KEEP_PREFIX = ("src/",)


# --------------------------------------------------------------------------
# deterministic compression
# --------------------------------------------------------------------------
def tar_gz9(entries: list[tuple[str, bytes, int]]) -> bytes:
    """A deterministic gzip-9 tar: sorted entries, zeroed ownership/mtime."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for name, data, mode in entries:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            info.mode = mode
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    return gz9(raw.getvalue())


def write_artifact(out: Path, dest_rel: Path, payload: bytes) -> dict:
    """Write one blob and describe it: the common `artifact` envelope."""
    dest = out / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    return {"blob": dest_rel.as_posix(), "sha256": hashlib.sha256(payload).hexdigest(), "format": "tar.gz"}


# --------------------------------------------------------------------------
# network helpers
# --------------------------------------------------------------------------
def fetch(url: str, *, retries: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError) as exc:  # noqa: PERF203
            last = exc
            if attempt + 1 < retries:
                print(f"  retry {attempt + 1}/{retries - 1}: {url} ({exc})", file=sys.stderr)
    raise RuntimeError(f"cannot fetch {url}: {last}")


# --------------------------------------------------------------------------
# source adapters — one per `kind`
# --------------------------------------------------------------------------
# An adapter enumerates a source's releases and materializes a blob for each.
# It receives the provider entry, the source spec, the prior index (keyed by
# (provider_id, kind, release_id)), the output dir, and the CLI filters. It
# returns (source_record, provider_meta, new_count, reused_count):
#   source_record  {"kind", "spec", "releases": [{"id","meta","artifact"}]}
#   provider_meta  optional provider-level fields (repository, description, …)
ADAPTERS: dict[str, Callable[..., tuple[dict, dict, int, int]]] = {}


def adapter(kind: str):
    def register(fn):
        ADAPTERS[kind] = fn
        return fn

    return register


def sparse_rows(index_path: str) -> list[dict]:
    """The sparse-index rows for one crates.io package (newline-delimited JSON)."""
    text = fetch(f"{INDEX_HOST}/{index_path}").decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def api_meta(package: str) -> tuple[dict, dict[str, dict]]:
    """(crate-level metadata, version num -> version metadata) from the API.

    Best-effort: the API enriches the index (repository, rust_version) but the
    sparse index alone is sufficient, so a failure degrades rather than aborts.
    """
    try:
        api = json.loads(fetch(f"{API_HOST}/{package}").decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - enrichment only
        print(f"warning: crates.io API failed for {package}: {exc}", file=sys.stderr)
        return {}, {}
    crate = api.get("crate", {}) or {}
    versions = {v["num"]: v for v in api.get("versions", [])}
    return crate, versions


def registry_meta(row: dict, api_version: dict) -> dict:
    """A crates.io release's `meta`: its registry truth, minus the id (vers)."""
    return {
        "yanked": bool(row.get("yanked", False)),
        "cksum": row.get("cksum"),
        "rust_version": api_version.get("rust_version") or None,
        "feature_names": sorted((row.get("features") or {}).keys()),
        "deps": [
            {
                "name": dep["name"],
                "req": dep["req"],
                "optional": bool(dep.get("optional", False)),
                "default_features": bool(dep.get("default_features", True)),
                "kind": dep.get("kind", "normal"),
            }
            for dep in row.get("deps", [])
        ],
    }


def crate_source_entries(crate: bytes) -> list[tuple[str, bytes, int]]:
    """A crate tarball's measurement input, stripped of the top-level dir."""
    keep: list[tuple[str, bytes, int]] = []
    with tarfile.open(fileobj=io.BytesIO(crate), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            parts = member.name.split("/", 1)
            if len(parts) != 2:
                continue
            rel = parts[1]
            if rel not in KEEP_EXACT and not rel.startswith(KEEP_PREFIX):
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            keep.append((rel, handle.read(), 0o755 if member.mode & 0o111 else 0o644))
    keep.sort(key=lambda entry: entry[0])
    if not any(name == "Cargo.toml" for name, _, _ in keep):
        raise RuntimeError("crate tarball has no Cargo.toml")
    return keep


@adapter("crates-io")
def sync_crates_io(
    provider: dict,
    source: dict,
    prior: dict[tuple[str, str, str], dict],
    out: Path,
    *,
    only: set[tuple[str, str]],
    dry_run: bool,
) -> tuple[dict, dict, int, int]:
    package = source.get("package") or provider["package"]
    index_path = source.get("index_path") or _sparse_path(package)
    rows = sparse_rows(index_path)
    crate_meta, api_versions = api_meta(package)

    releases: list[dict] = []
    new = reused = 0
    for row in rows:
        vers = row["vers"]
        meta = registry_meta(row, api_versions.get(vers, {}))
        before = prior.get((provider["id"], "crates-io", vers))
        fresh = bool(before) and before.get("meta", {}).get("cksum") == meta.get("cksum") and before.get("artifact")

        if dry_run:
            print(f"  crates-io {package}@{vers}: {'reuse' if fresh else 'would fetch'}")
            releases.append({"id": vers, "meta": meta, "artifact": before.get("artifact") if fresh else None})
            continue

        if fresh and (package, vers) not in only:
            artifact, reused = before["artifact"], reused + 1
        else:
            crate = fetch(f"{STATIC_HOST}/{package}/{package}-{vers}.crate")
            payload = tar_gz9(crate_source_entries(crate))
            dest = Path("sources") / "crates-io" / package / f"{vers}.tar.gz"
            artifact, new = write_artifact(out, dest, payload), new + 1
            print(f"  crates-io {package}@{vers}: sourced -> {artifact['blob']}")
        releases.append({"id": vers, "meta": meta, "artifact": artifact})

    record = {
        "kind": "crates-io",
        "spec": {"package": package, "index_path": index_path},
        "releases": releases,
    }
    provider_meta = {"repository": crate_meta.get("repository"), "description": crate_meta.get("description")}
    return record, provider_meta, new, reused


def _sparse_path(package: str) -> str:
    """The crates.io sparse-index path for a package (the documented scheme)."""
    name = package.lower()
    if len(name) == 1:
        return f"1/{name}"
    if len(name) == 2:
        return f"2/{name}"
    if len(name) == 3:
        return f"3/{name[0]}/{name}"
    return f"{name[:2]}/{name[2:4]}/{name}"


# --------------------------------------------------------------------------
# index
# --------------------------------------------------------------------------
def load_index(out: Path) -> dict | None:
    path = out / "index.json.gz"
    if not path.is_file():
        return None
    try:
        return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: ignoring unreadable {path}: {exc}", file=sys.stderr)
        return None


def prior_releases(index: dict | None) -> dict[tuple[str, str, str], dict]:
    """(provider_id, kind, release_id) -> prior release, for the incremental skip."""
    found: dict[tuple[str, str, str], dict] = {}
    for provider in (index or {}).get("providers", []):
        for source in provider.get("sources", []):
            for release in source.get("releases", []):
                found[(provider["id"], source["kind"], release["id"])] = release
    return found


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def lib_name(package: str) -> str:
    """The default Rust lib name for a package (Cargo normalizes `-` to `_`)."""
    return package.replace("-", "_")


def validate_config(config: dict) -> None:
    """Reject a config the pipeline cannot honor, naming the offending entry."""
    entries = config.get("providers", [])
    ids = [entry["id"] for entry in entries]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate provider id(s) in the providers config")
    known = set(ids)
    for entry in entries:
        role = entry.get("role", "fork")
        if role not in ROLES:
            raise RuntimeError(
                f"provider '{entry['id']}': unknown role '{role}' (known: {', '.join(ROLES)})"
            )
        target = entry.get("for")
        if target is not None and target not in known:
            raise RuntimeError(f"provider '{entry['id']}': `for` names unknown provider '{target}'")
        if not entry.get("package"):
            raise RuntimeError(f"provider '{entry['id']}': missing `package`")
        if not entry.get("sources"):
            raise RuntimeError(f"provider '{entry['id']}': no sources declared")


def sync_provider(
    entry: dict,
    prior: dict[tuple[str, str, str], dict],
    out: Path,
    *,
    only: set[tuple[str, str]],
    dry_run: bool,
) -> tuple[dict, int, int]:
    sources: list[dict] = []
    provider_meta: dict = {}
    new = reused = 0
    for source in entry["sources"]:
        kind = source["kind"]
        sync = ADAPTERS.get(kind)
        if sync is None:
            known = ", ".join(sorted(ADAPTERS))
            raise RuntimeError(f"no adapter for source kind '{kind}' (known: {known})")
        record, meta, s_new, s_reused = sync(entry, source, prior, out, only=only, dry_run=dry_run)
        sources.append(record)
        provider_meta.update({k: v for k, v in meta.items() if v})
        new += s_new
        reused += s_reused

    provider = {
        "id": entry["id"],
        "package": entry["package"],
        "lib_name": entry.get("lib_name") or lib_name(entry["package"]),
        "role": entry.get("role", "fork"),
        "for": entry.get("for"),
        "repository": provider_meta.get("repository") or entry.get("repository"),
        "description": provider_meta.get("description"),
        "note": entry.get("note"),
        "sources": sources,
    }
    return provider, new, reused


def build_index(config: dict, providers: list[dict]) -> dict:
    return {
        "schema": CORPUS_SCHEMA,
        "contract": config["contract"],
        "recommended_provider": config.get("recommended_provider"),
        "providers": providers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Source version data into incremental blobs.")
    parser.add_argument("--out", type=Path, required=True, help="output dir (the derived-branch checkout)")
    parser.add_argument("--config", type=Path, default=CONFIG, help="providers config (default: %(default)s)")
    parser.add_argument("--only", action="append", default=[], metavar="PKG@VERS",
                        help="download this crates-io spec even if already present (repeatable)")
    parser.add_argument("--provider", action="append", default=[], metavar="ID",
                        help="limit to this provider id (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="report what would be fetched, change nothing")
    args = parser.parse_args()

    config = json.loads(args.config.read_text())
    try:
        validate_config(config)
    except RuntimeError as exc:
        print(f"error: invalid config: {exc}", file=sys.stderr)
        return 1
    entries = config["providers"]
    if args.provider:
        wanted = set(args.provider)
        entries = [e for e in entries if e["id"] in wanted]
        unknown = wanted - {e["id"] for e in entries}
        if unknown:
            print(f"error: unknown provider id(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 1

    only: set[tuple[str, str]] = set()
    for spec in args.only:
        if "@" not in spec:
            print(f"error: --only expects PKG@VERS, got '{spec}'", file=sys.stderr)
            return 1
        package, vers = spec.split("@", 1)
        only.add((package, vers))

    out: Path = args.out
    prior = prior_releases(load_index(out))

    providers: list[dict] = []
    total_new = total_reused = 0
    for entry in entries:
        print(f"{entry['id']} ({entry['package']})")
        try:
            provider, new, reused = sync_provider(entry, prior, out, only=only, dry_run=args.dry_run)
        except Exception as exc:  # noqa: BLE001 - name the provider, then fail the run
            print(f"error: sourcing {entry['package']} failed: {exc}", file=sys.stderr)
            return 1
        providers.append(provider)
        total_new += new
        total_reused += reused
        n_versions = sum(len(s["releases"]) for s in provider["sources"])
        print(f"  {provider['role']}: {n_versions} release(s): {new} sourced, {reused} already present")

    if args.dry_run:
        print(f"\ndry run: {len(providers)} provider(s) inspected; nothing written")
        return 0

    index_bytes = json_gz9(build_index(config, providers))
    index_path = out / "index.json.gz"
    changed = not index_path.is_file() or index_path.read_bytes() != index_bytes
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_bytes(index_bytes)

    releases_total = sum(len(s["releases"]) for p in providers for s in p["sources"])
    print(f"\nindex: {releases_total} release(s) across {len(providers)} provider(s) "
          f"({'updated' if changed else 'unchanged'}); {total_new} new blob(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
