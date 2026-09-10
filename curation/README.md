# Curation (hand-edited inputs)

`main` is the human side of this repo: everything here is edited by hand,
reviewed like code, and passed to the tools — which are **data-agnostic** — via
`--curation <DIR>` / `GOCAR_CURATION`
(`crates/gocar-gpui/src/curation.rs` in `cargo-gocar`). Nothing here is baked
into a crate.

## Files the tools read

All six are optional: a missing file is an empty section, not an error (the
storage repo decides which curated inputs exist). A malformed one fails loudly.

- **`migration-rules.json`** (`gocar.rules.v1`) — the human-confirmed rename
  recipes keyed to measured item deltas (T-14, UC-07). Consumed by
  `cargo gocar migrate` and by the fork-map bundle's `rules`.
- **`companions.json`** (`gocar.companions.v1`) — the per-fork platform
  companion:

  ```jsonc
  { "schema": "gocar.companions.v1",
    "companions": {
      "gpui-unofficial": { "package": "gpui-platform-gpui-unofficial",
                           "pin": "mirror",                    // or { "fixed": "0.1.0" }
                           "features": ["x11", "wayland"],
                           "note": "…provenance…" }
    } }
  ```

  `gocar-gpui` overlays these onto the dataset's providers at load, so the
  scaffold (`new`), the resolver (`add`/`plan`/`lock`) and the fork-map export
  all see them. The dataset itself carries no curated fields.
- **`compile-markers.json`** (`gocar.compile-markers.v1`) — the docs/07
  compile-verified rows, keyed by provider, projected into the fork-map
  bundle's `compile_verified` badge (T-17/T-20):

  ```jsonc
  { "schema": "gocar.compile-markers.v1",
    "markers": {
      "gpui-unofficial": { "vers": "1.18.1", "toolchain": "1.95.0",
                           "evidence": "docs/04-user-docs/07-real-fork-compile-case-study.md" }
    } }
  ```

  A row may exist here only if the evidence doc records the compile; the
  marker-completeness test in `export-fork-map` refuses a row that is no longer
  the provider's latest stable.
- **`kit-probes.json`** (`gocar.kit-probes.v1`) — the docs/12 kit-rebase
  compile probes (T-24) — the only places the configurator (UC-10) may offer an
  alias-shim bundle. A list of `{ kit, kit_version, binds, target, outcome,
  caveat?, evidence }` entries, serialized verbatim into the bundle.
- **`upstream-sources.json`** (`gocar.upstream-sources.v1`) — the upstream
  github source maps (T-45), keyed by provider: `{ repo, tree, tag?, note? }`.
  A fork with no entry (an independent fork, or a republish whose versions name
  no concrete upstream tag) stays on its docs.rs anchor.
- **`binding-notes.json`** (`gocar.binding-notes.v1`) — the docs-grounded
  reasons a specific `(provider, version)` row was never compile-probed (T-31).
  Each note names its provider and either an explicit `versions` list or a
  `below` version bound; the first matching note wins. The note text never
  derives from the dataset — it is the docs' own record.

## Still to move here

- **`research/`** — the study documents the `evidence` fields above point at
  (docs/07, docs/12, …), so the curated data and its provenance live together.
  Until then those files are read from `cargo-gocar`'s
  `docs/04-user-docs/`.

## Rules

- Nothing derived lives here (no crates.io data, no measurements) — that is the
  `data` / `measured` branches.
- Every rule, companion, or source link names the measured evidence that
  justifies it, and is reviewed like code.
