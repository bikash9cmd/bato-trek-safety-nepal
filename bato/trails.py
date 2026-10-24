"""The trail network: overnight stops, the sections between them, and walking times."""

from __future__ import annotations

import heapq
import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from . import config

DATA = Path(__file__).resolve().parents[1] / "data"


@dataclass(frozen=True)
class Stop:
    id: str
    name: str
    elev_m: int
    connectivity: str
    checkpost: bool
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class Path_:
    stops: tuple[str, ...]
    hours: float
    passes: tuple[str, ...]


class Trails:
    def __init__(self, doc: dict):
        self.stops = {s["id"]: Stop(**{**s, "aliases": tuple(s["aliases"])}) for s in doc["stops"]}
        self.itineraries = doc["itineraries"]
        self.edges: dict[str, list[tuple[str, float, str | None]]] = {k: [] for k in self.stops}
        for sec in doc["sections"]:
            a, b, hours = sec[0], sec[1], float(sec[2])
            pass_id = sec[3] if len(sec) > 3 else None
            self.edges[a].append((b, hours, pass_id))
            self.edges[b].append((a, round(hours * config.REVERSE_FACTOR, 2), pass_id))
        self._names = self._build_name_index()

    def path(self, a: str, b: str) -> Path_:
        """Quickest way between two stops (Dijkstra; the graph has a few dozen nodes)."""
        if a == b:
            return Path_((a,), 0.0, ())
        best = {a: 0.0}
        prev: dict[str, tuple[str, str | None]] = {}
        heap = [(0.0, a)]
        while heap:
            d, u = heapq.heappop(heap)
            if u == b:
                break
            if d > best[u]:
                continue
            for v, h, p in self.edges[u]:
                if d + h < best.get(v, float("inf")):
                    best[v] = d + h
                    prev[v] = (u, p)
                    heapq.heappush(heap, (d + h, v))
        if b not in best:
            raise ValueError(f"no trail between {a} and {b}")
        stops, passes, cur = [b], [], b
        while cur != a:
            u, p = prev[cur]
            if p:
                passes.append(p)
            stops.append(u)
            cur = u
        return Path_(tuple(reversed(stops)), round(best[b], 2), tuple(reversed(passes)))

    # --- names as people type them in an SMS ---------------------------------------------
    @staticmethod
    def _norm(s: str) -> str:
        return re.sub(r"[^a-z]", "", s.lower())

    def _build_name_index(self) -> dict[str, list[str]]:
        idx: dict[str, list[str]] = {}
        for s in self.stops.values():
            for n in (s.id, s.name, *s.aliases):
                ids = idx.setdefault(self._norm(n), [])
                if s.id not in ids:
                    ids.append(s.id)
        return idx

    def resolve(self, text: str, prefer: frozenset[str] | set[str] = frozenset()) -> str | None:
        """'Thorung Phedi', 'thorong-phedi', 'PHEDI' -> 'thorong-phedi'. None if unknown.

        Several places share a name ("Phedi" just means the foot of a climb), so a stop on the
        group's own itinerary wins over one elsewhere in the country.
        """
        key = self._norm(text)
        candidates = list(self._names.get(key, []))
        if not candidates and len(key) >= 6:
            # One typo or missing letter in a long name ("dingbche", "gorakshepp").
            for name, ids in self._names.items():
                if len(name) >= 6 and _within_one_edit(key, name):
                    candidates += [i for i in ids if i not in candidates]
        if not candidates:
            return None
        preferred = [c for c in candidates if c in prefer]
        if preferred:
            return preferred[0]
        return candidates[0] if len(candidates) == 1 else None


def _within_one_edit(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) <= 1
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1 :]


@cache
def load() -> Trails:
    return Trails(json.loads((DATA / "trails.json").read_text()))


@cache
def passes() -> dict[str, dict]:
    return {p["id"]: p for p in json.loads((DATA / "passes.json").read_text())["passes"]}
