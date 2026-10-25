"""Build design report (.67): sections B1-B8 of a checked build.json as chat Markdown (English; Claude
translates only the free text around it).

    B1 Summary            B5 Devotions (build order) and celestial powers
    B2 Findings           B6 Damage: conversion and resistance reduction
    B3 Skills and ranks   B7 Attribute points
    B4 Item sets          B8 Open points (every ASSUMED row, data the game database does not have)
"""
from __future__ import annotations

STATUS_ORDER = {"ERROR": 0, "WARN": 1, "INFO": 2, "OK": 3}
NOT_IN_DATABASE = (
    "quest skill and attribute points (community count, ruleset 'design')",
    "item attribute requirements (not in the index)",
    "how often a host skill is really used in a fight (proc rates need the frame model of a save check)",
    "skills granted by items (itemSkillName) - not part of the conversion and RR tables",
)


def _cell(t) -> str:
    return str(t).replace("|", "/").replace("\n", " ")


def _pct(p: float) -> str:
    """A conversion percentage: 25 -> '25', 10/3 -> '3.3'."""
    return f"{round(p, 1):g}"


def _table(key: str, title: str, cols, rows, notes=()) -> list[str]:
    out = [f"#### {key} {title}", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows] or ["| " + " | ".join("-" for _ in cols) + " |"]
    out += [f"\n_{n}_" for n in notes]
    out.append("")
    return out


def _last(v, n: int):
    """Value of a per-piece array for n pieces (beyond the array the last value counts)."""
    if not isinstance(v, list):
        return v or 0
    return v[min(n, len(v)) - 1] if v and n > 0 else 0


def _aff(a: dict) -> str:
    return ", ".join(f"{k[:3]} {v}" for k, v in sorted(a.items())) or "-"


def summary(d, cat) -> list[str]:
    s = d.spec
    n = {k: sum(1 for f in d.findings if f.status == k) for k in STATUS_ORDER}
    sp, lv, qu = d.budgets.get("skill", (0, 0, 0))
    ap, alv, aqu = d.budgets.get("attribute", (0, 0, 0))
    dev = sum(cat.constellations()[k]["points"] for k in d.final_devotions)
    rows = [("Build", s.name), ("Masteries", ", ".join(f"{s.mastery_names[m]} {b}" for m, b in s.masteries.items())),
            ("Level / damage type", f"{s.level} / {s.damage_type or '-'}"),
            ("Main skill", cat.display(s.main_skill) if s.main_skill else "-"),
            ("Weapons", ", ".join(s.weapons) + (" (dual wield)" if s.dual_wield else "") if s.weapons else "-"),
            ("Points", f"skills {sp}/{lv} + up to {qu} (quests), attributes {ap}/{alv} + up to {aqu} (quests), devotions {dev}/"
                       f"{d.budgets.get('devotion', (0, 55, 0))[1]}"),
            ("Result", f"{n['ERROR']} ERROR, {n['WARN']} WARN, {n['INFO']} INFO, {n['OK']} OK")]
    if d.data_note:
        rows.append(("Game data", d.data_note))
    return _table("B1", "Summary", ("Item", "Value"), rows)


def findings(d) -> list[str]:
    rows = [(f.status if f.status not in ("ERROR", "WARN") else f"**{f.status}**", f.rule, f.text, f.basis, f.confidence)
            for f in sorted(d.findings, key=lambda f: STATUS_ORDER.get(f.status, 9))]
    return _table("B2", "Findings", ("Status", "Rule", "Finding", "Basis", "Confidence"), rows)


def skills(d, cat) -> list[str]:
    rows = []
    for r, (pts, bonus, rank, ult) in d.ranks.items():
        if pts <= 0 and not rank:
            continue
        src = ", ".join(f"{n} +{v:g}" for n, v in d.bonus_sources.get(r, ()) if pts > 0)
        granted = pts == 0 and rank
        rows.append((cat.display(r), pts if not granted else "-", f"+{bonus:g}" if bonus else "-",
                     f"{rank} / {ult or '-'}", "rank of the form" if granted else (src or "-")))
    return _table("B3", "Skills and ranks", ("Skill", "Points", "Bonus", "Rank / ultimate", "Bonus from"), rows,
                  ("Bonuses count only with at least one point; transmuters stay at rank 1.",))


def item_sets(d, cat) -> list[str]:
    rows = []
    for st, n in d.set_pieces.items():
        s = cat.sets.get(st) or {}
        act = []
        for k, v in (s.get("fields") or {}).items():
            val = _last(v, n)
            if val:
                act.append(f"{k} {val:g}")
        for e in s.get("mastery") or []:
            if _last(e["ranks"], n):
                act.append(f"+{_last(e['ranks'], n):g} to all {cat.display(e['mastery'])} skills")
        act += [f"+{_last(e['ranks'], n):g} {cat.display(e['skill'])}" for e in s.get("skills") or [] if _last(e["ranks"], n)]
        for e in s.get("skill_modifiers") or []:
            if n >= e.get("pieces", 99):
                conv = ", ".join(f"{a}->{b} {p:g}%" for a, b, p in e.get("conversions") or [])
                extra = ", ".join(f"{k} {v:g}" for k, v in (e.get("modifier_fields") or {}).items()
                                  if not k.startswith("skillMax") and isinstance(v, (int, float)))
                act.append(f"{cat.display(e['modified'])}: " + "; ".join(x for x in (conv, extra) if x))
        rows.append((s.get("name", st), f"{n}/{len(s.get('members') or [])}", "; ".join(act) or "-"))
    return _table("B4", "Item sets", ("Set", "Pieces", "Active bonuses (game database fields)"), rows)


def devotions(d, cat) -> list[str]:
    rows = [(i, "+" if a == "+" else "-", name, pts, _aff(aff), "ok" if ok else f"**{why}**")
            for i, a, name, pts, aff, ok, why in d.devotion_steps]
    out = _table("B5", "Devotions - build order", ("Step", "Action", "Constellation", "Points", "Affinity after", "Check"),
                 rows, ([f"Proposed order: {', '.join(('-' if a == '-' else '') + n for a, n in d.proposed_order)}"]
                        if d.proposed_order else []))
    prow = []
    for b in d.bindings:
        trig = {"AttackEnemy": "on attack", "AttackEnemyCrit": "on crit", "LowHealth": "low health",
                "HitByEnemy": "when hit", "HitByMelee": "when hit (melee)", "HitByProjectile": "when hit (ranged)"}.get(
            b["trigger"], b["trigger"])
        cond = f"{trig} {b['chance']:g}%" + (f" at {b['param']:g}%" if b.get("param") else "")
        cd = f"{b['cooldown']:g} s" if b["cooldown"] == b.get("cooldown_level1", b["cooldown"]) else \
            f"{b['cooldown_level1']:g} -> {b['cooldown']:g} s (level 1 -> {b['power_level']})"
        hcd = f"{b['host_cd']:g} s" if b["host_cd"] else "-"
        if b.get("host_cd_changes"):
            hcd += " (" + ", ".join(f"{n} {v:+g}" for n, v in b["host_cd_changes"]) + ")"
        prow.append((b["power"], cond, cd, b["host"], hcd, b["note"] or "-"))
    out += _table("B5b", "Celestial powers", ("Power", "Trigger", "Cooldown", "Bound to", "Host cooldown", "Note"), prow)
    return out


def damage(d, cat) -> list[str]:
    rows = []
    for r, c in d.composition.items():
        fate = "; ".join(f"{t} -> " + ", ".join(f"{k} {v:.0%}" for k, v in sorted(f.items(), key=lambda x: -x[1]))
                         for t, f in c["fate"].items())
        src = "; ".join(f"{lab}: {a}->{b} {_pct(p)}% ({kind})" for lab, a, b, p, kind in c["sources"]) or "-"
        rows.append((cat.display(r), fate, src))
    out = _table("B6", "Damage after conversion", ("Skill", "Each damage type ends as", "Conversions (order: skill, then global)"),
                 rows, ("Amounts per type depend on weapon and gear - each type is followed on its own.",))
    rr = d.rr or {}
    rrows = [("-X% (stacks)", n, f"-{v:g}") for n, v in rr.get("minus", ())]
    rrows += [("X% Reduced (strongest)", n, f"{v:g}%") for n, v in rr.get("reduced", ())]
    rrows += [("Reduced by X (strongest)", n, f"-{v:g}") for n, v in rr.get("flat", ())]
    ex, exp = rr.get("examples") or {}, rr.get("examples_with_procs") or {}
    procs = any("celestial power" in n for n, _ in list(rr.get("minus", ())) + list(rr.get("reduced", ())) + list(rr.get("flat", ())))
    notes = []
    if ex:
        notes.append(("Enemy resistance after RR" + (", always on" if procs else "") + ": "
                      + ", ".join(f"{k}% -> {v:g}%" for k, v in ex.items())))
    if ex and procs:
        notes.append("Enemy resistance after RR while celestial powers are up: "
                     + ", ".join(f"{k}% -> {v:g}%" for k, v in exp.items()))
        notes.append("Sources marked 'celestial power' count only while the power is up (trigger chance and cooldown "
                     "apply) - the first line leaves them out.")
    out += _table("B6b", f"{d.spec.damage_type} resistance reduction", ("Type", "Source", "Value"), rrows, notes)
    return out


def attributes(d) -> list[str]:
    rows = [(a, f"+{h:g}", ab, f"+{e:g}" if e else "-", dmg) for a, h, ab, e, dmg in d.attribute_rows]
    return _table("B7", "Attribute points (one point, before % bonuses)", ("Attribute", "Health", "OA / DA", "Energy",
                                                                         "Damage"), rows)


def open_points(d) -> list[str]:
    rows = [(f.rule, f.text, f.confidence) for f in d.findings if f.confidence in ("ASSUMED", "ESTIMATE", "HEURISTIC")]
    rows += [("not in the game database", t, "-") for t in NOT_IN_DATABASE]
    return _table("B8", "Open points", ("Rule", "Point", "Confidence"), rows)


def render_md(d, cat) -> str:
    out = summary(d, cat) + findings(d) + skills(d, cat) + item_sets(d, cat) + devotions(d, cat) + damage(d, cat) \
        + attributes(d) + open_points(d)
    return "\n".join(out).rstrip() + "\n"


def to_json(d, cat) -> dict:
    """The checked design as plain data (findings with basis and confidence)."""
    return {"build": d.spec.name, "findings": [f.__dict__ for f in d.findings],
            "ranks": {cat.display(k): {"points": v[0], "bonus": v[1], "rank": v[2], "ultimate": v[3]}
                      for k, v in d.ranks.items()},
            "devotion_steps": [{"step": s[0], "action": s[1], "constellation": s[2], "points": s[3],
                                "affinity": s[4], "ok": s[5], "why": s[6]} for s in d.devotion_steps],
            "final_affinity": d.affinity, "bindings": d.bindings,
            "composition": {cat.display(k): v["fate"] for k, v in d.composition.items()},
            "rr": {k: v for k, v in (d.rr or {}).items()}, "budgets": d.budgets}
