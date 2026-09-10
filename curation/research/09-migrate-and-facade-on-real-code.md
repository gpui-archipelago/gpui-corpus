# 09 — Study: do migrate and the facade carry a real app across the 1.17.2 break?

**Status:** completed study, 2026-09-06 (T-19, MVP blocker sweep). Verdict:
**partial** — the migrate/facade mechanics are exact and byte-surgical on
real code, the classifications and sites are honest, but across the real
unofficial 1.17.2 profiler rewrite the tool's *auto-driven* share is zero for
realistic app code: the only confirmed rule targets a function real apps
never call, and the rule's rename does **not compile** at the real target
(the successor exists only under the fork's non-default `profiler` feature
and takes a wrapped payload). The real move is manual, with the tool naming
every site and reason. The fix is **rule-shape + compile-vouching**, named
below, not a report/rewriter defect. **T-26 (2026-09-07) implemented the
cfg-vouching half**: the item model measures the successor's gate, so the
tool now *refuses* the seed rename at any target that carries the successor
only under `cfg(feature = "profiler")` — the v1 flow's non-compiling rewrite
(H3) and the v1 facade's un-compilable alias (H4) are refused with the gate
as the reason; the payload-wrap rule shape and full compile-vouching remain
T-27/T-28 deltas.

## Question

Migrate (T-14, UC-07) and the facade (T-13/T-16, UC-06) had only ever run on
fixture source that is *never compiled* (the e2e harness) or on fixture forks
mirroring recorded surfaces — the T-14 demo's call site was the synthetic
`record_frame_timing(())`, and the facade's debt signal had never been
checked against a real fork's compile behavior. UC-06/07's claim — "switching
is report → guided codemod → clean verify-env (or an explicit facade
deferral)" — was unverified. So: **does report → facade → migrate → add →
lock → build → verify-env carry a real, compiling-at-1.16.1 app across
1.17.2 to 1.18.1 — and where do hands take over?**

## The transition under test (real API shapes)

Measured surfaces (dataset, all 67/67 attested) say the 1.17.2 rewrite
removed `fn:set_frame_trace_enabled`, `fn:frame_trace_enabled` and
`fn:record_frame_timing` and added `fn:record_frame_event` +
`enum:FrameEvent`. The real signatures (cached fork sources) add the facts
item-level digests cannot see:

| 1.16.1 (`ef93dce2…`) | 1.18.1 (`d143b846…`) |
| --- | --- |
| `pub fn set_frame_trace_enabled(bool) -> bool` — unconditional | gone (rustc: "similarly named function `set_trace_enabled` defined here") |
| `pub fn frame_trace_enabled() -> bool` — unconditional | gone (same) |
| `pub fn record_frame_timing(FrameTiming)` — unconditional | gone; `pub fn record_frame_event(FrameEvent)` is `#[cfg(feature = "profiler")]` (non-default) |
| `FrameTiming` (pub fields) — unconditional | `FrameTiming` still exists — `#[cfg(feature = "profiler")]`; wrapped: `FrameEvent::Draw(FrameTiming)` |
| `FrameTimingCollector::new()` / `collect_unseen() -> Vec<FrameTiming>` — unconditional | struct + methods `#[cfg(feature = "profiler")]` (absent in a default build) |
| `pub fn set_trace_enabled` / `trace_enabled` — unconditional | same pair, unconditional — the task-level toggles the 1.17.2 rewrite unified frame tracing into |

The corpus is cfg-blind (STATUS limitation 2): it measures `fn:record_frame_event`,
`struct:FrameTiming` and `struct:FrameTimingCollector` at 1.17.2/1.18.1 even
though a default-feature build ships none of them.

## Probe design

One project, `target/study/usage-probe-migrate` — the T-18 uno16 probe's
exact source (compiles at gpui-unofficial 1.16.1 + companion, rustc 1.97.1)
plus one new module:

- **C1** (`profiling.rs`, byte-identical to T-18): realistic app
  instrumentation calling the frame-trace toggles through the `gpui` rename —
  direct-path fn calls, the shape the v0 corpus measures.
- **C4** (`frame_record_probe.rs`, added): *deliberately rule-shaped*. No real
  app calls `record_frame_timing` (the fork records its own frames inside
  `Window::draw`, and the payload's fields are built from the fork-internal
  scheduler `Instant`, which app code cannot name). To point the rewriter at
  real, compiling-at-1.16.1 fork source, C4 drains whole `FrameTiming` values
  off `FrameTimingCollector` (the one public API that yields them without
  naming the scheduler types) and re-records them through the rule's subject.
  Semantically nonsense (double-counting frames); documented in-module as a
  compile-and-rules probe.

Baseline ground truth: `cargo build --locked` green at 1.16.1 (offline,
2m02s cold), zero app warnings; `verify-env` exit 0.

## The flow, step by step (evidence in `target/study/migrate-evidence/`)

| Step | Tool output (abridged) | Driven by |
| --- | --- | --- |
| 1. `report` at 1.16.1 | used slice: **3 checkable** (the C1 toggles + C4's `record_frame_timing`), 19 unprovable, 32 unscanned. Windows: unofficial `1.16.1…1.16.3`, gpui-box `0.1.0…0.1.1`, gpui-ce `0.2.2`. Each of the 3 checkable fns: "removed at **1.17.2** — every later measured release", with its exact site | tool |
| 2. `facade` (bound 1.16.1) | 1 forward polyfill (`record_frame_event` via the rule), not referenced by this app; 0 debt | tool |
| 2b. `facade --provider gpui-unofficial` (target 1.18.1) | 1 deprecated alias `record_frame_timing → record_frame_event`, **IN USE** at `src/frame_record_probe.rs:28:9`; debt 1. The C1 toggles appear *nowhere* — not rule subjects, so no shim exists for them | tool (coverage bounded by the rule store) |
| 3. `migrate --provider gpui-unofficial` (dry run) | 0 kept · **1 rewrite** (the seed rule, C4 site) · **2 manual** (`set_frame_trace_enabled` @`profiling.rs:15:5`, `frame_trace_enabled` @`profiling.rs:20:5` — "removed at the target … the stream's item diff offers no plausible same-kind successor — a semantic change") · 19 unprovable. Diff: exactly one line | tool |
| 3b. `migrate --write` | wrote 1 rewrite in 1 file. `main.rs` + `profiling.rs` byte-identical (cmp) — untouched code preserved | tool |
| 4. `add gpui-unofficial` | rebinds fork **and** companion to `=1.18.1` in one manifest delta (the T-17 binding unit) | tool |
| 5. `lock` | pin 1.18.1, proof ledger written, 703 packages resolved; cargo notes `tinyvec 1.12.0 (available: 1.13.2)` | tool |
| 6. `cargo build --locked` after migrate | **4 errors** on the migrate-rewritten source (see below) | — |
| 7. manual leg | rename the C1 toggles to their real successors (`set_trace_enabled`/`trace_enabled` — the 1.17.2 rewrite unified frame tracing into the task toggles, a semantic broadening a human decides); remove the synthetic C4 module (its subject no longer exists in a default 1.18.1 build) | hands |
| 8. end state | `build --locked` green, zero app warnings; `verify-env` exit 0 (`✓ verified gpui-unofficial 1.18.1`, 0 violations, 702 untracked warnings); `check-workspace` clean (1 binding · 1 match · 0 clash); `report` at 1.18.1: 2 checkable (`set_trace_enabled`/`trace_enabled`) — kept, window `1.18.1…1.18.1` + gpui-ce 0.2.2; `migrate` rerun: "nothing to migrate" | tool (audit) |

### Step 6 in full — what the migrated source hit at the real 1.18.1

```
error[E0433]: cannot find `FrameTimingCollector` in `gpui`
  --> src/frame_record_probe.rs:24:31     (collector machinery is cfg(profiler) at 1.18.1)
error[E0425]: cannot find function `record_frame_event` in crate `gpui`
  --> src/frame_record_probe.rs:28:15     (migrate's own rewrite — cfg(profiler) at 1.18.1)
error[E0425]: cannot find function `set_frame_trace_enabled` in crate `gpui`
  --> src/profiling.rs:15:11
      "similarly named function `set_trace_enabled` defined here"  (profiler.rs:707)
error[E0425]: cannot find function `frame_trace_enabled` in crate `gpui`
  --> src/profiling.rs:20:11             (same suggestion)
```

The dataset called `record_frame_event`, `FrameTiming` and
`FrameTimingCollector` "present at 1.18.1" (cfg-blind measurement) and the
default-feature build contradicts that — the exact cfg-blindness failure
T-20's blocker hypothesis predicts, caught here on real code. rustc's own
"similarly named function" suggestions name the manual successors a human
confirmed by hand in step 7.

## Verdicts (hypotheses H1–H4)

- **H1 — real profiler signatures differ from the fixture shape: confirmed.**
  The rule's subject takes a real `FrameTiming` whose fields need the
  fork-internal scheduler `Instant`; the T-14 fixture shape
  (`record_frame_timing(())`) was never compile-valid at either end. Pointed
  at a real compiling-at-1.16.1 call (C4), the rewriter fires correctly and
  byte-surgically.
- **H2 — the report cannot attribute the profiler call sites in realistic
  code: partial.** Direct-path fn calls are attributed exactly — report,
  migrate and the facade's in-use scan all name the same sites
  (`profiling.rs:15/20`, `frame_record_probe.rs:28`). The *method-shaped*
  profiler consumption (`FrameTimingCollector::new`/`collect_unseen`) is
  invisible to the v0 surface (unprovable/unscanned), which is the T-18 gap —
  and at the target those methods are cfg-hidden anyway, so compilation fails
  where the report is silent.
- **H3 — the seed rule was dataset-vouched, not compile-vouched: confirmed,
  sharpened.** At 1.18.1 the successor `record_frame_event` exists only under
  the non-default `profiler` feature, and its payload is `FrameEvent` — the
  mechanical successor of `record_frame_timing(x: FrameTiming)` is
  `record_frame_event(FrameEvent::Draw(x))` plus a feature enable, not a bare
  rename. Migrate's one rewrite therefore produced a non-compiling end state
  (E0425). The rule's own note ("re-verify payload semantics before relying on
  this rule") is now compile-evidenced.
- **H4 — the facade tables were never compiled against real forks: partial.**
  The table is exactly right about what it covers: the 1.18.1-bound alias
  row is marked IN USE at the precise site migrate rewrote. But it is
  bounded by rule coverage: the app's actual breakage at that bound (3
  removed fn calls; 2 with no rule, 1 whose alias cannot exist in a
  default-feature build because its delegation target is cfg'd out) is
  invisible to the debt signal, which reported "1 deprecated alias in use"
  while the app could not compile. A v1 facade *crate* built from the table
  alone would not keep this app compiling.

## Verdict — **partial**

On real code the migrate/facade mechanics are sound (exact sites, byte-surgical
edits, untouched files byte-identical, honest manual classifications with
reasons) and the audit loop closes clean — but the **auto-driven share of the
real move is zero**: the one confirmed rule targets a function real apps do
not call, and the rule as shaped cannot compile at the real target. Every
real app-facing break of the 1.17.2 rewrite is manual, and the tool's value
is exact attribution plus the manual list — real value, but not UC-07's
guided-codemod promise on this transition. The facade's debt signal
under-counts real breakage (rule-coverage-bounded, not compile-bounded).

## Named v1 deltas (not solved here)

1. **Compile-vouching for confirmed rules.** The seed rule passed the
   dataset-backed provenance test yet produced non-compiling code at its own
   declared target — a rule should be compile-proven (or carry an explicit
   "not compile-vouched" marker the plan prints) before the engine applies it.
2. **Rule-store shape beyond bare renames.** The real successor here is a
   payload wrap (`FrameEvent::Draw(x)`) plus a feature enablement; v1 recipes
   have no vocabulary for either. Parameter-level/payload recipes wait on the
   phase-3 canonicalizing pass, but a "requires-feature" gate on a rule (fire
   only when the target can expose the successor) is expressible now.
3. **App-facing rule coverage.** The frame-trace toggle pair is the app-facing
   face of the same rewrite, and its successor (`set_trace_enabled` /
   `trace_enabled`, unconditional at both ends) is expressible in today's
   store and compile-verified by this study's step 7 — but it is a *semantic*
   successor (frame-only → whole-trace scope), so it needs a human
   confirmation with that note, and the derived-suggestion pass cannot offer
   it (the successor is not an *added* item — it exists at the baseline too).
4. **Method-role and cfg awareness in the surface.** The collector API
   (`FrameTimingCollector::collect_unseen`) is app-visible at 1.16.1, absent
   at 1.18.1's default build, and invisible to the v0 model — the T-18
   measurement gap plus the STATUS limitation-2 cfg gap, both on the same
   code path.

## Reproducing

The study project (`usage-probe-migrate`, full source + `.gocar` proof +
lock) and its step evidence (`migrate-evidence/step1…step8`) live under
`target/study/` — gitignored, rebuildable. Baseline compile needs the warm
registry cache or network; the end-state graph needs crates.io (the 1.18.1
companion/backend pair was not cached locally).

## Claims this study refines

- Root README "Honest limitations" migrate bullet and the STATUS.md migrate
  limitation 9: the v1 rename-only scope is now *measured on real code* — and
  sharpened from "parameter-level recipes wait on phase 3" to "even the seed
  rename does not compile at its own target without a payload wrap and a
  feature gate; compile-vouching is the missing guard".
- Root README facade bullet and the STATUS.md facade limitation 10: the
  "real-fork compilation is the deferred verification tier" caveat is now
  executed — the table's coverage (rule-bounded) and the debt signal's
  completeness (alias-debt only) are measured against a real compiling app;
  the fixture-compile demo overstates what a v1 facade could keep compiling.
- docs/04-user-docs/07/08's equivalence story: unchanged — 1.16.1's default
  build *does* ship the measured profiler names the C1 module calls; the
  compile-proven `ef93dce2…` generation story stands.
- UC-06/UC-07's implemented-v1 status: unchanged in mechanics, refined in
  expectation — see the added "Measured on real code" sections there.
