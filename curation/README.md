# Curation (hand-edited inputs)

`main` is the human side of this repo. Everything here is edited by hand,
reviewed like code, and consumed by the automation on the `data` branch.

Planned contents (not populated yet — sourced data comes first):

- **`migration-rules.json`** — the human-confirmed rename recipes keyed to
  measured item deltas. Today this lives in `cargo-gocar`
  (`crates/gocar-gpui/data/migration-rules.json`); it belongs here.
- **`sources.json`** — the upstream source links per provider (`repository`,
  `tree`, and the per-version git tag/ref pattern) that back the site's
  docs.rs / GitHub source anchors. Today these ride the dataset's provider
  `upstream` map; they are curation, not measurement, so they belong here.
- **`research/`** — field notes and case-study documents that explain *why* a
  rule or a source link exists.

Rules:

- Nothing derived is committed here (no crates.io data, no measurements) —
  that is the `data` branch.
- Every rule/link should name the measured evidence that justifies it.
