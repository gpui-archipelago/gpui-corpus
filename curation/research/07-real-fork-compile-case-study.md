# 07 — Case study: a real hello world, switched between real forks

**Status:** completed study, 2026-09-06. Validation evidence against real
crates.io artifacts; it refines claims made in 02/03 and STATUS.md. The
consumer-workflow blocker it confirmed was filed as [T-17](../../tasks/archive/T-17-platform-companion-binding.md)
in the MVP blocker sweep and **fixed on 2026-09-06** — the dataset
`platform_companion` mapping, binding-unit semantics across
scaffold/`add`/`plan`/`lock`, era-correct templates, the `verify-env`
two-fork violation and companion-aware `check-workspace` now land; see the
"Claims this study refines" section below for what each claim became.

## Question

Everything before this study ran on fixtures: e2e tests spawn `cargo gocar`
against fixture manifests that are *"never compiled"*, `lock` tests use
`--offline`, and the facade demo compiles fixture forks that mirror recorded
surfaces. UC-01 itself warns that the scaffolded `main.rs` is *"a template to
compile-check after locking"* — nobody had ever compile-checked it.

So: **can a real hello world be scaffolded, locked, built, and switched
between real GPUI backends — and do the tool's measured claims survive actual
compilation?**

## What the study found first: the ecosystem moved (Feb 2026)

The fork crates published since the `gpui_platform` extraction (February
2026) ship **no platform layer at all** — verified in the unpacked sources of
`gpui-unofficial` 1.16.1 and 1.18.1: `src/app.rs` exposes only
`Application::with_platform`/`new_inaccessible`, the `x11`/`wayland` features
are empty, and no platform modules exist in the crate. An application that
wants to launch is a **two-package binding**: the fork crate (renamed
`gpui`) plus a **per-fork platform companion**, version-locked 1:1:

| Fork | Platform companion (crates.io) | Linux backend | Renderer |
| --- | --- | --- | --- |
| `gpui-unofficial` | `gpui-platform-gpui-unofficial@<fork version>` | `gpui-linux-gpui-unofficial` | `gpui-wgpu-gpui-unofficial` |
| `gpui-box` | `gpui-box-platform@0.1.x` | `gpui-box-linux` | `gpui-box-wgpu` |
| `gpui-ce` | `gpui_ce_platform` | (same pattern) | |
| `gpui-pre` | `gpui-pre-platform` | (same pattern) | |
| `kael` | **none** — forked before the extraction; ships its own x11/wayland stack in-crate | | |

The companion crates re-export the launch entry point under a stable extern
name (`gpui_platform::application()`), mirroring the `gpui` rename trick, so
app code never names a fork. Kael is the only provider that is
self-contained. The platform companions are *not* in the gocar dataset —
structurally they are exactly the "kit" shape of UC-02 G4: an ecosystem crate
that pins a fork transitively.

## Environment (this run)

- rustc 1.95.0 stable; 1.97.1 installed for gpui-box (declares `rust-version`
  1.97) and kael (1.97.1). gpui-unofficial 1.18.1 compiled on 1.95.
- System libraries present: vulkan/egl/gl, xkbcommon, x11, wayland,
  fontconfig, openssl, alsa.
- `CARGO_HOME` pointed at a persistent cache; each study project is a
  standalone crate with its own empty `[workspace]` and per-project
  `target/`.

## Compile matrix (all real crates.io artifacts, debug builds)

| Binding | Launch API used | Result |
| --- | --- | --- |
| Unmodified `cargo gocar new` scaffold (`hello-s1`), unofficial 1.18.1 | `Application::new()` + 1-arg window callback | ✗ — E0599 (`new` missing), E0593 (callback arity) |
| Same template, unofficial 1.16.1 (`hello-161`) | same | ✗ — identical two errors (post-split API on both) |
| `hello-box`: gpui-box 0.1.1 + gpui-box-platform 0.1.1 | `gpui_platform::application()` + 2-arg callback | ✓ compiles (1m43s) |
| `hello-uno16`: gpui-unofficial 1.16.1 + gpui-platform-gpui-unofficial 1.16.1 | **byte-identical `main.rs`** to `hello-box` | ✓ compiles (2m11s) |
| `hello-kael`: kael 0.4.1, no companion | template, one-line fix (2-arg callback) | ✓ compiles (`Application::new()` survives on kael) |

The measured-equivalence headline held at compile level: `hello-box` and
`hello-uno16` differ only in two `Cargo.toml` dependency lines — the app
compiles unchanged on both sides of the dataset-asserted `ef93dce2…`
generation (gpui-box 0.1.1 ≡ gpui-unofficial 1.12.0–1.16.3). That is
UC-02's `--equivalent` switch, proven with a real compiler on real code.

## The gocar workflow, run end-to-end on real code

Starting from `hello-uno16` (unofficial 1.16.1, gocar-managed metadata, plus
the manually-added platform companion):

1. `cargo gocar add gpui-box --equivalent --dry-run` — resolves correctly:
   baseline `ef93dce2…`, target gpui-box 0.1.1 (same hash), records
   `api-baseline-hash`, rebinds the `gpui` line. **The `gpui-platform` line
   is untouched** — still `gpui-platform-gpui-unofficial 1.16.1`.
2. Real `add` + `cargo gocar lock` — the stale companion's `^1.16.1`
   requirement floats to **gpui-unofficial 1.18.1** on re-lock. The graph now
   holds two forks: gpui-box 0.1.1 (`ef93dce2…`) *and* gpui-unofficial 1.18.1
   (`d143b846…`) — a second engine in a *different* generation than the
   recorded baseline.
3. `cargo gocar verify-env` — **exit 0** on that two-engine graph: both rows
   `✓ verified` (gpui-box "matches recorded equivalence baseline ef93dce2…",
   gpui-unofficial verified against its own dataset record), 723 untracked
   warnings, 0 violations. Nothing in the audit knows a workspace must
   contain exactly one contract engine.
4. `cargo gocar check-workspace` — catches it: `✗ clash`, exit 1, naming both
   bindings and generations (`d143b8461933 vs ef93dce22b1e — structurally
   different`).
5. Manual fix: swap the companion line to `gpui-box-platform 0.1.1`,
   re-lock, rebuild — single engine, compiles; `verify-env` ✓ and
   `check-workspace` clean (exit 0).

## Findings

1. **The scaffold does not compile for post-split providers.** The shipped
   template's launch API predates the Feb-2026 extraction and the manifest
   omits the platform companion. Kael (pre-split) compiles the template with
   a one-line callback-arity fix.
2. **The binding unit gocar models — the fork package alone — is incomplete.**
   A compiling app on gpui-unofficial/gpui-box/gpui-ce/gpui-pre is fork +
   version-locked companion. `add --equivalent` rebinds only the `gpui` line;
   `plan`/`lock`/`verify-env` have no notion of the companion.
3. **The tool can create the two-engine state its own audit refuses.** The
   tool's `add --equivalent` produced the two-fork graph above; nothing in
   the workflow between `add` and `check-workspace` prevents it, and
   `verify-env` (the step UC-02 frames as the audit loop) reports green on it.
4. **T-15's gate works as designed.** `check-workspace` refused the stale
   companion's fork exactly like a kit's transitive binding — the UC-02 G4 /
   UC-08 semantics generalized to platform companions, correctly.
5. **The measured core held.** Resolution, byte-exact pins, the proof
   ledger, the baseline audit (on a single-engine lock), and compile-level
   generation equality all behaved as documented on real artifacts.

## Implications

The gap is **product-surface, not measurement**: gocar's consumer manifest
model predates the post-split ecosystem. Fixing it is v1 consumer-workflow
work — not Phase-3 compiler work, and the T dimension would not catch a
two-engine lock. A follow-up task should cover: a per-provider
`platform_companion` field in the dataset (companion names are irregular —
`gpui-platform-gpui-unofficial` vs `gpui-box-platform` vs `gpui_ce_platform` —
so the mapping must be data, not derived), binding-unit semantics threaded
through scaffold/`add`/`plan`/`lock`, a generation-correct scaffold template
per provider, and a `verify-env` rule that a lock holding two dataset forks
is a violation even when both rows verify individually.

Phase-3 is de-risked by the same work: the corpus TVM pass must compile real
fork releases, and this study already built three real fork graphs cleanly
(gpui-box, gpui-unofficial 1.16.1, kael) plus gpui-unofficial 1.18.1's
library — the rustc + dependency-resolution machinery works on the hard
cases in this environment.

## Reproducing

The study's throwaway projects live under `target/study/` in this repo
(`hello-s1`, `hello-161`, `hello-box`, `hello-uno16`, `hello-kael`, each with
its manifest/lock/`.gocar` proof; build dirs were removed — they are
gitignored and rebuildable). To reproduce from scratch you need network
(crates.io), ~10 GB, and a few minutes per backend:

```console
# post-split consumer shape (gpui-box 0.1.1, rust-version 1.97):
# Cargo.toml
#   gpui          = { package = "gpui-box", version = "=0.1.1" }
#   gpui-platform = { package = "gpui-box-platform", version = "=0.1.1",
#                     features = ["x11", "wayland"] }
# src/main.rs uses `gpui_platform::application()` and 2-arg window callbacks
# (mirror the fork crate's own examples/hello_world.rs).
cargo +1.97.1 build

# the equivalent side (measured equal, ef93dce2): swap the two lines to
# gpui-unofficial 1.16.1 + gpui-platform-gpui-unofficial 1.16.1, same main.rs.
# kael (self-contained, rust-version 1.97.1): only `gpui = { package = "kael",
# version = "=0.4.1" }` — the original template compiles after the one-line
# callback fix.
```

## Claims this study refines

- README quickstart + UC-01: the scaffolded hello world is not yet
  compile-checked for post-split providers; treat the quickstart's
  `new → lock → run` flow as kael-verified only until the follow-up task
  lands.
- STATUS.md CLI table row for `new`, and the honest-limitations list: the
  template limitation is now measured, with a root cause (Feb-2026 platform
  extraction) and a shape for the fix.

### Refined by T-17 (landed 2026-09-06)

- **Finding 1 → resolved.** `cargo gocar new` now writes the era-correct
  binding unit: the post-split launch shape (`gpui_platform::application()`,
  2-arg window callbacks) *plus* the version-locked companion dependency for
  companion providers, the pre-split shape for kael and the legacy `gpui`
  provider. The tool-only loop (`new → cargo gocar lock → cargo build
  --locked`) compiled green on real artifacts for gpui-box 0.1.1 (rustc
  1.97.1), gpui-unofficial 1.18.1 (rustc 1.95) and kael 0.4.1 (rustc 1.97.1)
  in this run; gpui-ce/gpui-pre/gpui-official rows followed in T-20's
  compile matrix below (all six providers compile-green, 2026-09-07).
- **Finding 2 → resolved.** The binding unit gocar models is fork +
  version-locked companion, carried in the dataset as a per-provider
  `platform_companion` record (`{ package, pin, features[], note }`, names
  and pairing verified against the crates.io sparse index — e.g. gpui-box
  and gpui-pre publish version-for-version mirror companions, while
  gpui_ce_platform is a single fixed 0.1.0 release). `plan`/`add`/`lock`
  rewrite and pin both lines together; the `gpui-platform` line can no
  longer float to a second fork.
- **Finding 3 → resolved.** `verify-env` gained the single-engine rule: a
  gocar-managed lock holding two dataset forks is a violation even when both
  rows verify individually, naming both forks. The study's exact two-engine
  lock (gpui-box 0.1.1 + gpui-unofficial 1.18.1) now exits 1.
- **Finding 4 → kept + extended.** `check-workspace` still refuses a second
  engine (the healthy switched project audits clean, exit 0); it now also
  counts a member's platform-companion binding as one engine unit — the
  app provider's own companion is a ✓ match, a foreign provider's companion
  is a ✗ clash.
- **Finding 5 → unchanged.** The measured core (resolution, byte-exact pins,
  proof ledger, baseline audit, compile-level generation equality) behaved
  as documented then and through the fix.

Re-running the study's workflow now: scaffold/lock/build on the post-split
providers needs no hand edits, and `add gpui-box --equivalent` from a
companion-carrying unofficial project swaps both lines in one diff.

---

## Compile matrix, extended to all six providers (T-20, 2026-09-07)

T-20 (tasks/archive/T-20) closed the remaining uncompile-checked rows: every
provider the dataset lists now compiles through today's tool loop
(`cargo gocar new` → `lock` → `cargo build --locked`) with **zero hand
edits**, on real crates.io artifacts. This run's environment: rustc 1.95.0
stable and 1.97.1 (gpui-box/kael declare `rust-version` 1.97/1.97.1),
`CARGO_HOME` = the shared persistent cache; probe projects under
`target/t20/` (gitignored; build trees removed after capture, per the
convention above).

### The six provider rows

| Provider row | Binding unit (dataset `platform_companion` map) | Toolchain | Result |
| --- | --- | --- | --- |
| legacy `gpui` | `gpui` 0.2.2 alone — pre-split, ships its own platform | 1.95 | ✓ 1m59s |
| `gpui-ce` | `gpui-ce` 0.2.2 + `gpui_ce_platform` 0.1.0 (fixed pin) | 1.95 | ✓ 2m11s |
| `gpui-unofficial` head | 1.18.1 + `gpui-platform-gpui-unofficial` 1.18.1 | 1.95 | ✓ 1m52s |
| `gpui-pre` | `gpui-pre` 0.3.3 + `gpui-pre-platform` 0.3.3 (hard `=N` pin) | 1.95 | ✓ 1m41s |
| `kael` head | 0.4.1 alone — pre-split | 1.97.1 | ✓ 2m39s |
| `gpui-box` | 0.1.1 + `gpui-box-platform` 0.1.1 | 1.97.1 | ✓ 1m52s |

Same scaffold `main.rs` per era (post-split shape for the four companion
providers, pre-split `Application::new()` for kael/`gpui`); every row
audits clean (`verify-env` exit 0, 0 violations; the kael rows only warn on
their declared 1.97.1 floor when audited under rustc 1.95).
`check-workspace` on the companion rows: 1 binding · 1 match · 0 clash,
exit 0.

### Window edges the sweep added

1. **The `546fcb11…` correlation compiles on both sides.** gpui-pre 0.3.3 is
the tool-bound row above; its measured-equal unofficial **1.19.0-pre** +
`gpui-platform-gpui-unofficial` 1.19.0-pre also compiles the same
`main.rs` (plain cargo, exact pins, rustc 1.95). Binding-path finding:
`cargo gocar lock` refuses a prerelease floor (`no candidate version
satisfies the request`; `lock` has no `--allow-prerelease` — resolve's flag
only admits candidates). The supported way to bind that generation is the
stable-numbered `gpui-pre` provider row — which is exactly why the
correlation is exposed as its own provider. (The `ef93dce2…` generation
needs no such row: unofficial's 1.12.0–1.16.3 side is stable-numbered, so
both sides are tool-bindable.)
2. **Within-epoch equality holds at compile level.** kael 0.4.0 (epoch mate
of the case-study 0.4.1, both `ffd8c3fd…`) compiles the identical source —
no signature drift inside measured-equal items on this surface.
3. **kael 0.3.1** (previous epoch `8267ca35…`) also compiles the same source
under 1.97.1 — the measured 0.3.x→0.4.x break does not touch the
hello-world surface (it lives in kael's deeper API), and the template's
`Application::new()` exists across both epochs (the 0.3.1+ READMEs prefer
`Application::try_new()?`, but `new()` still compiles).
4. **kael 0.2.0 / 0.1.1 — environment-blocked, not a dataset
contradiction.** Both older kael releases carry a *mandatory* Linux `wry
0.55.1` dependency (features `os-webview`, not optional in 0.1.1/0.2.0's
manifests; 0.3.1 made wry optional, 0.4.x dropped it from the default
graph). Building an app bound to them dies inside `javascriptcore-rs-sys`
(needs system `javascriptcoregtk-4.1`) and, for 0.1.1, `soup3-sys`
(`libsoup-3.0`) — pkg-config failures, i.e. system libraries this
environment does not carry (the case-study list above has no webkitgtk).
Not a compile failure of kael or the template, and not a dataset claim —
but a real era-drift fact for consumers: **an app bound to kael ≤0.2.0
requires the webkit2gtk system stack on Linux**. Their locks still audit
`verify-env` green, which is a scope note worth keeping: the audit sees the
dataset + compiler, not system libraries.

### Verdict

**No compile-vs-dataset-claim contradiction was found.** The feature/cfg
blindness hypotheses from the task file did not bite on these windows: the
post-split companion rows, the prerelease-generation correlation, and the
within-epoch pairs all compiled as the dataset's name-level equality
predicts for this app surface. The only new facts are consumer-side:
prerelease floors are unmanageable by design (use the stable provider row),
and kael ≤0.2.0 needs webkitgtk system libs on Linux. `report` on the
scaffolded app stays mute (0 checkable used symbols at v0 granularity —
the T-18 scanner gap on single-file crate roots), so the matrix rests on
build evidence, and the used-slice window claims remain
fixture-/report-tested rather than compile-tested on this app shape.

Claims this section refines: T-17's acceptance input is closed — the
"gpui-ce/gpui-pre/gpui rows" deferrals above and in STATUS.md are now
compile-checked, and the scaffold template is compile-verified on all six
providers (2026-09-07).
