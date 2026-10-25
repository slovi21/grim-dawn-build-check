"""Boss preparation / risk (AP9, N-007, X-012) - never win/lose, never 'ready'.

Per boss of the active profile (General/SR/Ascendant: story, dungeon and
nemesis bosses; Celestial: superbosses) the damage profile from the game
database is checked against the character in combat:
  resistance of every damage type the boss deals (target: own cap; Celestial
  ENDGAME: cap + boss RR), Physical, life leech, crowd control, DA shred,
  Health. A consumable that closes a gap is named ("with consumable").
Risk: HIGH = a resistance more than 10 below its target, two or more ointments
needed at once with a gap over OINTMENT_GAP_TOLERANCE (N-010) or three MEDIUM reasons; MEDIUM = at least
one reason (also two ointments needed at once with all gaps within the tolerance); LOW = nothing found.

Ravager resistance reduction (X-012): ravager_rr reads the debuffs of the Ravagers' skill graphs
from the game database (Ravager's Presence for all three, Siphon Souls and Eruption of Anguish of
Ravager of Souls' Sundering Wraiths); the target bonus RAVAGER_RR takes the database value only
where it equals the reviewed constant (boss_rr) - no silent target change.

Boss hazards (X-012, E5): boss_hazards reads, per boss of the boss data, the resistance reduction of every
distinct debuff in its skill graph (same rule as the Ravagers), damage reflection (defensiveReflect of a
graph skill, e.g. Blade Barrier) and purges (skills of Class *DispelMagic*, which remove your buffs).

Two ointments (N-010): since 1.2.1 only ONE ointment works at a time. A boss whose gaps can be closed by
consumables only with two or more different ointments always leaves one of the gaps open: HIGH when the
largest of those gaps is over OINTMENT_GAP_TOLERANCE points (requirements V2.1, 04.10.2026), else MEDIUM
with the note that only one ointment works at a time.
"""
from __future__ import annotations

import math
import weakref
from dataclasses import dataclass
from pathlib import Path

from ..data.bosses import load as load_profiles
from ..mechanics.policy import Confidence, rule
from ..domain.enums import ContentProfile, Stage
from .proposals.consumables import catalogue, stock

ROOT = Path(__file__).resolve().parents[2]
RAVAGER_RR = {"Ravager of Souls": {"Vitality": 39, "Aether": 39}, "Ravager of Flesh": {"Bleeding": 25}}
RAVAGER_LEVEL = 100            # Ultimate: monster level = player level 100, clamped to the record (ASSUMED)
# debuff field (game database) -> your resistances it lowers
RR_FIELDS = {"defensivePhysical": ("Physical",), "defensivePierce": ("Pierce",),
             "defensiveFire": ("Fire",), "defensiveCold": ("Cold",), "defensiveLightning": ("Lightning",),
             "defensiveElementalResistance": ("Fire", "Cold", "Lightning"), "defensivePoison": ("Acid",),
             "defensiveLife": ("Vitality",), "defensiveAether": ("Aether",), "defensiveChaos": ("Chaos",),
             "defensiveBleeding": ("Bleeding",)}
CC_FIELD = {"Freeze": "defensiveFreeze", "Stun": "defensiveStun", "Petrify": "defensivePetrify"}
RES_FIELD = {"Physical": "defensivePhysical", "Pierce": "defensivePierce", "Fire": "defensiveFire",
             "Cold": "defensiveCold", "Lightning": "defensiveLightning", "Acid": "defensivePoison",
             "Vitality": "defensiveLife", "Aether": "defensiveAether", "Chaos": "defensiveChaos",
             "Bleeding": "defensiveBleeding"}


OINTMENT_GAP_TOLERANCE = 5.0     # points; N-010 V2.1 (player decision 04.10.2026): a gap of up to 5 is small
REFLECT_FIELD = "defensiveReflect"           # % of the damage you deal that the monster sends back
PURGE_CLASS = "DispelMagic"                  # SkillBuff_DispelMagic / Skill_DispelMagic: removes your buffs


def _debuff_rr(idx, boss: dict, lv: int) -> list:
    """Resistance reduction parts of one boss: the distinct debuffs (SkillBuff_Debuf*) of its skill graph
    at their skill level - [{"record", "name", "owner", "skill_level", "values": {type: points}}]."""
    from ..data.bosses import skill_label
    from ..mechanics import monster as MON
    parts, seen = [], set()
    for n in boss.get("graph") or []:
        rec = n.get("record")
        if not str(n.get("class", "")).startswith("SkillBuff_Debuf") or rec in seen:
            continue
        seen.add(rec)
        fields = idx.record(rec) if hasattr(idx, "record") else None
        if not fields:
            continue
        slv = MON.skill_level(n.get("level_eq"), lv)
        vals: dict = {}
        for f, types in RR_FIELDS.items():
            v = MON.at_level(fields.get(f), slv)
            if v < 0:
                for t in types:
                    vals[t] = vals.get(t, 0) + int(round(-v))
        if vals:
            parts.append({"record": rec, "name": skill_label(idx, rec), "owner": n.get("owner"),
                          "skill_level": slv, "values": vals})
    return parts


def _combat_bosses(idx) -> dict:
    from ..data import versioned as V
    from ..data.bosses import combat_meta
    ver, fp = V.index_identity(idx)
    return combat_meta(ROOT, ver or "1.3.0.8", fp).get("bosses") or {}


@rule("ravager_rr", Confidence.DERIVED,
      "resistance reduction of the Ravagers = sum of the distinct debuffs (SkillBuff_Debuf) in the boss's "
      "skill graph at their skill level (monster level 100 on Ultimate, ASSUMED; spawned monsters at the "
      "boss level, ASSUMED; different skills stack, one skill counts once)")
def ravager_rr_parts(idx, level: int = RAVAGER_LEVEL) -> dict:
    """{boss: [{"record", "name", "owner", "skill_level", "values": {type: points}}]} for every
    'Ravager of ...' boss of the boss data file; debuff fields from the index. {} without data."""
    from ..mechanics import monster as MON
    out = {}
    for name, boss in sorted(_combat_bosses(idx).items()):
        if not name.startswith("Ravager of"):
            continue
        parts = _debuff_rr(idx, boss, MON.monster_level(level, boss.get("minLevel"), boss.get("maxLevel")))
        if parts:
            out[name] = parts
    return out


def ravager_rr(idx, level: int = RAVAGER_LEVEL) -> dict[str, dict[str, int]]:
    """Resistance reduction per Ravager and resistance from the game database (X-012), summed over
    the distinct debuffs (ravager_rr_parts)."""
    out = {}
    for name, parts in ravager_rr_parts(idx, level).items():
        tot: dict = {}
        for p in parts:
            for t, v in p["values"].items():
                tot[t] = tot.get(t, 0) + v
        out[name] = tot
    return out


_RR_CACHE: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def boss_rr(idx) -> tuple[dict, dict]:
    """(target bonus per boss and type, source per 'boss/type'): RAVAGER_RR, confirmed by the game
    database where ravager_rr gives the same number ('game database'), else the constant stays
    ('game guide'). Types the constant does not list are never added (no silent target change).
    The source is shown in E1 next to the Ravager targets. Read once per index (assess runs per analysis)."""
    try:
        src = _RR_CACHE.get(idx)
    except TypeError:                                    # an index that cannot be weak-referenced: no cache
        src = None
    if src is None:
        try:
            db = ravager_rr(idx)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            db = {}
        src = {f"{b}/{t}": ("game database" if db.get(b, {}).get(t) == v else "game guide")
               for b, types in RAVAGER_RR.items() for t, v in types.items()}
        try:
            _RR_CACHE[idx] = src
        except TypeError:
            pass
    return {b: dict(types) for b, types in RAVAGER_RR.items()}, dict(src)


def hazard_level(boss: dict, player_level: int, override: int | None = None) -> int:
    """Monster level of a boss for its hazards (.67): your level through the boss record's charLevel equation,
    clamped to minLevel/maxLevel - the same rule as the combat check (target_state.boss_monster_level);
    --boss-level is the monster level itself. Was monster_level(100) without the equation (Benn'Jahr 100
    instead of 112)."""
    from ..mechanics import monster as MON
    if override:
        return MON.monster_level(0, boss.get("minLevel"), boss.get("maxLevel"), int(override))
    return MON.monster_level(player_level, boss.get("minLevel"), boss.get("maxLevel"),
                             char_level=boss.get("charLevel"))


@rule("boss_hazards", Confidence.DERIVED,
      "boss hazards from the boss's skill graph in the game database: resistance reduction = sum of the "
      "distinct debuffs (SkillBuff_Debuf, as ravager_rr), damage reflection = highest defensiveReflect of a "
      "graph skill, purge = a skill of Class *DispelMagic* (removes your buffs); skill levels at your level "
      "through the boss's charLevel equation (DERIVED; area cap ASSUMED not binding), spawned monsters at "
      "the boss level (ASSUMED)")
def boss_hazards_uncached(idx, level: int = RAVAGER_LEVEL, override: int | None = None) -> dict:
    """{boss: {"rr": {type: points}, "reflect": (percent, skill name) | None, "purge": (skill names)}}
    for every boss of the boss data file with at least one hazard; {} without data. `level` = your level,
    `override` = --boss-level (hazard_level)."""
    from ..data.bosses import skill_label
    from ..mechanics import monster as MON
    out = {}
    for name, boss in sorted(_combat_bosses(idx).items()):
        lv = hazard_level(boss, level, override)
        rr: dict = {}
        for p in _debuff_rr(idx, boss, lv):
            for t, v in p["values"].items():
                rr[t] = rr.get(t, 0) + v
        reflect, purge = None, []
        for n in boss.get("graph") or []:
            if n.get("kind") != "skill":
                continue
            rec, cls = n.get("record"), str(n.get("class", ""))
            # a skill of a spawned monster (totem, summon) says so - the boss itself does not use it
            lab = skill_label(idx, rec, name) + ("" if n.get("owner") in (None, boss.get("record")) else ", summons")
            if PURGE_CLASS in cls and lab not in purge:
                purge.append(lab)
            fields = (idx.record(rec) if hasattr(idx, "record") else None) or {}
            if fields.get(REFLECT_FIELD):
                v = MON.at_level(fields.get(REFLECT_FIELD), MON.skill_level(n.get("level_eq"), lv))
                if v > 0 and (reflect is None or v > reflect[0]):
                    reflect = (v, lab)
        if rr or reflect or purge:
            out[name] = {"rr": rr, "reflect": reflect, "purge": tuple(purge)}
    return out


_HAZ_CACHE: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def boss_hazards(idx, level: int = RAVAGER_LEVEL, override: int | None = None) -> dict:
    """boss_hazards_uncached, read once per index and level (assess runs per analysis); {} when the data
    cannot be read."""
    key = (int(level), int(override) if override else None)
    try:
        per_index = _HAZ_CACHE.setdefault(idx, {})
    except TypeError:
        per_index = {}
    hit = per_index.get(key)
    if hit is None:
        try:
            hit = boss_hazards_uncached(idx, *key)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            hit = {}
        per_index[key] = hit
    return hit


RES_ORDER = ("Physical", "Pierce", "Fire", "Cold", "Lightning", "Acid", "Vitality", "Aether", "Chaos", "Bleeding")


def rr_text(rr: dict) -> str:
    """'-39 Vitality/Aether, -28 Physical, -25 all other' - largest reduction first; Fire/Cold/Lightning
    with one value read 'Elemental'; a value shared by five or more types when every resistance is
    reduced reads 'all other' (or 'all' when it is the only value)."""
    groups: dict = {}
    for t in sorted(rr, key=lambda t: RES_ORDER.index(t) if t in RES_ORDER else len(RES_ORDER)):
        if rr[t]:
            groups.setdefault(rr[t], []).append(t)
    if not groups:
        return ""
    complete = set(RES_ORDER) <= {t for ts in groups.values() for t in ts}
    widest = max(groups, key=lambda v: (len(groups[v]), v))
    rest = complete and len(groups[widest]) >= 5
    parts = []
    for v in sorted(groups, key=lambda v: -v):
        if rest and v == widest:
            continue
        ts = groups[v]
        if {"Fire", "Cold", "Lightning"} <= set(ts):
            ts = ["Elemental" if t == "Fire" else t for t in ts if t not in ("Cold", "Lightning")]
        parts.append(f"-{v} " + "/".join(ts))
    if rest:
        parts.append(f"-{widest} " + ("all other" if parts else "all"))
    return ", ".join(parts)


@dataclass(frozen=True)
class BossRisk:
    name: str
    kind: str                  # SuperBoss / Quest / Boss ...
    risk: str                  # LOW / MEDIUM / HIGH
    reasons: tuple             # plain text, most important first
    consumable: str | None
    damage_types: tuple
    leech: bool
    cc: tuple
    da_shred: bool
    rr: tuple = ()             # ((resistance, points), ...) the boss's debuffs take off you (game database)
    reflect: tuple = ()        # (percent, skill name) of its damage reflection, () without
    purge: tuple = ()          # names of its skills that remove your buffs
    ointments: tuple = ()      # N-010: two or more ointments needed at once (only one works) - their names

    def hazards(self) -> str:
        """X-012: 'RR -25 Bleeding; CC Freeze/Stun; reflect 16% (Blade Barrier); purge (Nullified)' or ''."""
        out = []
        if self.rr:
            out.append("RR " + rr_text(dict(self.rr)))
        if self.cc:
            out.append("CC " + "/".join(self.cc))
        if self.reflect:
            out.append(f"reflect {self.reflect[0]:.0f}% ({self.reflect[1]})")
        if self.purge:
            out.append("purge (" + ", ".join(self.purge) + ")")
        return "; ".join(out)


@rule("ointment_clash_tolerance", Confidence.HEURISTIC,
      "N-010 V2.1: a boss that needs two different ointments at once (only one works at a time, 1.2.1 hotfix, "
      "Forum) is HIGH when one of those gaps is larger than OINTMENT_GAP_TOLERANCE = 5 resistance points "
      "(or points of Life Leech / crowd control resistance), else MEDIUM; the 5 is a decision of the user 04.10.2026")
def clash_is_high(gap: float) -> bool:
    """True when the largest gap of a two-ointment clash is over the tolerance (exactly 5 is not over)."""
    return gap > OINTMENT_GAP_TOLERANCE + 1e-9


def ointment_clash(fields_missing, cat, have) -> tuple[tuple, float]:
    """N-010: (names of the fewest different ointments that close every gap a consumable can close at all,
    largest of the gaps that need an ointment) when that takes two or more (only one ointment works at a
    time) - ((), 0.0) otherwise. A gap an elixir or other non-ointment closes needs no ointment (those
    combine); a gap no single consumable closes is left out (no consumable answer). In-stock ointments
    first, then by name."""
    from itertools import combinations
    need: dict = {}
    for f, gap in fields_missing:
        need[f] = max(need.get(f, 0.0), gap)

    def closes(c, f):
        return c["fields"].get(f, 0) >= need[f] - 1e-9
    free = {f for f in need if any(closes(c, f) for c in cat if not c["ointment"])}
    oint = sorted((c for c in cat if c["ointment"]), key=lambda c: (not have.get(c["record"]), c["name"]))
    left = [f for f in need if f not in free and any(closes(c, f) for c in oint)]
    if len(left) < 2:
        return (), 0.0
    useful = [c for c in oint if any(closes(c, f) for f in left)]
    for k in range(1, len(left) + 1):
        for combo in combinations(useful, k):
            if all(any(closes(c, f) for c in combo) for f in left):
                return (tuple(c["name"] for c in combo), max(need[f] for f in left)) if k >= 2 else ((), 0.0)
    return (), 0.0


def two_ointments(fields_missing, cat, have) -> tuple:
    """N-010: the names of ointment_clash (() when no two ointments are needed at once)."""
    return ointment_clash(fields_missing, cat, have)[0]


def _in_profile(name, kind, profile: ContentProfile) -> bool:
    if profile == ContentProfile.CELESTIAL:
        return kind == "SuperBoss"
    if kind == "SuperBoss" or kind in ("Common", "Champion"):
        return False
    if profile == ContentProfile.SR:
        return "Shattered" in name or kind in ("Boss", "Quest")
    return "Shattered" not in name


def assess(ctx, snap, stage: Stage, results, melee: bool) -> list[BossRisk]:
    if stage == Stage.EARLY:
        return []                     # boss preparation starts at MID (Ultimate story bosses)
    profiles, _ = load_profiles(ctx.index, ROOT)
    cat = catalogue(ctx.index)
    have = stock(getattr(ctx.character, "storage_items", None), cat)
    endgame = stage == Stage.ENDGAME
    rr_bonus = boss_rr(ctx.index)[0] if endgame else {}
    phys_target = next((r.target for r in results if r.id.startswith("physical")), 20.0) or 20.0
    da_target = next((r.target for r in results if r.id.startswith("da_")), 2600.0) or 2600.0
    hazards = boss_hazards(ctx.index, getattr(ctx.character, "level", None) or RAVAGER_LEVEL,
                           getattr(getattr(ctx, "options", None), "boss_level", None))
    out = []
    for name, p in sorted(profiles.items()):
        kind = p.get("klasse") or ""
        if not _in_profile(name, kind, ctx.profile):
            continue
        reasons_hi, reasons_mid, fields_missing = [], [], []
        for t in p.get("typen") or []:
            if t == "Physical":
                v = min(snap.combat.resists["Physical"]["value"], snap.combat.resists["Physical"]["cap"])
                if v < phys_target - 4:
                    reasons_mid.append((phys_target - v, f"hits physical - Physical Res {math.floor(v)}% "
                                                         f"(target {phys_target:.0f}%)"))
                    fields_missing.append(("defensivePhysical", phys_target - v))
                continue
            r = snap.combat.resists.get(t)
            if not r:
                continue
            target = r["cap"] + (rr_bonus.get(name, {}).get(t, 0) if endgame else 0)
            if not endgame:
                target = min(r["cap"], 80.0)
            gap = target - r["value"]
            if gap > 10:
                reasons_hi.append((gap, f"{t} Res {math.floor(r['value'])} vs {target:.0f} needed"))   # B7: down
                fields_missing.append((RES_FIELD[t], gap))
            elif gap > 3:
                reasons_mid.append((gap, f"{t} Res {math.floor(r['value'])} vs {target:.0f} needed"))
                fields_missing.append((RES_FIELD[t], gap))
        leech_t = 50.0 if (endgame and kind == "SuperBoss") else 0.0
        if p.get("leech") and snap.sheet.leech["value"] < leech_t:
            reasons_mid.append((leech_t - snap.sheet.leech["value"],
                                f"leeches your life - Life Leech Res {math.floor(snap.sheet.leech['value'])}%"))
            fields_missing.append(("defensiveSlowLifeLeach", leech_t - snap.sheet.leech["value"]))
        for cc in p.get("cc") or []:
            f = CC_FIELD.get(cc)
            if f and snap.combat.fields.get(f, 0.0) < 50:
                reasons_mid.append((50 - snap.combat.fields.get(f, 0.0),
                                    f"can {cc.lower()} you - {cc} Res {math.floor(snap.combat.fields.get(f, 0.0))}%"))
                fields_missing.append((f, 50 - snap.combat.fields.get(f, 0.0)))
        if p.get("da_minus") and snap.combat.abilities["da"] < da_target:
            short = 100 * (da_target - snap.combat.abilities["da"]) / da_target     # points short, in %
            reasons_mid.append((short, f"lowers your DA - DA {math.floor(snap.combat.abilities['da']):,}"))
        cons, clash, clash_gap = None, (), 0.0
        if fields_missing:
            for c in sorted(cat, key=lambda c: (not have.get(c["record"]), not c["ointment"], c["name"])):
                if all(c["fields"].get(f, 0) >= need - 1e-9 for f, need in fields_missing):
                    n = have.get(c["record"], 0)
                    cons = f"{c['name']} ({int(round(c['duration'] / 60))} min" + (f", you have {n})" if n else ")")
                    break
            if cons is None:
                clash, clash_gap = ointment_clash(fields_missing, cat, have)
        clash_high = bool(clash) and clash_is_high(clash_gap)          # N-010 V2.1: small gaps stay MEDIUM
        risk = "HIGH" if reasons_hi or clash_high or len(reasons_mid) >= 3 else ("MEDIUM" if reasons_mid else "LOW")
        # worst first (review .66: Manifestation of Hunger showed Chaos -25 while Fire -50 sat in '+5 more'):
        # the HIGH reasons, then the MEDIUM ones, each by the size of the gap (points, DA in % of its target);
        # N-010: a two-ointment clash that makes the boss HIGH comes after the large gaps and before the MEDIUM
        # reasons - it leads where it is what makes the boss HIGH; V2.1: with small gaps only (MEDIUM) it is a
        # note after the gaps it is about
        count = {2: "two", 3: "three"}.get(len(clash), str(len(clash)))
        clash_txt = []
        if clash:
            names = " + ".join(clash)
            clash_txt = [f"needs {count} ointments ({names}) - only one works at a time"] if clash_high else \
                        [f"small gaps need {count} ointments ({names}) - only one ointment works at a time"]
        reasons = [txt for _g, txt in sorted(reasons_hi, key=lambda x: -x[0])] + \
                  (clash_txt if clash_high else []) + \
                  [txt for _g, txt in sorted(reasons_mid, key=lambda x: -x[0])] + \
                  ([] if clash_high else clash_txt)
        hz = hazards.get(name) or {}
        rr = sorted((hz.get("rr") or {}).items(), key=lambda x: RES_ORDER.index(x[0]) if x[0] in RES_ORDER else 99)
        out.append(BossRisk(name, kind, risk, tuple(reasons), cons,
                            tuple(p.get("typen") or ()), bool(p.get("leech")),
                            tuple(p.get("cc") or ()), bool(p.get("da_minus")),
                            tuple(rr), tuple(hz.get("reflect") or ()), tuple(hz.get("purge") or ()), clash))
    rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    out.sort(key=lambda b: (rank[b.risk], -len(b.reasons), b.name))
    return out
