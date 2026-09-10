# gpui-corpus

Storage for the GPUI fork lineage — kept separate from the tools that consume
it. `cargo-gocar` stays pure code; the data it needs lives here.

The repo is deliberately two-layered:

| Branch | Holds | Edited by |
| --- | --- | --- |
| `main` | curation inputs (migration rules, source links, research) + the pipeline | humans, reviewed like code |
| `data` | derived, immutable, gzip-9 blobs of crates.io version data | `pipeline/` automation only |

Nothing derived is committed to `main`; nothing hand-written is committed to
`data`.

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
kind — so adding a source is one adapter function, not a schema change. Formats
and semantics: [`schema/README.md`](schema/README.md). Providers and their
sources are declared in [`providers.json`](providers.json).

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

## Next stage

Measurement (`gocar-index analyze`) consumes `sources/**`; the merge step
consumes `index.json.gz`. Those are tools, not storage, so they live in
`cargo-gocar` and are published independently — this repo never depends on a
published gocar crate to be sourced.
