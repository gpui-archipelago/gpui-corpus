# 10 — Auditing third-party UI kits and Rust compiler requirements

## What this establishes

The workspace audit catches a real third-party kit dragging a second GPUI
engine into the lock, names both forks with their measured generations, and
refuses it. Compiler floors are the other half: the tool's own warning is
advisory, cargo is the gate that actually refuses the build — and the study
found, and fixed, one declaration shape where the warning stayed silent.

## What was not known before

Two promises had only been reasoned about, never run against real ecosystem
shapes.

1. **A real kit crate.** A workspace whose app binds one fork while a kit
   dependency transitively pins another. The model said `check-workspace`
   refuses it, naming the kit's fork — but "a kit is not a provider row; its
   fork shows as a transitive clash" had never been executed.
2. **Declared compiler floors.** On bindings whose declared `rust-version`
   exceeds the installed toolchain (`gpui-box` declares `1.97`, `kael`
   `1.97.1`), what do resolve, lock, the environment check and `cargo build`
   each actually do — warn, refuse, or fail at compile time?

## What was run

On 2026-09-07, rustc 1.95.0 active with 1.97.1 installed alongside it, on
Linux. An offline cargo cache held the crates; the kit lock needed the
registry for the kit family's index records.

### Probe 1 — a real kit on the workspace audit

The workspace: a root app (`kit-app`) bound to `gpui-unofficial` 1.18.1 with
its companion — the era-correct binding unit — and a plain member library
(`widget-lib`) whose only dependency is `gpui-kit` 0.6.0. The kit itself is a
layer on a fork: its manifest renames `gpui` to `gpui-pre` `^0.3.1`, and it
needs `gpui-pre-platform` `^0.3.1` as a hard dependency — [doc 07](07-switching-a-hello-world-app-between-real-gpui-forks.md)'s
binding unit, in the wild.

Dependency resolution put both engines in one lock: `gpui-unofficial` 1.18.1
(the app's) and `gpui-pre` 0.3.3 (the kit's), each with its companion.

| Command | Exit | What it did |
| --- | --- | --- |
| `lock` | 0 | resolved 903 packages, wrote the proof ledger |
| `check-workspace` | 1 | refused: a second engine would compile |
| `verify-env` | 1 | one violation (`gpui-pre` 0.3.3), one verified (the bound fork) |

The workspace audit's refusal, verbatim:

```
[WORKSPACE CHECK] contract gpui — one engine per binary
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

`verify-env` flags the same fork as the violation, with the single-engine rule
as its reason: a managed lock holding two fork packages is not a one-engine
lock, and both rows verifying individually does not make it one. The kit and
the companion crates are untracked there — they are not provider rows, so only
the fork package trips the rule.

### Probe 2 — declared floors under real rustc versions

Each probe project (managed, exact pins) was run through resolve, plan, lock,
the environment check, and `cargo build --locked`, first under rustc 1.95.0 and
then under 1.97.1.

| Package | Declares | Advisory warning under 1.95.0 | `cargo build --locked` |
| --- | --- | --- | --- |
| `gpui-box` 0.1.1 | `1.97` (two components) | yes, after the fix below | refused, exit 101 |
| `kael` 0.4.1 | `1.97.1` (three components) | yes | refused, exit 101 |
| `gpui-unofficial` 1.18.1 | nothing | none — correctly | built, exit 0 |

Under 1.97.1 both floors build clean: no warning, environment check verified.

Cargo's refusal is the enforceable one, and it arrives before anything is
compiled:

```
error: rustc 1.95.0 is not supported by the following packages:
  gpui-box@0.1.1 requires rustc 1.97
  … every box companion crate
Either upgrade rustc or select compatible dependency versions with
`cargo update … --precise`
```

**The gap this study found.** The advisory compared the declared floor against
the active compiler, but parsed the declaration as a full `X.Y.Z` triplet.
`gpui-box`'s real declaration is the two-component `1.97` (which cargo reads as
a `1.97.0` floor), so the parse failed, the warning was skipped, and the
environment check audited that row **green under a rustc that cargo refuses to
build the crate with**. `kael`'s `1.97.1` parsed, so the advisory was
silently *declaration-shape-dependent*. The fix landed with the study:
partial floors are padded with `.0` (so `1.97` is read as `1.97.0`, matching
cargo) and garbage is rejected; both the resolve-time warning and the
environment check use it, and the warning prints the crate's raw declared
string rather than the padded parse.

## What it means for you

- **A kit's fork is caught where it matters.** If a library you depend on pulls
  in a different engine, the workspace audit refuses the workspace, so two
  engines cannot be compiled into one binary — and it shows you the packages
  and both measured generations rather than making you diff a lockfile.
- **The rule is one fork package per lock, not one version.** Two rows
  verifying individually is not enough; if the lock holds two fork packages,
  it is refused.
- **Floor warnings are advice; cargo is the gate.** The tool tells you a
  declared floor exceeds your rustc (and it now warns on every declaration
  shape — two components, three, or none). Cargo is what stops the build, with
  an exit-101 error naming every offending package and the `--precise` way out.
- **Keeping the toolchain current is the cheap fix.** `gpui-box` and `kael`
  want 1.97 or newer; on 1.95 nothing compiles them, and the toolchain check is
  advisory about it by design — a floor is *declared*, not attested.

## What this does not establish

- **The audit names the fork, not the culprit.** The clash row says
  `gpui-pre 0.3.3`; it does not say `gpui-kit 0.6.0` pulled it in through
  `widget-lib`. The lock parse keeps a package's name, version, checksum and
  source, so it cannot walk dependency edges back up to the member. Naming the
  culprit needs a reverse-edge walk, which is scoped elsewhere.
- **The kit was audited, not compiled.** This probe runs the lock and the
  audit; whether the kit's stack actually builds against another engine is
  [doc 11](11-using-third-party-ui-kits-with-alternative-gpui-forks.md) and
  [doc 12](12-verifying-the-alias-shim-with-real-ui-kits.md)'s subject.
- Attested-floor handling (pruning to a verified floor rather than a declared
  one) is out of scope here.
- One kit, one workspace, one app: other kit shapes and other graph layouts
  were not exercised. Linux only.

## Provenance

- **Packages:** `gpui-kit` 0.6.0, `gpui-pre` 0.3.3, `gpui-unofficial` 1.18.1,
  `gpui-box` 0.1.1, `kael` 0.4.1 — all real crates.io artifacts.
- **Generations in the clash:** `d143b846…` (the app's engine) versus
  `546fcb11…` (the kit's) — structurally different.
- **Environment:** rustc 1.95.0 active with 1.97.1 installed, Linux,
  2026-09-07. Probe projects and their captured outputs were temporary.
- **Related reading:** [07 — Switching a Hello World app between real GPUI
  forks](07-switching-a-hello-world-app-between-real-gpui-forks.md) for the binding unit the kit
  needs, and [11 — Using third-party UI kits with alternative GPUI
  forks](11-using-third-party-ui-kits-with-alternative-gpui-forks.md)
  for what to do about it.
