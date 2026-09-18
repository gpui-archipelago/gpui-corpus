# 08 — Checking real application code with the compatibility report

## What this establishes

On real, compiling GPUI code the compatibility report reasons about 16 of the
20 used symbols (80%), and it names the exact release that removes a function.
Its limits are honest ones: it cannot follow method chains on visual elements —
30 of the app's 64 call sites stay unscanned — and it refuses cross-fork
upgrades its type-level data cannot prove, even where the compiler succeeds.

## What was not known before

The report exists to inspect an application's source, compare its used API
against every measured library release, and print which version upgrades are
safe. But the item model it was built on measured `type`, `trait` and `fn`
names only, and real GPUI code is overwhelmingly method chains on elements
(`div().flex().child(…)`), impl-block items, and derive attributes — every one
of which has to be listed as *unprovable* rather than assumed.

So: on a real, compiling GPUI app, does the report advise — or does it answer
"nothing checkable" on exactly the code it exists for? And are its claimed
compatibility windows compile-true?

## What was run

One app, written against the real post-split fork API and mirroring the forks'
own `hello_world` / `text` / `data_table` examples: a window root view
(`impl Render`), a repeated child component built from
`#[derive(IntoElement)]` + `impl RenderOnce`, element builder chains
(`div().flex().px_2().text_color(…)`), method calls on statically typed
receivers (`cx.open_window`, `cx.activate`, `Bounds::centered`), and a
separate module that calls exactly the two names the old corpus measured on
this generation (`set_frame_trace_enabled`, `frame_trace_enabled` — the
profiler functions the real 1.17.2 transition removed), so the checkable share
of the app is attributable separately from the method-heavy share.

The same source was compiled at two baselines, both sides of the
dataset-asserted `ef93dce2…` generation:

| Binding | Toolchain | Result |
| --- | --- | --- |
| `gpui-box` 0.1.1 + `gpui-box-platform` 0.1.1 | rustc 1.97.1 | ✓ compiles (1m53s, zero app warnings), `verify-env` exit 0 |
| `gpui-unofficial` 1.16.1 + `gpui-platform-gpui-unofficial` 1.16.1 | rustc 1.97.1 | ✓ compiles (1m58s), `verify-env` exit 0 |

The profiler calls compile on both, which is what makes them real API on the
window's interior rather than a name the dataset happens to hold.

Pointed at the app, the report first missed nearly everything: 2 of the 20 used
symbols were checkable at the box and 1.16.1 baselines, 0 at 1.18.1. The cause
was the scan, not the report's logic — the corpus walker descended only into
`pub mod` file modules, while every fork exposes its app-facing API from
*private* modules through glob re-export hubs (`mod app;` + `pub use app::*`,
the Zed layout), so those items were never recorded. With crate-local
re-export chains resolved and inherent methods of public types measured, the
same source reports:

| Baseline | Checkable | Unprovable | Unscanned | Window claimed |
| --- | --- | --- | --- | --- |
| `gpui-box` 0.1.1 (`ed121d14…`) | 16 of 20 | 4 | 30 | exact copy only |
| `gpui-unofficial` 1.16.1 (`0d9280ec…`) | 16 of 20 | 4 | 30 | `1.16.1…1.16.3` |
| `gpui-unofficial` 1.18.1 (`b78c7b86…`) | 14 of 20 | 6 | 30 | `1.18.1…1.18.1` |

What the remaining symbols are unprovable *for* is now a policy set with a
reason each, not a measurement gap: the external `SharedString` (counted twice,
its type and a method on it — `gpui_shared_string` is not a measured member, so
it is opaque by design), one trait-scoped member (`Windowed::WindowBounds`,
which lives inside the trait's own text), and the `IntoElement` derive
(derives stay outside the item model). At the 1.18.1 baseline the two removed
profiler functions are unprovable as well — see the forward-looking note below.

The 30 unscanned sites are unchanged across baselines: 1 glob import
(`prelude::*`) plus 29 method calls whose receiver type the scan cannot infer —
23 of them gpui-shaped builder-chain calls, 6 std/companion noise
(`cloned`, `map`, `into`, `unwrap`, `is_none`).

### Prediction against the compiler

| Target release | Report says | Compiler says | Match |
| --- | --- | --- | --- |
| `gpui-unofficial` 1.16.1–1.16.3 | compatible | compiles | exact |
| `gpui-unofficial` ≥ 1.17.2 | incompatible — `set_frame_trace_enabled` removed at 1.17.2 | fails to compile | exact |
| `gpui-box` 0.1.1 | incompatible — type-level digests differ | compiles | over-conservative |

## What it means for you

- **Trust it for names and for removals.** It validated 16 of 20 used symbols
  and named the exact release that drops a function.
- **Review visual chains yourself.** `div().flex().text_color(…)` and friends
  are outside the scan; the 23 builder-chain sites here are the shape of that
  gap, and closing it needs receiver typing rather than more measured data.
- **Run it looking forward from the release you are on.** It identifies
  removals by comparing your baseline against newer targets, so run from a
  pre-break baseline the removals are named; run from an already-broken one and
  the missing functions appear as unprovable instead of "removed at 1.17.2".
  (That is why the 1.18.1 baseline reads 14 of 20 rather than 16.)
- **Read its cross-fork refusals as conservative.** Type-level digests differ
  between forks even where an app compiles on both — the report has no
  field/member granularity, so it refuses. Where a compile has been run, the
  compile is the better evidence; `gpui-box` 0.1.1 is the case in point here.
- **It needs a project it manages.** The report reads gocar-managed metadata,
  so a plain Cargo project is not reportable as-is; and `cargo gocar new`
  floors at a fork's latest stable, so binding an older baseline (1.16.1 here)
  takes a hand-set floor.

## What this does not establish

- Nothing about fields or members. Cross-fork refusals rest on whole-type
  digests; an app that touches only fields of a drifted type can compile while
  the report refuses it (measured, not hypothetical — that is the `gpui-box`
  row above).
- Nothing about impl-block items or derives beyond "unprovable by policy".
  Those need a member-level pass; until then they are listed, never assumed.
- One app shape: a two-file task board. Widgets, layout trees, custom
  shaders, and the many other ways real code is written were not exercised.
- Linux only (rustc 1.97.1). macOS and Windows were not evaluated.
- Compile-time compatibility only — nothing here benchmarks rendering or the
  event loop.

## Provenance

- **Baselines:** `gpui-box` 0.1.1 (generation `ed121d14…`) and
  `gpui-unofficial` 1.16.1 (`0d9280ec…`); target comparison
  `gpui-unofficial` 1.18.1 (`b78c7b86…`).
- **Environment:** rustc 1.97.1 on Linux, 2026-09-06, re-measured 2026-09-07
  after the corpus walker was fixed. The probe projects were temporary and are
  not part of the shipped dataset.
- **Related reading:** [07 — switching a hello world between real
  forks](07-real-fork-compile-case-study.md) for the baseline setup across
  forks, and [09 — does migrate carry a real app across the
  1.17.2 break?](09-migrate-and-facade-on-real-code.md) for the compiler side
  of that same break.
