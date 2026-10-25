"""Effect sources and their uptime (port of the .35 wirkdauer module and of pruefungen
effektquellen/_mit_buff, Task 4a). Works on an analysis.save_facts.RuleContext.

Idea (user proposal 26.09.2026): an effect that lasts X seconds and can be triggered every Y
seconds is on average worth only X/Y of the same effect with duration >= cooldown.

    uptime = min(1, duration / effective cooldown)
    effective cooldown = cooldown * (1 - global cooldown reduction)

Sources:
  - gear effect with a duration (weapon suffix "14 % Reduced target's Damage for 5 s"):
    refreshed on every hit -> 1 while attacking; with its own chance practically 1 as well, but
    reported as an assumption;
  - passive skills, auras, toggles: 1;
  - active skills and item skills with a cooldown: duration / cooldown - an UPPER bound (recast
    as soon as ready);
  - an active skill neither on the hotbar nor auto-triggered: 0;
  - missing data (index without timing fields, unknown trigger): None - then nothing is
    calculated and that is said openly.

Non-stacking sources ("only the highest value applies") at each moment: when the strongest is
not active the next weaker one applies. Expected value for independent sources:

    E = sum_i  v_i * u_i * prod_{j<i} (1 - u_j)      (v sorted descending)

Effect-source dicts keep the legacy keys (quelle, gruppe, art, felder, klassen, slot,
schluessel, autocast, record, wirkung; art in ausruestung/item_skill/skill/devotion) so the rule
code can switch by import. Old -> new: W.anteil -> uptime, W.erwartungswert -> expected_value,
W.lueckenanteil -> gap_share, W.dauer_feld -> duration_field, W.chance_field -> chance_field,
W.PASSIV_KLASSEN -> PASSIVE_CLASSES, W.AUTOMATISCH -> AUTOMATIC_CLASSES,
P.effektquellen -> effect_sources, P._mit_buff -> with_buff.
"""
from __future__ import annotations

from ..domain.fields import value_at
from .save_facts import ITEM_PART_KEYS, buff_record, parent_skill

# Only what acts permanently without the player's doing. Skill_BuffSelfDuration and the like
# are time-limited buffs and do NOT belong here.
PASSIVE_CLASSES = ("Passive", "Toggled", "Aura")
# WPAttack = weapon pool skill: triggers on default attacks [Wiki, Guide Val]
AUTOMATIC_CLASSES = ("OnHit", "OnAttack", "Proc", "Contageous", "WPAttack")


def _raw_at(val, level):
    """Value at a rank without type checks (the .35 wirkdauer variant)."""
    if isinstance(val, list):
        if not val:
            return None
        return val[min(max(level, 1), len(val)) - 1]
    return val


def duration_field(field: str) -> str | None:
    """offensiveTotalDamageReductionPercentMin -> ...PercentDurationMin"""
    return field[:-3] + "DurationMin" if field.endswith("Min") else None


def chance_field(field: str) -> str | None:
    return field[:-3] + "Chance" if field.endswith("Min") else None


def _meta(ctx, rec_name) -> dict:
    e = ctx.index.records.get(rec_name) if hasattr(ctx.index, "records") else None
    return (e or {}).get("m", {}) if e else {}


def with_buff(ctx, rec_name) -> list:
    """Record plus its buff/debuff record."""
    rec = ctx.record(rec_name)
    out = [rec] if rec else []
    buff = buff_record(rec, ctx.index) if rec else None
    if buff:
        out.append(buff)
    return out


def _values(recs, level) -> dict:
    out = {}
    for r in recs:
        for k, v in r.items():
            w = value_at(v, level)
            if w is not None and not k.startswith("_"):
                out[k] = w
    return out


def effect_sources(ctx) -> list:
    """Every source with its fields. 'gruppe' = the same effect in game (skill + modifier)."""
    out = []
    for slot, it in ctx.worn:
        iname = ctx.item_name(it)
        for key in ITEM_PART_KEYS:
            dbr = it.get(key)
            rec = ctx.record(dbr)
            if not rec:
                continue
            fields = {k: v for k, v in ((k, value_at(v, 1)) for k, v in rec.items()
                                        if not k.startswith("_")) if v is not None}
            out.append({"quelle": f"{iname} ({slot})", "gruppe": dbr, "art": "ausruestung",
                        "felder": fields, "klassen": [str(rec.get("Class", ""))], "slot": slot,
                        "schluessel": key})
            skill = rec.get("itemSkillName")
            if isinstance(skill, str) and skill:
                recs = with_buff(ctx, skill)
                out.append({"quelle": f"{ctx.name(skill)} (item skill of {iname})",
                            "gruppe": skill, "art": "item_skill", "felder": _values(recs, 1),
                            "klassen": [str(r.get("Class", "")) for r in recs], "slot": slot,
                            "autocast": bool(rec.get("itemSkillAutoController"))})
    for s in ctx.skills:
        if s.get("level", 0) < 1:
            continue
        rn = s["skill_name"]
        # potion modifiers act only while drinking - no build effect
        if "/potionmodifiers/" in rn or "container_" in rn:
            continue
        lvl = ctx.effective_level(rn)
        recs = with_buff(ctx, rn)
        if not recs:
            continue
        out.append({"quelle": f"{ctx.name(rn)} (rank {lvl})",
                    "gruppe": parent_skill(rn, ctx.index) or rn,
                    "art": "devotion" if "/devotion/" in rn else "skill",
                    "felder": _values(recs, lvl), "klassen": [str(r.get("Class", "")) for r in recs],
                    "record": rn, "wirkung": ctx.effect_kind(rn)})
    return out


def _result(share, duration, cooldown, basis, upper):
    return {"anteil": share, "dauer": duration, "abklingzeit": cooldown, "grundlage": basis,
            "obergrenze": upper}


def uptime(ctx, source: dict, field: str) -> dict:
    """Uptime of an effect source for one field: {"anteil": float|None, "dauer", "abklingzeit",
    "grundlage": text, "obergrenze": bool} (legacy keys)."""
    f = source.get("felder", {})
    df = duration_field(field)
    duration = f.get(df) if df else None
    classes = " ".join(source.get("klassen") or [])
    kind = source.get("art")
    schema = getattr(ctx.index, "schema", 1)

    # 1) gear (no item skill): on-hit effect or permanent
    if kind == "ausruestung":
        cf = chance_field(field)
        chance = f.get(cf) if cf else None
        if not duration:
            return _result(1.0, None, None, "permanent (gear)", False)
        text = (f"refreshed on every hit ({duration:g} s)" if not chance
                else f"{chance:g}% chance per hit, {duration:g} s")
        # Flat effects on a weapon apply only to hits of THAT weapon; on jewellery/armour to
        # both hands. Flat debuffs scale with the skill's Weapon Damage (pro rata below 100 %).
        # [Crate forum, Stupid_Dragon 2020; Steam guide Val 2017]
        slot = source.get("slot", "")
        if slot in ("Main hand", "Off hand"):
            text += f"; {slot} hits only"
        text += "; value scales with the skill's Weapon Damage (pro rata below 100%)"
        return _result(1.0, duration, None, text + " - assumes you attack continuously", bool(chance))

    # 2) skills, devotions, item skills
    rn = source.get("record") or source.get("gruppe")
    if any(k in classes for k in PASSIVE_CLASSES) and "Attack" not in classes:
        return _result(1.0, duration, None, "aura/passive/toggle - always active", False)
    if schema < 2:
        return _result(None, duration, None, "cooldown/trigger unknown - index without timing fields "
                                             "(rebuild with GD-Tools.bat)", False)

    # celestial power: triggers only through the bound skill [Crate forum
    # "Chance on Attack Question" 2020]
    if kind == "devotion" and rn and rn.endswith("_skill.dbr"):
        binding = ctx.bindings.get(rn)
        if not binding:
            return _result(0.0, duration, None, "celestial power not bound - never triggers", False)
        target = binding[0]
        target_rec = ctx.index.record(target) if hasattr(ctx.index, "record") else None
        target_cls = str((target_rec or {}).get("Class", ""))
        if "SpawnPet" in target_cls:
            return _result(None, duration, None, "bound to a pet summon - the pet triggers it, "
                                                 "frequency depends on the pet", False)
        if target not in ctx.hotbar and not any(k in target_cls for k in PASSIVE_CLASSES + AUTOMATIC_CLASSES):
            return _result(0.0, duration, None, "bound to a skill that is not used", False)

    # active skill: must be usable
    usable = True
    if kind == "skill" and rn and "/devotion/" not in rn:
        bound = {z for z, _ in ctx.bindings.values()}
        # modifiers act through their base skill (gruppe): on the hotbar = in use
        parent = source.get("gruppe")
        usable = rn in ctx.hotbar or rn in bound \
            or (parent and (parent in ctx.hotbar or parent in bound)) \
            or any(k in classes for k in AUTOMATIC_CLASSES)
    if kind == "item_skill":
        # item skills: automatic (controller on the item) or from the hotbar
        usable = source.get("autocast") or source.get("gruppe") in ctx.hotbar \
            or any(k in classes for k in AUTOMATIC_CLASSES)
    if not usable:
        return _result(0.0, duration, None, "active skill, neither on the hotbar nor auto-triggered", False)

    m = _meta(ctx, source.get("gruppe") or rn)
    rank = ctx.effective_level(rn) if (rn and kind != "item_skill") else 1
    cd = _raw_at(m.get("skillCooldownTime"), rank)
    if not duration:
        duration = _raw_at(m.get("skillActiveDuration"), rank)
    if not duration:
        return _result(None, None, cd, "duration unknown", False)
    if not cd:
        return _result(1.0, duration, 0, f"no cooldown, {duration:g} s per use - assumes continuous use", True)
    cdr = min(max(ctx.sheet.get("skillCooldownReduction", 0), 0), 100) / 100
    cd_eff = cd * (1 - cdr)
    share = min(1.0, duration / cd_eff) if cd_eff > 0 else 1.0
    text = f"{duration:g} s duration / {cd_eff:.1f} s cooldown"
    if cdr:
        text += f" (base {cd:g} s, -{cdr*100:.0f}% cooldown)"
    return _result(share, duration, cd_eff, text + " - upper bound: recast immediately", True)


def expected_value(entries: list) -> float | None:
    """[(value, uptime), ...] of non-stacking sources -> mean effect."""
    if any(u is None for _, u in entries):
        return None
    rest, total = 1.0, 0.0
    for v, u in sorted(entries, key=lambda x: -x[0]):
        total += v * u * rest
        rest *= (1 - u)
    return total


def gap_share(entries: list, index: int) -> float | None:
    """Share of time in which source `index` (in descending order) is the strongest ACTIVE one."""
    ordered = sorted(entries, key=lambda x: -x[0])
    if any(u is None for _, u in ordered):
        return None
    rest = 1.0
    for j, (_, u) in enumerate(ordered):
        if j == index:
            return u * rest
        rest *= (1 - u)
    return None
