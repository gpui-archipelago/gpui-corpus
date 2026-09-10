# 10 — Study: a real kit on the workspace audit + toolchain floors under real rustc versions (T-22)

**Status:** completed study, 2026-09-07 (T-22, MVP blocker sweep). Verdicts:
**fine** — UC-08's kit semantics execute on the real `gpui-kit` 0.6.0 exactly
as modeled (`check-workspace` refuses the kit's fork, naming it and both
measured generations; `verify-env` flags the transitive fork as a
two-fork violation, exit 1), and declared toolchain floors behave predictably
under a too-old toolchain (cargo itself refuses the build with a precise
"requires rustc" error; gocar's advisory warn now fires for *every* real
declaration shape — one shape-dependent gap was found and fixed in-task).

## Question

Two MVP promises had only ever been reasoned about, never run against real
ecosystem shapes:

1. **A real kit crate** (UC-02 G4 / UC-08): a workspace whose app binds one
   fork while a kit dependency transitively pins another. The model says
   `cargo gocar check-workspace` refuses it, naming the kit's fork and its
   measured generation — but "a kit is not a provider row; its fork shows as a
   transitive clash" had never been executed.
2. **Declared toolchain floors** (UC-02 G3): on real bindings whose declared
   `rust-version` exceeds the active toolchain (gpui-box declares `1.97`;
   kael `1.97.1`), what do `plan`/`resolve`/`lock`/`verify-env`/`cargo build`
   each *actually* do — warn, refuse, or fail at compile time?

## Environment (this run)

- rustc 1.95.0 stable (active) + 1.97.1 installed (rustup). gpui-box 0.1.1
  declares `rust-version = "1.97"`; kael 0.4.1 declares `"1.97.1"` (dataset
  rows, registry verbatim).
- `CARGO_HOME = target/cargo-home` (persistent cache, 949 crates);
  probe projects under `target/t22/` (gitignored, per study convention).
- The floor probes are copies of T-20's gocar-managed `box-head`/`kael-head`
  probe projects (package names inherited; the `rust-toolchain.toml` pin was
  removed from the gpui-box copy so the default rustc 1.95.0 applies).

## Probe 1 — a real kit (`gpui-kit` 0.6.0) on the workspace audit

### The real kit's manifest (sparse-index record, 0.6.0)

The record confirms UC-02 G4's description *and* adds the T-17 companion
fact:

- `gpui` → `package: gpui-pre`, req `^0.3.1` (the rename that makes the kit a
  layer on top of a fork);
- **`gpui_platform` → `package: gpui-pre-platform`, req `^0.3.1`, features
  `["font-kit","x11","wayland","runtime_shaders"]` — a hard (non-optional)
  dependency**: the kit needs the T-17 platform-companion model to even
  build. Per the task note, the *audit* probe (lock + check-workspace) needs
  no full compile, so the build is deferred; the companion fact is recorded.
- plus non-wasm `reqwest_client` (renamed `gpui-pre-reqwest-client`), and
  default features pulling `gpui-component` 0.6.0 + `gpui-kit-assets` 0.6.0
  (+ `gpui-base` 0.6.0).

### The workspace under test

`target/t22/kit/`:

- root package `kit-app` — gocar-managed, `gpui = { package =
  "gpui-unofficial", version = "=1.18.1" }` + the `gpui-platform-gpui-unofficial`
  1.18.1 companion (era-correct binding unit);
- member `widget-lib` — a plain cargo lib whose only dependency is
  `gpui-kit = "=0.6.0"` (default features). The kit's fork binding is
  transitive from this member's view.

`cargo gocar lock` (network leg: the `gpui-kit` family index records;
everything else cached) resolved 903 packages and exited 0. The lock holds
two dataset fork packages: `gpui-unofficial` 1.18.1 (the app's engine) and
`gpui-pre` 0.3.3 (the kit's), each with its companion.

### `cargo gocar check-workspace` — refuses, naming fork + generations

Exit **1**. Rows (verbatim):

```
[WORKSPACE CHECK] contract gpui — one engine per binary (T-15, UC-08)
workspace root: .
app choice: kit-app
lockfile: Cargo.lock (905 registry package(s), 2 fork package(s))

  ✓ binding      kit-app     gpui -> gpui-unofficial 1.18.1   (generation d143b8461933)
      └─ app choice — every other binding is compared against this one
  · no binding   widget-lib  — -> —
      └─ no gpui-contract binding — outside the audit
  ✗ clash        (lock graph)  gpui -> gpui-pre 0.3.3   (generation 546fcb114739)
      └─ transitive contract binding in the locked graph — would compile a second engine (gpui-pre 0.3.3)
      └─ measured generation 546fcb114739 vs the app's d143b8461933 — structurally different
  ✓ match        (lock graph)  gpui -> gpui-unofficial 1.18.1   (generation d143b8461933)
      └─ the app's engine in the locked graph — matches the manifest pin

summary: 1 binding · 1 match(es) · 1 clash(es) · 0 unverifiable
error: check-workspace: 1 clash(es) — a second engine would compile; see the ✗ rows above
```

The member with no direct binding is an informational row; the kit's fork
comes from the lock view exactly as modeled, and the refusal names the fork
package, version, and both measured generations (`546fcb11…` vs `d143b846…`
— structurally different).

### `cargo gocar verify-env` — the transitive kit fork is a violation

Exit **1**; counts `{untracked: 901, verified: 1, violation: 1, warning: 0}`.
Rows for the fork packages:

- `gpui-pre 0.3.3` → ✗ violation. Its notes carry the T-17 single-engine
  rule, which names this exact situation: *"second dataset fork in one lock:
  gpui-pre 0.3.3 beside the bound 'gpui-unofficial' — a single-engine graph
  holds exactly one gpui fork package, and both rows verifying individually
  does not make this a one-engine lock. This is the stale-companion state a
  pre-T-17 switch produced (add rewrote only the gpui line) **or a transitive
  kit binding**; the bound fork gpui-unofficial 1.18.1 is in this lock. Fix:
  `cargo gocar lock` after `add` …, or align the kit — `cargo gocar
  check-workspace` names the bindings"*
- `gpui-unofficial 1.18.1` → ✓ verified (checksum + attestation).
- `gpui-kit` / `gpui-pre-platform` / `gpui-platform-gpui-unofficial` →
  untracked (no shadow-index record — companions and kits are not provider
  rows; only the fork package trips the rule).

**The case study's "two-row-green hole" does not reappear here.** The pre-T-17
hole was two fork rows each verifying individually with nothing knowing a
graph must hold one engine; T-17's rule reads the managed manifest's
provider and flips any second dataset fork row to ✗. On this real kit
workspace the kit's fork is exactly such a row — `verify-env` fails the
two-engine lock, `check-workspace` refuses it, both name the fork.

### Kit verdict + the one gap named (not a blocker)

**Verdict: fine.** UC-08's detect-and-refuse semantics and UC-02 G4's
"no dataset shape change" decision hold against the real `gpui-kit` 0.6.0;
the refusal names the fork and both generations; companion/kit crates are
correctly outside the dataset's fork rows.

One naming gap, minor and phase-5-shaped: the clash row names the **fork**
(`gpui-pre 0.3.3`) and its generation — the evidence that answers *why* it is
there — but not the **kit** that dragged it in (`gpui-kit 0.6.0`, via
`widget-lib`). `parse_lock` keeps only package name/version/checksum/source,
so the audit cannot walk the lock's dependency edges from the fork package
up to the member. Culprit naming needs a reverse-edge walk over `Cargo.lock`
dependency lists — the same whole-graph machinery phase 5's rebind needs —
so it is named here, not implemented.

## Probe 2 — declared toolchain floors under real rustc versions

Per probe project (gocar-managed, exact pins, no toolchain override): run
`resolve`/`plan`/`lock`/`verify-env` and `cargo build --locked`, first under
the active rustc 1.95.0, then under 1.97.1.

### gpui-box 0.1.1 (declares `1.97`) — rustc 1.95.0

| Tool | Behavior | Exit |
| --- | --- | --- |
| `resolve` / `plan` | resolve normally; **no floor warning** (see the finding below) | 0 |
| `lock` | pins + proof; cargo `generate-lockfile` resolves and notes every box crate *"(requires Rust 1.97)"* (rust-version fallback: exact pins leave no compatible alternative, so cargo locks them anyway) | 0 |
| `verify-env` | `✓ verified gpui-box 0.1.1` — **no ⚠ floor signal** (same root cause) | 0 |
| `verify-env --strict` | fails on the 699 untracked noise only | 1 |
| `cargo build --locked` | **refuses before compiling**: `error: rustc 1.95.0 is not supported by the following packages: gpui-box@0.1.1 requires rustc 1.97 …` (+ every box companion crate), *"Either upgrade rustc or select compatible dependency versions with `cargo update … --precise`"* | 101 |

Under rustc 1.97.1: no warning; `cargo build --locked` **exit 0** (1m46s);
`verify-env` `✓ verified` (only 699 untracked rows; `--strict` exit 1 on
those, never on the floor).

### kael 0.4.1 (declares `1.97.1`) — rustc 1.95.0

| Tool | Behavior | Exit |
| --- | --- | --- |
| `resolve` / `plan` / `lock` | resolve normally and **warn**: `warning: kael 0.4.1 declares rust-version 1.97.1 > active rustc 1.95.0 (declared, not attested); build may fail until the toolchain is upgraded or an EAC floor is attested` | 0 |
| `verify-env` | `⚠ warning kael 0.4.1` row (0 verified, 703 warnings incl. untracked) | 0 |
| `verify-env --strict` | fails (703 incl. the floor ⚠) | 1 |
| `cargo build --locked` | refuses before compiling: `kael@0.4.1 … requires rustc 1.97.1` (+ kael_util, kael_util_macros, …) | 101 |

Under rustc 1.97.1: no warning; `verify-env` `✓ verified`.

### Floor finding — one shape-dependent gap in gocar's own warn, fixed in-task

`verify-env` and `resolve`'s advisory compare the *declared* floor against the
active compiler, but the comparison parsed the declared value with
`toolchain::parse_rustc_version`, which mirrors rustc's own version lines and
requires a full `X.Y.Z` semver triplet. **gpui-box's real declaration is the
two-component `"1.97"`** (cargo reads it as a `1.97.0` floor) — the parse
failed, the warn was skipped, and `verify-env` audited the gpui-box row ✓
green under a rustc 1.95.0 that cargo refuses to build the crate with. kael's
parseable `1.97.1` warned correctly, so the advisory was silently
*declaration-shape-dependent* — the one place the documented "declared
floors shown and warned" (UC-02 G3 `partial`) did not hold.

Fix (landed in this task, T-17-style — a confirmed gap scoped inside the
sweep task that found it): `gocar-core::toolchain::parse_declared_rust_version`
normalizes partial floors by padding missing components with `.0` (`"1.97"`
⇔ `1.97.0`, cargo's caret reading) and rejects garbage; `resolve`'s
`warn_on_rust_version_gap` and `verify_env::assess` now use it, and the
warning prints the crate's *raw* declared string (`1.97`), not the padded
parse. Re-run on the gpui-box probe under rustc 1.95.0:

```
warning: gpui-box 0.1.1 declares rust-version 1.97 > active rustc 1.95.0 (declared, not attested); build may fail until the toolchain is upgraded or an EAC floor is attested
  ⚠ warning    gpui-box 0.1.1      # verify-env row now; summary: 0 verified, 700 warning(s) incl. untracked, 0 violation(s)
```

Tests: `toolchain::tests::parses_declared_rust_version_partial_floors` (padding,
non-widening, garbage rejection) and
`verify_env::tests::two_component_declared_floor_is_a_warning` (dataset-backed:
`rust_version "1.97"` → ⚠ under 1.95.0, ✓ under 1.97.1).

### Floor verdict

**Verdict: fine.** Cargo itself is the real gate and it is consistent and
precise: resolution tolerates a too-new declared floor under exact pins
(noting each *"requires Rust N"*), and `cargo build --locked` refuses with an
exit-101 error naming every offending package and the compatible-upgrade
command — before compiling anything. Gocar's own handling is advisory by
design (declared ≠ attested; EAC pruning is Phase 3/4) and now warns on every
real declaration shape: two-component (`gpui-box`), three-component
(`kael`), and none (`gpui-unofficial` 1.18.1 — no warning, correctly). The
only pre-fix defect was the parse gap above. EAC-era floor *pruning*
remains explicitly out of scope (Phase 3/4 work), per the task note.

## Reproducing

Probe projects (manifests, locks, `.gocar` proofs, captured outputs) live
under `target/t22/` — `kit/` (root `kit-app` + member `widget-lib`),
`floor-box/`, `floor-kael/` — gitignored and rebuildable. The kit lock needs
network for the `gpui-kit` family index records (`index.crates.io`); the
floor probes are fully offline with `CARGO_HOME=target/cargo-home`.

## Claims this study refines

- **UC-02 G3 (`partial`) → confirmed with evidence.** Declared floors are
  shown (`providers`, `resolve`'s `rust-ver` line) and warned on — the
  gpui-box two-component gap found here is fixed in-task — while cargo's
  own build-time refusal is the enforceable gate; attested-floor pruning
  stays Phase 3/4 EAC work.
- **UC-02 G4 / UC-08 kit semantics (`design`/`partial`) → executed.**
  `gpui-kit` 0.6.0 resolves as modeled (renamed `gpui-pre` ^0.3.1, hard
  `gpui-pre-platform` companion dep), `check-workspace` refuses the fork
  (exit 1, both generations named), `verify-env` flags the transitive fork
  as a two-fork violation (exit 1) — the case-study two-row-green hole does
  not reappear. The kit needs the T-17 companion model to even build
  (recorded; compile deferred per task note). Named delta: clash rows could
  name the kit that pulled the fork in via a lock-edge walk (phase-5 scope).
- **STATUS/ROADMAP sweep state:** T-22's verdict rows moved to
  tasks/archive/README (the 2026-09-07 close); T-21 (temporal-stability
  loop) closed the sweep the same day — verdicts + named deltas in
  [doc 14](../04-user-docs/14-temporal-stability-loop.md).
