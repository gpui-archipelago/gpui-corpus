# 12 — Study: the alias-shim compile half, run on both real kits (T-24)

**Status:** completed study, 2026-09-07 (T-24). Verdicts: **direction 1
fines — the doc-11 open risk closes**: `gpui-kit` (longbridge) 0.6.0's
default-feature stack **compiles** against unofficial 1.19.0-pre through the
shim (exit 0, one engine). **Direction 2 blocked for the untouched kit,
unblocked by one line**: `gpuikit` (Nate Butler) 0.9.0 cannot compile on the
`546fcb11…` generation as published — its `Window::blur()` call hits the
`d143b846…` → `546fcb11…` re-signature (`blur` gained `&mut App`) and the
published manifest cannot even resolve there (a caret excludes the 1.19.0-pre
prerelease). The re-signature is the **entire** break: with the disclosed
manifest steer plus one source line (`window.blur()` → `window.blur(cx)`),
the kit compiles against gpui-pre 0.3.3 through the shim (exit 0). No
engine-side compat shim is expressible (inherent-method ownership;
verified: extension traits are shadowed at the call site). Doc 11's
reverse-direction gate now has real-kit evidence. **T-26 (2026-09-07): the
`blur` re-signature is measured** — the v2 item model adds inherent methods
of public types, so `fn:Window::blur` is a dataset item on both sides with
different digests (see the measurement note below).

## Question

[Doc 11](../04-user-docs/11-alias-shim-for-kits.md) documented the
alias-shim — a same-name re-export crate + `[patch]` that routes a kit's
fork binding onto another release of the same **measured** generation — and
verified it to *resolution + lock shape* on the real `gpui-kit` 0.6.0 (T-22).
Its status line named the open risk: *"Nothing has compiled `gpui-kit` 0.6.0's
stack against uno 1.19.0-pre. … a default-feature build of the aliased
workspace is the test that confirms or kills it"*. And the **reverse**
direction — a layer that binds `gpui-unofficial` compiled onto `gpui-pre` —
had never been run on a real uno-bound kit at all (doc 11 reasoned about a
"hypothetical uno-bound kit"). Both halves executed here, on real crates.io
artifacts, with `cargo build`.

## Environment (this run)

- rustc 1.95.0 stable (active), 12 cores. Probe projects under `target/t24/`
  (gitignored, rebuildable); captured outputs in `target/t24/RESULTS.md`.
- Kits under test, from the live index + unpacked `.crate` sources:
  `gpui-kit` 0.6.0 (longbridge — binds `gpui-pre` `^0.3.1` + hard
  `gpui-pre-platform` companion) and `gpuikit` 0.9.0 (Nate Butler,
  `iamnbutler` — binds `gpui-unofficial` `^1.14.2` + hard
  `gpui-platform-gpui-unofficial` companion, feature `font-kit`).
- Dataset: `gpui-pre 0.3.x` ≡ `gpui-unofficial 1.19.0-pre` (`546fcb11…`),
  measured syn-level item-set equality; unofficial head stable is **1.18.1**
  (`d143b846…`, a different generation) and 1.19.0-pre is the newest
  published release (live index, verified for this run).

## Probe A — gpui-kit (longbridge) 0.6.0 with unofficial 1.19.0-pre: the compile half closes

Workspace (recreated from T-22's lock-shape probe, `target/t24/kit-on-uno/`):
the app binds the impersonated row per doc-11 step 6 —
`gpui = { package = "gpui-pre", version = "=0.3.3" }` +
`gpui-platform = { package = "gpui-pre-platform", version = "=0.3.3", features = ["x11","wayland"] }`
— a member lib depends on `gpui-kit = "=0.6.0"` (default features:
component + assets), and `[patch.crates-io] gpui-pre` points at the shim
(`target/t24/gpui-pre-alias/`: a crate named `gpui-pre` 0.3.3 whose entire
content is `pub use gpui_unofficial::*;`, feature surface replicated from the
real gpui-pre 0.3.3 and forwarded, `inspector` as an empty marker).

**`cargo build --workspace` → exit 0, Finished in 3m05s.** The whole kit
stack compiled against uno 1.19.0-pre's items: `gpui-kit`, `gpui-component`,
`gpui-base`, `gpui-kit-assets`, `gpui-component-macros`, the pre *platform*
family (`gpui-pre-platform`, `gpui-pre-linux`, `gpui-pre-wgpu`, …), and the
uno engine + uno family behind the shim. Doc 11's step-7 shape holds:

```
$ cargo tree -i gpui-unofficial
gpui-unofficial v1.19.0-pre
└── gpui-pre v0.3.3 (…/target/t24/gpui-pre-alias)     # the shim — its only path in
    ├── gpui-pre-linux v0.3.3 … gpui-pre-platform v0.3.3 … kit-app …
```

uno 1.19.0-pre appears **once**; the lock's `gpui-pre` row has no source line
(the path patch). **Verdict: fine.** The 546fcb11-class drop-in claim holds
for the *pre-bound* kit at default features — the strongest compile evidence
the phase-5 rebind design has for this swap. The remaining caveat is the one
doc 11 already states: this is compile evidence at default features on one
Linux surface, not a proof of interchangeability beyond it.

## Probe B — gpuikit (Nate Butler) 0.9.0 with gpui-pre 0.3.3: the reverse direction, measured

### The gate (doc-11 reverse constraint, now evidenced)

gpuikit 0.9.0 binds `gpui-unofficial ^1.14.2`. A caret excludes prereleases,
and unofficial's only `546fcb11…`-class release is the 1.19.0-pre prerelease
— so the published kit resolves uno **1.18.1** (`d143b846…`, verified in
`target/t24/b0-gate/`), and that generation has **no** gpui-pre counterpart.
Cargo refuses even an explicit lock steer
(`cargo update -p gpui-unofficial@1.18.1 --precise 1.19.0-pre`):

```
candidate versions found which didn't match: 1.19.0-pre
if you are looking for the prerelease package it needs to be specified explicitly
```

And the *pure* reverse shim (published gpuikit, app pinned to
`=1.19.0-pre`, `[patch] gpui-unofficial` → shim) fails at resolution with
doc-11 step-6's exact shape:

```
previously selected package `gpui-unofficial v1.19.0-pre (…/gpui-unofficial-alias)`
    ... which satisfies dependency `gpui = "=1.19.0-pre"` of package `b0-gate …`
failed to select a version for `gpui-unofficial` which could resolve this conflict
```

**gpuikit 0.9.0 is a `d143b846…`-era kit.** Doc 11's "only 1.19.0-pre-era
layers can be aliased in reverse" is confirmed on the real uno-bound kit —
twice: resolution (caret × prerelease) and, once forced, compilation (below).

### The steer and the compile

To reach the measured class at all, the kit's requirements must be steered.
The steer used here is the minimal disclosed form of doc 11's "fork the
layer" row: `target/t24/gpuikit-vendor/` is a copy of the published 0.9.0
`.crate` source whose **only** diff against the published `Cargo.toml` is the
version text of its three gpui-family requirements (`1.14.2` →
`=1.19.0-pre` on `gpui`, `gpui_platform`, and the optional `gpui_util`) —
zero source edits. Then the reverse shim (`target/t24/gpui-unofficial-alias/`:
a crate named `gpui-unofficial` 1.19.0-pre whose entire content is
`pub use gpui_pre::*;`) is `[patch]`ed in, and the app binds the impersonated
row `gpui-unofficial =1.19.0-pre` + its uno-platform companion.

**`cargo build` → exit 101, exactly one error, in gpuikit's own lib** — and
the error message is itself the proof the shim routed the kit onto the real
gpui-pre crate (the `note` names gpui-pre's source):

```
error[E0061]: this method takes 1 argument but 0 arguments were supplied
  --> …/gpuikit-vendor/src/elements/input.rs:199:24
     | window.blur();
note: method defined here
  --> …/registry/src/index.crates.io-…/gpui-pre-0.3.3/src/window.rs:2222:12
     | pub fn blur(&mut self, cx: &mut App) {
```

### Attribution — era break, not shim break

Three datapoints separate the mechanism from the generation boundary:

| Config | Engine | Result |
| --- | --- | --- |
| published gpuikit, uno 1.18.1 (native — its caret's resolution) | `d143b846…` | ✓ builds (exit 0, 1m45s) |
| vendored gpuikit, uno 1.19.0-pre (registry, **no shim**) | `546fcb11…` | ✗ same `window.blur()` error (uno's `window.rs:2082`) |
| vendored gpuikit, gpui-pre 0.3.3 (**through the reverse shim**) | `546fcb11…` | ✗ same `window.blur()` error (gpui-pre's `window.rs:2222`) |

Source check of the signatures: uno 1.18.1 has `pub fn blur(&mut self)`
(`window.rs:2074`); uno 1.19.0-pre and gpui-pre 0.3.3 both have
`pub fn blur(&mut self, cx: &mut App)` — a **re-signature across the
generation boundary**, and gpuikit has exactly one call site
(`input.rs:199`). The shim is exonerated: the aliased build fails *because*
the 1.19.0-pre-generation build fails, identically, without any shim.

### A measurement note: the corpus cannot see this break

The re-signature is **invisible to the v0 dataset**: both generations'
per-version surfaces carry `use:pub use window::*` as an opaque item (the
T-18 corpus gap — the walker records the re-export statement, not the
module it opens), so `Window::blur` is not a measured item on either side.
Nothing in the dataset is contradicted — 1.18.1 and 1.19.0-pre are different
measured generations, and an alias was never claimed to bridge generations —
but this is a concrete, compile-found example of the class of breaks only a
compiler pass (or the corpus re-export-chain fix) can see.

**Resolved by T-26 (2026-09-07) — the v2 item model measures it.** The
re-export-chain fix (T-25) opened `window.rs`; the v2 model adds its
inherent methods, so `fn:Window::blur` is a measured item on both sides of
the boundary — unconditional, digest `…a07c1800e1` on uno 1.16.1–1.18.1,
`…bdc5659251` on 1.19.0-pre/gpui-pre 0.3.3: the add-argument re-signature
is a dataset-visible item delta (parameter-type fn text). The report
bridges method-role usage onto those members (the doc-08 census re-run
makes method calls checkable), and the facade refuses to shim type members
— the doc-12 finding that inherent methods cannot be aliased is now
enforced as data policy.

### How deep is the era break, and what carries the kit across (follow-up probe, same day)

The failure was exactly **one re-signature deep**. The Escape handler at
gpuikit `src/elements/input.rs:199` sits inside
`interactivity.on_action::<Escape>(|_action, window, _cx| …)`, and in this
generation the action-handler signature is
`Fn(&A, &mut Window, &mut App)` — so the handler's (previously unused)
`cx` is already a `&mut App`, and the successor call is literal:
`window.blur()` → `window.blur(cx)` (one source line in the vendored kit,
plus the disclosed manifest steer). Rebuild against the real registry
`gpui-pre 0.3.3` through the reverse shim: **exit 0, Finished in 1m37s** —
the kit's lib and an app main exercising a `gpuikit::elements::button`
compile and link on gpui-pre's code. (The feature-gated `editor`/`stitch`/
`schema` surfaces were not compiled; depth is measured on the default-feature
surface.)

Why no **engine-side** compat shim is possible, and what that means:

- **Inherent methods cannot be injected.** `Window` is defined in the engine
  crate; only that crate can add inherent impls. "Patching gpui" to restore
  a no-arg `blur()` means forking the engine — the one identity the whole
  toolchain exists to avoid tracking, and the fork stops being the measured
  crate.
- **An extension trait cannot rescue the call site.** A 0-arg `blur`
  extension trait in scope would still lose: rustc's method resolution
  prefers inherent candidates by name, and `w.blur()` against an inherent
  `blur(cx)` errors E0061 without consulting the trait (verified with a
  minimal rustc probe, `target/t24/probe-shadowing/` — the exact error shape
  the kit hit).
- **A no-arg blur would be semantically wrong anyway.** The 1.19.0-pre body
  is the 1.18.1 body *plus* `clear_pending_keystrokes(cx)` — the new
  argument is not ceremony, it is the new behavior; a compat no-arg blur
  could not clear pending keystrokes.

The workable mechanism is the kit-side patch above — doc 11's "fork the
layer" row in its minimal form, and precisely the *re-signature rule seed*
(`blur` → `blur(cx)`, add-argument shape) that `gocar.rules.v1`'s
rename-only model cannot yet express (the T-19 named delta).

## Verdicts

- **Direction 1 (gpui-kit → uno 1.19.0-pre): fine.** Doc 11's open risk
  closes on the real longbridge kit at default features: the drop-in stack
  compiles against the twin release, one engine in the lock/tree.
- **Direction 2 (gpuikit → gpui-pre): blocked for the untouched kit;
  unblocked by one source line, with the mechanism verified.** gpuikit 0.9.0
  is a `d143b846…`-era kit on a caret that cannot reach the 546fcb11 class;
  forced there, its used surface trips on exactly one re-signature —
  `Window::blur` gaining `&mut App` — the identical failure occurs on
  registry uno 1.19.0-pre with no shim. The reverse alias itself worked (the
  kit compiled against gpui-pre's items up to its one era break), and the
  one-line kit patch carries the whole build across (exit 0).

## Reproducing

Probe projects under `target/t24/` (gitignored, rebuildable):
`kit-on-uno/` + `gpui-pre-alias/` (direction 1), `gpuikit-pre/` +
`gpui-unofficial-alias/` + `gpuikit-vendor/` + `uno190-control/` +
`b0-gate/` (direction 2 + controls). Each probe needs network for its first
cargo run (registry sources); the controls share the repo's persistent
`target/cargo-home` cache. Captured outputs and the exact commands live in
`target/t24/RESULTS.md`.

## Claims this study refines

- **Doc 11's "Open risk — the compile half is unproven" → closed (direction
  1) and measured (direction 2).** The guide's step-by-step recipe compiles
  end-to-end on the real gpui-kit stack; its reverse-direction gate is now
  evidenced by a real uno-bound kit (caret resolution lands outside the
  measured class, and the class's own surface breaks the kit at `blur`).
- **Doc 11's alternatives table** gets a measured datapoint for the "fork
  the layer" row: on a caret-floated kit the reverse direction needs a
  vendored manifest (three version lines steered, zero source edits) **and**
  — because the 1.18.1 → 1.19.0-pre break is a re-signature, not a rename —
  exactly one source line (`blur` → `blur(cx)`). That two-part delta is the
  measured cost of carrying gpuikit 0.9.0 onto the `546fcb11…` class.
- **STATUS limitation 7 (the corpus re-export gap) gets a third measured
  instance**: the `Window::blur` re-signature between `d143b846…` and
  `546fcb11…` is invisible to the item surfaces on both sides.
- **STATUS limitation 8's doc-11 note** gains its compile evidence: the
  audits refuse the aliased locks exactly as documented, and the refusal is
  "correct about v1's model" — including now for a reverse shim whose build
  fails anyway on era grounds.
