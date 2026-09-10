# Derived formats (`data` + `measured` branches)

Everything on the derived branches is produced by the pipeline and never
hand-edited: a re-run with no new releases leaves each branch byte-identical.
`main` holds the inputs; the derived branches hold these.

- **`data`** — sourced crates.io version data, produced by
  `pipeline/fetch_corpus.py` (this document, top half).
- **`measured`** — the measured contract dataset, produced by
  `pipeline/measure.py` (this document, bottom half).

## Layout

```
index.json.gz                            # gocar.corpus.v1 — one small index blob
sources/<kind>/<…>/<release>.tar.gz      # one immutable blob per release
```

Blobs are namespaced by source **kind**, so a new kind never collides:

```
sources/crates-io/<package>/<vers>.tar.gz
sources/github/<owner>/<repo>/<ref>.tar.gz        # once the github adapter lands
```

## `gocar.corpus.v1` (`index.json.gz`)

Compact, key-sorted, gzip-9 (mtime 0). No wall-clock field — the index is a
pure function of the sources at fetch time. The structure is **source-agnostic**:
a provider lists typed sources, and every release is the same
`{id, meta, artifact}` envelope whatever the source.

```jsonc
{
  "schema": "gocar.corpus.v1",
  "contract": { "id": "gpui", "description": "…" },
  "recommended_provider": "gpui-unofficial",
  "providers": [
    {
      "id": "gpui-unofficial",
      "package": "gpui-unofficial",      // the binding package identity
      "lib_name": "gpui",                // defaults to package with `-` → `_`
      "role": "fork",                    // fork | companion | dependency
      "for": null,                        // provider id a companion/dependency serves
      "repository": "https://github.com/zed-industries/zed",
      "description": "…",
      "note": "…",
      "sources": [
        {
          "kind": "crates-io",
          "spec": { "package": "gpui-unofficial", "index_path": "gp/ui/gpui-unofficial" },
          "releases": [
            {
              "id": "1.18.1",            // source-native release key (semver here)
              "meta": {                  // kind-specific
                "yanked": false,
                "cksum": "…",            // crates.io SHA-256 of the .crate tarball
                "rust_version": "1.82",
                "feature_names": [ … ],
                "deps": [ { "name": …, "req": …, "optional": …, "default_features": …, "kind": … } ]
              },
              "artifact": {              // common envelope; null when not yet sourced
                "blob": "sources/crates-io/gpui-unofficial/1.18.1.tar.gz",
                "sha256": "…",           // of the blob file
                "format": "tar.gz"
              }
            }
          ]
        }
      ]
    }
  ]
}
```

The measured fields (`versem`, `api_hash`, `api_epoch`, surfaces) are **not**
here — they are the next stage's output and never enter this repo's source
index.

## Roles

An entity is not necessarily a fork. Every provider carries a `role` and,
when it serves another, a `for` link:

| `role` | Meaning | `for` |
| --- | --- | --- |
| `fork` (default) | a bindable fork lineage | absent |
| `companion` | a fork's platform layer crate (e.g. `gpui-platform-gpui-unofficial`) | the fork provider id |
| `dependency` | a library sourced for measurement | the provider it belongs to (optional) |

A companion (or dependency) is declared **exactly like a fork** — same
`id`/`package`/`sources` shape, same blob layout (`sources/crates-io/<package>/…`)
— so the pipeline needs no companion-specific code. For example (not populated
yet):

```jsonc
{
  "id": "gpui-platform-gpui-unofficial",
  "package": "gpui-platform-gpui-unofficial",
  "role": "companion",
  "for": "gpui-unofficial",
  "sources": [ { "kind": "crates-io" } ]   // index_path derives from the package
}
```

A companion's **dependencies** are already carried per release in
`meta.deps` (name, req, kind, optional, default features). When a dependency's
*code* also needs measuring, declare it as its own entry with `role:
"dependency"` and its own `sources` — no schema change.

`validate_config` enforces: unique ids, a known `role`, a `for` that names an
existing provider, a `package`, and at least one source. Binding-specific
curation (a companion's `pin` — mirror/fixed — and its `features`) belongs in
[`curation/`](../curation/README.md), not in this derived index.

## Source kinds

Every kind has a `spec` (how to enumerate) and a per-release `meta` (what was
learned). The `artifact` envelope is identical across kinds.

### `crates-io`

- **spec:** `{ package, index_path }` — registry truth from the sparse index
  (`index.crates.io`); `repository`/`rust_version` enriched from the crates.io API.
- **release `id`:** the semver version.
- **meta:** `yanked`, `cksum`, `rust_version`, `feature_names`, `deps`.
- **blob:** the measurement input (`Cargo.toml` + `src/**` + `build.rs`).

### `github` (planned — schema reserved, adapter not yet written)

A GitHub source names a repo and an optional sub-path (the zed monorepo case),
and maps releases to tags/refs:

```jsonc
{ "kind": "github", "repo": "zed-industries/zed", "path": "crates/gpui", "ref": "v{id}" }
```

- **spec:** `{ repo, path? , ref }` — `path` narrows a monorepo sub-tree; `ref`
  maps a release id to a git ref (or `refs` lists explicit ones).
- **release `id`:** the tag/ref.
- **meta:** `{ ref, commit, path }` — resolved commit SHA recorded, never assumed.
- **blob:** the sub-tree's measurement input.

Adding the adapter is a single function in `fetch_corpus.py`:
`fetch → extract → filter → tar_gz9 → write_artifact`, registered under
`@adapter("github")`. Neither the config, the index envelope nor the blob
namespace changes shape.

## Source blobs (`sources/<kind>/…/<release>.tar.gz`)

A deterministic gzip-9 tar (fixed `mtime = 0`, zeroed uid/gid/uname/gname,
sorted entries) containing exactly the measurement input:

```
Cargo.toml
build.rs          (when present)
src/**            (when present)
```

Every other tarball member (`tests/`, `examples/`, `benches/`, assets, …) is
dropped: `gocar-index analyze` reads only the manifest and `src/`, so the blob
stays lean and its diff is stable. Extraction yields the layout the analyzer
expects (`<package>/<version>/Cargo.toml` + `src/`).

## Incrementality

The pipeline keys on the release's identity digest — `cksum` for `crates-io`;
a resolved commit SHA for `github`. A release already present with the same
digest keeps its blob (never rewritten); only new releases are downloaded.
`--only PKG@VERS` forces a re-fetch of one crates-io spec.

## Integrity

`artifact.sha256` is the blob's own digest; a kind's `meta` carries the
upstream digest (`cksum` for crates-io, `commit` for github). The measurement
stage should verify the upstream digest where it exists, and `artifact.sha256`
against the downloaded blob.

---

# `measured` branch

## Layout

```
gpui-contract.json.gz   # gocar.contract.v0 — registry truth + measured fields
```

One monolithic, deterministic gzip-9 dataset (mtime 0, no wall clock). It is
the `data` branch's registry truth with the measured fields filled in by
`gocar-index`: per-version `versem`, `api_hash`, and `surface` (the canonical
item set with its sparse fn/doc/src/member payloads), plus the provider
`api_epoch` heads.

## Production

`pipeline/measure.py` materializes a null-attestation dataset from
`data/index.json.gz`, extracts `data/sources/**` into the analyzer's corpus
layout, and runs the published `gocar-index` (`analyze` then `merge`). It
adapts shapes only — the measurement is the tool.

## Incrementality (current limit)

Monolithic: the whole file is rewritten whenever any measured field moves, and
`versem`/`api_epoch` can shift for *existing* versions when a new release lands
(they are cumulative along a stream), so even per-version files would not be
append-only. Committing the whole dataset is the simple option while the corpus
is small (≈11 MB gzip-9 at 71 releases, well under GitHub's 100 MiB per-file cap).

The future-proof layout — a small rewritten `index.json.gz` (headers + surface
pointers) plus immutable `surfaces/<package>/<version>.json.gz` — is tracked as
task **T-49**; it needs a store loader in the consuming tools first.
