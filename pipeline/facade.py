"""Compose a facade crate's measured surface from its measured layer crates.

`gocar-index` measures one crate at a time: its syn walker follows
**crate-local** re-export chains (T-25) but deliberately stops at an
external-crate re-export (``pub use dep::*``), because the target crate's
source is not in the measured corpus entry. For most forks that is the whole
story — a fork is one crate. A *facade* is different: its crate is a thin
``pub use`` layer over several independently published crates, so the
facade's own measured surface is just its re-export lines plus the handful of
items it declares locally.

This module closes that gap **without changing the measurement model**: the
layer crates are measured as ordinary corpus entities (``role:
"dependency"``), and their already-measured public surfaces are composed into
the facade's surface from the facade's own canonical ``use:`` entries. The
composition is a deterministic projection of measured data — no source is
re-read, no item is inferred.

Two honest boundaries are preserved:

- Only re-exports the facade (or a crate it re-exports) actually declares are
  followed. A crate that the facade merely depends on contributes nothing.
- A leaf whose target is not a measured corpus entity stays opaque (the
  existing ``use:`` entry remains the only record). ``unresolved`` names
  those boundaries for the caller's report; they are never guessed.

The surface items' ``src`` coordinates are dropped on composition: they point
at a declaration inside the *layer crate*, so the facade's docs.rs source URL
would be wrong. ``key``/``digest``/``text``/``doc``/``members`` are carried
verbatim — the composed item is the same item.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

# One canonical `use:` surface item looks like
#   `pub use gpui_authoring :: * ;`
#   `# [cfg (feature = "platform")] pub use platform_entry :: * ;`
#   `pub use gpui_util :: arc_cow :: ArcCow ;`
#   `pub use http :: { self , Method , request :: Builder } ;`
# and may be qualified by the module it lives in (`prelude::pub use …`).
# The text is the analyzer's canonical token rendering, not source bytes.

USE_PREFIX = "use:"


@dataclass(frozen=True)
class Reexport:
    """One leaf of a `pub use` tree.

    ``path`` is the module path *before* the leaf (``gpui_util::arc_cow``),
    ``name`` the leaf (``ArcCow``) or ``"self"`` for a module self-import,
    ``glob`` marks ``*``, and ``alias`` the ``as`` rename when present.
    """

    path: tuple[str, ...]
    glob: bool
    name: str | None
    alias: str | None


@dataclass
class Projection:
    """The composed surface and the facts used to label it."""

    surface: list[dict]
    sources: list[dict] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    added: int = 0


def _split_leading_attrs(text: str) -> tuple[list[str], int]:
    """Return (cfg gates, offset after leading attributes).

    Attributes are canonical `# [ … ]` groups; only `cfg` gates are kept as
    provenance. Bracket matching is string-aware (a doc attribute can contain
    brackets and the word `pub use`).
    """
    gates: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i + 1 >= n or text[i] != "#":
            break
        j = i + 1
        while j < n and text[j].isspace():
            j += 1
        if j >= n or text[j] != "[":
            break
        depth = 0
        k = j
        in_string = False
        escaped = False
        while k < n:
            ch = text[k]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        break
            k += 1
        attr = text[j:k + 1]
        inner = attr[1:-1].strip()
        if inner.startswith("cfg"):
            gates.append(inner)
        i = k + 1
    return gates, i


def parse_use(text: str) -> tuple[tuple[str, ...], list[str], list[Reexport]]:
    """Parse one canonical `use:` text into (module prefix, cfg gates, leaves).

    The module prefix is the path of the body the re-export line lives in
    (``prelude`` for ``prelude::pub use …``), empty at a crate root. A text
    that is not a `pub use` yields no leaves.
    """
    gates, offset = _split_leading_attrs(text)
    body = text[offset:].strip()
    prefix: tuple[str, ...] = ()
    if not body.startswith("pub use "):
        marker = body.find("pub use ")
        if marker <= 0:
            return (), gates, []
        head = body[:marker].strip()
        if not head.endswith("::"):
            return (), gates, []
        prefix = tuple(part for part in head[:-2].split("::") if part)
        body = body[marker:].strip()
    body = body[len("pub use "):].strip()
    if body.endswith(";"):
        body = body[:-1].strip()
    tokens: list[str] = []
    for raw in body.replace("::", " :: ").split():
        if raw == ",":
            tokens.append(",")
            continue
        core = raw.rstrip(",")
        if core:
            tokens.append(core)
        if core != raw:
            tokens.append(",")

    leaves: list[Reexport] = []

    def parse_group(index: int, base: tuple[str, ...]) -> int:
        while index < len(tokens):
            token = tokens[index]
            if token == "}":
                return index + 1
            if token == ",":
                index += 1
                continue
            index = parse_item(index, base)
        return index

    def parse_item(index: int, base: tuple[str, ...]) -> int:
        segs: list[str] = []
        while index < len(tokens) and tokens[index] not in ("{", "}", "*", ",", "as"):
            if tokens[index] == "::":
                index += 1
                continue
            segs.append(tokens[index])
            index += 1
        if index < len(tokens) and tokens[index] == "*":
            leaves.append(Reexport(path=base + tuple(segs), glob=True, name=None, alias=None))
            return index + 1
        if index < len(tokens) and tokens[index] == "{":
            return parse_group(index + 1, base + tuple(segs))
        if not segs:
            return index + 1
        alias = None
        if index < len(tokens) and tokens[index] == "as":
            if index + 1 < len(tokens):
                alias = tokens[index + 1]
            index += 2
        leaves.append(
            Reexport(path=base + tuple(segs[:-1]), glob=False, name=segs[-1], alias=alias)
        )
        return index

    parse_group(0, ())
    return prefix, gates, leaves


def _name_part(key: str) -> str:
    return key.split(":", 1)[1] if ":" in key else key


def _combine(a: Iterable[str], b: Iterable[str]) -> list[str]:
    return sorted(set(a) | set(b))


def _select(
    items: list[dict], path: tuple[str, ...], *, glob: bool, name: str | None, alias: str | None
) -> list[dict]:
    """The source items a leaf exposes, with its alias applied when named."""
    target = "::".join(path)
    out: list[dict] = []
    for item in items:
        key = item.get("key", "")
        if key.startswith(USE_PREFIX):
            continue
        name_part = _name_part(key)
        if glob or name in (None, "self"):
            matched = target == "" or name_part == target or name_part.startswith(target + "::")
            rename_to = target
        else:
            rename_to = "::".join((*path, name))
            matched = (
                name_part == rename_to or name_part.startswith(rename_to + "::")
            )
        if not matched:
            continue
        item = dict(item)
        if alias and alias != "_":
            kind = key.split(":", 1)[0]
            rest = name_part[len(rename_to):] if rename_to else name_part
            item["key"] = f"{kind}:{alias}{rest}"
        out.append(item)
    return out


def _is_module(items: list[dict], path: tuple[str, ...], name: str | None) -> bool:
    """Whether a named leaf selects a module (items live under it)."""
    if name in (None, "self"):
        return True
    target = "::".join((*path, name))
    for item in items:
        key = item.get("key", "")
        if key.startswith(USE_PREFIX):
            continue
        name_part = _name_part(key)
        if name_part.startswith(target + "::"):
            return True
    return False


def project_surface(
    surface: list[dict],
    *,
    package: str,
    vers: str,
    surfaces: Mapping[tuple[str, str], list[dict]],
    graph: Mapping[str, str],
    dependencies: Mapping[tuple[str, str], set[str]] | None = None,
    direct: Iterable[str] = (),
) -> Projection:
    """Compose one facade release's surface from measured layer surfaces.

    ``graph`` maps an extern crate name (the lib name the `use` text names,
    e.g. ``gpui_authoring``) to its corpus package. ``direct`` names the
    facade's own declared sources (``surface_source.from``) — used only for
    the per-source glob/filtered report.
    """
    direct = tuple(direct)
    own = [item for item in surface if not item.get("key", "").startswith(USE_PREFIX)]
    uses = [item for item in surface if item.get("key", "").startswith(USE_PREFIX)]
    out: list[dict] = [dict(item) for item in own] + [dict(item) for item in uses]
    unresolved: set[str] = set()
    sources: dict[str, dict] = {}
    touched: dict[str, str] = {}
    visited: set[tuple[str, str, tuple[str, ...]]] = set()

    def add(items: list[dict], gates: list[str]) -> None:
        for item in items:
            projected = dict(item)
            projected["cfg"] = _combine(gates, item.get("cfg", []))
            projected.pop("src", None)
            out.append(projected)

    def follow(
        prefix: tuple[str, ...],
        gates: list[str],
        leaves: list[Reexport],
        *,
        src_package: str,
        src_vers: str,
        at_root: bool,
    ) -> None:
        for leaf in leaves:
            if not leaf.path:
                continue  # a local path (`crate::…`, `self::…`): not a layer crate
            if leaf.alias == "_":
                continue  # an anonymous import: it creates no public name
            if prefix:
                # A re-export inside a module: the composed item's public path
                # would need that module prefix, which the measured item key
                # space does not carry for an external target. Keep it opaque
                # rather than invent a path.
                if leaf.path[0] in graph:
                    unresolved.add(f"{leaf.path[0]} (module-scoped re-export)")
                continue
            lib = leaf.path[0]
            target_package = graph.get(lib)
            if target_package is None:
                if dependencies is not None and lib in dependencies.get((src_package, src_vers), set()):
                    unresolved.add(f"{lib} (external crate)")
                continue  # a local module name, not a re-exported crate
            target_items = surfaces.get((target_package, src_vers))
            if target_items is None:
                unresolved.add(f"{target_package}@{src_vers} (not measured)")
                continue
            touched[target_package] = src_vers
            if at_root and target_package in direct:
                mode = "glob" if leaf.glob else "filtered"
                current = sources.get(lib)
                if current is None or current["mode"] != "glob":
                    sources[lib] = {"package": target_package, "vers": src_vers, "mode": mode}
            module_path = leaf.path[1:]
            selected = _select(
                target_items, module_path, glob=leaf.glob, name=leaf.name, alias=leaf.alias
            )
            if selected:
                add(selected, gates)
            if not (leaf.glob or _is_module(target_items, module_path, leaf.name)):
                continue
            # A module selection also exposes the module's own re-export lines.
            # Follow only lines that live inside the selected module; their
            # crate-local targets were already resolved into `target_items`.
            module = module_path if leaf.name in (None, "self") else (*module_path, leaf.name)
            key = (target_package, src_vers, module)
            if key in visited:
                continue
            visited.add(key)
            for use_item in target_items:
                use_key = use_item.get("key", "")
                if not use_key.startswith(USE_PREFIX):
                    continue
                inner_prefix, inner_gates, inner_leaves = parse_use(use_key[len(USE_PREFIX):])
                if module and inner_prefix[: len(module)] != module:
                    # A line outside the selected module (or in a sibling) is
                    # not part of this selection.
                    continue
                follow(
                    inner_prefix,
                    _combine(gates, inner_gates),
                    inner_leaves,
                    src_package=target_package,
                    src_vers=src_vers,
                    at_root=False,
                )

    for use_item in uses:
        prefix, gates, leaves = parse_use(use_item.get("key", "")[len(USE_PREFIX):])
        follow(prefix, gates, leaves, src_package=package, src_vers=vers, at_root=True)

    deduped: list[dict] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for item in out:
        marker = (
            item.get("key", ""),
            item.get("digest", ""),
            tuple(item.get("cfg", [])),
        )
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(item)
    deduped.sort(key=lambda item: (item.get("key", ""), item.get("digest", ""), item.get("cfg", [])))

    by_package = {entry["package"]: entry for entry in sources.values()}
    report = []
    for source_package in direct:
        if source_package in by_package:
            report.append(dict(by_package[source_package]))
        elif source_package in touched:
            # Declared as a source, but reached through another layer's
            # re-export rather than the facade's own lines.
            report.append({
                "package": source_package,
                "vers": touched[source_package],
                "mode": "transitive",
            })
        elif (source_package, vers) in surfaces:
            # Measured, but no re-export line in the facade names it: a stale
            # or mistaken declaration, reported rather than silently skipped.
            report.append({"package": source_package, "vers": vers, "mode": "unreferenced"})
        else:
            report.append({"package": source_package, "vers": vers, "mode": "missing"})
    # Net added items: the final surface minus the crate's own measured
    # surface, so a composition whose items all collapse into existing rows
    # reports zero (and is left uncomposed).
    added = len(deduped) - len(surface)
    return Projection(
        surface=deduped,
        sources=report,
        unresolved=sorted(unresolved),
        added=added,
    )
