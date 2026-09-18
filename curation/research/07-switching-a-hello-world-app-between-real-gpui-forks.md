# 07 — Switching a Hello World app between real GPUI forks

## What this establishes

You can switch an application between different GPUI forks without changing a
single line of Rust code, provided both forks belong to the same measured API
generation. Because the modern forks split their platform code into separate
companion crates, a compiling project is always *the fork plus its
version-locked companion*.

This study is where the gap in that second half was found: the tool could
scaffold and lock a project whose companion had drifted onto a different fork,
and only the workspace audit refused it.

## What was not known before

Earlier tests only verified project setup and fork switching against mock
configuration files and code snippets that were never actually compiled.
Nobody had checked whether a freshly generated project could build against
real packages published on crates.io, or whether switching an app between
forks would survive a real compiler run.

Three questions needed answers: does a newly generated starter app compile out
of the box? Are equivalent forks truly interchangeable in practice? And what
happens when a dependency configuration goes wrong?

## What was run

On 2026-09-06 and 2026-09-07 we tested project generation, dependency locking,
and fork switching across all six GPUI providers on crates.io, with rustc
1.95.0 and 1.97.1 on Linux.

The first failure explained itself: in February 2026 the ecosystem moved the
platform layer out of the fork crates into separate companion crates (such as
`gpui-box-platform`), and the shipped template still used the pre-split launch
shape — the old startup call no longer exists in a modern fork engine. Once
each fork was paired with its matching companion, all six providers built
through the tool-only loop (`cargo gocar new` → `lock` → `cargo build
--locked`), on real crates.io artifacts, with zero hand edits:

| Provider | Binding unit | Toolchain | Result |
| --- | --- | --- | --- |
| legacy `gpui` | `gpui` 0.2.2 alone (pre-split) | 1.95 | ✓ 1m59s |
| `gpui-ce` | `gpui-ce` 0.2.2 + `gpui_ce_platform` 0.1.0 | 1.95 | ✓ 2m11s |
| `gpui-unofficial` | 1.18.1 + `gpui-platform-gpui-unofficial` 1.18.1 | 1.95 | ✓ 1m52s |
| `gpui-pre` | 0.3.3 + `gpui-pre-platform` 0.3.3 | 1.95 | ✓ 1m41s |
| `kael` | 0.4.1 alone (pre-split) | 1.97.1 | ✓ 2m39s |
| `gpui-box` | 0.1.1 + `gpui-box-platform` 0.1.1 | 1.97.1 | ✓ 1m52s |

Switching was then tested directly: two projects differing only in their two
`Cargo.toml` dependency lines, `gpui-unofficial` 1.16.1 and `gpui-box` 0.1.1,
both in measured generation `ef93dce2…`. The same `main.rs` compiled unchanged
on both sides.

The third run was deliberately broken: the engine line was updated and the
older companion left behind, so the lock held two forks. Re-locking floated
the stale companion to `gpui-unofficial` 1.18.1 and the graph held both it
and `gpui-box` 0.1.1; `verify-env` reported green. The workspace audit caught
it, and named both bindings:

```console
✗ clash — d143b8461933 vs ef93dce22b1e — structurally different
```

Swapping the companion line to `gpui-box-platform` 0.1.1 and re-locking left a
single engine: it compiles, `verify-env` is clean, `check-workspace` exits 0.

### What the run established

- The shipped scaffold did not compile for the post-split providers: its
  launch API predated the extraction and its manifest omitted the companion.
  That is fixed — `cargo gocar new` now writes the era-correct binding unit
  (post-split for the four companion providers, pre-split for `kael` and
  legacy `gpui`), and both lines are rewritten and pinned together.
- The binding unit the tool modelled — the fork package alone — was
  incomplete. It is now fork plus version-locked companion, carried in the
  dataset as a per-provider `platform_companion` record (the companion names
  are irregular — `gpui-platform-gpui-unofficial` vs `gpui-box-platform` vs
  `gpui_ce_platform` — so the pairing is data, not something derived).
- The tool could create the two-engine state its own audit refuses: nothing
  between `add` and `check-workspace` prevented it. `verify-env` now enforces
  the single-engine rule, and this study's exact lock (gpui-box 0.1.1 +
  gpui-unofficial 1.18.1) exits 1.
- The audit's gate works on platform companions the same way it works on a
  kit's transitive binding: the app provider's own companion matches, a
  foreign provider's companion is a clash.
- The measured core held throughout: resolution, byte-exact pins, the proof
  ledger, the baseline audit, and compile-level generation equality all
  behaved as documented on real artifacts.

## What it means for you

- **A modern project is a two-package deal.** Except `kael` and legacy
  `gpui` (both pre-split), every fork needs its core crate and a companion
  platform crate — `gpui-box` with `gpui-box-platform`, and so on.
- **Switch both lines together.** `cargo gocar add --equivalent` swaps the
  engine and its companion in one diff; the pairing comes from the dataset,
  so it is the same operation for every provider.
- **Never compile two engines into one binary.** Your workspace must resolve
  to exactly one fork; if an unpinned companion pulls in a second one,
  `check-workspace` refuses it, naming both bindings and generations.
- **Prerelease generations are bound through their stable-numbered row.**
  `cargo gocar lock` refuses a prerelease floor by design, so
  `gpui-unofficial` 1.19.0-pre is not bindable directly; its measured-equal
  `gpui-pre` 0.3.3 is, and that is why the correlation exists as its own
  provider row.
- **Older Kael releases need system libraries.** An app bound to `kael`
  ≤ 0.2.0 needs the Linux webkit stack (`javascriptcoregtk-4.1`, and
  `libsoup-3.0` for 0.1.1) — a pkg-config failure, not a compile failure of
  the fork or the template. `kael` 0.4.1 and the other forks build without
  it. Note that a lock like this still audits green: the audit sees the
  dataset and the compiler, not your machine's libraries.

## What this does not establish

- Only basic window initialization was compiled. Complex widgets, layout
  trees, custom shaders, and runtime behaviour were not exercised — this is
  evidence about the binding and the build, not about an app.
- Linux only (Wayland and X11). macOS and Windows platform layers were not
  evaluated.
- The matrix rests on build evidence. `report` returns nothing checkable on
  this app shape (a single-file crate root at the time), so the used-slice
  window claims are not compile-tested here.
- Compile-time compatibility is not runtime equivalence: nothing here
  benchmarks rendering or memory.

## Provenance

- **Evaluated packages:** all six providers on crates.io — `gpui`,
  `gpui-ce`, `gpui-unofficial`, `gpui-pre`, `kael`, `gpui-box`.
- **Verified generation:** `ef93dce2…`, shared by `gpui-box` 0.1.1 and
  `gpui-unofficial` 1.12.0–1.16.3.
- **Environment:** rustc 1.95.0 and 1.97.1 on Linux, 2026-09-06, extended to
  all six providers 2026-09-07. Probe projects were temporary and are not
  part of the shipped dataset.
- **Related reading:** [08 — Checking real application code with the compatibility
  report](08-checking-real-application-code-with-the-compatibility-report.md) and
  [10 — Auditing third-party UI kits and Rust compiler
  requirements](10-auditing-third-party-ui-kits-and-rust-compiler-requirements.md).
