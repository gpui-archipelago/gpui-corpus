# 11 — Using third-party UI kits with alternative GPUI forks

## What this establishes

An alias shim lets you use a third-party kit built for one GPUI fork inside an
app bound to another, without compiling two engines: one small crate and one
`[patch]` line, no fork of the kit. It is sound only when the two releases are
the same *measured generation* — `gpui-pre` 0.3.x and `gpui-unofficial`
1.19.0-pre are — and that constraint decides whether the technique is
available to you at all.

## What was not known before

A layer crate pins its framework through a normal dependency with a rename:
`gpui-kit` 0.6.0 declares `gpui = { package = "gpui-pre", version = "^0.3.1" }`,
so cargo compiles *that package* (and the whole `pre` family under it) into
your graph no matter which fork your own manifest binds. If your app binds a
different fork you get **two fork crates in one binary**: separate types that
do not unify — your `gpui::Window` is not the kit's `gpui::Window` — which is
the two-engine state the workspace audit refuses.

The question was whether Cargo's `[patch]` could redirect the kit's dependency
onto an equivalent fork without maintaining a private fork of the whole kit —
and what the steps and the pitfalls are.

## What was run

On 2026-09-07, against real crates.io artifacts under rustc 1.95.0 on Linux,
in both directions of the twin generation `546fcb11…`:

| Kit | Binds | Target | Outcome |
| --- | --- | --- | --- |
| `gpui-kit` 0.6.0 (its whole stack) | `gpui-pre` `^0.3.1` | `gpui-unofficial` 1.19.0-pre | built, exit 0 ([doc 12](12-verifying-the-alias-shim-with-real-ui-kits.md): 3m05s) |
| `gpuikit` 0.9.0, untouched | `gpui-unofficial` `^1.14.2` | `gpui-pre` 0.3.3 | blocked at resolution — the caret excludes a prerelease |
| `gpuikit` 0.9.0, requirements steered | `gpui-unofficial` `=1.19.0-pre` | `gpui-pre` 0.3.3 | failed to compile — one re-signature ([doc 12](12-verifying-the-alias-shim-with-real-ui-kits.md)) |
| `gpuikit` 0.9.0, one line patched | `gpui-unofficial` `=1.19.0-pre` | `gpui-pre` 0.3.3 | built, exit 0 ([doc 12](12-verifying-the-alias-shim-with-real-ui-kits.md): 1m37s) |

The `gpui-kit` stack compiled against `gpui-unofficial` with `cargo tree -i
gpui-unofficial` showing the release **once**, reachable only through the shim.
For the reverse direction, the kit's single call site needed
`window.blur()` → `window.blur(cx)`; the same error appears on registry uno
1.19.0-pre with no shim at all, so the alias is not what broke it.

The interchangeability evidence is the measured dataset, and it is the whole
justification for the technique:

| Measured generation | Releases in the class |
| --- | --- |
| `ef93dce2…` | `gpui-box` 0.1.x ≡ `gpui-unofficial` 1.12.0–1.16.3 |
| `546fcb11…` | `gpui-pre` 0.3.x ≡ `gpui-unofficial` **1.19.0-pre** |

## What it means for you

**Check the generation first.** "I use unofficial, the kit binds gpui-pre" is
expressible *only* onto uno 1.19.0-pre — the uno release in the kit's
generation. It is **not** expressible onto uno 1.18.1, a different generation
(`d143b846…`): that shim would compile the kit against a different API with no
data behind it. If your pair is not one of the classes above, the honest moves
are to **adopt the layer's fork** (bind the provider the layer uses) or to
**fork the layer** — not to alias.

Then the recipe, worked on `gpui-kit` 0.6.0 → uno 1.19.0-pre:

1. **Find the package the layer binds.** `cargo metadata` on a scratch project
   that depends on it, or its index record: look for the dependency whose
   `package` is a fork. Here `gpui-pre` `^0.3.1`, plus its stack
   (`gpui-pre-platform`, `gpui-component`, `gpui-kit-assets` — same line).
2. **Choose the impersonated version** — the version the graph resolves for
   that package (`cargo tree | grep gpui-pre`, or the lock): `0.3.3` here.
3. **Write the alias crate, outside your workspace.** It must be *named* the
   package the layer binds, at that version, and re-export the target release:

   ```toml
   # gpui-pre-alias/Cargo.toml
   [package]
   name = "gpui-pre"          # the package the layer binds — not your fork
   version = "0.3.3"          # the version the graph resolves
   edition = "2021"
   publish = false

   [dependencies]
   gpui_unofficial = { package = "gpui-unofficial", version = "=1.19.0-pre" }

   [features]
   default = ["font-kit", "wayland", "x11", "windows-manifest"]
   font-kit = ["gpui_unofficial/font-kit"]
   wayland = ["gpui_unofficial/wayland"]
   x11 = ["gpui_unofficial/x11"]
   windows-manifest = ["gpui_unofficial/windows-manifest"]
   ```

   ```rust
   // gpui-pre-alias/src/lib.rs
   pub use gpui_unofficial::*;
   ```

   Keep it a *sibling* of the workspace that consumes it, so it is a pure
   `[patch]` source and never a workspace member.
4. **Replicate the feature surface — cargo enforces this.** Resolution fails
   outright (*"depends on `gpui-pre` with feature `windows-manifest` but
   `gpui-pre` does not have that feature"*) unless the shim declares every name
   the dependents ask for. Read the impersonated package's real `[features]`
   and forward each onto the target release. In the `546fcb11…` class the two
   surfaces are near-identical; the one name uno 1.19.0-pre lacks is
   `inspector` — declare it as an empty marker, and treat any dependent that
   enables it as unsupported until proven.
5. **Patch it in at the workspace root**, then regenerate the lock:

   ```toml
   [patch.crates-io]
   gpui-pre = { path = "../gpui-pre-alias" }
   ```

   In the lock the `gpui-pre` row now has **no registry source** (it is the
   path patch) and `gpui-unofficial` 1.19.0-pre appears once.
6. **Move the whole graph — this is a generation move, not a coexist trick.**
   Cargo refuses an app pinned to uno 1.18.1 beside a kit aliased to uno
   1.19.0-pre (`previously selected package gpui-unofficial v1.18.1`). The
   clean shape, verified in the probe, is that the app binds the *impersonated*
   row too:

   ```toml
   gpui = { package = "gpui-pre", version = "=0.3.3" }
   gpui-platform = { package = "gpui-pre-platform", version = "=0.3.3", features = ["x11", "wayland"] }
   ```

   That row is stable-numbered, so the tool can manage `gpui-pre` as a provider
   — and what actually compiles underneath is uno 1.19.0-pre.
7. **Verify.** `cargo tree -i gpui-unofficial` — the target release appears
   once and the alias is its only path in; `cargo build` of the workspace, the
   real test; and the lock shape from step 5.

**Expect the audits to refuse, and do not fight them.** The tooling has no
`[patch]`/alias concept — it keys on package *names*, because names are the
binding. On the aliased workspace, measured: `check-workspace` exits 1 with uno
1.19.0-pre as a clash, noting *"measured equal to the app's generation
(546fcb11…) — same structural contract, but cargo compiles a second crate"*,
and `verify-env` exits 1 on the path-patched row as a second fork. Both are
false alarms about the real graph and *correct* about the model: the alias
deliberately breaks name↔identity, and only a whole-graph rebind can
re-verify it. Until that exists, treat this as a plain-cargo technique — keep
such a project out of audit-gated flows, or expect the refusal knowingly.

Worth knowing what you are choosing between:

| Path | What you maintain | Status |
| --- | --- | --- |
| Adopt the layer's fork (bind `gpui-pre`) | nothing | the supported path |
| Alias shim (this guide) | the alias crate: impersonated version + feature surface, revised as the kit's requirements move | plain cargo only; audits refuse |
| Fork the layer | every crate in its stack | visible to the tool; you own the stack |
| Whole-graph rebind | nothing | designed, not built |

The shim earns its keep when the layer's stack is large: one crate and one
patch line re-route the whole family, where a manifest fork would cascade
through every crate.

## What this does not establish

- **It cannot bridge generations.** It routes a package name to another
  release; it does not make two APIs interchangeable. Only the measured
  classes above count as evidence, and outside them an alias compiles
  something nobody measured.
- **It cannot fix a re-signed method.** The reverse direction needed a real
  one-line kit patch: `Window::blur` gained a `&mut App` argument between the
  two generations, and `window.blur()` → `window.blur(cx)` is the fix. There is
  **no engine-side shim for this class**: inherent methods cannot be injected
  into the engine's type from outside its crate, and an extension trait loses
  to the inherent method at the call site.
- **Non-default features are unproven.** A feature the target release lacks is
  declared as an empty marker, so a dependent that needs it is unsupported
  until someone compiles it.
- One kit family and one two-kit sample: other stacks, other generations, and
  other platforms (Linux only here) were not exercised.

## Provenance

- **Packages:** `gpui-kit` 0.6.0 (Longbridge) and `gpuikit` 0.9.0 (Nate
  Butler), against `gpui-unofficial` 1.19.0-pre and `gpui-pre` 0.3.3.
- **Twin generation:** `546fcb11…`, shared by `gpui-pre` 0.3.x and
  `gpui-unofficial` 1.19.0-pre.
- **Compile evidence:** [doc 12](12-verifying-the-alias-shim-with-real-ui-kits.md)'s
  runs — rustc 1.95.0, 12 cores, Linux, 2026-09-07. The aliased projects were
  temporary and are not part of the shipped dataset.
- **Related reading:** [10 — Auditing third-party UI kits and Rust compiler
  requirements](10-auditing-third-party-ui-kits-and-rust-compiler-requirements.md)
  for what the workspace audit does with the clash, and
  [12](12-verifying-the-alias-shim-with-real-ui-kits.md) for the compile half in full.
