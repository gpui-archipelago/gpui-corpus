# 13 — Field note: two kits, one measured generation (a cross-era shim story)

*Field note / draft in the maintainer's voice — publishable outside the repo
with the internal pointers trimmed. Everything factual here was measured on
real crates.io artifacts on 2026-09-07 (probe evidence under `target/t24/`;
the mechanics live in [doc 11](11-alias-shim-for-kits.md), the full study in
[doc 12](12-alias-shim-compiled-both-real-kits.md)).*

Since `gpui-pre` 0.3.x ≡ `gpui-unofficial` 1.19.0-pre,
it looks like I've been able to compile the app that uses
`gpui-kit` + `gpui-unofficial` and the app with
`gpuikit` + `gpui-pre`.
I hope it's not confusing at all 😆

It *looks* confusing — two kits, two fork names, cross-wired — so let me
say the thing that makes it not confusing: **the names are the only thing
that differs. Both apps are running the same code.**

## The setup

There are two UI kits on crates.io that build on GPUI, and they picked
different names for the same underlying tree:

- **`gpui-kit`** (longbridge) — its manifest says
  `gpui = { package = "gpui-pre", version = "^0.3.1" }`. It's a *pre* kit.
- **`gpuikit`** (Nate Butler) — its manifest says
  `gpui = { package = "gpui-unofficial", version = "^1.14.2" }`. It's an
  *uno* kit.

And the two fork packages they bind are, as far as anyone has measured,
the same release of the same project published twice:

- `gpui-pre` 0.3.x and `gpui-unofficial` **1.19.0-pre** share one measured
  API generation (`546fcb11…`): same public items, same signatures, item
  for item. (That's not a vibe — it's an item-set fingerprint over both
  published crates.)

So "compile gpui-kit on unofficial" and "compile gpuikit on gpui-pre" are
the same sentence wearing different hats: **run each kit on the generation
it was built for, under the other name.**

## The trick (one paragraph)

Cargo's `[patch]` can replace a package only with another package of the
same name and version — which is why `gpui-pre` can never be patched to
`gpui-unofficial` directly. But nothing forces the replacement to contain
gpui-pre's *code*. A tiny crate **named `gpui-pre`** at version 0.3.3 whose
entire `lib.rs` is `pub use gpui_unofficial::*;` satisfies the patch rule
while compiling unofficial's items. Re-exports are the same items, so every
crate that depended on `gpui-pre` — the kit, its platform companion,
`gpui-component`, the whole stack — now compiles against unofficial, and an
app that also binds unofficial holds **one** engine whose types unify. The
reverse direction is the same recipe with the names swapped: a crate named
`gpui-unofficial` at 1.19.0-pre whose body is `pub use gpui_pre::*;`.

## What compiled

| App | Kit's binding | What actually compiles | Result |
| --- | --- | --- | --- |
| gpui-kit app, bound to **uno** | `gpui-pre` (shimmed → uno) | uno 1.19.0-pre + kit stack (gpui-kit, gpui-component, gpui-base, assets, macros, pre platform family) | ✅ `cargo build` exit 0, zero kit edits |
| gpuikit app, bound to **pre** | `gpui-unofficial` (shimmed → pre) | gpui-pre 0.3.3 + gpuikit | ✅ exit 0, after the two caveats below |

Both are one-engine graphs: `cargo tree -i` shows the target release once,
reachable only through the shim, and an app that renders a kit component
next to its own `gpui::` code compiles — because the kit's types and the
app's types *are* the same compiled crate.

## The fine print (the 😆 is doing work)

Three honest asterisks, because this is a *measured* claim, not magic:

1. **The blur caveat.** The generation boundary right below this one is
   real: between uno 1.18.1 and the 1.19.0-pre line, `Window::blur` was
   re-signed from `blur(&mut self)` to `blur(&mut self, cx: &mut App)`
   (it now clears pending keystrokes — the new argument is the new
   behavior). `gpuikit`'s caret currently floats on 1.18.1, so its one
   `window.blur()` call is written for the old era. Compiling it on the
   pre/1.19 tree needs that one call updated — mechanical (`blur(cx)`), one
   line, and I've proposed a dual-era feature upstream so future gpuikit
   versions carry both `blur()` and `blur(cx)` behind a cargo feature
   (both arms compile-verified: uno 1.18.1 and gpui-pre 0.3.3).
2. **Today you pin.** uno 1.19.0-pre is a prerelease, and a caret excludes
   prereleases — so reaching this generation on the uno side currently
   means exact pins (`=1.19.0-pre`) or, on the stable side, the `gpui-pre`
   row. The day uno 1.19.0 goes stable, `gpuikit`'s own `^1.14.2` floats
   onto it and the pinning caveat evaporates.
3. **The measurement is syn-level.** The equivalence is item-set equality
   (no compiler pass: no cfg evaluation, and auto-traits are unattested).
   Compile evidence on a real stack is the strongest check this can get,
   and it passed on both arms — but "measured equal" is a statement about
   what was measured, so treat the gap between them (methods behind
   re-export globs, cfg-gated code) as the honest boundary it is.

## Why it's really not confusing

The GPUI fork ecosystem is six crates.io names for a handful of code trees.
Cargo resolves *names*; gocar (the tooling behind this) measures *code*.
The reason "gpui-kit on unofficial" and "gpuikit on gpui-pre" both compile
is that they were never two different engineering problems — they're the
same measured generation (`546fcb11…`) reached through two package names.
Confusion lives in the names; the compilers and the fingerprints agree.

*Dig deeper: the alias-shim recipe and its measured-generation gate — gocar
doc 11; the compile study on both real kits (this post's evidence) — doc 12.*
