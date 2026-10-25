"""Reroll advice per affix (Inventor) - port of the pool and recommendation part of the .35
`affixe` module (Task 7 of release .67/.68).

Since Fangs of Asterkarn (1.3.0.0) the Inventor rerolls prefix, suffix and ascended affix ONE
BY ONE. A rare affix stays rare, a magic one may become rare. Cost: Ashes of Awakening + Iron
Bits, rising with every roll (capped); the item becomes untradeable. [Crate: Grim Misadventure
#190; grimdawn.com guide "Service NPCs"]

The old finding "+2 Onslaught wasted -> reroll" was too coarse: the affix "Demonic" also brings
+50 % Chaos, flat Chaos damage and 15 % Physical -> Chaos. So the WHOLE affix is valued:

  1. Profile of the character (value_scale.ValueProfile).
  2. Value per affix line, split into useful / wasted. The points are a rough ranking (guide
     value), no build grade - the lines are always shown for an own comparison.
  3. Carrying values: if a resistance falls below cap or need when the affix is rerolled
     (Resistances.swap_check), a new affix must replace it. Results that do not are not
     "better".
  4. Pool: which affixes can roll on this item. Exact only with the loot tables of the index
     (schema 4+). Without them: estimated from the naming convention of the affix records
     (slot code, tier, rarity) - marked "estimated" in the report.

De-globalised: the lower-case path map of the index (.35 stored it on the index object as
`db._klein`) lives in a `RecordCase` object per run; the strongest non-stacking values of the
other worn parts are passed per affix instead of being written into the profile.

Old -> new: BESSER_AB -> BETTER_FROM, LOHNT_ANTEIL -> REROLL_SHARE, OPTIONAL_ANTEIL ->
OPTIONAL_SHARE, LOHNT_ABSTAND -> REROLL_LEAD, RES_NAMEN -> RESIST_NAMES, SLOT_KUERZEL ->
SLOT_CODES, NICHT_STAPELND -> NON_STACKING, ITEM_TEILE -> ITEM_PARTS, _stamm -> _stem,
slot_kuerzel -> slot_codes, _stufe_b -> _tier_letter, _item_kuerzel -> _item_codes,
loot_tabellen_pool -> loot_table_pool, geschaetzter_pool -> estimated_pool, empfehlungen ->
recommendations, _skills -> _skill_ranks, _felder -> _summable_fields, vergleich ->
comparison_table. Result keys: see generators/__init__.py.
"""
from __future__ import annotations

from types import MappingProxyType

from .value_scale import ValueProfile, affix_value

# From this lead (points) a new affix counts as "better".
BETTER_FROM = 0.5
# The first roll removes the old affix. One can roll again until a better one comes; on
# average that takes 1 / share rolls (cost rises per roll, capped). Verdict:
#   reroll    share >= 25 % (on average <= 4 rolls) and the best >= 1.5 ahead
#   optional  share >= 10 % and the best >= 1.5 ahead
#   keep      otherwise
REROLL_SHARE = 0.25
OPTIONAL_SHARE = 0.10
REROLL_LEAD = 1.5

RESIST_NAMES = MappingProxyType({
    "defensiveFire": "Fire", "defensiveCold": "Cold", "defensiveLightning": "Lightning",
    "defensivePoison": "Acid", "defensivePierce": "Pierce", "defensiveBleeding": "Bleeding",
    "defensiveLife": "Vitality", "defensiveAether": "Aether", "defensiveChaos": "Chaos",
    "defensiveElementalResistance": "Elemental", "defensivePhysical": "Physical"})

# slot codes in affix file names -> which items they fit (estimate)
SLOT_CODES = frozenset({"je", "ri", "ar", "to", "we", "we2h", "fo", "melee1h", "melee2h",
                        "ranged1h", "ranged2h", "sh"})

NON_STACKING = ("offensiveTotalResistanceReductionPercentMin",
                "offensiveTotalResistanceReductionAbsoluteMin",
                "offensiveTotalDamageReductionPercentMin")
ITEM_PARTS = ("basename", "prefix_name", "suffix_name", "ascended_name",
              "augment_name", "relic_name", "relic_bonus", "modifier_name")
_VERDICT_RANK = MappingProxyType({"reroll": 0, "optional": 1, "keep": 2, "unknown": 3})


class RecordCase:
    """Loot tables list paths in lower case; this maps them back to the spelling of the index
    so index.record() finds them. Built lazily, once per run (never stored on the index)."""

    def __init__(self, index):
        self._index = index
        self._map = None

    def __call__(self, path: str) -> str:
        if self._map is None:
            self._map = {k.lower(): k for k in getattr(self._index, "records", {})}
        return self._map.get(path.lower(), path)


# ---------------------------------------------------------------------------
# pool: what can roll on this item
# ---------------------------------------------------------------------------

def _stem(path):
    return (path or "").rsplit("/", 1)[-1].rsplit(".", 1)[0]


def slot_codes(path):
    return {t for t in _stem(path).split("_") if t in SLOT_CODES}


def _tier_letter(path):
    """b_ar011_ar_f -> 'f' (tier letter of the MI affixes)."""
    t = _stem(path).split("_")
    return t[-1] if t and len(t[-1]) == 1 and t[-1].isalpha() else None


def _item_codes(item_rec, item_path):
    """Which slot codes fit an item (estimate)."""
    cls = str((item_rec or {}).get("Class", ""))
    path = (item_path or "").lower()
    if cls.startswith("WeaponMelee"):
        return {"melee2h", "we2h"} if "2h" in cls or "2h" in path else {"melee1h", "we"}
    if cls.startswith("WeaponHunting") or "ranged" in cls.lower():
        return {"ranged2h", "we2h"} if "2h" in cls or "2h" in path else {"ranged1h", "we"}
    if "Shield" in cls:
        return {"sh"}
    if "OffhandWeapon" in cls or "focus" in path:
        return {"fo"}
    if cls in ("ArmorJewelry_Ring",) or "ring" in path:
        return {"je", "ri"}
    if "Jewelry" in cls or "necklace" in path or "medal" in path or "amulet" in path:
        return {"je"}
    if cls.startswith("Armor"):
        return {"ar", "to"}
    return set()


def loot_table_pool(index, item_path, kind, rare=False, case: RecordCase | None = None):
    """Exact pool from the loot tables (index schema 8 with content).

    kind = 'prefix' or 'suffix'. rare=True (the current affix is Rare): pool 'rare_<kind>',
    since a rare affix stays rare at the Inventor. A magic affix uses the normal pool; the
    chance that it becomes rare is not in the database and not counted.
    [(affix path, weight)] or None when the index has no tables for this item."""
    tab = getattr(index, "loot_tabellen", None)
    if not tab:
        return None
    entry = tab.get((item_path or "").lower())
    if not entry:
        return None
    entries = (entry.get("rare_" + kind) if rare else None) or entry.get(kind)
    if not entries:
        return None
    case = case or RecordCase(index)
    out = [(case(a), float(g)) for a, g in entries]
    return [(a, g) for a, g in out if index.record(a)] or None


def estimated_pool(index, item_rec, item_path, current_path, char_level):
    """Pool by naming convention. Only affixes of the same kind (folder prefix/suffix), the
    same rarity, a fitting slot code, a tier near the current affix; per display name only the
    highest tier."""
    if not hasattr(index, "records") or not current_path:
        return []
    folder = "/prefix/" if "/prefix/" in current_path else "/suffix/"
    cur = index.record(current_path) or {}
    rarity = cur.get("itemClassification", "")
    allowed = {rarity} if rarity == "Rare" else {"Magical", "Rare"}
    cur_lr = cur.get("levelRequirement") or 0
    fitting = _item_codes(item_rec, item_path) or slot_codes(current_path)
    b_family = _stem(current_path).startswith("b_")
    b_tier = _tier_letter(current_path)
    best = {}
    for k, v in index.records.items():
        if v.get("t") != "LootRandomizer" or folder not in k:
            continue
        m = v.get("m") or {}
        if m.get("itemClassification", "") not in allowed:
            continue
        lr = m.get("levelRequirement") or 0
        if lr > char_level or lr < cur_lr - 12:
            continue
        kz = slot_codes(k)
        if kz and not (kz & fitting):
            continue
        stem = _stem(k)
        if stem.startswith("b_") and b_family and b_tier and _tier_letter(k) \
                and _tier_letter(k) != b_tier:
            continue
        if stem.startswith("b_class"):
            # class prefixes (e.g. "Death Knight's"): documented on jewelry/medals only (Cpt
            # Wolf, Gryphon Eye). Not assumed on weapons and armor.
            if "je" not in fitting:
                continue
        elif stem.startswith("b_") and not kz:
            continue                          # MI affix without a slot code: unclear
        name = v.get("n") or stem
        # same name, other skills (class prefixes a/b/c): own entries
        key = (name, m.get("augmentSkillName1"), m.get("augmentSkillName2"))
        old = best.get(key)
        if old is None or lr > old[1]:
            best[key] = (k, lr)
    return [(k, 1.0) for k, _ in best.values()]


# ---------------------------------------------------------------------------
# recommendation per item
# ---------------------------------------------------------------------------

def recommendations(worn, index, pr: ValueProfile, swap_check, char_level) -> list:
    """worn: [(slot, item)]; swap_check: delta -> (rows, problems). One recommendation per
    affix, sorted by verdict and share of better rolls."""
    out = []
    case = RecordCase(index)
    # non-stacking values of every worn part: (slot, part) -> fields
    sources = []
    for slot, it in worn:
        for part in ITEM_PARTS:
            rec = index.record(it.get(part) or "") if it.get(part) else None
            if rec:
                f = {k: rec[k] for k in NON_STACKING if isinstance(rec.get(k), (int, float))}
                if f:
                    sources.append((slot, part, f))
    for slot, it in worn:
        base = it.get("basename") or ""
        irec = index.record(base) if base else None
        klass = str((irec or {}).get("itemClassification", ""))
        if klass in ("Epic", "Legendary"):
            continue
        for kind, key in (("prefix", "prefix_name"), ("suffix", "suffix_name")):
            cur = it.get(key) or ""
            if not cur or not index.record(cur):
                continue
            cur_rec = index.record(cur)
            others = {}
            for qs, qt, f in sources:
                if (qs, qt) == (slot, key):
                    continue
                for k, v in f.items():
                    others[k] = max(others.get(k, 0), v)
            now = affix_value(cur_rec, pr, others)
            rare = str(cur_rec.get("itemClassification", "")) == "Rare"
            pool = loot_table_pool(index, base, kind, rare, case)
            exact = pool is not None
            if not exact:
                pool = estimated_pool(index, irec, base, cur, char_level)
            # carrying resistances: what falls when the affix is gone?
            minus = {f: -v for f, v in _summable_fields(cur_rec).items()}
            _, problems = swap_check(minus)
            candidates, rejected = [], 0
            total_weight = sum(g for _, g in pool) or 1.0
            better_weight = 0.0
            cur_name = index.display(cur) or _stem(cur)
            for kpath, weight in pool:
                if kpath == cur:
                    continue
                # The same affix family in another tier is no roll target: which tier drops
                # depends on the drop level of the item, which is not in the savefile (check
                # 01.10.2026: else "of Solael's Malice _f" from 92 on an item of level 84).
                if (index.display(kpath) or _stem(kpath)) == cur_name:
                    continue
                krec = index.record(kpath)
                n = affix_value(krec, pr, others)
                delta = {}
                for f in set(_summable_fields(cur_rec)) | set(_summable_fields(krec)):
                    delta[f] = _summable_fields(krec).get(f, 0) - _summable_fields(cur_rec).get(f, 0)
                _, kprob = swap_check(delta)
                if kprob:
                    rejected += 1
                    continue
                if n["value"] >= now["value"] + BETTER_FROM:
                    better_weight += weight
                    candidates.append({"record": kpath, "name": index.display(kpath) or _stem(kpath),
                                       "value": n["value"], "useful": n["useful"],
                                       "wasted": n["wasted"], "weight": weight,
                                       "effect": n["effect"], "extra": n["extra"],
                                       "res": n["res"], "skills": _skill_ranks(krec, pr)})
            candidates.sort(key=lambda k: -k["value"])
            share = better_weight / total_weight if pool else 0.0
            best = candidates[0]["value"] if candidates else now["value"]
            ahead = best - now["value"] >= REROLL_LEAD
            if not pool:
                verdict = "unknown"
            elif share >= REROLL_SHARE and ahead:
                verdict = "reroll"
            elif share >= OPTIONAL_SHARE and ahead:
                verdict = "optional"
            else:
                verdict = "keep"
            out.append({
                "slot": slot, "kind": kind, "item": index.item_name(it),
                "affix": index.display(cur) or _stem(cur), "record": cur,
                "value": now["value"], "useful": now["useful"], "wasted": now["wasted"],
                "effect": now["effect"], "res": now["res"], "extra": now["extra"],
                "skills": _skill_ranks(cur_rec, pr),
                "carrying": problems, "pool": len(pool), "exact": exact,
                "rejected": rejected, "better_share": share,
                "candidates": candidates[:5], "verdict": verdict,
                "rolls_used": it.get("affix_rerolls_used") or 0,
            })
    out.sort(key=lambda e: (_VERDICT_RANK[e["verdict"]], -e["better_share"]))
    return out


def _skill_ranks(rec, pr: ValueProfile) -> float:
    """Sum of +ranks on skills with points."""
    n = 0.0
    for i in range(1, 6):
        rn = (rec or {}).get(f"augmentSkillName{i}")
        if isinstance(rn, str) and pr.points.get(rn, 0) > 0:
            n += (rec or {}).get(f"augmentSkillLevel{i}", 0) or 0
    return n


def _summable_fields(rec) -> dict:
    """Only summable bonus fields (for the resistance check)."""
    if not rec:
        return {}
    return {k: v for k, v in rec.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
            and (k.startswith("defensive") or k.startswith("character"))}


def comparison_table(e, main_name="Main") -> list:
    """Old against proposals: [(row, [old, A, B, C])], only values that count for the
    character."""
    columns = [e] + list(e.get("candidates") or [])[:3]

    def get(x, path):
        v = x
        for k in path:
            v = (v or {}).get(k) if isinstance(v, dict) else None
        return v or 0

    def fmt(v, pct=False, signed=True):
        if not v:
            return "-"
        t = f"{v:+.0f}" if signed else f"{v:.0f}"
        return t + ("%" if pct else "")

    rows = []

    def row(label, path, pct=False):
        values = [get(x, path) for x in columns]
        if any(abs(v) >= 0.5 for v in values):
            rows.append((label, [fmt(v, pct) for v in values]))
    row(f"{main_name} dmg (% total)", ("effect", "main_pct"), True)
    row("Health", ("effect", "health"))
    row("Defensive Ability", ("effect", "da"))
    row("Offensive Ability", ("effect", "oa"))
    values = [x.get("skills") or 0 for x in columns]
    if any(values):
        rows.append(("+skills (with points)", [fmt(v) for v in values]))
    row("Attack Speed", ("extra", "attack"), True)
    row("Crit Damage", ("extra", "crit"), True)
    row(f"flat {main_name} (avg)", ("extra", "flat"))
    row(f"conversion to {main_name}", ("extra", "conversion"), True)
    row("Enemy res. reduction", ("extra", "rr_pct"), True)
    row("Enemy res. flat", ("extra", "rr_flat"))
    row("Enemy damage reduced", ("extra", "enemy_damage"), True)
    for fld, name in RESIST_NAMES.items():
        if fld == "defensiveElementalResistance":
            continue
        row(f"{name} Resistance", ("res", fld), True)
    rows.append(("Value (G)", [f"{x['value']:.1f}" for x in columns]))
    return rows
