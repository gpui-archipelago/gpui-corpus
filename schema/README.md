# Derived blob formats (`data` branch)

Everything under the `data` branch is produced by `pipeline/fetch_corpus.py`.
It is derived, deterministic, and never hand-edited: a no-new-release re-run
leaves the tree byte-identical. `main` holds the inputs; `data` holds these.

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
  "contract": { "id": "gpui", "description": "…", "api_surface": [ … ] },
  "recommended_provider": "gpui-unofficial",
  "providers": [
    {
      "id": "gpui-unofficial",
      "package": "gpui-unofficial",      // the binding package identity
      "lib_name": "gpui",
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
