"""Reuse of already-measured compiler-pass docs (`attestations/` on `measured`).

The compiler passes (T-27 TVM, T-28 EAC) are the dominant cost of a measure
run: each one builds a release's whole dependency graph. They emit one doc per
release, and `gocar-index analyze --tvm/--eac` reads exactly those docs — so a
later run can keep them and skip the work they stand for.

Keeping them is honest only when the doc *measured today* is the one already on
disk, which is a claim about the measurement's inputs, not about the file being
there. A doc is reusable precisely when every input it recorded is unchanged:

- the release's **source bytes** — the registry `cksum` (the same identity
  digest stage 1's incrementality keys on),
- the **resolved dependency graph** — the crate's `Cargo.lock` digest. Cargo
  resolves an extracted crate to the newest matching dep versions on every run,
  so a dependency release can move what the passes measure: an auto-trait
  allocation rides on a type's field types (`tvm`), and a dep that stops
  building takes the verified toolchain with it (`eac`). Recording the lock is
  what stops a dep bump from being silently absorbed,
- the **measurement environment** — the digest of the `gocar-index` binary
  itself (any rebuild, patch release or toolchain bump moves it) plus the
  `rustc`/`rustdoc` versions the passes drive,
- the **floor build** (T-28) — whether the EAC was asked to build under a
  declared MSRV, and which compiler that was. A release the run can attempt a
  floor for is measured again; the one case that does *not* invalidate an entry
  is a run that cannot attempt it because the compiler is not installed
  (`Floor.missing`): an attested floor is a fact about the crate and that
  compiler, not about this runner's toolchains, so it is kept — with a warning —
  rather than replaced by a `null`.

The environment is compared once per run and invalidates the whole cache when it
moves, so the branch re-measures as it did before reuse existed. Per release the
cksum and lock must match, and each doc must still digest to what the index
recorded and carry the schema it was recorded under.

What is never reused is a *failure*: a release whose pass produced no doc has no
entry, so it is attempted again on every run — a transient build or network
failure must not become a permanent `null`. The one exception is a release whose
`.crate` cannot be materialized at all: it cannot be re-measured either, so
whatever it already has is carried forward, and the run says so.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

# Schema string of the `attestations/index.json` this module writes.
SCHEMA = "gocar.attestations.v1"
INDEX_NAME = "index.json"
# The compiler passes, in the `<kind>/<package>/<vers>.json` layout
# `gocar-index analyze --tvm/--eac` reads and `gocar-index`'s passes write.
PASSES = ("tvm", "eac")
LOCK_NAME = "Cargo.lock"

# A full semver version — what `gocar_core::parse_rustc_version` accepts and
# prints back, so a nightly's `1.98.0-nightly` counts and a bare `1.98` does not.
_VERSION = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.+-]*)?$")


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_file(path: Path) -> str | None:
    """The file's sha256, or `None` when it cannot be read."""
    try:
        return digest_bytes(path.read_bytes())
    except OSError:
        return None


def _probe(argv: tuple[str, ...]) -> str | None:
    """Run a version probe; `None` when the command is missing or fails."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True)
    except OSError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def _tail(text: str, lines: int = 6) -> str:
    kept = [line for line in text.strip().splitlines() if line.strip()][-lines:]
    return " | ".join(kept)


def rustc_version() -> str | None:
    """The active compiler as the EAC producer records it.

    `gocar_core::toolchain::parse_rustc_version` takes the first `rustc
    --version` token that parses as semver and prints that back, so
    `rustc 1.98.1 (48a229cea 2026-09-01)` records `1.98.1`. Mirrored here so the
    pipeline can check a cached doc without asking the tool, which has no
    `--version` of its own.
    """
    text = _probe(("rustc", "--version"))
    if text is None:
        return None
    for token in text.split()[:2]:
        candidate = token.rstrip(",")
        if _VERSION.match(candidate):
            return candidate
    return None


def rustdoc_version() -> str | None:
    """The `rustdoc --version` line verbatim — what the TVM producer records."""
    text = _probe(("rustdoc", "--version"))
    stripped = (text or "").strip()
    return stripped or None


def environment(gocar_index: str) -> dict | None:
    """The measurement environment a cached doc is only valid within.

    The tool's identity is its *binary digest*, not a version string: the CLI
    reports no version, and a rebuild — toolchain bump, patch release, local
    `cargo build` — is exactly what must invalidate a cached measurement. All
    three components must be known, or nothing is reusable.
    """
    tool = shutil.which(gocar_index)
    digest = digest_file(Path(tool)) if tool else None
    rustc, rustdoc = rustc_version(), rustdoc_version()
    if digest is None or rustc is None or rustdoc is None:
        return None
    return {"tool": digest, "rustc": rustc, "rustdoc": rustdoc}


def environment_note(current: dict, recorded: dict) -> str:
    """One line naming **every** environment component that moved.

    The binary is listed first (the tool's identity), but a rebuild is usually
    caused by a compiler change, so naming only the first mover would hide the
    actual cause: a run that reports a moved tool binary should also say whether
    `rustc`/`rustdoc` moved with it.
    """
    moved = []
    for field in ("tool", "rustc", "rustdoc"):
        if current.get(field) != recorded.get(field):
            before, after = recorded.get(field), current.get(field)
            if field == "tool":
                moved.append(
                    "the gocar-index binary changed " f"({_short(before)} → {_short(after)})"
                )
            else:
                moved.append(f"{field} changed ({before} → {after})")
    return "; ".join(moved) if moved else "the recorded environment differs"


def _short(value: str | None) -> str:
    """A digest shortened for a log line."""
    return (value if value is not None else "unknown")[:12]


class Floor(NamedTuple):
    """One release's floor build (T-28): what to attempt, and what it would need.

    `attempt` is the rustup toolchain to hand `gocar-index eac
    --floor-toolchain`, or `None` for no attempt at all. `missing` names the
    declared MSRV that warranted an attempt but has no installed compiler — the
    two must not be confused: a compiler that is absent was never measured to
    fail, so it can never be recorded as `incompatible`.
    """

    attempt: str | None
    missing: str | None


def normalized_version(text: str) -> str | None:
    """A `rust-version` padded to three components (`1.85` → `1.85.0`), or `None`.

    Mirrors `gocar_core::toolchain::parse_declared_rust_version`: cargo accepts a
    two-component `rust-version` and reads it as `1.85.0` (caret semantics), and
    the corpus declares floors both ways. Rustup names toolchains both ways too,
    which is why the policy matches on the padded form and then passes the name
    rustup actually lists.
    """
    parts = text.strip().split(".")
    if not parts or len(parts) > 3 or not all(part.isdigit() for part in parts):
        return None
    return ".".join(parts + ["0"] * (3 - len(parts)))


def _components(version: str) -> tuple[int, int, int]:
    """The three numeric components of an already-normalized `X.Y.Z` version."""
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def _version_key(text: str) -> tuple[int, int, int] | None:
    """The leading components of a version string, prerelease and build dropped."""
    normalized = normalized_version(text.strip().split("-")[0].split("+")[0])
    return _components(normalized) if normalized else None


def installed_toolchains() -> dict[str, str]:
    """Installed rustup toolchains that name a version: padded version → its name.

    `cargo +<name>` resolves a toolchain by the name it was *installed* under, so
    `+1.85.0` does not mean an installed `1.85` — rustup would go and fetch a
    different channel mid-measurement. A floor build therefore asks for the name
    rustup lists (`+1.85`), while the candidate is matched on the padded version
    (so a `1.85` declaration still finds an installed `1.85.0`). A named channel
    (`stable`, `nightly`) carries no version to compare and is never a floor
    candidate; no rustup on `PATH` means no installed set at all.
    """
    try:
        proc = subprocess.run(
            ["rustup", "toolchain", "list"], capture_output=True, text=True
        )
    except OSError:
        return {}
    if proc.returncode != 0:
        return {}
    toolchains: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        tokens = line.split()
        if not tokens:
            continue
        version, separator, target = tokens[0].partition("-")
        padded = normalized_version(version)
        if padded and separator and target:
            toolchains[padded] = version
    return toolchains


def floor_for(declared: str | None, installed: dict[str, str], active: str | None) -> Floor:
    """The floor build one release warrants, given the release's declared MSRV.

    The candidate is the release's *declared* `rust-version` — the crate's own
    claim, and the thing T-22 showed can lie or drift. It is attempted only when
    it is a version **below the active compiler** (nothing below the compiler
    that already verified the crate is being proved; a floor at or above it
    would only re-run that build) and a matching compiler is **installed**.
    """
    padded = normalized_version(declared or "")
    if padded is None:
        return Floor(None, None)
    compiler = _version_key(active or "")
    if compiler is None or _components(padded) >= compiler:
        return Floor(None, None)
    name = installed.get(padded)
    return Floor(name, None if name is not None else padded)


def wanted_floor_toolchains(declared: list[str]) -> list[str]:
    """The toolchains a runner must install for the floor policy to have candidates.

    Emitted padded (`1.97.0`, not the corpus's `1.97`) so the toolchain that gets
    installed is named in full: that name is what the EAC records as the floor,
    and a three-component version is what every consumer of `toolchain_floor`
    parses. The workflow installs exactly this list, so the names the pipeline
    later asks for are the installed ones.
    """
    active = rustc_version()
    wanted: list[str] = []
    for value in declared:
        padded = normalized_version(value)
        if padded is None or padded in wanted:
            continue
        compiler = _version_key(active or "")
        if compiler is None or _components(padded) >= compiler:
            continue
        wanted.append(padded)
    return wanted


def resolve_lock(crate_dir: Path) -> str | None:
    """The digest of the crate's freshly resolved `Cargo.lock`, `None` on failure.

    `cargo generate-lockfile` resolves without compiling — the cheap proof that
    the dependency graph a pass would build *today* is the one a cached doc was
    measured against. The pass that follows resolves to the same lock, so this
    costs one resolution, not a second build. An unprovable resolution means the
    release is measured again: the safe direction is always to measure.
    """
    print(f"+ cargo generate-lockfile  (in {crate_dir})", flush=True)
    try:
        proc = subprocess.run(
            ["cargo", "generate-lockfile"], cwd=crate_dir, capture_output=True, text=True
        )
    except OSError as exc:
        print(f"  warning: cannot resolve {crate_dir}: {exc}", file=sys.stderr)
        return None
    if proc.returncode != 0:
        print(
            f"  warning: cannot resolve {crate_dir}: {_tail(proc.stderr)}", file=sys.stderr
        )
        return None
    return digest_file(crate_dir / LOCK_NAME)


def _key(package: str, vers: str) -> str:
    return f"{package}/{vers}"


def _doc_schema(path: Path) -> str | None:
    """The doc's own schema string — a shape check that costs one read."""
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("schema")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


class Cache:
    """The `attestations/` tree of a `measured` checkout: the docs + their index.

    The tree is both the store and `analyze`'s input — the layout the analyzer
    reads (`<tvm|eac>/<package>/<vers>.json`) is the layout the docs already
    have, so reuse copies nothing. A run rewrites the tree to exactly what it
    publishes: the entries it reused or measured, and no doc the index does not
    list.
    """

    def __init__(self, root: Path, environment: dict | None, reuse: bool = True):
        self.root = root
        self.environment = environment
        self.reuse_ok = reuse
        self.entries: dict[str, dict] = {}
        self.note = "no attestations yet"
        self.previous = self._load()

    @property
    def enabled(self) -> bool:
        """Whether attestations can be recorded at all — needs a known environment."""
        return self.environment is not None

    def describe(self) -> str:
        """The cache's state as one line for the run log."""
        if not self.enabled:
            return f"attestations: disabled — {self.note}"
        if not self.reuse_ok:
            return f"attestations: not reused (--no-reuse) — {self.note}"
        return f"attestations: reuse enabled — {self.note}"

    def dir_for(self, kind: str) -> Path:
        return self.root / kind

    def path_for(self, kind: str, package: str, vers: str) -> Path:
        return self.root / kind / package / f"{vers}.json"

    def _load(self) -> dict[str, dict]:
        """The previous index's entries — empty unless it is this environment's."""
        if self.environment is None:
            self.note = "cannot identify the measurement environment (gocar-index, rustc, rustdoc)"
            return {}
        try:
            index = json.loads((self.root / INDEX_NAME).read_text(encoding="utf-8"))
        except OSError:
            self.note = "none on the measured branch yet"
            return {}
        except json.JSONDecodeError as exc:
            self.note = f"unreadable index ({exc})"
            return {}
        if index.get("schema") != SCHEMA:
            self.note = f"index is {index.get('schema')!r}, expected {SCHEMA!r}"
            return {}
        recorded = index.get("environment") or {}
        if recorded != self.environment:
            self.note = "measurement environment moved: " + environment_note(
                self.environment, recorded
            )
            return {}
        entries = {
            key: entry
            for key, entry in (index.get("entries") or {}).items()
            if isinstance(entry, dict)
        }
        self.note = f"{len(entries)} attested release(s)"
        return entries

    def _adopt(self, package: str, vers: str, entry: dict) -> bool:
        """Take an entry into this run's index when both its docs are still intact."""
        if not all(self._doc_ok(kind, package, vers, entry) for kind in PASSES):
            return False
        self.entries[_key(package, vers)] = entry
        return True

    def _doc_ok(self, kind: str, package: str, vers: str, entry: dict) -> bool:
        """A doc is usable when it is the file the index recorded, unreadable never."""
        recorded = entry.get(kind)
        if not isinstance(recorded, dict):
            return False
        path = self.path_for(kind, package, vers)
        if digest_file(path) != recorded.get("sha256"):
            return False
        return _doc_schema(path) == recorded.get("schema")

    def reuse(
        self,
        package: str,
        vers: str,
        cksum: str | None,
        crate_dir: Path,
        floor: Floor,
    ) -> bool:
        """Keep this release's cached docs when every input they recorded is unchanged.

        True means the docs stay where they are (they are already the analyzer's
        input) and are carried into the new index; False means the caller must
        measure the release again. The dependency proof is only paid once an
        entry exists under this identity — an empty cache resolves nothing.

        The floor build is an input like any other: an attempt this run that the
        entry does not record (or records under another compiler) re-measures the
        release. The single exception is `Floor.missing` — see the module
        docstring: the entry keeps its attested floor and says so.
        """
        if not (self.enabled and self.reuse_ok) or cksum is None:
            return False
        entry = self.previous.get(_key(package, vers))
        if entry is None or entry.get("cksum") != cksum:
            return False
        attested = entry.get("floor")
        if floor.attempt != attested and not (attested and floor.missing):
            return False
        if floor.attempt != attested:
            print(
                f"  warning: {package} {vers} keeps its attested floor {attested}: "
                f"no {floor.missing} compiler is installed",
                file=sys.stderr,
            )
        lock = resolve_lock(crate_dir)
        if lock is None or entry.get("lock") != lock:
            return False
        return self._adopt(package, vers, entry)

    def carry_forward(self, package: str, vers: str, cksum: str | None) -> bool:
        """Keep an entry whose crate could not be materialized at all.

        Nothing about the release was measured or verified this run — the crate
        that would be measured could not even be fetched. Re-measuring is
        impossible and dropping the docs would let a transient fetch failure
        become a permanent `null`, so the entry is kept as it stands (its cksum,
        the only input that can be checked without the crate, must still match).
        """
        if not self.enabled or cksum is None:
            return False
        entry = self.previous.get(_key(package, vers))
        if entry is None or entry.get("cksum") != cksum:
            return False
        return self._adopt(package, vers, entry)

    def record(
        self, package: str, vers: str, cksum: str | None, crate_dir: Path, floor: str | None
    ) -> None:
        """Index the docs the passes just wrote — never one they did not write."""
        if not self.enabled:
            return
        # The passes resolve the crate before building, so the lock is normally
        # already there; resolving it here (rather than recording a `null`)
        # keeps the entry reusable instead of permanently unprovable.
        lock = digest_file(crate_dir / LOCK_NAME) or resolve_lock(crate_dir)
        entry: dict[str, object] = {"cksum": cksum, "lock": lock, "floor": floor}
        for kind in PASSES:
            path = self.path_for(kind, package, vers)
            digest = digest_file(path)
            if digest is None:
                continue
            entry[kind] = {"sha256": digest, "schema": _doc_schema(path)}
        if any(kind in entry for kind in PASSES):
            self.entries[_key(package, vers)] = entry

    def write(self) -> None:
        """Publish the index, then drop every doc it does not list."""
        if not self.enabled:
            return
        index = {
            "schema": SCHEMA,
            "environment": self.environment,
            "entries": dict(sorted(self.entries.items())),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / INDEX_NAME).write_text(
            json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        keep: set[Path] = set()
        for key, entry in self.entries.items():
            package, _, vers = key.partition("/")
            keep.update(
                self.path_for(kind, package, vers) for kind in PASSES if kind in entry
            )
        for kind in PASSES:
            base = self.dir_for(kind)
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*.json")):
                if path not in keep:
                    path.unlink()
            for holder in sorted(base.iterdir()):
                if holder.is_dir() and not any(holder.iterdir()):
                    holder.rmdir()
