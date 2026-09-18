# 12 — Verifying the alias shim with real UI kits

## What this establishes

A same-generation alias shim carries a real UI kit's whole stack onto the
other fork: `gpui-kit` 0.6.0 built against `gpui-unofficial` 1.19.0-pre with
one engine in the graph. Across a generation boundary the shim cannot help —
the reverse direction needed a one-line kit patch, because what breaks there
is a method signature, not a package name.

## What was not known before

[Doc 11](11-using-third-party-ui-kits-with-alternative-gpui-forks.md) designed the alias shim — a same-name
re-export crate plus `[patch]` that routes a kit's fork binding onto another
release of the same *measured* generation — and verified it as far as
resolution and lock shape on the real `gpui-kit` 0.6.0. Its own status line
named the open risk: *nothing had compiled that stack against the twin
release, and a default-feature build is the test that confirms or kills it*.
The reverse direction — a layer that binds `gpui-unofficial`, compiled onto
`gpui-pre` — had never been run on a real uno-bound kit at all.

So: do real kits compile end to end through a shim, or do hidden type errors
appear? And what happens when an older kit meets a newer generation's
signatures?

## What was run

On 2026-09-07, rustc 1.95.0 on a 12-core Linux machine, against live crates.io
artifacts: `gpui-kit` 0.6.0 (Longbridge, binds `gpui-pre` `^0.3.1` plus its
platform companion) and `gpuikit` 0.9.0 (Nate Butler, binds
`gpui-unofficial` `^1.14.2` plus its companion). The twin generation is
`546fcb11…` — `gpui-pre` 0.3.x ≡ `gpui-unofficial` 1.19.0-pre; unofficial's
newest *stable* is 1.18.1, a different generation (`d143b846…`).

### Direction 1 — `gpui-kit` 0.6.0 onto `gpui-unofficial` 1.19.0-pre: the compile half closes

The workspace is doc 11's recipe in full: the app binds the *impersonated*
row (`gpui = { package = "gpui-pre", version = "=0.3.3" }` with the
`gpui-pre-platform` companion), a member library depends on
`gpui-kit = "=0.6.0"`, and `[patch.crates-io]` points `gpui-pre` at a shim
crate named `gpui-pre` 0.3.3 whose entire content is
`pub use gpui_unofficial::*;`, with the real feature surface replicated
(`inspector` as an empty marker).

`cargo build --workspace` → **exit 0, finished in 3m05s**. The whole stack
compiled against unofficial's items: `gpui-kit`, `gpui-component`,
`gpui-base`, `gpui-kit-assets`, `gpui-component-macros` (the kit's own
proc-macro crate), the `pre` platform family
(`gpui-pre-platform`, `gpui-pre-linux`, `gpui-pre-wgpu`, …), and the uno
engine behind the shim. The tree confirms one engine:

```
$ cargo tree -i gpui-unofficial
gpui-unofficial v1.19.0-pre
└── gpui-pre v0.3.3 (…/gpui-pre-alias)     # the shim — its only path in
    ├── gpui-pre-linux v0.3.3 … gpui-pre-platform v0.3.3 … kit-app …
```

**Verdict: fine.** This is the strongest compile evidence the repo has for a
whole-stack generation swap — at default features, on one Linux surface.

### Direction 2 — `gpuikit` 0.9.0 onto `gpui-pre` 0.3.3: blocked, then one line

**First wall: resolution.** The kit's requirement is a caret, and a caret
excludes prereleases — so the published kit resolves `gpui-unofficial` 1.18.1,
the generation with *no* `gpui-pre` counterpart. Cargo refuses even an
explicit lock steer:

```
candidate versions found which didn't match: 1.19.0-pre
if you are looking for the prerelease package it needs to be specified explicitly
```

Steering the kit's own requirements onto `=1.19.0-pre` (a vendored manifest —
doc 11's "fork the layer" row, minimal form) and shimming unofficial → gpui-pre
gets past resolution and stops at one place:

```
error[E0061]: this method takes 1 argument but 0 arguments were supplied
  --> …/gpuikit-vendor/src/elements/input.rs:199:24
     | window.blur();
note: method defined here
  --> …/gpui-pre-0.3.3/src/window.rs:2222:12
     | pub fn blur(&mut self, cx: &mut App) {
```

**Attribution: an era break, not a shim break.** Three configurations separate
the two:

| Configuration | Engine | Result |
| --- | --- | --- |
| published `gpuikit`, uno 1.18.1 (what its caret resolves) | `d143b846…` | ✓ builds (exit 0, 1m45s) |
| vendored `gpuikit`, uno 1.19.0-pre, **no shim at all** | `546fcb11…` | ✗ the same `blur` error (uno's `window.rs:2082`) |
| vendored `gpuikit`, gpui-pre 0.3.3, **through the reverse shim** | `546fcb11…` | ✗ the same `blur` error (gpui-pre's `window.rs:2222`) |

`blur` is `pub fn blur(&mut self)` at 1.18.1 and `pub fn blur(&mut self, cx:
&mut App)` on both `546fcb11…` sides — a re-signature across the boundary —
and the kit has exactly one call site for it. The identical failure without any
shim is what exonerates the shim.

**The break is one line deep.** The call sits in the Escape handler
(`on_action::<Escape>(|_action, window, _cx| …)`), whose signature in this
generation already passes `&mut App` — the handler's previously unused `cx`.
So `window.blur()` → `window.blur(cx)` is the entire source change; with the
manifest steer, the kit builds against real registry `gpui-pre` 0.3.3 (**exit
0, 1m37s**), including an app main that compiles and links a
`gpuikit::elements::button`.

Why no *engine-side* shim exists for this class:

- **Inherent methods cannot be injected from outside.** `Window` belongs to the
  engine crate, so restoring a no-arg `blur` means forking the engine — which
  stops being the measured crate.
- **An extension trait loses at the call site.** rustc prefers inherent
  candidates by name; `w.blur()` against an inherent `blur(cx)` errors E0061
  without ever consulting the trait (verified with a minimal probe).
- **A no-arg blur would be wrong anyway.** The new body is the old body plus
  `clear_pending_keystrokes(cx)` — the argument *is* the behaviour.

### What the corpus could and could not see

The re-signature was **invisible to the measured surfaces**: both generations
carried `pub use window::*` as an opaque item, so `Window::blur` was not
measured on either side. The compiler found what the item set could not.

That is now fixed at the item level: the re-export-chain work opened
`window.rs`, the v2 item model measures inherent methods, and `fn:Window::blur`
is a dataset item on both sides — digest `…a07c1800e1` through
`uno 1.16.1–1.18.1`, `…bdc5659251` on `1.19.0-pre` and `gpui-pre` 0.3.3. The
add-argument re-signature is a visible item delta, and the finding that
inherent methods cannot be aliased is enforced as data policy: the facade
refuses to shim a type member.

## What it means for you

- **A same-generation shim works, and it works on real stacks.** For a kit
  whose release sits in the same measured generation as your fork, the shim
  needs no source edits at all — the kit's whole family compiles against your
  engine with one engine in the graph.
- **Carets do not reach prereleases.** A kit on `^1.14.2` resolves the newest
  *stable* release of that line, which may be a different generation from yours;
  reaching a prerelease twin takes an explicit `=1.19.0-pre` pin, and even
  `cargo update --precise` refuses on its own.
- **A changed signature is not a shim problem.** When a method gains an
  argument between generations, no re-export can absorb it: the fix is a kit
  patch, and here it was one line, using a parameter the handler already had.
- **Know the measured cost before you start.** Carrying `gpuikit` 0.9.0 onto
  the `546fcb11…` class cost three steered version lines in a vendored manifest
  plus that one source line — measured, not estimated.

## What this does not establish

- **Default features only.** The kits' feature-gated surfaces (`editor`,
  `stitch`, `schema` on gpuikit; `inspector` on the impersonated package) were
  not compiled, so nothing here covers them.
- **One Linux surface, at compile time.** macOS and Windows were not
  evaluated, and nothing here benchmarks rendering or the event loop: type
  unification and linking are what was proven.
- **One kit per direction.** Other stacks and other generation boundaries were
  not exercised — and a kit that compiles here is not thereby proven
  interchangeable beyond the measured class.
- The aliased workspace is still refused by the tool's own audits, by design —
  doc 11's audit section covers why, and what to do about it.

## Provenance

- **Kits:** `gpui-kit` 0.6.0 (Longbridge) and `gpuikit` 0.9.0 (Nate Butler),
  on real crates.io artifacts.
- **Twin generation:** `546fcb11…` (`gpui-pre` 0.3.x ≡ `gpui-unofficial`
  1.19.0-pre). **Baseline comparison:** `gpui-unofficial` 1.18.1
  (`d143b846…`).
- **Environment:** rustc 1.95.0, 12 cores, Linux, 2026-09-07. Probe projects,
  vendored manifests and captured outputs were temporary.
- **Related reading:** [11 — Using third-party UI kits with alternative GPUI
  forks](11-using-third-party-ui-kits-with-alternative-gpui-forks.md) for the recipe this study compiled, and
  [13 — Two UI kits running on one underlying engine](13-two-ui-kits-running-on-one-underlying-engine.md)
  for the short version.
