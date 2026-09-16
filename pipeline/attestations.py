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
  `rustc`/`rustdoc` versions the passes drive.

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
    """One line naming the first environment component that moved."""
    for field in ("tool", "rustc", "rustdoc"):
        if current.get(field) != recorded.get(field):
            before, after = recorded.get(field), current.get(field)
            if field == "tool":
                return "the gocar-index binary changed " f"({_short(before)} → {_short(after)})"
            return f"{field} changed ({before} → {after})"
    return "the recorded environment differs"


def _short(value: str | None) -> str:
    """A digest shortened for a log line."""
    return (value if value is not None else "unknown")[:12]


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

    def reuse(self, package: str, vers: str, cksum: str | None, crate_dir: Path) -> bool:
        """Keep this release's cached docs when every input they recorded is unchanged.

        True means the docs stay where they are (they are already the analyzer's
        input) and are carried into the new index; False means the caller must
        measure the release again. The dependency proof is only paid once an
        entry exists under this identity — an empty cache resolves nothing.
        """
        if not (self.enabled and self.reuse_ok) or cksum is None:
            return False
        entry = self.previous.get(_key(package, vers))
        if entry is None or entry.get("cksum") != cksum:
            return False
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

    def record(self, package: str, vers: str, cksum: str | None, crate_dir: Path) -> None:
        """Index the docs the passes just wrote — never one they did not write."""
        if not self.enabled:
            return
        # The passes resolve the crate before building, so the lock is normally
        # already there; resolving it here (rather than recording a `null`)
        # keeps the entry reusable instead of permanently unprovable.
        lock = digest_file(crate_dir / LOCK_NAME) or resolve_lock(crate_dir)
        entry: dict[str, object] = {"cksum": cksum, "lock": lock}
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
