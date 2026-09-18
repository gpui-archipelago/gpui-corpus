# 13 — Two UI kits running on one underlying engine

## What this establishes

Two UI kits that appear to need different GPUI forks are the same measured
generation published under two names, so either kit can be routed to either
fork with a one-crate alias shim. That equality is an item-set fingerprint
rather than byte-identical sources — and the generation below it still costs a
kit one line.

It looks confusing: two kits, two fork names, cross-wired. What makes it not
confusing is that in the measured surface the names are the only difference,
and both apps end up running the same generation's items. The short version:
this ecosystem is six crates.io names for a handful of code trees — cargo
resolves *names*, the tooling measures *code*.

## What was not known before

There are two UI kits on crates.io that build on GPUI, and they picked
different names for the same underlying tree:

- **`gpui-kit`** (Longbridge) declares
  `gpui = { package = "gpui-pre", version = "^0.3.1" }` — a *pre* kit.
- **`gpuikit`** (Nate Butler) declares
  `gpui = { package = "gpui-unofficial", version = "^1.14.2" }` — an *uno*
  kit.

Because that binding is a rename, cargo compiles the named package whichever
fork your own manifest uses — so it looked as if picking a kit locked you into
that kit's fork. Nobody had shown that an app on `gpui-unofficial` could use
`gpui-kit` components (or the reverse) without two engines in one binary, and
nobody had compiled it.

## What was run

The two packages the kits bind — `gpui-pre` 0.3.x and `gpui-unofficial`
**1.19.0-pre** — share one measured API generation (`546fcb11…`): the same
public items with the same signatures, item for item. That is a fingerprint
over both published crates, not a judgement about their sources.

The shim is then mechanical, in either direction. Cargo's `[patch]` can only
replace a package with another package of the **same name and version**, which
is why `gpui-pre` can never be patched straight to `gpui-unofficial`. But
nothing forces the replacement to contain gpui-pre's code: a tiny crate
*named* `gpui-pre` at 0.3.3 whose whole `lib.rs` is
`pub use gpui_unofficial::*;` satisfies the patch rule while compiling
unofficial's items. Re-exports **are** the same items, so every dependent —
the kit, its platform companion, `gpui-component`, the whole stack — compiles
against unofficial, and an app that binds unofficial too holds one engine
whose types unify. The reverse is the same recipe with the names swapped: a
crate named `gpui-unofficial` at 1.19.0-pre whose body is
`pub use gpui_pre::*;`.

Both arms were built on real crates.io artifacts (rustc 1.95.0, Linux,
2026-09-07):

| App | Kit's binding | What actually compiles | Result |
| --- | --- | --- | --- |
| `gpui-kit` app, bound to **uno** | `gpui-pre` → shimmed to uno | uno 1.19.0-pre + the kit stack (`gpui-kit`, `gpui-component`, `gpui-base`, assets, macros, the pre platform family) | ✓ exit 0, zero kit edits, 3m05s |
| `gpuikit` app, bound to **pre** | `gpui-unofficial` → shimmed to pre | gpui-pre 0.3.3 + `gpuikit` | ✓ exit 0, 1m37s, after one source line |

Both are one-engine graphs: `cargo tree -i` shows the target release once,
reachable only through the shim. An app that renders a kit component next to
its own `gpui::` code compiles, because the kit's types and the app's types
*are* the same compiled crate.

Three honest asterisks, because this is a measured claim rather than magic:

1. **The `blur` caveat.** The generation below is a real boundary: between uno
   1.18.1 and the 1.19.0-pre line `Window::blur` was re-signed from
   `blur(&mut self)` to `blur(&mut self, cx: &mut App)` — it now clears pending
   keystrokes, so the new argument *is* the new behaviour. `gpuikit`'s caret
   floors on 1.18.1, so its one `window.blur()` call is written for the old
   era; compiling it on the 1.19 line needs that call updated (`blur(cx)`,
   one line). A dual-era feature has been proposed upstream so future versions
   carry both forms behind a cargo feature — both arms were compile-verified.
2. **Today you pin.** uno 1.19.0-pre is a prerelease and a caret excludes
   prereleases, so reaching this generation on the uno side means exact pins
   (`=1.19.0-pre`) or the stable `gpui-pre` row. The day uno 1.19.0 goes
   stable, `gpuikit`'s own `^1.14.2` floats onto it and this caveat
   evaporates.
3. **The measurement is syn-level.** The equivalence is item-set equality — no
   compiler pass, no cfg evaluation, auto-traits unattested. Compiling a real
   stack is the strongest check available and it passed on both arms, but
   "measured equal" is a statement about what was measured.

## What it means for you

- **Pick a kit for its features, not its fork name.** Neither kit forces its
  fork on you: their bindings are renames, and the generation they share can
  be reached under either name.
- **Do not fork a toolkit to re-point it.** A thin relay crate
  (`pub use other_fork::*;`, named after the package the kit expects) plus one
  `[patch]` line re-routes the entire stack — companions, components, assets,
  and the kit's own proc-macro crate included.
- **Exact-pin prereleases.** `=1.19.0-pre` is how you reach the twin
  generation while it is a prerelease; a caret will not do it.
- **Expect one line when crossing a generation.** The `blur(cx)` change is the
  whole cost here, and it is mechanical — but it is a source edit, and no
  re-export shim can absorb it.
- **The audits will still refuse the aliased workspace.** They key on package
  names, and an alias deliberately breaks name↔identity — see
  [doc 11](11-using-third-party-ui-kits-with-alternative-gpui-forks.md) for what the refusal says and why it is
  correct about the model while wrong about the graph.

## What this does not establish

- **Nothing across generations.** It does not make `gpuikit` 0.9.0 as
  published compile on `gpui-pre`, or an unmodified 1.18.1 app link with it: 1.18.1
  is a different generation with no twin in the `pre` line.
- **Nothing about byte-identical sources.** The generation is an item-set
  fingerprint — methods behind re-export globs and cfg-gated code are the
  honest boundary, and auto-traits are unattested.
- **Nothing about non-default features.** Feature-gated surfaces (a kit's
  editor or inspector tooling) were not compiled.
- **Nothing about runtime.** Unification and linking were proven at compile
  time on Linux; rendering, performance and other platforms were not tested.

## Provenance

- **Kits:** `gpui-kit` 0.6.0 (Longbridge) and `gpuikit` 0.9.0 (Nate Butler),
  on real crates.io artifacts.
- **Shared generation:** `546fcb11…`, measured across `gpui-pre` 0.3.x and
  `gpui-unofficial` 1.19.0-pre. The boundary below it is
  `d143b846…` (`gpui-unofficial` 1.18.1).
- **Environment:** rustc 1.95.0, 12 cores, Linux, 2026-09-07. The probe
  projects were temporary.
- **Related reading:** this note is the summary — the recipe is
  [doc 11](11-using-third-party-ui-kits-with-alternative-gpui-forks.md) and the full compile study, with its
  controls, is [doc 12](12-verifying-the-alias-shim-with-real-ui-kits.md).
