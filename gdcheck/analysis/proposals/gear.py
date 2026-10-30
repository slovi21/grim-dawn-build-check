"""Gear proposals (AP7.3): augments and components that raise a missing value,
and swaps from bags/stash. Stage 1 ranks candidates by the raw field gain;
stage 2 recalculates the best few completely (counterfactual)."""
from __future__ import annotations


from ...data.text import field_label, short
from ...mechanics import resist as RES
from . import counterfactual as CF
from .model import Proposal

SLOTMAP = {"Head": "head", "Amulet": "amulet", "Chest": "chest", "Legs": "legs", "Feet": "feet",
           "Hands": "hands", "Ring 1": "ring", "Ring 2": "ring", "Waist": "waist",
           "Shoulders": "shoulders", "Medal": "medal"}
PARTS = (("ItemEnchantment", "augment_name", "Augment"), ("ItemRelic", "relic_name", "Component"))


def target_fields(base) -> list[tuple[str, str]]:
    """(field, target id) for every target below ok, most important first."""
    out = []
    order = {"gap": 0, "tight": 1}
    for r in sorted(base.results.values(), key=lambda r: (order.get(r.status, 9), r.basis != "fact", r.id)):
        if r.status not in ("gap", "tight"):
            continue
        if r.id in ("res_ultimate", "res_own_cap"):
            for n in RES.RESISTS:
                rs = base.snap.sheet.resists[n]
                if rs["value"] < rs["cap"]:
                    out.append((RES.FIELD[n], r.id))
        elif r.field:
            out.append((r.field, r.id))
    seen, uniq = set(), []
    for f, t in out:
        if f not in seen:
            seen.add(f)
            uniq.append((f, t))
    return uniq


def _fields(idx, rec_name) -> dict:
    e = idx.records.get(rec_name) if rec_name else None
    return {k: v for k, v in ((e or {}).get("f") or {}).items() if isinstance(v, (int, float))}


def _neg_res(old: dict, new: dict) -> int:
    return sum(1 for f in RES.FIELD.values() if new.get(f, 0) < old.get(f, 0))


def candidates(base, field: str, per_slot: int = 4) -> list[dict]:
    """Stage 1: augments/components for each worn slot that add the field."""
    idx, ch = base.ctx.index, base.ctx.character
    out = []
    for w in ch.worn:
        if w.slot not in SLOTMAP:
            continue
        for typ, part, label in PARTS:
            cur = w.item.get(part) or ""
            old = _fields(idx, cur)
            n = 0
            for rec_name, v in idx.suche(typ=typ, slot=SLOTMAP[w.slot], feld=field,
                                         mindestens=old.get(field, 0)):
                req = (v.get("m") or {}).get("levelRequirement")
                if req and float(req) > ch.level:        # .62: never propose what the character cannot use
                    continue
                new = {k: x for k, x in (v.get("f") or {}).items() if isinstance(x, (int, float))}
                gain = new.get(field, 0) - old.get(field, 0)
                if gain < 1:
                    continue
                out.append({"slot": w.slot, "part": part, "label": label, "record": rec_name,
                            "name": v.get("n") or short(rec_name), "old": cur,
                            "old_name": (idx.display(cur) or short(cur)) if cur else "(empty)",
                            "gain": gain, "neg": _neg_res(old, new),
                            "stock": ch.stock.get(rec_name, 0),
                            "faction": (v.get("m") or {}).get("factionSource"),
                            "lost": lost_offense(old, new),
                            "rank_lost": CF.rank_bonus_lost(CF.rank_bonuses(idx, [cur]), CF.rank_bonuses(idx, [rec_name])),
                            "level": (v.get("m") or {}).get("levelRequirement")})
                n += 1
                if n >= per_slot:
                    break
    out.sort(key=lambda c: (c["neg"], -c["gain"], -c["stock"], c["slot"], c["record"]))
    return out


_ELEMENT = {"Life": "Vitality", "Poison": "Acid", "TotalDamage": "All", "Elemental": "Elemental"}
_DOT = {"Bleeding": "Bleeding", "Fire": "Burn", "Cold": "Frostburn", "Lightning": "Electrocute",
        "Poison": "Poison", "Life": "Vitality Decay", "Physical": "Internal Trauma"}
_SPEED = {"characterTotalSpeedModifier": "Total Speed", "characterAttackSpeedModifier": "Attack Speed",
          "characterSpellCastSpeedModifier": "Casting Speed", "offensiveCritDamageModifier": "Crit Damage"}


def _offense_label(f: str) -> str | None:
    if f in _SPEED:
        return _SPEED[f]
    if f.startswith("offensiveSlow") and f.endswith("Modifier"):
        e = f[len("offensiveSlow"):-len("Modifier")]
        return f"{_DOT[e]} Damage" if e in _DOT else None
    if f.startswith("offensive") and f.endswith("Modifier"):
        e = f[len("offensive"):-len("Modifier")]
        if e in ("Physical", "Pierce", "Fire", "Cold", "Lightning", "Aether", "Chaos", "Bleeding") or e in _ELEMENT:
            return f"{_ELEMENT.get(e, e)} Damage"
    return None


def lost_offense(old: dict, new: dict, n: int = 3) -> list[str]:
    """Damage and speed bonuses a change gives up (.62, N-006: the counterfactual compares defenses,
    Health and OA/DA only - lost % damage was shown as 'cost: none')."""
    lost = []
    for f, v in old.items():
        lab = _offense_label(f)
        d = float(v) - float(new.get(f, 0) or 0)
        if lab and d >= 1:
            lost.append((d, lab))
    lost.sort(key=lambda x: (-x[0], x[1]))
    return [f"+{d:g}% {lab}" for d, lab in lost[:n]]


def required_level(idx, item: dict) -> float:
    """Highest level requirement of an item's base and affixes (0 when none is stored)."""
    req = 0.0
    for key in ("basename", "prefix_name", "suffix_name", "relic_name", "augment_name"):
        rec = item.get(key)
        e = idx.records.get(rec) if rec else None
        try:
            req = max(req, float(((e or {}).get("m") or {}).get("levelRequirement") or 0))
        except (TypeError, ValueError):
            continue
    return req


STASH_PARTS = ("basename", "prefix_name", "suffix_name", "modifier_name", "transmute_name",
               "relic_name", "relic_bonus", "augment_name", "ascended_name", "ascended_2h_name")


def stash_candidates(base, field: str) -> list[dict]:
    idx, ch = base.ctx.index, base.ctx.character
    out = []

    def total(it):
        s = {}
        for key in STASH_PARTS:
            for f, v in _fields(idx, it.get(key)).items():
                s[f] = s.get(f, 0) + v
        return s

    def ranks(it):
        return CF.rank_bonuses(idx, [it.get(key) for key in STASH_PARTS])
    for w in ch.worn:
        if w.slot in ("Main hand", "Off hand", "Relic"):
            continue
        mine = (idx.record(w.basename) or {}).get("_type")
        if not mine:
            continue
        a = total(w.item)
        for it in ch.storage_items:
            if (idx.record(it.get("basename") or "") or {}).get("_type") != mine:
                continue
            if required_level(idx, it) > ch.level:    # .62: stash items the character cannot wear yet
                continue
            b = total(it)
            gain = b.get(field, 0) - a.get(field, 0)
            if gain >= 1:
                out.append({"slot": w.slot, "item": it, "gain": gain, "neg": _neg_res(a, b), "lost": lost_offense(a, b),
                            "rank_lost": CF.rank_bonus_lost(ranks(w.item), ranks(it)),
                            "name": idx.item_name(it), "old_name": idx.item_name(w.item)})
    out.sort(key=lambda c: (c["neg"], -c["gain"], c["slot"], c["name"]))
    return out


def _slot_key(raw) -> str:
    return str(raw) if str(raw).startswith("faction") else f"faction{raw}"


def _faction(index, raw) -> str:
    from ...data.text import FACTION_SLOT_KEYS
    try:
        return index.faction_name(FACTION_SLOT_KEYS.index(_slot_key(raw)))
    except (ValueError, AttributeError):
        return str(raw)


def faction_standing(character, raw) -> float | None:
    """Reputation with the faction of a vendor item from the save (block 13), None if unknown.
    .63 (Windows run 03.10.2026): Cpt Blade was told 'buy: Kymon's Chosen' while Despised (-2,816)."""
    from ...data.savefile import get_block
    from ...data.text import FACTION_SLOT_KEYS
    try:
        i = FACTION_SLOT_KEYS.index(_slot_key(raw))
        b13 = get_block(getattr(character, "doc", None) or {}, 13) or {}
        return float(b13["factions"][i]["value"])
    except (ValueError, KeyError, IndexError, TypeError):
        return None


# .66 (A2): the faction rank a vendor augment needs - references/vendor-ranks-<version>.json, extracted from
# the faction vendor tables of the game database (gdcheck.app.refs_extract). Record paths are stable across
# patches, so a file of other game data is used too (versioned.resolve), unless the item's factionSource in
# the index names another faction now; no file / no entry -> None (never a guessed rank).
VENDOR_PREFIX = "vendor-ranks"
_FACTION_ALIAS = {"factionUser0": "factionDrifters", "factionUser1": "factionNeutralNPC"}   # gamefactions.dbr


def _vendor_entry(idx, record) -> dict | None:
    from ...data import versioned as V
    if not record:
        return None
    try:
        items = V.resolve(VENDOR_PREFIX, *V.index_identity(idx)).data.get("items") or {}
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    e = items.get(record) if isinstance(items, dict) else None
    if not isinstance(e, dict) or not e.get("rank") or not e.get("faction"):
        return None
    src = (((getattr(idx, "records", None) or {}).get(record) or {}).get("m") or {}).get("factionSource")
    if src:
        key = _slot_key(src)
        if _FACTION_ALIAS.get(key, key) != e["faction"]:
            return None                           # the game data moved the item to another faction
    return e


def required_rank(idx, record: str) -> str | None:
    """Reputation tier (data.text.REPUTATION_TIERS name) a faction vendor sells the item at, None if unknown."""
    e = _vendor_entry(idx, record)
    return str(e["rank"]) if e else None


def vendor_faction(idx, record: str) -> str | None:
    """Save slot key (data.text.FACTION_SLOT_KEYS) of the faction that sells the item, None if unknown."""
    e = _vendor_entry(idx, record)
    return str(e["faction"]) if e else None


def _tier_rank(name) -> int:
    from ...data.text import REPUTATION_TIERS
    return next((i for i, (n, _v) in enumerate(REPUTATION_TIERS) if n == name), -1)


def _vendor(base, c) -> tuple:
    """(faction key, standing or None, required rank or None) of a part to buy."""
    idx = base.ctx.index
    key = vendor_faction(idx, c.get("record")) or c.get("faction")
    rep = faction_standing(base.ctx.character, key) if key else None
    return key, rep, required_rank(idx, c.get("record"))


def rank_missing(base, c) -> str | None:
    """The rank a buyable part needs when the character's standing is known and below it (else None)."""
    from ...data.text import reputation_tier
    if c.get("stock"):
        return None
    _key, rep, need = _vendor(base, c)
    if rep is None or need is None or rep < 0:
        return None
    return need if _tier_rank(reputation_tier(rep)) < _tier_rank(need) else None


def _where(base, c) -> str | None:
    """Where to get a part; None when the character cannot buy it (hostile with the faction, not in stock).
    .66: names the rank the vendor needs when the standing is too low - 'buy: Kurn (needs Honored, you: Friendly)'
    (a LOW proposal, never an action); a standing that meets the rank (or an unknown rank) shows only your tier -
    'buy: Kurn (you: Honored)' (keeps every action within two report lines)."""
    from ...data.text import reputation_tier
    if c["stock"]:
        return "in your stash"
    key, rep, need = _vendor(base, c)
    if not key:
        return "craft or find"
    name = _faction(base.ctx.index, key)
    if rep is None:
        return f"buy: {name} (needs {need})" if need else f"buy: {name}"
    if rep < 0:
        return None
    you = reputation_tier(rep)
    if not need:
        return f"buy: {name} (you: {you})"
    if _tier_rank(you) < _tier_rank(need):
        return f"buy: {name} (needs {need}, you: {you})"
    return f"buy: {name} (you: {you})"


def _target_label(base, tid):
    r = base.results.get(tid)
    return r.label if r else tid


def proposals(base, max_evals: int = 80, top_k: int = 5) -> list[Proposal]:
    """Gear and stash proposals, each recalculated on the whole character. The work budget is a
    fixed number of recalculations (deterministic on every machine; .44 replaced a 0.8 s wall
    clock budget that gave fewer proposals on a slow or busy PC). 19 test characters need at
    most 59."""
    out = []
    used = 0
    for field, tid in target_fields(base):
        if used >= max_evals:
            base.ctx.extra["gear_budget_reached"] = max_evals    # named in the report notes
            break
        tgt = base.results[tid]
        prio = "HIGH" if tgt.basis == "fact" else "MEDIUM"
        evaluated = 0
        for c in candidates(base, field)[:top_k]:
            if used >= max_evals:
                break
            used += 1
            ch2 = CF.with_item_part(base.ctx.character, c["slot"], c["part"], c["record"])
            ctx2, src2, snap2 = CF.snapshot_for(base, ch2)
            cmp = CF.compare(base, ctx2, snap2, lost=c["lost"], src2=src2, unrated=c.get("rank_lost", False))
            evaluated += 1
            if not CF.worth_it(base, cmp):
                continue
            effort = "low" if c["stock"] else "medium"
            where = _where(base, c)
            if where is None:                  # .63: hostile with the vendor's faction - cannot be bought
                continue
            need = rank_missing(base, c)       # .66: rank too low - a LOW proposal, never an action
            out.append(Proposal(
                f"gear:{c['slot']}:{c['part']}:{c['record']}", "gear", "Gear",
                f"{_target_label(base, tid)}: {c['label']} on {c['slot']}",
                f"{c['name']} on {c['slot']} " + ("(free slot)" if c["old_name"] == "(empty)"
                                                  else f"(replaces {c['old_name']})"),
                cmp["gains"], cmp["costs"], cmp["fixes"], cmp["breaks"],
                # .64: a partial step towards a gap stays MEDIUM/HIGH only for a fact target or when it costs
                # nothing (no stat loss, no lost damage); a costly step towards a community target is LOW
                "LOW" if need else
                prio if cmp["fixes"] or (tgt.status == "gap" and (tgt.basis == "fact" or not (cmp["costs"] or c["lost"])))
                else "LOW", effort,
                in_stock=bool(c["stock"]), source=where,
                explain=(f"raises {field_label(field)} by {c['gain']:g}", "recalculated with all gear",
                         f"target: {tgt.label} {tgt.now_text} -> {tgt.target_text} ({tgt.basis})"),
                score=cmp["score"], detail={"field": field, "target": tid, "level": c["level"],
                        "slot_part": f"{c['slot']}:{c['part']}", "lost": c["lost"], "name": c["name"],
                        "slot": c["slot"], "old_name": c["old_name"], **CF.annotate(cmp),
                        "apply": (("item_part", c["slot"], c["part"], c["record"]),),
                        **({"needs_rank": need} if need else {})}))
        for c in stash_candidates(base, field)[:2]:
            if used >= max_evals:
                break
            used += 1
            ch2 = CF.with_item(base.ctx.character, c["slot"], c["item"])
            ctx2, src2, snap2 = CF.snapshot_for(base, ch2)
            cmp = CF.compare(base, ctx2, snap2, lost=c["lost"], src2=src2, unrated=c.get("rank_lost", False))
            if not CF.worth_it(base, cmp):
                continue
            out.append(Proposal(
                f"stash:{c['slot']}:{c['name']}", "gear", "Gear",
                f"{_target_label(base, tid)}: swap {c['slot']} from your stash",
                f"{c['name']} instead of {c['old_name']} ({c['slot']})",
                cmp["gains"], cmp["costs"], cmp["fixes"], cmp["breaks"],
                prio if cmp["fixes"] else "LOW", "low", in_stock=True, source="in your stash",
                explain=(f"raises {field_label(field)} by {c['gain']:g}", "recalculated with all gear"),
                score=cmp["score"], detail={"field": field, "target": tid, "slot_part": f"{c['slot']}:item",
                                            "lost": c["lost"], "name": c["name"], "slot": c["slot"],
                                            "old_name": c["old_name"], **CF.annotate(cmp),
                                            "apply": (("item", c["slot"], c["item"]),)}))
    return out
