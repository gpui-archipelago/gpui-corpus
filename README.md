# gpui-corpus

Storage for the GPUI fork lineage — kept separate from the tools that consume
it. `cargo-gocar` stays pure code; the data it needs lives here.

The repo is layered, one branch per layer:

| Branch | Holds | Written by |
| --- | --- | --- |
| `main` | curation inputs (migration rules, source links, research) + the pipeline | humans, reviewed like code |
| `data` | derived, immutable, gzip-9 blobs of crates.io version data | `pipeline/fetch_corpus.py` |
| `measured` | the derived measured contract dataset (`gocar.contract.v0`) | `pipeline/measure.py` + the published `gocar-index` |

Nothing derived is committed to `main`; nothing hand-written is committed to a
derived branch.

## Why it exists

`gocar-gpui` used to embed a 66 MB dataset (`include_str!`), which made
updating it a crate release and made the crate unpublishable (crates.io caps a
`.crate` at 10 MB). Splitting storage out fixes both: the tools stay small and
reusable, and each published fork version is sourced **once** into an
append-only blob that later stages consume from cache.

## Stage 1 — source version data (implemented)

Each provider declares one or more **typed sources** (`crates-io` today;
`github` tags/paths next) and `pipeline/fetch_corpus.py` (Python stdlib) turns
its releases into immutable blobs plus a small index, on the `data` branch:

```
index.json.gz                                # gocar.corpus.v1
sources/<kind>/<…>/<release>.tar.gz          # gzip-9 tar: Cargo.toml + src/** + build.rs
```

The index is source-agnostic — a release is `{id, meta, artifact}` whatever the
kind — so adding a source is one adapter function, not a schema change. Entities
are role-tagged too (`fork` | `companion` | `dependency`), so a fork's platform
companion crate and its dependencies are sourced the same way, with no
companion-specific code. Formats and semantics:
[`schema/README.md`](schema/README.md). Providers and their sources are declared
in [`providers.json`](providers.json).

```console
python3 pipeline/fetch_corpus.py --out ../gpui-corpus-data   # write the data branch checkout
python3 pipeline/fetch_corpus.py --out out --dry-run         # report only
python3 pipeline/fetch_corpus.py --out out --only kael@0.4.1 # force one spec
```

Incremental: a release whose upstream identity digest (`cksum` for crates-io)
is already indexed keeps its blob and is never re-downloaded or rewritten; a
no-new-release re-run is byte-identical. Network: `index.crates.io`,
`crates.io`, `static.crates.io`.

### Automation

[`.github/workflows/corpus.yml`](.github/workflows/corpus.yml) runs the
pipeline on a daily schedule (and on demand), and commits any new blobs to the
`data` branch. It never touches `main`.

## Adding a tracked fork

`providers.json` (on `main`) is the human-edited list of tracked entities — the
only place a fork is declared. To track a new fork, append an entry to
`providers`:

```jsonc
{
  "id": "gpui-…",              // stable id, unique; what `cargo gocar` binds by
  "package": "gpui-…",         // the crates.io package to bind
  "lib_name": "gpui",          // extern name consumers compile against; every fork is "gpui"
  "repository": null,          // optional; filled from the crates.io API when null
  "note": "what this fork is",  // human note, carried into the corpus index
  "sources": [
    { "kind": "crates-io" }    // index_path is derived from `package`; set it if you prefer to be explicit
  ]
}
```

Rules the pipeline enforces (`validate_config`, before it touches the network):

- `id` is unique across the file, `package` is required, and at least one
  source is required.
- `role` is optional and defaults to `fork` (a bindable lineage). A fork sets
  no `for`.
- `for`, when present, must name another provider in the file — that is how
  companions and dependencies link to the fork they serve.

`index_path` follows crates.io's sparse-index rule, so it can normally be
omitted; the pipeline derives it:

| package length | path | example |
| --- | --- | --- |
| 1 | `1/<x>` | `a` → `1/a` |
| 2 | `2/<xy>` | `ab` → `2/ab` |
| 3 | `3/<x>/<xyz>` | `abc` → `3/a/abc` |
| 4+ | `<ab>/<cd>/<name>` | `gpui-unofficial` → `gp/ui/gpui-unofficial` |

Then:

1. Preview it: `python3 pipeline/fetch_corpus.py --out out --dry-run`
   (needs the crates.io hosts; add `--provider <id>` to limit the run to the
   new entry).
2. Commit the edit to `main`. The daily `corpus.yml` — or a manual dispatch —
   sources it into `data`, and `measure.yml` follows automatically and
   republishes `measured`. Nothing else is needed: the measured dataset is
   rebuilt from whatever the corpus index lists.

Leave `contract` alone for a new fork (it changes only when the contract
itself does), and change `recommended_provider` only if the new fork should
become the default binding.

**Removing a fork** is the same edit in reverse: delete its entry (its already-
derived blobs stay on their branches, merely unindexed). Deleting branches is a
separate, deliberate act.

**Companions and dependencies** use the same shape plus `role`/`for`:

```jsonc
{ "id": "gpui-platform-gpui-unofficial", "package": "gpui-platform-gpui-unofficial",
  "role": "companion", "for": "gpui-unofficial", "sources": [ { "kind": "crates-io" } ] }
```

A companion's dependency edges already ride each release's `meta.deps`; a
dependency whose *code* also needs measuring gets its own entry with
`role: "dependency"`. (The measured `gocar.contract.v0` dataset is
fork-oriented, so the measurement stage currently measures forks only — see
`schema/README.md`.)

## Stage 2 — measure the corpus (implemented)

`pipeline/measure.py` consumes the `data` branch and produces the measured
contract dataset on `measured`:

```
gpui-contract.json.xz   # gocar.contract.v0 — registry truth + api_hash/versem/surface/tvm/eac
```

The measurement itself is the published `gocar-index` binary; the script only
adapts shapes (it holds no fork-specific knowledge):

1. materialize a null-attestation `gocar.contract.v0` dataset from the corpus
   index (`data/index.json.gz`) — registry truth only,
2. extract each release's source blob into the corpus layout
   (`<corpus>/<package>/<version>/Cargo.toml` + `src/`),
3. run the **compiler passes** (T-27 TVM, T-28 EAC): fetch each release's
   full published `.crate` (checksum-verified against the index) and run
   `gocar-index tvm` + `gocar-index eac` over it — best-effort, a release
   that does not build records `null` + a printed reason,
4. `gocar-index analyze <corpus> --tvm <dir> --eac <dir> --out analysis.json`,
5. `gocar-index merge <dataset.json> <analysis.json> merged.json`,
6. xz-9 the merged dataset to `measured/gpui-contract.json.xz`.

```console
cargo install gocar-index --version 0.5.0   # the measurement tool (crates.io)
python3 pipeline/measure.py --data in --out out
python3 pipeline/measure.py --data in --out out --work /tmp/measure   # keep scratch
python3 pipeline/measure.py --data in --out out --no-compile-passes   # syn-level only
```

The compiler passes add the measured `tvm` (auto-trait allocations; a drop on
an unchanged surface is a T-break) and `eac`/`toolchain_floor` to each released
row. They are the dominant cost — a dependency-graph build per release — so all
releases share one cargo target dir (a `target/` symlink per crate into
`<work>/cargo-target`): cargo fingerprints compiled artifacts by package id +
flags, not by path, so a dependency one release already built is reused by every
later release that resolves the same version. Only ~167 distinct dependency
names appear across the corpus's ~5 600 direct dep edges, so this is the
difference between a per-release rebuild and a per-distinct-dep build. The
workflow caches cargo and allows up to 6 h; `--no-compile-passes` reproduces the
pre-T-27 syn-level dataset offline from the pruned blobs. Because the TVM/EAC
docs carry the toolchain provenance string, a compiler update re-measures (a
real measurement change) and moves the `measured` branch.

So measuring no longer needs the 66 MB dataset baked into a crate: the tool is
~60 KiB, the data comes from here, and a no-new-release syn-level run rewrites
byte-identical output (the `measured` branch only moves when the measurement
does).

### Automation

[`.github/workflows/measure.yml`](.github/workflows/measure.yml) runs after
`corpus.yml` completes and on demand. It is triggered by `workflow_run`, not
`push`: `corpus.yml` pushes `data` with the default `GITHUB_TOKEN`, and such
pushes deliberately do not start other workflows.

## Layout

```
providers.json            the entities + their typed sources (curation/config)
pipeline/fetch_corpus.py  stage 1 — source crates.io version data
pipeline/measure.py       stage 2 — measure the corpus with gocar-index
pipeline/gz.py            the shared deterministic gzip helpers
pipeline/selftest.py      offline tests for both stages
curation/                 hand-edited migration rules, source links, research
schema/                   the derived formats (data + measured branches)
.github/workflows/        corpus.yml (data) · measure.yml (measured) · check.yml
```

The measurement tool (`gocar-index`) is a published crate, not code in this
repo — see `cargo-gocar` (`crates/gocar-index`). The switch from the monolithic
measured dataset to a split index + immutable surfaces is tracked as task
**T-49** in `cargo-gocar/tasks/`.
