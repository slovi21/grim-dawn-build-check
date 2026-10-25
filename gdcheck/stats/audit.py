"""Stacking audit (AP4.4, X-004): which duplicate or competing sources count how.

Classes: STACKS, INDEPENDENT, STRONGEST ONLY, CURRENTLY WASTED, DEAD.
Replaces the .35 rules A6 (speed above cap), A7 (shield values without a
shield), B1 (non-stacking 'Reduced' effects) and part of A2 (+skills on unused
skills).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..data.text import short
from ..domain.enums import ON_SHEET, Activity, SourceKind
from ..domain.loader import NON_STACKING

REDUCED_NAMES = {
    "offensiveTotalDamageReductionPercentMin": "Reduced target's Damage",
    "offensiveTotalResistanceReductionPercentMin": "Reduced target's Resistances (%)",
    "offensiveTotalResistanceReductionAbsoluteMin": "Reduced target's Resistances (flat)",
    "offensiveSlowDefensiveAbilityMin": "Reduced target's Defensive Ability",
    "offensiveSlowDefensiveReductionMin": "Reduced target's Defensive Ability (%)",
    "offensiveSlowOffensiveAbilityMin": "Reduced target's Offensive Ability",
    "offensiveSlowOffensiveReductionMin": "Reduced target's Offensive Ability (%)",
    "offensivePhysicalResistanceReductionPercentMin": "Reduced Physical Resistance (%)",
    "offensivePhysicalResistanceReductionAbsoluteMin": "Reduced Physical Resistance (flat)",
    "offensiveElementalResistanceReductionPercentMin": "Reduced Elemental Resistances (%)",
    "offensiveElementalResistanceReductionAbsoluteMin": "Reduced Elemental Resistances (flat)",
    "offensiveTotalDamageReductionAbsoluteMin": "Reduced target's Damage (flat)",
}
SHIELD_FIELDS = ("defensiveBlockModifier", "defensiveBlockAmountModifier", "defensiveBlockChance",
                 "defensiveBlock", "blockAbsorption", "defensiveBlockRecoveryReduction")
SPEED_NAMES = {"attack": ("Attack Speed", "characterAttackSpeedModifier"),
               "cast": ("Casting Speed", "characterSpellCastSpeedModifier"),
               "run": ("Movement Speed", "characterRunSpeedModifier")}


@dataclass(frozen=True)
class AuditRow:
    cls: str            # STACKS / INDEPENDENT / STRONGEST ONLY / CURRENTLY WASTED / DEAD
    subject: str        # what it is about
    sources: tuple      # readable source labels
    effect: str         # what results
    source_ids: tuple = ()


def _label(sources, sid):
    s = sources.source(sid)
    return s.label if s else sid


def audit(ctx, sources, snap) -> list[AuditRow]:
    idx = ctx.index
    rows: list[AuditRow] = []
    points = ctx.character.skills

    # STACKS: the same modifier record from several items on one skill
    by_mod: dict = {}
    for m in sources.modifiers:
        by_mod.setdefault((m.target_skill, m.modifier_record), []).append(m)
    for (target, _mod), ms in sorted(by_mod.items()):
        if len(ms) < 2:
            continue
        parts = []
        wd = sum(m.wd_pct for m in ms)
        if wd:
            parts.append(f"+{wd:g}% weapon damage")
        combo = sum(m.combo_level for m in ms)
        if combo:
            parts.append(f"{combo:g} Onslaught stacks")
        f_all: dict = {}
        for m in ms:
            for k, v in m.fields:
                if not k.startswith("skillCombo"):
                    f_all[k] = f_all.get(k, 0.0) + v
        for k, v in sorted(f_all.items()):
            if k == "characterAttackSpeedModifier":
                parts.append(f"+{v:g}% Attack Speed")
        if any(m.conversions for m in ms):
            parts.append("conversion (pro rata, max 100%)")
        rows.append(AuditRow("STACKS", f"{len(ms)}x {idx.display(target) or short(target)} modifier",
                             tuple(_label(sources, m.source_id) for m in ms),
                             ", ".join(parts) or "values add up", tuple(m.source_id for m in ms)))

    # INDEPENDENT: the same proc from several items
    by_proc: dict = {}
    for p in sources.procs:
        if p.bound_to is None:
            by_proc.setdefault(p.skill_record, []).append(p)
    for rec, ps in sorted(by_proc.items()):
        if len(ps) < 2:
            continue
        rows.append(AuditRow("INDEPENDENT", f"{len(ps)}x {idx.display(rec) or short(rec)}",
                             tuple(p.label for p in ps),
                             f"{len(ps)} separate rolls ({ps[0].chance * 100:g}% each"
                             + (f", {ps[0].cooldown:g} s cooldown each)" if ps[0].cooldown else ")"),
                             tuple(p.id for p in ps)))

    # STRONGEST ONLY: 'Reduced' effects from several sources (gear, skills, procs)
    from ..domain.fields import leveled_fields
    by_key: dict = {}
    for e in sources.effects:
        if e.key in NON_STACKING and e.activity != Activity.WEAPON_CONDITION_UNMET and e.value:
            src = sources.source(e.source_id)
            rec = src.record_id if src else e.source_id
            by_key.setdefault(e.key, {}).setdefault(rec, []).append((abs(e.value), _label(sources, e.source_id),
                                                                      e.source_id))
    granted = [(p.skill_record, p.level, p.label, p.id) for p in sources.procs] + \
        [(g.skill_record, g.level, g.label, g.id) for g in sources.granted]
    for rec_name, lvl, label, gid in granted:
        rec = idx.record(rec_name) or {}
        from ..domain.activity import buff_record
        for r in (rec, buff_record(rec, idx) or {}):
            for k, v in leveled_fields(r, lvl).items():
                if k in NON_STACKING and v:
                    by_key.setdefault(k, {}).setdefault(rec_name, []).append((abs(v), label, gid))
    for key, groups in sorted(by_key.items()):
        if len(groups) < 2:
            continue
        best = sorted((max(vs)[0] for vs in groups.values()), reverse=True)
        labels = tuple(sorted({lab for vs in groups.values() for _, lab, _ in vs}))
        rows.append(AuditRow("STRONGEST ONLY", REDUCED_NAMES.get(key, key), labels,
                             " / ".join(f"{v:g}" for v in best) + f" - {best[0]:g} counts",
                             tuple(sorted({sid for vs in groups.values() for _, _, sid in vs}))))

    # CURRENTLY WASTED: speed above the cap
    for kind, sp in snap.combat.speeds.items():
        if sp["excess"] > 0.5:
            name, key = SPEED_NAMES[kind]
            srcs = tuple(sorted({_label(sources, sid) for sid, v in snap.combat.provenance.get(key, []) if v > 0}))
            rows.append(AuditRow("CURRENTLY WASTED", f"{name} above cap",
                                 srcs[:6], (f"{sp['raw']:.0f}% x {sp.get('weapon_factor', 1.0):.2f} (weapon speed) = "
                                            f"{sp['raw'] * min(sp.get('weapon_factor', 1.0), 1.0):.0f}%"
                                            if sp.get("weapon_factor", 1.0) < 1.0 else f"{sp['raw']:.0f}%")
                                 + f" vs cap {sp['cap']:.0f}% - {sp['excess']:.0f}% wasted"))

    # DEAD: +skill bonuses on skills without points / foreign masteries. Bonuses on
    # changeable parts (affix, component, augment, ascended) one row each; fixed item
    # stats and item modifiers on unused skills are summed up in one row (.35 rule A2).
    my_masteries = {f"records/skills/playerclass{n:02d}/_classtraining_class{n:02d}.dbr"
                    for n, _, _ in ctx.character.masteries}
    dead_skill: dict = {}
    fixed: list = []
    for b in sources.skill_bonuses:
        dead = (b.kind == "skill" and b.target and points.get(b.target, 0) < 1) or \
            (b.kind == "mastery" and b.target and b.target not in my_masteries)
        if not dead:
            continue
        src = sources.source(b.source_id)
        if src is not None and src.kind == SourceKind.ITEM:
            fixed.append(f"+{b.levels} {idx.display(b.target) or short(b.target)} ({src.label})")
        else:
            dead_skill.setdefault(b.target, []).append(b)
    for target, bs in sorted(dead_skill.items()):
        total = sum(b.levels for b in bs)
        rows.append(AuditRow("DEAD", f"+{total} {idx.display(target) or short(target)}",
                             tuple(sorted({_label(sources, b.source_id) for b in bs})),
                             "no points in this skill - the bonus does nothing"
                             + (f" ({len(bs)}x)" if len(bs) > 1 else ""),
                             tuple(b.source_id for b in bs)))
    for m in sources.modifiers:
        tgt = next((s for s in sources.skills if s.record == m.target_skill), None)
        if (tgt is None or (tgt.points < 1 and not tgt.form)) and points.get(m.target_skill, 0) < 1:
            src = sources.source(m.source_id)
            fixed.append(f"{idx.display(m.target_skill) or short(m.target_skill)} modifier "
                         f"({src.label if src else m.source_id})")
    if fixed:
        rows.append(AuditRow("DEAD", f"{len(fixed)} fixed item bonuses for skills you do not use",
                             tuple(sorted(fixed)), "fixed item stats - only matters if an equal "
                             "replacement is available"))
    # DEAD: shield values without a shield
    has_shield = any("Shield" in t for t in snap.weapon_types)
    if not has_shield:
        sh = [e for e in sources.effects if e.key in SHIELD_FIELDS and e.activity in ON_SHEET and e.value > 0]
        if sh:
            rows.append(AuditRow("DEAD", "Shield values without a shield",
                                 tuple(sorted({_label(sources, e.source_id) for e in sh})),
                                 "only work with a shield in the off hand",
                                 tuple(sorted({e.source_id for e in sh}))))
    # DEAD: devotion bonuses with an unmet weapon condition
    wc = sorted({e.source_id for e in sources.effects if e.activity == Activity.WEAPON_CONDITION_UNMET})
    if wc:
        rows.append(AuditRow("DEAD", "Devotion with a weapon condition",
                             tuple(_label(sources, s) for s in wc), "condition not met by the active weapons",
                             tuple(wc)))
    order = {"STACKS": 0, "INDEPENDENT": 1, "STRONGEST ONLY": 2, "CURRENTLY WASTED": 3, "DEAD": 4}
    rows.sort(key=lambda r: (order.get(r.cls, 9), r.subject))
    return rows
