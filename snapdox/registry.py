"""The conversion graph.

Every converter registers itself as an edge ``(src_ext -> dst_ext)``.  What
SnapDox can do is therefore *data*: the CLI's ``--list`` output and the web
UI's target dropdown are both generated from this table, so they can never
drift from what the engines actually implement.

Pairs with no direct edge are resolved by a breadth-first search capped at two
hops, which gets things like ``pptx -> png`` (via PDF) for free without letting
quality decay through a long chain of lossy steps.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .errors import UnknownFormat, UnsupportedPair
from .formats import FORMATS, Kind, normalize
from .options import Options

#: A converter writes ``dst`` (and possibly siblings of it) and returns every
#: file it produced, in order.
Converter = Callable[[Path, Path, Options], list[Path]]


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    fn: Converter
    engine: str
    #: Lower is preferred when several edges cover the same pair.
    weight: int
    note: str
    #: True when one input can yield many outputs (e.g. pdf -> png per page).
    fan_out: bool
    #: True when the edge recovers text from a document and is therefore
    #: pointless behind an image source, which never has any.
    needs_text: bool

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Edge {self.src}->{self.dst} via {self.engine}>"


@dataclass(frozen=True)
class Route:
    """One or more edges chained from source to target."""

    edges: tuple[Edge, ...]

    @property
    def src(self) -> str:
        return self.edges[0].src

    @property
    def dst(self) -> str:
        return self.edges[-1].dst

    @property
    def is_direct(self) -> bool:
        return len(self.edges) == 1

    @property
    def fan_out(self) -> bool:
        return any(e.fan_out for e in self.edges)

    @property
    def engines(self) -> tuple[str, ...]:
        return tuple(e.engine for e in self.edges)

    def describe(self) -> str:
        hops = " -> ".join([self.edges[0].src, *(e.dst for e in self.edges)])
        return f"{hops}  [{', '.join(dict.fromkeys(self.engines))}]"


# src -> dst -> edge (best one kept)
_EDGES: dict[str, dict[str, Edge]] = {}

#: Formats worth routing *through*.  A two-hop chain must pass through one of
#: these, which stops the search inventing silly paths like xlsx -> csv -> txt.
HUBS: frozenset[str] = frozenset({"pdf", "png", "docx", "html"})


def _expand(spec: str | Kind | Iterable[str | Kind]) -> list[str]:
    """Accept an extension, a Kind, or any mix of them, return extensions."""
    if isinstance(spec, (str, Kind)):
        spec = [spec]
    out: list[str] = []
    for item in spec:
        if isinstance(item, Kind):
            out.extend(f.ext for f in FORMATS.values() if f.kind == item)
        else:
            ext = normalize(item)
            if ext not in FORMATS:
                raise UnknownFormat(f"cannot register converter for unknown format {item!r}")
            out.append(ext)
    return out


def converter(
    src: str | Kind | Iterable[str | Kind],
    dst: str | Kind | Iterable[str | Kind],
    *,
    engine: str,
    weight: int = 10,
    note: str = "",
    fan_out: bool = False,
    needs_text: bool = False,
) -> Callable[[Converter], Converter]:
    """Register a function as the handler for every ``src`` x ``dst`` pair."""

    def decorate(fn: Converter) -> Converter:
        for s in _expand(src):
            for d in _expand(dst):
                if s == d:
                    continue
                edge = Edge(
                    src=s,
                    dst=d,
                    fn=fn,
                    engine=engine,
                    weight=weight,
                    note=note,
                    fan_out=fan_out,
                    needs_text=needs_text,
                )
                existing = _EDGES.setdefault(s, {}).get(d)
                if existing is None or edge.weight < existing.weight:
                    _EDGES[s][d] = edge
        return fn

    return decorate


def _ensure_loaded() -> None:
    """Import the engine modules so their decorators run."""
    from . import engines  # noqa: F401  (import side effect is the point)


def direct_edge(src: str, dst: str) -> Edge | None:
    _ensure_loaded()
    return _EDGES.get(normalize(src), {}).get(normalize(dst))


def _routes_from(src: str) -> dict[str, Route]:
    """Every route out of ``src``, best-first.  Never raises.

    Two-hop candidates are collected first and then overwritten by direct
    edges, so a direct conversion always wins over a chained one regardless of
    weights.  Keeping this separate from ``resolve`` matters: ``resolve``'s
    error message wants to list the available targets, and if that listing went
    back through ``resolve`` the two would recurse into each other forever.
    """
    routes: dict[str, Route] = {}
    costs: dict[str, int] = {}
    pictorial = FORMATS[src].kind in (Kind.RASTER, Kind.VECTOR) if src in FORMATS else False

    for mid, first in _EDGES.get(src, {}).items():
        if mid not in HUBS:
            continue
        for dst, second in _EDGES.get(mid, {}).items():
            if dst == src or dst == mid:
                continue
            # An image routed through PDF has no text to recover, so chains
            # like png -> pdf -> docx would always fail. Don't offer them.
            if pictorial and second.needs_text:
                continue
            cost = first.weight + second.weight
            if dst not in costs or cost < costs[dst]:
                costs[dst] = cost
                routes[dst] = Route((first, second))

    for dst, edge in _EDGES.get(src, {}).items():
        routes[dst] = Route((edge,))

    return routes


def resolve(src: str, dst: str, *, engine: str | None = None) -> Route:
    """Find the best route from ``src`` to ``dst``, or raise ``UnsupportedPair``."""
    _ensure_loaded()
    s, d = normalize(src), normalize(dst)

    for ext, label in ((s, "source"), (d, "target")):
        if ext not in FORMATS:
            raise UnknownFormat(
                f"unknown {label} format {ext!r}",
                hint="Run `snapdox --list` to see everything SnapDox handles.",
            )
    if s == d:
        raise UnsupportedPair(f"source and target are both {s}; nothing to convert")

    if engine:
        edge = _EDGES.get(s, {}).get(d)
        if edge is None or edge.engine != engine:
            candidates = sorted({e.engine for e in _EDGES.get(s, {}).values()})
            raise UnsupportedPair(
                f"no {engine!r} converter for {s} -> {d}",
                hint=f"Available engines for {s}: {', '.join(candidates) or 'none'}",
            )
        return Route((edge,))

    routes = _routes_from(s)
    if d in routes:
        return routes[d]

    raise UnsupportedPair(
        f"SnapDox can't convert {s} to {d}",
        hint=f"Targets available for {s}: {', '.join(sorted(routes)) or 'none'}",
    )


def targets_for(src: str) -> dict[str, Route]:
    """Every format ``src`` can reach, mapped to the route that gets there."""
    _ensure_loaded()
    s = normalize(src)
    if s not in FORMATS:
        return {}
    return {dst: route for dst, route in _routes_from(s).items() if dst in FORMATS}


def sources() -> list[str]:
    """Every format SnapDox can read."""
    _ensure_loaded()
    return sorted(_EDGES)


def all_edges() -> Sequence[Edge]:
    _ensure_loaded()
    return [edge for row in _EDGES.values() for edge in row.values()]


def capability_matrix() -> dict[str, dict[str, Route]]:
    """``{source: {target: route}}`` for the whole graph — used by --list and the UI."""
    return {src: targets_for(src) for src in sources()}
