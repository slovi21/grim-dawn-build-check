"""Fact tables for designing a build (.67): what the game database says about a set, the skills of a
mastery and the devotion constellations - looked up, never ranked."""
from __future__ import annotations

from .catalog import Catalog, match, pick


def _last(v, n: int):
    if not isinstance(v, list):
        return v or 0
    return v[min(n, len(v)) - 1] if v and n > 0 else 0


def set_table(cat: Catalog, name: str) -> tuple[str, list, list]:
    """(title, columns, rows) of one item set by set name or member item name."""
    opts = {k: v.get("name", k) for k, v in cat.sets.items()}
    key, cand = pick(name, opts)
    if key is None:
        items = cat.items()
        it, _c, _how = match(name, items, lambda cs: max(cs, key=lambda c: (cat.item_level(c), c)))
        key = cat.item_set(it) if it else None
    if key is None or key not in cat.sets:
        return f"no set '{name}'" + (f" (did you mean: {', '.join(cand)})" if cand else ""), [], []
    s = cat.sets[key]
    rows = []
    for m in s.get("members") or []:
        r = cat.rec(m)
        aug = [f"+{r.get(f'augmentSkillLevel{i}'):g} {cat.display(r.get(f'augmentSkillName{i}'))}"
               for i in range(1, 6) if r.get(f"augmentSkillName{i}") and r.get(f"augmentSkillLevel{i}")]
        mod = [f"modifies {cat.display(r.get(f'modifiedSkillName{i}'))}" for i in range(1, 6)
               if r.get(f"modifiedSkillName{i}")]
        rows.append(("item", cat.display(m), f"level {r.get('levelRequirement', '?')}", "; ".join(aug + mod) or "-"))
    n_max = len(s.get("members") or [])
    for n in range(2, n_max + 1):
        act = [f"{k} {_last(v, n):g}" for k, v in (s.get("fields") or {}).items()
               if _last(v, n) and _last(v, n) != _last(v, n - 1)]
        act += [f"+{_last(e['ranks'], n):g} to all {cat.display(e['mastery'])} skills" for e in s.get("mastery") or []
                if _last(e["ranks"], n) and _last(e["ranks"], n) != _last(e["ranks"], n - 1)]
        act += [f"+{_last(e['ranks'], n):g} {cat.display(e['skill'])}" for e in s.get("skills") or []
                if _last(e["ranks"], n) and _last(e["ranks"], n) != _last(e["ranks"], n - 1)]
        for e in s.get("skill_modifiers") or []:
            if e.get("pieces") == n:
                conv = ", ".join(f"{a}->{b} {p:g}%" for a, b, p in e.get("conversions") or [])
                extra = ", ".join(f"{k} {v:g}" for k, v in (e.get("modifier_fields") or {}).items()
                                  if not k.startswith("skillMax") and isinstance(v, (int, float)))
                act.append(f"{cat.display(e['modified'])}: " + "; ".join(x for x in (conv, extra) if x))
        if act:
            rows.append(("bonus", f"{n} pieces (new)", "", "; ".join(act)))
    return f"Item set {s.get('name', key)}", ["Kind", "Name", "Level", "Effect"], rows


def skills_table(cat: Catalog, mastery: str) -> tuple[str, list, list]:
    """(title, columns, rows) of every skill of a mastery: tier, max/ultimate, base skill, type facts."""
    ms = cat.masteries()
    rec, cand = pick(mastery, {v: k for k, v in ms.items()})
    if rec is None:
        return f"no mastery '{mastery}' (known: {', '.join(sorted(ms))})", [], []
    tiers = cat.tier_levels()
    rows = []
    for r in sorted(cat.tree_skills(rec), key=lambda r: (int(cat.meta(r).get("skillTier") or 0), cat.display(r))):
        m = cat.meta(r)
        f = cat.skills.get(r) or {}
        tier = int(m.get("skillTier") or f.get("tier") or 0)
        parents = cat.parents(r)[0]
        notes = []
        if f.get("exclusive"):
            notes.append("exclusive")
        if f.get("conversions"):
            notes.append(", ".join(f"{a}->{b} {p:g}%" for a, b, p in f["conversions"]))
        if f.get("sub_skill"):
            notes.append(f"changes only {cat.display(f['sub_skill'])}")
        if f.get("granted"):
            notes.append("grants " + ", ".join(cat.display(g) for g in f["granted"]))
        if m.get("skillCooldownTime"):
            cd = m["skillCooldownTime"]
            notes.append(f"cooldown {cd[0] if isinstance(cd, list) else cd:g} s")
        rows.append((cat.display(r), tier, tiers[tier - 1] if tier and tier <= len(tiers) else "-",
                     f"{m.get('skillMaxLevel', '-')}/{m.get('skillUltimateLevel', '-')}",
                     " or ".join(cat.display(p) for p in parents) or "-", (cat.template(r) or "-").replace("skill_", ""),
                     "; ".join(notes) or "-"))
    return (f"Skills of {cat.display(rec)}", ["Skill", "Tier", "Bar", "Max/ult", "Needs", "Type", "Notes"], rows)


def devotions_table(cat: Catalog, weapons=()) -> tuple[str, list, list]:
    """(title, columns, rows) of every constellation: points, need, gives, weapon need, power facts."""
    rows = []
    for k, c in sorted(cat.constellations().items(), key=lambda x: (x[1]["points"], x[1]["name"])):
        need = cat.weapon_needs(k)
        wn = " or ".join(need) if need else "-"
        if need and weapons and not set(need) & set(weapons):
            wn = f"**{wn} (not yours)**"
        pw = cat.powers.get(c["power"]) if c["power"] else None
        pt = (f"{cat.display(c['power'])}: {pw['trigger']} {pw['chance']:g}%, cd "
              f"{pw['cooldown'][0] if isinstance(pw['cooldown'], list) else pw['cooldown']:g} s") if pw else "-"
        rows.append((c["name"], c["points"], ", ".join(f"{a} {n}" for a, n in sorted(c["needs"].items())) or "-",
                     ", ".join(f"{a} {n}" for a, n in sorted(c["gives"].items())) or "-", wn, pt))
    return "Devotion constellations", ["Constellation", "Points", "Needs", "Gives", "Weapon", "Celestial power"], rows
