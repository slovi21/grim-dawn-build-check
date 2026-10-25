"""Devotion constellations and affinity rules for build designs (.69): a thin wrapper over the ported
devotion rules (analysis/generators/devotions.py - Crate Game Guide, Devotion).

Affinity only from complete constellations, a complete constellation sustains itself, a constellation is
placed only when the others cover its need, points are refunded only when every remaining constellation
stays covered; Crossroads = five one-star constellations. The wrapper gives the design catalog the shape it
uses: English names ('Crossroads (Chaos)'), points, needs/gives and the weapon-free record per
constellation, and the build-order search (greedy, then bounded iterative depth-first) for a target set."""
from __future__ import annotations

from ..generators import devotions as G

AFFINITIES = G.AFFINITIES
CROSSROADS = G.CROSSROADS
covered = G.covered


def constellations(index) -> dict:
    """{key: {"name", "stars": [records], "power": record | None, "points": n, "record": ui record,
    "file_description", "needs", "gives"}}. Crossroads stars carry the affinity they give in the name:
    'Crossroads (Chaos)'."""
    sb = G.constellations(index)
    aff = G.affinities(index, sb) or {}
    raw = getattr(index, "sternbild_roh", None) or {}
    out = {}
    for key, c in sb.items():
        a = aff.get(key) or {}
        name = c["name"]
        if key.startswith(CROSSROADS):
            gives = sorted(a.get("gives") or {})
            name = f"Crossroads ({gives[0]})" if gives else name
        rec = a.get("record")
        out[key] = {"name": name, "stars": list(c["stars"]), "power": c["power"],
                    "points": len(c["stars"]),
                    "record": rec, "file_description": str((raw.get(rec) or {}).get("FileDescription") or ""),
                    "needs": dict(a.get("needs") or {}), "gives": dict(a.get("gives") or {})}
    return out


def affinity_table(cons: dict) -> dict:
    """{key: {"needs", "gives"}} for EVERY constellation (also one without a record): the build-order
    search reads the table for every constellation it may use as a stepping stone."""
    return {k: {"needs": c["needs"], "gives": c["gives"]} for k, c in cons.items()}


def affinity_sum(keys, cons: dict) -> dict:
    return G.affinity_sum(set(keys), affinity_table(cons))


def state_valid(keys, cons: dict) -> bool:
    """Every constellation in `keys` is covered by the affinity of all of them (itself included)."""
    keys = set(keys)
    return G.state_valid(keys, keys, affinity_table(cons))


def find_order(target, cons: dict, budget: int):
    """Build order for a target set from zero points (greedy, then bounded depth-first search with
    stepping stones). [(action '+'/'-', key, affinity after, points after)] or None (none found within
    the search limit - not proof that none exists)."""
    aff = affinity_table(cons)
    uni = {k: {"points": c["points"]} for k, c in cons.items()}
    return G.build_order(set(target), uni, aff, budget, uni)
