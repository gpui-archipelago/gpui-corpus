# 11 — Guide: use a kit that binds another fork (the alias-shim)

**Status:** documented workaround, 2026-09-07 (T-22 follow-up; compile half
executed 2026-09-07 by T-24 — see [doc 12](12-alias-shim-compiled-both-real-kits.md)).
Applies when you want a *published* layer crate — a UI kit like `gpui-kit`, or
its stack (`gpui-component`, …) — but the layer was built against a fork
package you do not use (e.g. the kit binds `gpui-pre` while your app binds
`gpui-unofficial`, or the reverse). Verified on the real `gpui-kit` 0.6.0
stack: resolution + lock shape (T-22), and now a default-feature compile
against uno 1.19.0-pre (T-24 — exit 0, one engine). The reverse direction on
a real uno-bound kit (`gpuikit` 0.9.0) is measured too: blocked for the
untouched kit, unblocked by one source line — see the Open risk section.

## When this applies (and when it does not)

A layer crate pins its framework through a **normal dependency with a rename**
— `gpui-kit` 0.6.0 declares `gpui = { package = "gpui-pre", version = "^0.3.1" }`
— so cargo compiles *that package* (and the whole pre family under it) into
your graph no matter which fork your own manifest binds. If your app binds a
different fork, you get **two fork crates in one binary**: separate types
that do not unify (your `gpui::Window` is not the kit's `gpui::Window`),
which is the two-engine state `cargo gocar check-workspace` refuses.

The alias-shim swaps *which package that dependency actually compiles*,
without touching the kit. **It does not create interchangeability — it only
routes a package name to another release.** It is sound only when the two
releases are interchangeable to begin with, and the only interchangeability
evidence this repo has is the measured dataset:

| Measured generation (`api_hash`) | Releases in the class |
| --- | --- |
| `ef93dce2…` | `gpui-box` 0.1.x ≡ `gpui-unofficial` 1.12.0–1.16.3 |
| `546fcb11…` | `gpui-pre` 0.3.x ≡ `gpui-unofficial` **1.19.0-pre** |

So "I use unofficial but the kit binds gpui-pre" is expressible **only onto
uno 1.19.0-pre** — the uno release in the kit's generation. It is **not**
expressible onto uno 1.18.1 (`d143b846…`, a different generation): a shim
there would silently compile the kit against a different API with no data
behind it. If your pair is not one of the measured classes, the honest moves
are *adopt the layer's fork* (bind the same provider the layer uses — the
supported gocar path) or *fork the layer and maintain it against your fork*
— not an alias.

**Reverse direction is the same recipe with the names swapped.** Name the
shim after whichever package the *layer* binds (its index record or `cargo
metadata` tells you — for a hypothetical uno-bound kit that is
`gpui-unofficial`), point its re-export at the other release of the same
measured generation (`gpui-pre` 0.3.3 for an uno 1.19.0-pre-era layer), and
forward the impersonated package's feature names onto it. The measured
classes constrain this as tightly in reverse: uno 1.18.x (`d143b846…`) has no
pre counterpart, so an uno kit from that generation cannot be aliased onto
pre by this technique — only 1.19.0-pre-era layers can.

## The mechanism in one paragraph

Cargo's `[patch]` can replace a package only with another package of the
**same name and version** — which is why `gpui-pre` can never be patched to
`gpui-unofficial` directly. But nothing forces the replacement to contain
gpui-pre's *code*. A crate **named `gpui-pre` at the version the kit's
requirement resolves to** (0.3.3 for `^0.3.1`) whose entire `lib.rs` is
`pub use gpui_unofficial::*;` satisfies the patch rule while compiling
unofficial's items. Because re-exports are the *same items*, every crate in
the graph that depended on `gpui-pre` — the kit, its platform companion,
`gpui-component`, everything — now compiles against unofficial, and if your
app binds unofficial too, the graph holds **one** real engine whose types
unify.

## Step-by-step recipe (worked on `gpui-kit` 0.6.0 → uno 1.19.0-pre)

### 1. Identify the package the layer binds

`cargo metadata` on a scratch project that depends on the layer, or its
index record: find the dependency whose `package` is a gpui fork. For
`gpui-kit` 0.6.0 that is `gpui-pre` `^0.3.1` (plus its stack: the platform
companion `gpui-pre-platform`, `gpui-component`, `gpui-kit-assets` — all on
the same line).

### 2. Choose the impersonated version

The version the graph resolves for that package — what a current lock holds,
or `cargo tree | grep gpui-pre`. The shim's version becomes the pin, so pick
deliberately (0.3.3 here).

### 3. Write the alias crate — outside your workspace

`gpui-pre-alias/Cargo.toml`:

```toml
[package]
name = "gpui-pre"          # the package the layer binds — not your fork
version = "0.3.3"          # the version the graph resolves
edition = "2021"
publish = false

[dependencies]
gpui_unofficial = { package = "gpui-unofficial", version = "=1.19.0-pre" }

[features]
# --- replicate the IMPERSONATED package's feature surface (step 4) ---
default = ["font-kit", "wayland", "x11", "windows-manifest"]
font-kit = ["gpui_unofficial/font-kit"]
wayland = ["gpui_unofficial/wayland"]
x11 = ["gpui_unofficial/x11"]
windows-manifest = ["gpui_unofficial/windows-manifest"]
```

`gpui-pre-alias/src/lib.rs`:

```rust
//! The entire drop-in surface: the target release's public items under the
//! package name the layer expects. Same items — dependents and an app that
//! also binds the target get ONE engine whose types unify.
pub use gpui_unofficial::*;
```

Keep the crate **outside** the workspace that consumes it (a sibling path),
so it is a pure `[patch]` source and never a workspace member.

### 4. Replicate the feature surface — cargo enforces this

This is the step that bites. Cargo validates feature names at resolution
time: the platform companion requests `gpui-pre`'s `windows-manifest`
feature, and resolution fails with *"package `gpui-pre-platform` depends on
`gpui-pre` with feature `windows-manifest` but `gpui-pre` does not have that
feature"* unless the shim declares it. Read the impersonated package's real
`[features]` (`cargo metadata` → `.features`, or its unpacked manifest under
`$CARGO_HOME/registry/src/...`) and declare every name, forwarding to the
target release's feature of the same name where it exists. In the
`546fcb11…` class the two surfaces are near-identical; the one name
uno 1.19.0-pre lacks is `inspector` — declare it as an empty marker and treat
any dependent that enables it as unsupported until proven.

### 5. Patch it in, at the workspace root

```toml
[patch.crates-io]
gpui-pre = { path = "../gpui-pre-alias" }
```

Then regenerate the lock (`cargo generate-lockfile`, or `cargo gocar lock` —
its cargo leg respects `[patch]`). The lock's `gpui-pre` row now has **no
registry source** (it is the path patch) and `gpui-unofficial` 1.19.0-pre
appears exactly once:

```toml
[[package]]
name = "gpui-pre"
version = "0.3.3"
dependencies = ["gpui-unofficial"]      # no source line = the path patch

[[package]]
name = "gpui-unofficial"
version = "1.19.0-pre"
source = "registry+..."
```

### 6. Move the whole graph — this is a generation move, not a coexist trick

Cargo **refused** an app pinned to uno 1.18.1 beside a kit aliased to uno
1.19.0-pre (`failed to select a version for gpui-unofficial …
previously selected package gpui-unofficial v1.18.1`). Every crate that
interacts must sit in the aliased generation. The clean shape, verified in
the probe: the app binds the *impersonated* row too —

```toml
gpui = { package = "gpui-pre", version = "=0.3.3" }
gpui-platform = { package = "gpui-pre-platform", version = "=0.3.3", features = ["x11", "wayland"] }
```

— which is stable-numbered (gocar can manage the `gpui-pre` provider row)
and keeps one platform companion. What actually compiles is uno 1.19.0-pre,
underneath.

### 7. Verify

- `cargo tree -i gpui-unofficial` — the target release appears **once**, and
  the alias is its only path into the graph;
- `cargo build` of the workspace — the real test (see the open risk below);
- inspect the lock as in step 5.

## What gocar's audits will say (and why you should not fight them)

The v1 audits have **no `[patch]`/alias concept** — they key on package
*names* (the truth hierarchy: names are binding). On the aliased workspace,
measured 2026-09-07:

- `cargo gocar check-workspace` — exit 1: the app binding `gpui-pre` 0.3.3
  matches the lock's `gpui-pre` row, but uno 1.19.0-pre is a ✗ clash, with
  the note *"measured equal to the app's generation (546fcb11…) — same
  structural contract, but cargo compiles a second crate"* — even though the
  `gpui-pre` row *is* unofficial's code;
- `cargo gocar verify-env` — exit 1: the path-patched `gpui-pre` row is a ✗
  two-fork violation (the single-engine rule scans lock entries by provider
  name), uno ✓ verified.

Both are false alarms about the real graph (one engine) and **correct** about
v1's model (a name that is not what it says). The alias deliberately breaks
name↔identity; only measurement could re-verify it, which is exactly the
whole-graph rebind gocar has scoped to ROADMAP phase 5. Until then: the
alias-shim is a **plain-cargo technique** — keep such a project out of
`verify-env`/`check-workspace`-gated flows, or expect (and understand) the
refusal.

## Alternatives, compared

| Path | What you maintain | Tool status |
| --- | --- | --- |
| **Adopt the layer's fork** — bind the same provider the kit uses (`gpui-pre` via gocar) | nothing | ✓ gocar-verified, supported today |
| **Alias-shim** (this guide) | the alias crate: impersonated version + feature surface, tracked as the kit's requirements move | plain-cargo only; audits refuse |
| **Fork the layer** — path dep with its manifest pointed at your fork | every crate in the layer's stack (or disable the features that pull them) | gocar-visible, but you own the stack |
| **Whole-graph rebind** | nothing | ROADMAP phase 5 (designed; expresses this swap with generation proof) |

The alias-shim shines when the layer's stack is large (`gpui-kit` pulls the
companion, `gpui-component`, macros, assets): one crate + one patch line
re-route the whole family, where a manifest fork would cascade through every
crate.

## Open risk — compile status, measured 2026-09-07 (T-24)

The `546fcb11…` equality is **syn-level measurement** (item names + digests;
no cfg evaluation, T dimension 0). That is why the compile was the open risk:
the test that confirms or kills the drop-in claim is a default-feature build
of the aliased workspace. T-24 ran it (see [doc 12](12-alias-shim-compiled-both-real-kits.md)):

- **Main direction (kit binds `gpui-pre` → uno 1.19.0-pre): closed.** The real
  `gpui-kit` 0.6.0 default-feature stack compiles against uno 1.19.0-pre
  through this guide's recipe (`cargo build` exit 0; uno appears once in
  `cargo tree -i gpui-unofficial`, reachable only through the shim). The
  result is exactly the evidence the phase-5 rebind design needs — the
  strongest compile datapoint this repo has for a whole-stack generation swap.
- **Reverse direction (a real uno-bound kit → `gpui-pre`): blocked for the
  untouched kit, unblocked by one line.** The
  probe used `gpuikit` 0.9.0 (Nate Butler), which binds `gpui-unofficial
  ^1.14.2`. Two independent walls, both measured:
  1. *Resolution:* the caret excludes the 1.19.0-pre prerelease, so the
     published kit resolves uno 1.18.1 (`d143b846…`) — the generation with no
     pre counterpart — and even `cargo update --precise 1.19.0-pre` refuses.
     A pure shim (published kit, no manifest change) fails to resolve with
     the step-6 refusal shape.
  2. *Compilation:* steering the kit's requirements onto `=1.19.0-pre` (a
     vendored manifest — the guide's "fork the layer" row, minimal form) and
     shimming uno→gpui-pre compiles the kit against gpui-pre's items up to
     its first era break: `Window::blur` gained a `&mut App` argument between
     `d143b846…` and `546fcb11…`, and gpuikit's single call site
     (`elements/input.rs:199`) trips it — identically on registry uno
     1.19.0-pre with no shim, so the alias is exonerated. The corpus cannot
     see the break (its surfaces record `pub use window::*` as an opaque
     item); compile found what the item-set cannot.

**The break is one re-signature deep, and the kit-side fix is one line.** The
Escape action handler's `cx` is `&mut App` in this generation, so
`window.blur()` → `window.blur(cx)` compiles the vendored kit against real
gpui-pre 0.3.3 (exit 0, T-24 follow-up; the handler's previously-unused `_cx`
is the argument). **No engine-side shim exists for this class of break:**
inherent methods cannot be injected into `Window` from outside the engine
crate ("patch gpui" means forking the engine, which stops being the measured
crate), an extension trait loses to the inherent `blur(cx)` at the call site
(rustc method resolution prefers inherent candidates by name — verified with
a minimal probe, same E0061), and a no-arg `blur` could not run the new
`clear_pending_keystrokes(cx)` behavior anyway. The one-line patch is the
seed of the add-argument re-signature rule shape `gocar.rules.v1` cannot yet
express.

So: the drop-in claim is **compile-confirmed for the pre-bound kit**, and the
guide's reverse gate is **confirmed on the real uno-bound kit** — a kit whose
requirements float on a caret is only aliasable once it actually sits in the
measured class, and even then a generation-boundary re-signature may need a
one-line kit patch (the blur case above) rather than a pure shim.
