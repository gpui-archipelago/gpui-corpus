# 08 — Study: is the used-API report meaningful on real GPUI code?

**Status:** completed study, 2026-09-06 (T-18, MVP blocker sweep) — **measurement
unblock landed 2026-09-07 by T-25** (crate-local re-export-chain resolution in
`gocar-index::analyze_crate`; corpus re-measured; dataset ripple), and the
**item-model v2 re-measure landed 2026-09-07 by T-26** (methods of public
types, module-qualified identity, cfg provenance — Phase-3 step 1; see the
[T-26 resolution](#t-26-resolution-2026-09-07) below for the re-run census).
The v0 verdict of the study was **partial**: the report was honest and exact on
the ~10% of a real app's used surface the old corpus measured, and mute on the
view-building majority because the corpus walker recorded the wrong slice of
the fork APIs. That blocker is now closed at the item level — see the
[T-25 resolution](#t-25-resolution-2026-09-07) below for the re-run census.
The *remaining* mute share is v2 policy, not measurement: trait-interface
items, derives and external-crate members stay outside the syn-level item
model (the rustdoc-JSON producer of T-27 is the next sharpener).

## Question

`cargo gocar report` (T-12) delivers UC-05's *targeted-upgrades* promise: it
checks an app's used-API slice against every measured release of every
provider and prints compatible windows plus exact incompatibilities. But the
v0 item model measures `type`/`trait`/`fn` names only, and real GPUI usage is
overwhelmingly **method chains on elements** (`div().flex().child(…)`),
impl-block items, and derive/macro attributes — every one of which v1 must
list as *unprovable, never assumed*. So: on a real, compiling GPUI app, does
the report advise — or return "nothing checkable" on exactly the code it
exists for? And are its claimed windows compile-true?

## Probe design (effect separable per the task's note)

One app, written against the **real post-split fork API** (patterns verified
against the gpui-box 0.1.1 / gpui-unofficial 1.16.1 sources in the local
registry cache, mirroring their `hello_world`/`text`/`data_table` examples):

- a window root view `TaskBoard` (`impl Render`, `Context<Self>`),
- a repeated child component `TaskRow` built from
  `#[derive(IntoElement)]` (the gpui derive macro) + `impl RenderOnce`,
  embedded via `.children(…)`,
- element builder chains (`div().flex().px_2().text_color(…)` — methods on
  `Styled`/`ParentElement` via the `prelude::*` glob),
- method calls on statically-typed receivers (`cx.open_window`,
  `cx.activate`, `Bounds::centered`, `self.title.clone()`),
- a separable **surface-named module** (`profiling.rs`, component C1) that
  calls exactly the names the v0 corpus measures on this generation
  (`set_frame_trace_enabled`, `frame_trace_enabled` — the profiler fns the
  real 1.17.2 transition removed), so the *checkable* share of the app is
  attributable separately from the method/derive-heavy share (C2).

The probe is a gocar-managed project scaffolded through the tool
(`cargo gocar new … --provider gpui-box`, floor 0.1.1); an unofficial-1.16.1
twin (hand-set floor — see findings) and an unofficial-1.18.1 twin carry the
same source. Probe code lives under `target/study/usage-probe-*` with raw
report/slice outputs in `target/study/usage-probe-evidence/` (gitignored).

## Compile ground truth

| Binding | Toolchain | Result |
| --- | --- | --- |
| gpui-box 0.1.1 + gpui-box-platform 0.1.1 (`usage-probe-box`) | rustc 1.97.1 | ✓ compiles (1m53s; zero app warnings), `verify-env` exit 0 |
| gpui-unofficial 1.16.1 + gpui-platform-gpui-unofficial 1.16.1 (`usage-probe-uno16`) | rustc 1.97.1 | ✓ compiles (1m58s), `verify-env` exit 0 |

Both bindings are the two sides of the dataset-asserted `ef93dce2…`
generation — the probe compiles across it, like the case-study hello
(docs/04-user-docs/07). The profiler calls compile on both, proving the C1
names are real API on the window's interior.

## Census (identical source at every baseline) — re-run 2026-09-07 (T-25)

Used slice: **20 used symbols / 34 attributed call sites, 30 unscanned
sites** (64 gpui-relevant sites across 2 files — unchanged source).

| Baseline | checkable | unprovable | unscanned | windows claimed (post-T-25) |
| --- | --- | --- | --- | --- |
| gpui-box 0.1.1 (`5d9475c8…`) | 13 | 7 | 30 | box `0.1.1…0.1.1` (exact-copy window) |
| gpui-unofficial 1.16.1 (`6b33d862…`) | 13 | 7 | 30 | unofficial `1.16.1…1.16.3` |
| gpui-unofficial 1.18.1 (`d59bbb49…`) | 11 | 9 | 30 | unofficial `1.18.1…1.18.1` |

Pre-T-25 the same source reported 2/18/30 (box, uno 1.16.1) and 0/20/30
(uno 1.18.1). **Of the 12 re-export-chain unprovables, 11 are checkable now**
(`div`, `px`, `rgb`, `size`, `App`, `Context`, `Window`, `WindowOptions`,
`IntoElement`, `Render`, `RenderOnce`) — the acceptance proof that the fix
lands where the census said it would. The remaining 7 unprovables split by
reason: **1 external re-export** (`SharedString` — `gpui_shared_string` is not
a corpus member; the T-25 boundary, opaque by design), **5 methods** outside
the v0 surface (`open_window::App`, `activate::App`, `centered::Bounds`,
`clone::SharedString`, `Windowed::WindowBounds`) and **1 derive**
(`IntoElement`) — all v1/Phase-3 policy, not measurement.

The 30 unscanned sites are unchanged: 1 glob (`prelude::*`) and 29
untyped-receiver method calls (23 gpui-shaped builder-chain calls, 6
std/companion noise).

**Share the report can reason about: 13 of 20 used symbols (65%); the two
profiler fns count on the pre-1.17.2 baselines, 11 on the 1.18.1 baseline.**

## T-26 resolution (2026-09-07) — item-model v2: methods are checkable

Phase-3 step 1 (the v2 item model: inherent methods of public types,
module-qualified identity, parameter-type fn text, cfg provenance) landed
with a full 67-version re-measure. Re-run census (same probe source, v2
dataset; hashes are the v2 values):

| Baseline | checkable | unprovable | unscanned | windows claimed (post-T-26) |
| --- | --- | --- | --- | --- |
| gpui-box 0.1.1 (`ed121d14…`) | 16 | 4 | 30 | box `0.1.1…0.1.1` |
| gpui-unofficial 1.16.1 (`0d9280ec…`) | 16 | 4 | 30 | unofficial `1.16.1…1.16.3` |
| gpui-unofficial 1.18.1 (`b78c7b86…`) | 14 | 6 | 30 | unofficial `1.18.1…1.18.1` |

**3 of the 5 method unprovables are checkable now** — `open_window::App`
(`fn:App::open_window`), `activate::App`, `centered::Bounds`
(`fn:Bounds::centered`) are measured members stable through the stream, and
method-role usage with a typed container bridges onto them. The 4 remaining
unprovables at the pre-break baselines are the v2 policy set, each with its
reason: the external `SharedString` (type + method — `gpui_shared_string` is
not a corpus member), the trait-shaped `Windowed::WindowBounds` member
(trait-interface items live inside the trait's canonical text) and the
`IntoElement` derive (derive definitions stay outside the item model). At
the 1.18.1 baseline the two removed profiler fns are unprovable too (the
report is forward-looking — the one-directionality finding stands). The
30 unscanned sites are unchanged (1 glob + 29 untyped-receiver calls — the
receiver-typing gap is consumer-side inference, a separately scoped note in
T-26). The windows narrowed to the exact-copy class on every baseline
(feasibility 1 of 6): `App`/`Window`/`WindowOptions` type digests refuse
across forks under v2 exactly as under T-25 — finding 5's cross-fork story
is unchanged, and the compile-ground-truth probe still builds on box 0.1.1
and uno 1.16.1.

## Windows claimed vs. compile reality (re-run, 2026-09-07)

| Claimed window (uno-1.16.1 baseline) | Cross-check |
| --- | --- |
| unofficial `1.16.1…1.16.3` (`6b33d862…`) | ✓ same exact-copy class — the probe compiled at 1.16.1 (this study); 1.16.2/1.16.3 are byte-identical republishes (T-25) |
| unofficial `≥ 1.17.2` excluded | ✓ `set_frame_trace_enabled` removed at 1.17.2 (measured; the probe's C1 calls would not compile there) |
| gpui-box, gpui-ce, gpui-pre, kael, gpui: no window | dataset-consistent — the T-25 full-surface re-measurement shows the forks' *type-level* digests for `App`/`Window`/`WindowOptions` genuinely differ from the baseline class (the snapshots drifted); compile evidence that the probe still builds on box 0.1.1 stands, but it exercises *fields* of those types, and the v0 item model has no field-level granularity to prove it (see findings 5–6) |

Feasibility on every baseline is now **1 of 6 providers** (the bound stream's
own exact-copy window) — down from 3 pre-T-25, because the two checkable
symbols that used to carry the box/ce cross-fork claim were the only ones
whose digests matched, and they are gone from the ≥1.17.2 baselines while the
newly measured type-level symbols refuse across forks.

## Findings

1. **The v0 corpus measured the wrong slice of the fork APIs** (fixed by
   T-25). The report's silence on `div`/`Render`/`Window`/… was measurement:
   `gocar-index::analyze_crate` descended only into `pub mod` file modules,
   while every fork exposes its app-facing API from **private** modules via
   glob re-export hubs (`mod app;` + `pub use app::*`, the Zed layout), so
   those items were never recorded — the surface captured the hub's `use:`
   lines plus the genuinely `pub mod` modules (profiler, colors, prelude…).
   Surface sizes said it: unofficial/box 104 items, kael 630 (its own feature
   modules, but *none* of the shared view names). T-25's re-export-chain
   resolution (private modules descended, items attributed under the hub's
   `use:` text, module-directory-aware file resolution) re-measured the
   corpus: unofficial 1.18.1 now carries 624 items and the view-building
   names are measured on every fork that publishes them.
2. **A real app's compile-contract bulk is still unprovable — but the reason
   narrowed.** The probe compiles on `div` chains, derives, `Render` and the
   method APIs; after T-25 the *names* are measured (13 of 20 used symbols
   checkable) while the *uses that are method calls on them* stay unscanned
   (29 sites) or unprovable by policy (5 methods, 1 derive). The remaining
   blocker is v1 granularity (impl-block items, receiver typing), not
   coverage.
3. **Removal advice is one-directional** (unchanged). From the *broken*
   baseline (unofficial 1.18.1, where `set_frame_trace_enabled` was removed
   at 1.17.2) the same source reports the two profiler calls as unprovable
   (no key in the new baseline's surface) instead of "removed at 1.17.2" —
   the report is a forward-looking instrument; only the pre-break baseline
   can name a removal. Every other measured symbol is checkable at 1.18.1
   (11 symbols), so the one-directionality now applies to the removed fns
   alone.
4. **Unscanned-bucket noise** (unchanged). 6 of the 29 "untyped receiver"
   entries are std/companion methods (`cloned`, `map`, `into`, `unwrap`,
   `is_none`, companion `run`); the 23 gpui-shaped builder-chain calls are
   the real gap — scanner-side receiver typing (unblock item 2 below).
5. **The full-surface fix surfaced real cross-fork drift at type granularity.**
   With the used symbols measured, the report now sees that `App`/`Window`/
   `WindowOptions` digests differ between the uno-1.16.1 baseline and every
   other fork's releases (and between gpui-box 0.1.0 and 0.1.1): the fork
   snapshots genuinely drifted (box 0.1.1 differs from uno 1.16.3 in 26 of
   88 source files — fields like gpui-pre's added
   `focused_text_input_active`). The old study's box/ce "unchanged" rows
   were an artifact of those symbols being *absent* from the old surfaces
   (unprovable ≠ equal). The report's windows are now exact-copy-only, and
   the probe's own compile ground truth (builds on box 0.1.1 and uno 1.16.1)
   exercises *fields* the v0 item model cannot see — so the report
   over-refuses cross-fork compatibility relative to compile evidence. This
   is the v0 price of type-level digests (no field/member granularity); the
   fix is Phase-3's parameter/member-level surfaces, not a measurement
   correction here.
6. **v1 tool-shape gaps observed along the way** (recorded, not fixed):
   `cargo gocar new` floors only at the provider's latest stable — the
   1.16.1 twin needed the case-study-style hand-set floor; and `report`
   needs a gocar-managed manifest (the plain `hello-box` project is not
   reportable without metadata).

## T-25 resolution (2026-09-07) — measurement unblock landed

The corpus walker now resolves crate-local re-export chains (the "Minimal
unblock" item 1 below, its own task): `pub use app::*` / `pub use
crate::…` descend into the private module and attribute its items
(cfg-blind, flat identity, multi-hop through private modules, cycle-guarded;
aliases keep their exported name; the hub `use:` entries stay). File-module
resolution is module-directory-aware (`src/app.rs` → `src/app/x.rs`), which
also dissolved the old benign `hang`/`journal` warnings. The 67-version
corpus was re-measured and merged: every surface grew (uno 1.18.1: 109 →
624 items), and the old cross-fork equivalence classes split — full-surface
equality now holds only for byte-identical republishes within a stream
(uno 1.16.1≡1.16.2≡1.16.3, kael 0.4.0≡0.4.1, gpui-pre 0.3.1–0.3.3, …), a
finding documented in the task outcome and STATUS.md limitation 7. The
re-run census above is the acceptance proof: the 12 re-export-chain
unprovables became 11 checkable symbols; the one holdout (`SharedString`)
is an external-crate re-export, opaque by design.

## Verdict — **partial → resolved at the item level (T-25)**

The study's measurement-layer blocker is closed: on real GPUI code the
report now reasons about 65% of used symbols (13/20), names exact removal
blame (1.17.2), and refuses nothing it could prove — while the two
remaining silent classes are honest v1 boundaries (impl-block methods and
derives = Phase-3 surfaces; cross-fork type-level drift = real, reported as
re-signatures, with the field-level caveat of finding 5). The
*targeted-upgrades* promise is delivered for the names, not yet for the
method/derive majority of view-building code — that is now purely a v1
granularity gap, not a data gap.

## Minimal unblock (status)

1. **Re-export-chain resolution in the corpus walker** — **solved by T-25**
   (2026-09-07): re-measured corpus + dataset ripple; see the resolution
   section above. (One *expected* result did not survive: the old
   `ef93dce2…` class *split* rather than strengthened — the fork snapshots
   drift in real items, so whole-surface equality is an exact-copy relation,
   and cross-fork compatibility lives at the used-slice item level, not the
   whole-surface hash level.)
2. **Scanner-side receiver typing** (v0) — open: attribute element-builder
   method calls after `div()` (a fn whose return type the measured surface
   now knows) and filter std-method noise (`cloned`/`map`/`into`/…) out of
   `unresolved_receivers`. This is the largest remaining checkable-share
   lever for real code (23 unscanned builder-chain sites here).
3. **Method/derive-level surfaces** (Phase 3's rustdoc/TVM pass) — the real
   fix for impl-block items and derives; also the fix for finding 5's
   field-level caveat (parameter/member granularity).

Not solved here by design: method surfaces are Phase-3 work; the corpus
re-measurement was a data task and landed with its own ripple (dataset,
dataset-sensitive tests, this doc, STATUS limitation 7 — all in the T-25
commit).

## Related claims this study refines

- STATUS.md limitation 7 and README's report bullet: the measurement root
  cause (pub-mod-only walking vs. private-module re-export hubs) is fixed by
  T-25; limitation 7 now records the exact-copy consequence.
- UC-05's "Gaps & design decisions": v1's method/derive unprovability
  confirmed with a census; the measurement-layer gap is closed (T-25).
- docs/04-user-docs/07's equivalence story: the *compile-proven* generation
  equality stands as compile evidence, but the dataset's whole-surface epoch
  labels changed — T-25's re-measurement split the old classes (see
  limitation 7 and data/README); app-side compile evidence remains the
  ground truth for used-slice claims the item model cannot yet express.
