"""Consumables (N-010): ointments and elixirs from the game database that close a
gap for a while. Since 1.2.1 only ONE ointment works at a time."""
from __future__ import annotations

from collections import Counter

from ...domain.enums import Activity, SourceKind, StackingPolicy
from ...domain.sources import EffectInstance, SourceInstance, SourceSet
from . import counterfactual as CF
from .model import Proposal

USEFUL = ("defensive", "characterLifeModifier", "characterLife", "characterDefensiveAbility")


def catalogue(index) -> list[dict]:
    out = []
    for path, e in sorted((getattr(index, "records", None) or {}).items()):
        if "/consumableskills/" not in path:
            continue
        f = e.get("f") or {}
        dur = (e.get("m") or {}).get("skillActiveDuration") or 0
        if isinstance(dur, list):
            dur = dur[0] if dur else 0
        useful = {k: v for k, v in f.items() if isinstance(v, (int, float)) and v > 0
                  and k.startswith(USEFUL) and not k.endswith("Duration")}
        if useful and dur >= 60 and e.get("n"):
            out.append({"record": path, "name": e["n"], "fields": useful, "duration": dur,
                        "ointment": "Ointment" in e["n"]})
    return out


# Item record -> consumable skill (both file stems). Verified in the test saves 02.10.2026 (Cpt Wolf:
# 41 Hoarfrost, 67 Vilescar; Ugdensalve, Elixir of the Dranghoul). The potion item records are not in
# the index (class filter), so the link item -> skill of the game database is not available here;
# other items match by stem only when exactly one consumable fits (never a guess between two).
KNOWN_ITEMS = {
    "potion_freezeresist01": "item_freezeresist_01",
    "potion_bleedresist01": "item_bleedresist_01",
    "potion_ugdensalve": "item_ugdensalve_01",
    "elixir_dranghoul01": "item_elixir_dranghoul_01",
}
STORAGE_HINT = "bags + character stash"


def _stem(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".dbr")


def stem_key(stem: str) -> str:
    """'potion_freezeresist01' / 'item_freezeresist_01' -> 'freezeresist'."""
    s = stem.lower()
    for pre in ("item_", "potion_"):
        if s.startswith(pre):
            s = s[len(pre):]
    return s.replace("_", "").rstrip("0123456789")


def stock(storage_items, cat: list[dict]) -> Counter:
    """Consumable skill record -> how many of its items are in the bags and the character stash
    (the shared stash is not in player.gdc)."""
    by_stem = {_stem(c["record"]): c["record"] for c in cat}
    keyed: dict = {}
    for st, rec in by_stem.items():
        keyed.setdefault(stem_key(st), []).append(rec)
    out: Counter = Counter()
    for it in storage_items or ():
        base = str(it.get("basename") or "").lower().replace("\\", "/")
        if "/consumables/" not in base and "/potions/" not in base:
            continue
        st = _stem(base)
        rec = by_stem.get(KNOWN_ITEMS[st]) if st in KNOWN_ITEMS else None
        if rec is None and st not in KNOWN_ITEMS:
            cands = keyed.get(stem_key(st), [])
            rec = cands[0] if len(cands) == 1 else None
        if rec:
            out[rec] += max(1, int(it.get("stack_count") or 1))
    return out


def as_sources(c: dict) -> SourceSet:
    sid = f"consumable:{c['record']}"
    src = SourceInstance(sid, SourceKind.GRANTED_SKILL, sid, None, c["record"], None, c["name"])
    eff = tuple(EffectInstance(f"{sid}:{k}", sid, k, float(v), Activity.TOGGLE, StackingPolicy.ADDITIVE,
                               "consumable") for k, v in sorted(c["fields"].items()))
    return SourceSet(sources=(src,), effects=eff)


def _priority(cmp, tgt, gain: float, need: float, early: bool = False) -> str:
    """Closing the target: HIGH (fact) / MEDIUM (community). .64: a consumable that covers at least half of
    the deficit of a situational target (Life Leech, Freeze, Stun, Slow) is MEDIUM too - for those targets the
    ointment before the fight is the answer, not a gear swap at a cost. EARLY (levelling, B4): always LOW -
    a 15-minute potion is no answer to a levelling gap (Cpt Doom: Elixir of the Dranghoul was MEDIUM)."""
    if early:
        return "LOW"
    if cmp["fixes"]:
        return "MEDIUM" if tgt.basis == "community" else "HIGH"
    if tgt.id.startswith(("leech", "freeze", "stun", "slow")) and need > 0 and gain >= 0.5 * need:
        return "MEDIUM"
    return "LOW"


def proposals(base, fields_targets) -> list[Proposal]:
    cat = catalogue(base.ctx.index)
    have = stock(getattr(base.ctx.character, "storage_items", None), cat)
    out = []
    for field, tid in fields_targets:
        tgt = base.results[tid]
        fits = [c for c in cat if c["fields"].get(field)]
        if not fits:
            continue
        need = max(tgt.deficit or 0.0, 0.0)
        closing = [c for c in fits if c["fields"][field] >= need - 1e-9]
        c = min(closing, key=lambda c: (not have.get(c["record"]), c["fields"][field], c["name"])) if closing \
            else max(fits, key=lambda c: (c["fields"][field], bool(have.get(c["record"])), c["name"]))
        n = have.get(c["record"], 0)
        ctx2, _, snap2 = CF.snapshot_for(base, None, base.sources.plus(as_sources(c)))
        cmp = CF.compare(base, ctx2, snap2)
        mins = int(round(c["duration"] / 60))
        out.append(Proposal(
            f"consumable:{c['record']}", "consumable", "Defense", f"{tgt.label}: {c['name']}",
            f"Use {c['name']} before hard fights", cmp["gains"], cmp["costs"], cmp["fixes"], cmp["breaks"],
            _priority(cmp, tgt, c["fields"][field], need, getattr(base.stage, "value", base.stage) == "EARLY"),
            "low" if n else "medium", in_stock=bool(n), consumable=f"{mins} min",
            source=(f"you have {n} in your bags/stash" if n else "craft (Blacksmith) or buy"),
            explain=(f"temporary: {mins} minutes", "only one ointment works at a time" if c["ointment"] else
                     "elixirs and ointments can be combined")
            + ((f"counted: {STORAGE_HINT} (the shared stash is not in the save file)",) if n else ()),
            score=cmp["score"], detail={"field": field, "target": tid, "ointment": c["ointment"], "in_stock": n}))
    return out
