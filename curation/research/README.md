# Research (the studies the curated data cites)

The evidence behind the curated inputs: the real-fork compile case study
(`07`), the used-API / migrate / facade studies (`08`, `09`), the kit +
toolchain-floor notes (`10`, `11`) and the alias-shim compile half (`12`,
`13`).

They live here because the fork map is public and its **study reader** links
them — "read the full doc" must resolve for anyone, and the curated
`compile-markers.json` / `kit-probes.json` entries cite them as their
`evidence`. The tool repo (`cargo-gocar`) is private, so the published copies
live with the curated data instead.

## Provenance

These are verbatim copies of `cargo-gocar`'s
`docs/04-user-docs/<same file name>`, which remains the authoring source. The
`evidence` fields in the curated JSON keep the tool-repo path
(`docs/04-user-docs/07-…md`) — that path names *which* study the probe came
from; this directory is where it is published.

To resync after a doc edit in `cargo-gocar`:

```bash
for n in 07 08 09 10 11 12 13; do
  cp "docs/04-user-docs/${n}-"*.md ../gpui-corpus/curation/research/
done
```

(a `cargo-gocar` checkout: `docs/04-user-docs/` → a `gpui-corpus` checkout:
`curation/research/`).

## Caveat

A few cross-references inside these docs point at documents that are not
published here (other `docs/04-user-docs/` files, task files). Those links
resolve only in the authoring repo.
