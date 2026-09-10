# Curation (hand-edited inputs)

`main` is the human side of this repo: everything here is edited by hand,
reviewed like code, and passed to the tools — which are **data-agnostic** — via
`--curation <DIR>` / `GOCAR_CURATION`
(`crates/gocar-gpui/src/curation.rs` in `cargo-gocar`). Nothing here is baked
into a crate.

## Files the tools read

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

## Still to move here

These are curated data today only by accident of being Rust `const`s in
`cargo-gocar`; their `evidence` points at the research docs, which belong here
alongside them:

- **`sources.json`** — the upstream GitHub source maps (`repo`/`tree`/`tag`) —
  today `const UPSTREAM_GITHUB` in `export-fork-map` (T-45).
- **`compile-markers.json`** — the docs/07 compile-verified badges.
- **`kit-probes.json`** — the docs/12 kit-rebase probes.
- **`research/`** — those study documents (docs/07, docs/12, …).

## Rules

- Nothing derived lives here (no crates.io data, no measurements) — that is the
  `data` / `measured` branches.
- Every rule, companion, or source link names the measured evidence that
  justifies it, and is reviewed like code.
