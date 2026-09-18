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

These began as verbatim copies of `cargo-gocar`'s
`docs/04-user-docs/<same file name>`, and the `evidence` fields in the curated
JSON keep the tool-repo path (`docs/04-user-docs/07-…md`) — that path names
*which* study the probe came from; this directory is where it is published.

**These published copies have since been rewritten for readability
(2026-09-18): one voice, a common section order (what this establishes / what
was not known / what was run / what it means for you / what it does not
establish / provenance), no ticket ids, and no pointers to documents that are
not published here.** Every number and quote was checked back against the
original. The tool repo's copies are still the originals — and are now *older*
than these — so a plain resync from there would overwrite this work. Carry
edits in both directions rather than copying one way:

```bash
# cargo-gocar checkout: docs/04-user-docs/  →  this directory
for n in 07 08 09 10 11 12 13; do
  diff -u "docs/04-user-docs/${n}-"*.md "curation/research/${n}-"*.md
done
```

## Caveat

None left: every cross-reference in these files now points at a document that
is published here, and the fork map's suite enforces that (a relative link to
an unpublished doc degrades to plain text in the reader, so it is asserted
against instead — see `web/forkmap-spa/tests/markdown.test.ts`). The study
docs' openings are additionally diff-checked against the map's committed
excerpts, so an edit here that moves an opening shows up as a test failure
rather than a stale teaser.
