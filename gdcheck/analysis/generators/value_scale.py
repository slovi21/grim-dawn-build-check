"""One value scale for every candidate generator (port of the valuation part of the .35
`affixe` module plus `skillpunkte.zusatz_wert` - Task 7 of release .67/.68).

1 point ~ +1 % of the main damage type OR +1 % Health. Every value is computed against the
current state of the character: 8 % Physique is worth what it really adds to Health and DA
(game formula through `abilities`), not a fixed rate. Occasion (user, 27.09.2026): 8 %
Physique = several hundred Health, for a melee character below 20,000 Health one of the most
important values. Rerolls, attribute spreads, skill points and devotions all use this scale
(architecture review 30.09.2026), so their candidates rank against each other. The numbers
only rank candidates; the counterfactual engine recalculates every proposal shown.

De-globalised: the weapon speed factor (.35 `affixe.WEAPON_FACTOR`, set by the adapter before
and reset after every run) is the `weapon_factor` field of the ValueProfile; the profile itself
(.35: a dict mutated per affix) is a frozen dataclass and `others` is passed per call.

Old -> new: GEWICHT -> WEIGHTS (keys schaden -> damage, neben -> secondary,
health_unter_ziel -> health_below_target, skill_stufe -> skill_rank, angriff_tempo ->
attack_speed, zauber_tempo -> cast_speed, krit -> crit, haupt_flach -> main_flat,
umwandlung -> conversion, weg -> conversion_away, res_bedarf -> res_need, res_rest ->
res_rest, gegner_schaden -> enemy_damage), HEALTH_ZIEL -> HEALTH_TARGET, TEMPO -> SPEEDS,
tempo -> speeds, profil -> make_profile (dict -> ValueProfile), _masteries -> masteries_of,
_skillname -> skill_name, werte_deltas -> stat_deltas, nutzen -> affix_value,
skillpunkte.zusatz_wert -> extra_value, skillpunkte._eigene -> own_value,
assess_core.text -> stat_line.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable

from ...data import gametables as _GT
from .. import damage_types as DT
from ..save_facts import stat_text
from .targets import LEECH_FIELD, LEECH_PENALTY_DEFAULT, TARGETS

WEIGHTS = MappingProxyType({
    "damage": 1.0,         # per % more main damage: d% / (100 + sum%) * 100
    "secondary": 0.25,     # the same for a secondary type
    "health": 1.0,         # per % more Health ...
    "health_below_target": 1.5,   # ... while Health is below the target
    "da": 0.8,             # per % more DA
    "oa": 0.5,             # per % more OA
    "skill_rank": 1.0,     # per +1 to a skill with points
    "attack_speed": 1.0,   # per % more attacks/s (melee)
    "cast_speed": 0.25,
    "crit": 0.1,           # per % Crit Damage
    "main_flat": 0.02,     # per point of flat main damage (average)
    "conversion": 0.05,    # per % conversion INTO the main damage
    "conversion_away": 0.1,  # per % conversion AWAY from the main damage (deduction)
    # A buffer above the cap only acts under enemy resistance reduction (bosses, nemeses) -
    # therefore small against damage and Health.
    "res_need": 0.05,      # per resistance point and type up to the need
    "res_rest": 0.005,     # per resistance point and type above it
    "physical": 0.5,       # per % less physical damage
    "rr": 0.3,             # per point of resistance reduction on enemies
    "enemy_damage": 0.3,   # per % "Reduced target's Damage"
})
HEALTH_TARGET = 20000      # user target for melee (27.09.2026), guide value - the one constant (runner too)

RESIST_FIELDS = _GT.RESIST_FIELDS     # the nine resistances (data.gametables, one definition)

# kind: (bonus field, max modifier, cap in gameengine.dbr, default)
SPEEDS = MappingProxyType({
    "attack": ("characterAttackSpeedModifier", "characterAttackSpeedMaxModifier",
               "playerAttackSpeedCapMax", 200.0),
    "cast": ("characterSpellCastSpeedModifier", "characterSpellCastSpeedMaxModifier",
             "playerSpellCastSpeedCapMax", 200.0),
    "run": ("characterRunSpeedModifier", "characterRunSpeedMaxModifier",
            "playerRunSpeedCapMax", 135.0),
})

# resistance type in a field name -> damage types of damage_types.DAMAGE_TYPES
RESIST_DAMAGE_TYPES = MappingProxyType({
    "Fire": ("Fire", "Burn"), "Cold": ("Cold", "Frostburn"),
    "Lightning": ("Lightning", "Electrocute"), "Poison": ("Poison", "PoisonDot"),
    "Life": ("Life", "VitalityDecay"), "Aether": ("Aether",), "Chaos": ("Chaos",),
    "Physical": ("Physical", "InternalTrauma"), "Pierce": ("Pierce",),
    "Bleeding": ("Bleeding",)})

_DOT_OF = {"Physical": "InternalTrauma", "Fire": "Burn", "Cold": "Frostburn",
           "Lightning": "Electrocute", "Poison": "PoisonDot", "Life": "VitalityDecay"}
# boss damage types whose max resistance counts in extra_value
_MAX_RES_FIELDS = (("Fire", "defensiveFire"), ("Lightning", "defensiveLightning"),
                   ("Chaos", "defensiveChaos"), ("Pierce", "defensivePierce"),
                   ("Aether", "defensiveAether"), ("Vitality", "defensiveLife"),
                   ("Bleeding", "defensiveBleeding"))


def stat_line(field_name: str, value: float, index=None) -> str:
    """One bonus line in game wording; unknown fields as '<value> <field>'."""
    if field_name == "defensiveSlowLifeLeach":           # tag DefenseLifeLeach
        return f"{value:g}% Life Leech Resistance"
    t = stat_text(field_name, value, index)
    return t if t else f"{value:g} {field_name}"


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@dataclass(frozen=True, eq=False)
class ValueProfile:
    """What a value is worth for THIS character. Built once per run; never mutated."""
    main: dict | None                 # main damage entry (damage_types.main_damage)
    main_type: str | None
    main_fields: frozenset            # % fields of the main type (and its DoT when strong)
    secondary_fields: frozenset       # % fields of strong secondary types
    sheet: dict                       # character sheet (permanent + toggles)
    combat: dict                      # sheet + kept-up buffs (for buffers)
    abilities: dict                   # health, da, oa, physique, cunning, spirit, energy
    resists: dict                     # resistance field -> resistance row (resistances.rows)
    points: dict                      # skill record -> points
    melee: bool
    masteries: frozenset              # playerclassNN folders with points
    index: object
    attributes: dict                  # final cunning/spirit
    abilities_fn: Callable | None = None   # sheet -> abilities (game formula); None = linear
    health_target: float = 0          # Health weight 1.5 below it
    weapon_factor: float = 1.0        # attack speed weapon factor (.59, KG-11)
    pet: bool = False
    leech_penalty: float = LEECH_PENALTY_DEFAULT
    physical_target: float = TARGETS["physical_combat"]
    da_target: float = TARGETS["da"]
    health_goal: float | None = None  # Health target of the stage
    leech_target: float = TARGETS["leech"]
    others: dict = field(default_factory=dict)  # strongest non-stacking values of other parts


def make_profile(sheet, combat, abilities, resist_rows, points, melee, attributes, index,
                 abilities_fn=None, health_target=None, **extra) -> ValueProfile:
    """Profile of a character. combat = sheet + kept-up buffs; abilities_fn maps a sheet to its
    abilities (without it Health/DA/OA are estimated linearly)."""
    main = DT.main_damage(sheet, attributes.get("cunning"), attributes.get("spirit"))
    percentages = DT.damage_percentages(sheet, attributes.get("cunning"), attributes.get("spirit"))
    main_type = main["typ"] if main else None
    main_fields, secondary_fields = set(), set()
    if main:
        main_fields.add(DT.DAMAGE_TYPES[main_type][0])
        dot = _DOT_OF.get(main_type)
        if dot and sheet.get(DT.DAMAGE_TYPES[dot][0], 0) >= 0.4 * max(main["eigen"], 1):
            main_fields.add(DT.DAMAGE_TYPES[dot][0])
        for e in percentages:
            if e["typ"] == main_type or DT.DAMAGE_TYPES[e["typ"]][0] in main_fields:
                continue
            if e["eigen"] >= 0.4 * max(main["eigen"], 1):
                secondary_fields.add(DT.DAMAGE_TYPES[e["typ"]][0])
    return ValueProfile(
        main=main, main_type=main_type, main_fields=frozenset(main_fields),
        secondary_fields=frozenset(secondary_fields), sheet=sheet, combat=combat or sheet,
        abilities=abilities, resists={z["field"]: z for z in resist_rows}, points=points,
        melee=melee, masteries=masteries_of(points), index=index, attributes=attributes,
        abilities_fn=abilities_fn,
        health_target=health_target if health_target is not None else (HEALTH_TARGET if melee else 0),
        **extra)


def masteries_of(points) -> frozenset:
    """playerclassNN folders of the skills with points."""
    out = set()
    for rn, p in (points or {}).items():
        m = re.search(r"/(playerclass\d+)/", rn or "")
        if m and p > 0:
            out.add(m.group(1))
    return frozenset(out)


def speeds(sheet, index=None, weapon_factor: float = 1.0) -> dict:
    """Speeds on the sheet against their caps (gameengine.dbr; max modifiers such as Form of
    the Beast raise them). Total Speed counts for all three; attack = sum x weapon factor
    (.59). {kind: {"value", "cap", "left", "wasted"}}"""
    ge = ((getattr(index, "tabellen", None) or {}).get("records/game/gameengine.dbr")) or {}
    out = {}
    for kind, (fld, maxf, kf, std) in SPEEDS.items():
        factor = weapon_factor if kind == "attack" else 1.0
        value = (100 + sheet.get(fld, 0.0) + sheet.get("characterTotalSpeedModifier", 0.0)) * factor
        cap = float(ge.get(kf, std)) + sheet.get(maxf, 0.0)
        out[kind] = {"value": value, "cap": cap, "left": max(0.0, cap - value),
                     "wasted": max(0.0, value - cap)}
    return out


# ---------------------------------------------------------------------------
# Value of an affix (or of any field delta)
# ---------------------------------------------------------------------------

def skill_name(index, rn) -> str:
    """Display name of a skill; buff skills often carry it in their _buff record."""
    if index:
        for cand in (rn, (index.record(rn) or {}).get("buffSkillName"),
                     rn[:-4] + "_buff.dbr" if rn.endswith(".dbr") else None):
            if isinstance(cand, str) and cand and index.display(cand):
                return index.display(cand)
    return (rn or "").rsplit("/", 1)[-1].replace(".dbr", "")


def _numbers(rec) -> dict:
    return {k: v for k, v in (rec or {}).items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)}


def stat_deltas(fields, pr: ValueProfile) -> dict:
    """What a set of fields changes on the character: Health, DA, OA (game formula), main
    damage in % of the sum. {'health': (old, new), ...}"""
    sheet, ab = pr.sheet, pr.abilities
    new_sheet = dict(sheet)
    for k, v in fields.items():
        if k.startswith(("character", "defensive", "offensive")):
            new_sheet[k] = new_sheet.get(k, 0) + v
    if pr.abilities_fn:
        ab_new = pr.abilities_fn(new_sheet)
    else:                                   # linear estimate without the formula
        phy = fields.get("characterStrength", 0) + fields.get(
            "characterStrengthModifier", 0) * (ab.get("physique") or 0) / 100
        ab_new = dict(ab)
        ab_new["health"] = (ab.get("health") or 0) + (phy * 2.5 + fields.get("characterLife", 0)) \
            * 1.5 + (ab.get("health") or 0) * fields.get("characterLifeModifier", 0) / 100
        ab_new["da"] = (ab.get("da") or 0) + phy * 0.5 + fields.get("characterDefensiveAbility", 0)
        ab_new["oa"] = (ab.get("oa") or 0) + fields.get("characterOffensiveAbility", 0)
    out = {k: (ab.get(k) or 0, ab_new.get(k) or 0) for k in ("health", "da", "oa")}
    mt = pr.main_type
    if mt:
        before = next(e for e in DT.damage_percentages(sheet, pr.attributes.get("cunning"),
                                                        pr.attributes.get("spirit")) if e["typ"] == mt)
        after = next(e for e in DT.damage_percentages(new_sheet, ab_new.get("cunning"),
                                                       ab_new.get("spirit")) if e["typ"] == mt)
        out["main"] = (before["summe"], after["summe"])
    return out


def affix_value(rec, pr: ValueProfile, others: dict | None = None) -> dict:
    """Value of an affix (or a field delta) for the character:
    {"value": float, "useful": [line], "wasted": [line], "res": {field: value},
     "effect": {"health": +x, "da": +x, "oa": +x, "main_pct": +x}, "extra": {...}}.
    others: strongest non-stacking values ("Reduced target's ...") of the other worn parts
    (default: pr.others)."""
    if not rec:
        return {"value": 0.0, "useful": [], "wasted": [], "res": {}, "effect": {}, "extra": {}}
    others = pr.others if others is None else others
    index = pr.index
    p, useful, wasted = 0.0, [], []
    fields = _numbers(rec)

    def line(fld, value):
        return stat_line(fld, value, index)

    # -- effect on Health, DA, OA and main damage (game formula) --------------
    d = stat_deltas(fields, pr)
    effect = {}
    h0, h1 = d["health"]
    if h0 and abs(h1 - h0) >= 1:
        target = pr.health_target or 0
        g = WEIGHTS["health_below_target"] if target and h0 < target else WEIGHTS["health"]
        p += g * (h1 - h0) / h0 * 100
        effect["health"] = h1 - h0
    for k in ("da", "oa"):
        a0, a1 = d[k]
        if a0 and abs(a1 - a0) >= 1:
            p += WEIGHTS[k] * (a1 - a0) / a0 * 100
            effect[k] = a1 - a0
    if "main" in d:
        s0, s1 = d["main"]
        if abs(s1 - s0) >= 0.5:
            p += WEIGHTS["damage"] * (s1 - s0) / (100 + s0) * 100
            effect["main_pct"] = s1 - s0

    # -- skills ---------------------------------------------------------------
    for n in range(1, 6):
        rn = rec.get(f"augmentSkillName{n}")
        lv = fields.get(f"augmentSkillLevel{n}", 0)
        if not (isinstance(rn, str) and rn and lv):
            continue
        name = skill_name(index, rn)
        if pr.points.get(rn, 0) > 0:
            p += WEIGHTS["skill_rank"] * lv
            useful.append(f"+{lv:g} {name}")
        else:
            wasted.append(f"+{lv:g} {name} (no points)")
    for n in range(1, 6):                   # .70: items carry up to three mastery bonuses (index schema 9)
        rn = rec.get(f"augmentMasteryName{n}")
        lv = fields.get(f"augmentMasteryLevel{n}", 0)
        if isinstance(rn, str) and rn and lv:
            m = re.search(r"/(playerclass\d+)/", rn)
            if m and m.group(1) in pr.masteries:
                p += WEIGHTS["skill_rank"] * lv * 3
                useful.append(f"+{lv:g} to all skills of {skill_name(index, rn)}")
            else:
                wasted.append(f"+{lv:g} {skill_name(index, rn)} (mastery not taken)")

    # -- damage (text lines; the value is in effect['main_pct']) ---------------
    if fields.get("offensiveTotalDamageModifier"):
        useful.append(line("offensiveTotalDamageModifier", fields["offensiveTotalDamageModifier"]))
    elem = fields.get("offensiveElementalModifier", 0)
    if elem:
        (useful if pr.main_type in DT.ELEMENTAL else wasted).append(
            line("offensiveElementalModifier", elem))
    s0 = d.get("main", (0, 0))[0]
    for _typ, (fld, _kind, _name) in DT.DAMAGE_TYPES.items():
        w = fields.get(fld, 0)
        if not w:
            continue
        if fld in pr.main_fields:
            useful.append(line(fld, w))
        elif fld in pr.secondary_fields:
            p += WEIGHTS["secondary"] * w / (100 + s0) * 100
            useful.append(line(fld, w) + " (secondary)")
        else:
            wasted.append(line(fld, w))
    mt = pr.main_type
    if mt:
        mn = fields.get(f"offensive{mt}Min", 0)
        mx = fields.get(f"offensive{mt}Max", 0) or mn
        if mn:
            p += WEIGHTS["main_flat"] * (mn + mx) / 2
            useful.append(f"+{mn:g}-{mx:g} {pr.main['name']} Damage")
    for n in ("", "2"):
        src, dst = rec.get(f"conversionInType{n}"), rec.get(f"conversionOutType{n}")
        pct = fields.get(f"conversionPercentage{n}", 0)
        if not (src and dst and pct):
            continue
        txt = f"{pct:g}% {src} converted to {dst}"
        if mt and dst == mt:
            p += WEIGHTS["conversion"] * pct
            useful.append(txt)
        elif mt and src == mt:
            p -= WEIGHTS["conversion_away"] * pct
            wasted.append(txt + " (away from your main damage)")
        else:
            wasted.append(txt)

    # -- offensive values -----------------------------------------------------
    for f in ("characterOffensiveAbility", "characterOffensiveAbilityModifier"):
        if fields.get(f):
            useful.append(line(f, fields[f]))
    # Speed counts only up to the cap (gameengine.dbr + max modifiers); above it is wasted
    # (user 30.09.2026: Cpt Wolf at the attack cap).
    sp = speeds(pr.combat or pr.sheet, index, pr.weapon_factor)
    at = fields.get("characterAttackSpeedModifier", 0)
    if at:
        acts = min(at, sp["attack"]["left"]) if at > 0 else at
        if pr.melee and acts > 0:
            p += WEIGHTS["attack_speed"] * acts / sp["attack"]["value"] * 100
            useful.append(line("characterAttackSpeedModifier", at)
                          + ("" if acts >= at else f" (only {acts:.0f} below the cap)"))
        elif at > 0:
            wasted.append(line("characterAttackSpeedModifier", at)
                          + (" (above the cap)" if pr.melee else ""))
    ct = fields.get("characterSpellCastSpeedModifier", 0)
    if ct:
        acts = min(ct, sp["cast"]["left"]) if ct > 0 else ct
        if acts > 0:
            p += WEIGHTS["cast_speed"] * acts / sp["cast"]["value"] * 100
            useful.append(line("characterSpellCastSpeedModifier", ct))
        else:
            wasted.append(line("characterSpellCastSpeedModifier", ct) + " (above the cap)")
    cr = fields.get("offensiveCritDamageModifier", 0)
    if cr:
        p += WEIGHTS["crit"] * cr
        useful.append(line("offensiveCritDamageModifier", cr))
    # "Reduced target's ..." does not stack: only the strongest acts. Counts only what exceeds
    # what the other worn parts already give.
    for fld, g in (("offensiveTotalResistanceReductionPercentMin", "rr"),
                   ("offensiveTotalResistanceReductionAbsoluteMin", "rr"),
                   ("offensiveTotalDamageReductionPercentMin", "enemy_damage")):
        w = fields.get(fld, 0)
        if not w:
            continue
        rest = max(0.0, w - others.get(fld, 0))
        p += WEIGHTS[g] * rest
        (useful if rest > 0 else wasted).append(
            line(fld, w) + ("" if rest > 0 else " (another piece has it, does not stack)"))

    # -- defensive values -----------------------------------------------------
    res = {}
    elem_res = fields.get("defensiveElementalResistance", 0)
    for fld in RESIST_FIELDS:
        w = fields.get(fld, 0) + (elem_res if fld in ("defensiveFire", "defensiveCold",
                                                      "defensiveLightning") else 0)
        if not w:
            continue
        res[fld] = w
        z = pr.resists.get(fld)
        if z:
            missing = max(0.0, z["need"] - (z.get("combat", z["value"])))
            p += WEIGHTS["res_need"] * min(w, missing) + WEIGHTS["res_rest"] * max(0, w - missing)
    if elem_res:
        useful.append(line("defensiveElementalResistance", elem_res))
    for fld in RESIST_FIELDS:
        if fields.get(fld):
            useful.append(line(fld, fields[fld]))
    ph = fields.get("defensivePhysical", 0)
    if ph:
        z = pr.resists.get("defensivePhysical") or {}
        now = z.get("combat", z.get("value", 0)) or 0
        p += WEIGHTS["physical"] * ph / max(100 - now, 1) * 100
        useful.append(line("defensivePhysical", ph))
        res["defensivePhysical"] = ph
    for f in ("characterDefensiveAbility", "characterDefensiveAbilityModifier",
              "characterLife", "characterLifeModifier",
              "characterStrength", "characterStrengthModifier"):
        if fields.get(f):
            useful.append(line(f, fields[f]))
    magic = pr.main and pr.main["art"] in ("direkt_magisch", "dot_magisch")
    for f in ("characterIntelligence", "characterIntelligenceModifier"):
        if fields.get(f):
            (useful if magic else wasted).append(line(f, fields[f]))
    extra = {}
    for f, k in (("characterAttackSpeedModifier", "attack"),
                 ("characterSpellCastSpeedModifier", "cast"),
                 ("offensiveCritDamageModifier", "crit"),
                 ("offensiveTotalResistanceReductionPercentMin", "rr_pct"),
                 ("offensiveTotalResistanceReductionAbsoluteMin", "rr_flat"),
                 ("offensiveTotalDamageReductionPercentMin", "enemy_damage")):
        if fields.get(f):
            extra[k] = fields[f]
    if mt and fields.get(f"offensive{mt}Min"):
        extra["flat"] = (fields[f"offensive{mt}Min"]
                         + (fields.get(f"offensive{mt}Max") or fields[f"offensive{mt}Min"])) / 2
    for n in ("", "2"):
        if mt and rec.get(f"conversionOutType{n}") == mt:
            extra["conversion"] = extra.get("conversion", 0) + fields.get(f"conversionPercentage{n}", 0)
    # Fields not valued above (multiplicative damage, dodge/deflect, max resistance, Life Leech
    # Resistance, Physical/Freeze/Stun below the endgame target, single-type RR, enemy DA)
    # are valued by extra_value - the same for rerolls, skills and devotions.
    more = extra_value(fields, pr)
    if more:
        p += more
        extra["extra_value"] = more
    return {"value": p, "useful": useful, "wasted": wasted, "res": res, "effect": effect,
            "extra": extra}


def own_value(pr: ValueProfile, delta) -> float:
    """Value of a field delta on the common scale (affix_value already includes extra_value)."""
    if not delta:
        return 0.0
    return affix_value(delta, pr)["value"]


def extra_value(delta, pr: ValueProfile) -> float:
    """Fields affix_value does not know but that are common on skills and devotions:
    - offensiveDamageMultModifier: multiplicative total damage (Form of the Beast: +1 % per
      rank) -> like main damage, 100 * ln(new/old)
    - reduction of enemy DA from toggles/forms (Recklessness) -> like OA
    - resistance reduction of single types on the main damage -> 'rr' (total RR is valued in
      affix_value)
    - Physical below the endgame target counts double, missing Freeze/Stun Resistance 0.05 per
      % up to the target, max resistance of the boss damage types, Life Leech Resistance,
      dodge and deflect (all G)."""
    g, p = WEIGHTS, 0.0
    m = delta.get("offensiveDamageMultModifier", 0.0)
    if m:
        old = (pr.sheet or {}).get("offensiveDamageMultModifier", 0.0)
        p += g["damage"] * 100 * math.log((100 + old + m) / max(100 + old, 1e-9))
    oa = (pr.abilities or {}).get("oa") or 0
    da_minus = delta.get("offensiveSlowDefensiveAbilityMin", 0.0)
    if oa and da_minus:
        p += g["oa"] * da_minus / oa * 100
    mt = pr.main_type
    for res, types in RESIST_DAMAGE_TYPES.items():
        if mt in types:
            for kind in ("Absolute", "Percent"):
                p += g["rr"] * delta.get(f"offensive{res}ResistanceReduction{kind}Min", 0.0)
    # Below the endgame target (targets.TARGETS, user 30.09.2026) Physical counts double
    # (affix_value counts it once), missing Freeze/Stun Resistance 0.05 per % up to the target
    # (G) - like Health below the Health target.
    combat = pr.combat or pr.sheet or {}
    ph = delta.get("defensivePhysical", 0.0)
    if ph:
        now = combat.get("defensivePhysical", 0.0)
        target = pr.physical_target
        below = max(0.0, min(target, now + max(ph, 0)) - now) if ph > 0 \
            else max(0.0, min(target, now) - (now + ph))
        p += (1 if ph > 0 else -1) * g["physical"] * below / max(100 - now, 1) * 100
    for f, goal in (("defensiveFreeze", TARGETS["freeze"]), ("defensiveStun", TARGETS["stun"])):
        d = delta.get(f, 0.0)
        if d:
            now = combat.get(f, 0.0)
            part = (min(goal, now + d) - min(goal, now))
            p += 0.05 * part
    # max resistance of the boss damage types: each point ~ 100/(100-cap) % less damage of that
    # type, spread over the 7 types (G)
    res = pr.resists or {}
    all_max = delta.get("defensiveAllMaxResist", 0.0)
    for _name, fld in _MAX_RES_FIELDS:
        d = delta.get(fld + "MaxResist", 0.0) + all_max
        if d:
            cap = (res.get(fld) or {}).get("cap", 80.0)
            p += g["physical"] * d * 100 / max(100 - cap, 1) / len(_MAX_RES_FIELDS)
    # Life Leech Resistance up to the target 0.1 per %, above 0.02 (G)
    d = delta.get(LEECH_FIELD, 0.0)
    if d:
        now = combat.get(LEECH_FIELD, 0.0) - pr.leech_penalty
        lt = pr.leech_target
        upto = max(0.0, min(lt, now + d) - min(lt, now))
        p += (0.1 * upto + 0.02 * (d - upto)) if d > 0 else 0.1 * d
    # dodge (melee) and deflect (projectiles): per % about half the hits of one kind fewer -
    # worth half of 1 % Health (G)
    for f in ("characterDodgePercent", "characterDeflectProjectile"):
        p += 0.5 * g["health"] * delta.get(f, 0.0)
    return p
