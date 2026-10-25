"""Damage benchmarks from the game database (.72): the Training Dummy and Mogara (pure data layer).

`boss-combat` schema 5 carries the key `benchmarks` = {"dummy": entry, "bench_boss": entry} with the raw
database fields of two monsters (refs_extract.benchmark_entries). This module reads them: the entry,
the display names, health (monster life equation x difficulty table x passives) and the shield of the
Training Dummy. Defense (armor, absorption, DA, OA) goes through data.bosses.defense_profile_of - the same
code as the story bosses. An older boss-combat file has no `benchmarks`: every function then returns
None / {} and the callers keep their fallback (no benchmark, heuristic dummy).

Health formula (ASSUMED, plan .72 N5) - decided on the database, see health_profile:
    HP = E_bio(level) x (1 + (L_diff + sum of the monster's passives' characterLifeModifier at their skill
         level) / 100) x (1 + L_mult / 100)
"""
from __future__ import annotations

from ..mechanics import monster as MON

BENCHMARK_RECORDS = {
    "dummy": "records/creatures/enemies/special/trainingdummy.dbr",
    "bench_boss": "records/creatures/enemies/boss&quest/manticore_jaggedwaste_01.dbr",
}
BENCHMARK_LABELS = {"dummy": "Training Dummy", "bench_boss": "Mogara, the Prime Matriarch"}
DEFENSIVE_PREFIX = "defensive"


def benchmark_entry(combat: dict | None, key: str) -> dict | None:
    """The `benchmarks` entry of boss-combat (None for a file older than schema 5 or without the key)."""
    entry = ((combat or {}).get("benchmarks") or {}).get(key)
    return entry if isinstance(entry, dict) and entry.get("attribute_equations") else None


def label_of(index, key: str, entry: dict | None = None) -> str:
    """Display name: the index text of the monster's name tag, else the fixed English name."""
    tag = (entry or {}).get("name_tag")
    name = index.tag(tag) if tag and hasattr(index, "tag") else None
    return str(name) if name else BENCHMARK_LABELS.get(key, key)


def monster_level_of(entry: dict, player_level: int) -> int:
    """Monster level of a benchmark at a player level: the record's charLevel equation, clamped to its
    minLevel/maxLevel (Training Dummy 'charLevel*1+2' = player + 2; --boss-level does not apply)."""
    return MON.monster_level(player_level, entry.get("minLevel"), entry.get("maxLevel"), None,
                             entry.get("charLevel"))


def passive_values(entry: dict, combat: dict, level: int, field: str) -> list:
    """[(skill record, value at its skill level)] of a numeric per-level skill field over the monster's
    passives (skill level equations of the record evaluated at the monster level, like the story bosses)."""
    skills = combat.get("skills") or {}
    out = []
    for rec, eq in sorted((entry.get("skill_levels") or {}).items()):
        f = skills.get(rec) or {}
        if "Passive" not in str(f.get("Class", "")) or field not in f:
            continue
        out.append((rec, MON.at_level(f[field], MON.skill_level(eq, level))))
    return out


def own_defensive(entry: dict, combat: dict, level: int) -> dict:
    """{defensive field: value}: the monster record's own defensive* fields plus its passives' at their
    skill level (entry["passive_defensive"]); used for the resistances and the life leech resistance."""
    out = {k: float(v) for k, v in (entry.get("defensive") or {}).items() if isinstance(v, (int, float))}
    for rec, fields in sorted((entry.get("passive_defensive") or {}).items()):
        lv = MON.skill_level((entry.get("skill_levels") or {}).get(rec), level)
        for k, v in fields.items():
            out[k] = out.get(k, 0.0) + MON.at_level(v, lv)
    return out


def health_profile(entry: dict, combat: dict, level: int, life_adj: tuple | None, label=None) -> dict:
    """{"health", "parts"} of a benchmark at monster level `level`, {} without the life equation or
    without the difficulty table (`life_adj` = data.gametables.monster_life_adjustment).

    Decided on the game database (plan .72 N5):
    - the Training Dummy lists armorbase04 (characterLifeModifier per level, a Skill_Passive like the bosses'
      armor passives) as skillName2 at skillLevel2 'charLevel*1' - the same passives this code adds for armor;
    - the community values 8.68 / 10.71 / 12.24 M at player level 50 / 70 / 85 (Crate forum 44194 and the
      threads of the orchestrator's research, not re-read here) come out as E_bio(52/72/87) x 6.78 =
      x (1 + (580 - 2) / 100): the passive IS in (-2 % at skill level 52...90, a flat stretch of the array),
      added to the difficulty modifier (multiplied it would give 8.53 / 10.53 / 12.03 M), and the skill level
      is the MONSTER level (player level 50 would pick -4 % and give 8.65 M). Without the passive:
      8.70 / 10.74 / 12.27 M - off at the third digit;
    - those three levels all sit in the flat -2 stretch, so the +70 % of the array at monster level 102 is
      extrapolated, not confirmed: ASSUMED (the same formula without the passive would give 13.8 M, -9 %)."""
    eq = (entry.get("attribute_equations") or {}).get("characterLife")
    if not eq or life_adj is None:
        return {}
    diff, mult = life_adj
    mods = passive_values(entry, combat, level, "characterLifeModifier")
    bio = MON.equation(eq, level)
    mod_pct = diff + sum(v for _r, v in mods)
    hp = MON.monster_health(eq, level, mod_pct, mult)
    parts = [f"life equation {eq} at monster level {level} = {bio:,.0f}",
             f"x (1 + {mod_pct:g}% = {diff:g}% difficulty table"
             + "".join(f" {v:+g}% {label(r) if label else r.rsplit('/', 1)[-1].removesuffix('.dbr')}"
                       for r, v in mods if v) + ")"]
    if mult:
        parts.append(f"x (1 + {mult:g}% players)")
    return {"health": hp, "bio": bio, "modifier_pct": mod_pct, "mult_pct": mult, "level": level, "parts": parts}


def shield_of(entry: dict | None) -> dict | None:
    """The Training Dummy's low-health shield {"skill", "life_pct", "absorption_pct", "duration_s", ...} or None."""
    s = (entry or {}).get("shield")
    return s if isinstance(s, dict) and s.get("life_pct") else None


def kill_share(entry: dict | None) -> float:
    """Share of the health that has to be taken: the Dummy heals fully and absorbs everything at its shield
    threshold (15 % health), so the measurement ends there: 1 - 15 % = 0.85; 1.0 without a shield."""
    s = shield_of(entry)
    return 1.0 - float(s["life_pct"]) / 100.0 if s else 1.0
