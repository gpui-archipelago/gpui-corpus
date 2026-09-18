# 09 — Testing automated migration and compatibility shims on real code

## What this establishes

Automated migration carries none of this upgrade on realistic application
code: the one rewrite rule the tool confirmed targets a function real apps
never call, and the edit it produced does not compile. What the tool is
excellent at is the other half — it names every broken line, with the reason,
and rewrites nothing it was not asked to touch.

## What was not known before

The migrate and facade tools had only ever run against fixture source that is
never compiled, or against fixture forks mirroring recorded surfaces. Their
claim — *switch by report → guided codemod → a clean environment check* — had
never been executed on real code.

The question: does `report → facade → migrate → add → lock → build →
verify-env` carry a real app, compiling at 1.16.1, across the 1.17.2 profiler
rewrite to 1.18.1 — and where do hands have to take over?

## What was run

On 2026-09-06, one project under rustc 1.97.1 on Linux: a real app bound to
`gpui-unofficial` 1.16.1 (the probe from [doc 08](08-used-api-report-on-real-code.md),
whose instrumentation calls the frame-trace toggles), plus **one deliberately
rule-shaped module**. That second module exists because no real app calls
`record_frame_timing`: the fork records its own frames internally, and the
payload's fields are built from a fork-internal type app code cannot name. To
point the rewriter at real, compiling code, the module drains whole
`FrameTiming` values off the public collector and re-records them through the
rule's subject — semantically nonsense, and documented in-file as a probe.

Baseline ground truth: `cargo build --locked` green (2m02s cold, zero app
warnings), `verify-env` exit 0.

| Step | What it did | Driven by |
| --- | --- | --- |
| 1. `report` at 1.16.1 | used slice: **3 checkable** (the two toggles + the probe's call), 19 unprovable, 32 unscanned; each checkable function named as "removed at **1.17.2**", with its exact site. Windows: unofficial `1.16.1…1.16.3`, gpui-box `0.1.0…0.1.1`, gpui-ce `0.2.2` | tool |
| 2. `facade` at 1.16.1 | 1 forward polyfill (`record_frame_event`), not referenced by this app; **0 debt** | tool |
| 3. `facade --provider gpui-unofficial` (target 1.18.1) | **1 deprecated alias in use**, `record_frame_timing → record_frame_event` at `src/frame_record_probe.rs:28:9`; debt 1. The two toggles appear *nowhere* — they are not rule subjects, so no shim exists for them | tool |
| 4. `migrate --provider gpui-unofficial` (dry run) | 0 kept · **1 rewrite** (the probe's site) · **2 manual** (`set_frame_trace_enabled` at `profiling.rs:15:5`, `frame_trace_enabled` at `profiling.rs:20:5` — "no plausible same-kind successor — a semantic change") | tool |
| 5. `migrate --write` | wrote that one rewrite, in one file. `main.rs` and `profiling.rs` byte-identical (`cmp`) — untouched code preserved | tool |
| 6. `add` + `lock` | rebinds the fork **and** its companion to `=1.18.1`; 703 packages resolved | tool |
| 7. `cargo build --locked` | **4 errors**, all on the source the tool had just written | compiler |
| 8. Hands | rename the two toggles to their real successors; drop the synthetic module | human |
| 9. End state | build green, zero app warnings; `verify-env` exit 0; `check-workspace` clean (1 binding, 1 match, 0 clash); `report` at 1.18.1: the 2 renamed toggles checkable, window `1.18.1…1.18.1`; `migrate` re-run: "nothing to migrate" | tool |

### What the migrated source hit

```
error[E0433]: cannot find `FrameTimingCollector` in `gpui`
  --> src/frame_record_probe.rs:24:31     (collector machinery is cfg(profiler) at 1.18.1)
error[E0425]: cannot find function `record_frame_event` in crate `gpui`
  --> src/frame_record_probe.rs:28:15     (migrate's own rewrite — cfg(profiler) at 1.18.1)
error[E0425]: cannot find function `set_frame_trace_enabled` in crate `gpui`
  --> src/profiling.rs:15:11
      "similarly named function `set_trace_enabled` defined here"
error[E0425]: cannot find function `frame_trace_enabled` in crate `gpui`
  --> src/profiling.rs:20:11             (same suggestion)
```

Two things went wrong with the one rewrite, and both are about the *shape* of
the rule rather than its aim:

1. Its successor only exists behind a non-default feature
   (`#[cfg(feature = "profiler")]`), so a default build has no such function.
2. The real successor is not a rename: `record_frame_timing(frame)` becomes
   `record_frame_event(FrameEvent::Draw(frame))` — a payload wrap — plus the
   feature enablement.

And the dataset had called `record_frame_event`, `FrameTiming` and
`FrameTimingCollector` "present at 1.18.1" — true of the measured surface,
false of a default-feature build. That cfg-blindness is the failure this study
caught on real code, and it is why the tool now checks a successor's gate
before offering the rename: a rename whose target is feature-gated is refused
outright instead of applied.

## What it means for you

- **Treat a migration run as an inspection list, not a codemod.** The sites
  and the reasons are exact; the rewrites are not safe to trust. Plan for the
  code edits to be yours.
- **Frame-tracing toggles need a human decision.** `set_frame_trace_enabled`
  and `frame_trace_enabled` became `set_trace_enabled` and `trace_enabled` —
  the rewrite unified frame tracing into whole-task tracing, so the swap
  broadens what gets recorded. You decide whether that scope is what you want,
  and re-check any timing you rely on.
- **A quiet facade is not a clean bill of health.** Its debt signal is bounded
  by its rule store: here it reported one alias in use while three calls were
  broken and the app could not compile. Run it against the *target* release to
  see an alias at all (bound to the baseline it reports none), and read a
  missing shim as "no rule", never as "no break".
- **Feature-gated successors are refused now.** A rename whose replacement sits
  behind something like `#[cfg(feature = "profiler")]` is reported rather than
  applied — the failure above is what that guard exists for.
- **Trust rustc's suggestions over the dataset.** The manual fix came straight
  from the compiler ("similarly named function `set_trace_enabled` defined
  here"), which knew something the measured surface did not.

## What this does not establish

- One transition: the 1.17.2 profiler rewrite, on one app. Renames whose shape
  is a plain same-signature substitution were not the subject here.
- The rule that fired was pointed at deliberately rule-shaped code. Real apps
  do not call `record_frame_timing` at all, so the *auto-driven share on real
  code* is zero — measured, not assumed, but from one app.
- Method-shaped consumption (`FrameTimingCollector::new` / `collect_unseen`)
  stays outside the measured surface and outside this study's accounting.
- Linux only, compile-time only: nothing here covers other platforms, or
  runtime behaviour after the swap.

## Provenance

- **Baseline:** `gpui-unofficial` 1.16.1, measured generation `ef93dce2…`.
- **Target:** `gpui-unofficial` 1.18.1, measured generation `d143b846…`.
- **Environment:** rustc 1.97.1 on Linux, 2026-09-06. The probe projects and
  their step-by-step evidence were temporary and are not part of the shipped
  dataset.
- **Related reading:** [08 — checking real application code with the
  compatibility report](08-used-api-report-on-real-code.md) for what the scan
  can and cannot see, and [10 — a real kit through the workspace audit, and
  declared toolchain floors](10-kit-and-toolchain-floors.md) for the audit
  side.
