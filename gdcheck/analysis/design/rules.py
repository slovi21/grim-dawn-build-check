"""Design lint (.67): every rule checks one claim of a planned build against the game database.

Each rule is registered in mechanics.policy (name, confidence, source). Findings carry a status
(ERROR = the build does not work like that, WARN = works but loses something, OK, INFO), a basis
(fact = game database / official guide, community = forum or tests) and a confidence. The rules came
from the review of the Cold Werewolf Reaver design (03.10.2026): Hydra needs a ranged weapon, Blight of
Ch'thon (Pierce -> Chaos) and the Fenris set (Pierce -> Cold) are both skill conversions of Feral Claws and
share the Pierce damage pro rata, 50/50 (not "Blight first"), Blizzard's cooldown is longer than Ring of Steel's,
type-2 resistance reduction only works on positive resistance, Voracity changes only Feral Claws."""
from __future__ import annotations

from dataclasses import dataclass, field

from ...data.design_data import conversions as record_conversions
from ...mechanics import conversion as CV
from ...mechanics import resist as RS
from ...mechanics.policy import Confidence, rule
from ...stats import damage_mods as DM
from . import devotions as DV
from .catalog import SLOT_LIMIT, TWO_HANDED, Catalog
from .spec import Resolved

ELEMENTAL = ("Fire", "Cold", "Lightning")
FIELD_TYPE = {"Physical": "Physical", "Pierce": "Pierce", "Fire": "Fire", "Cold": "Cold", "Lightning": "Lightning",
              "Poison": "Acid", "Life": "Vitality", "Aether": "Aether", "Chaos": "Chaos"}
TYPE_FIELD = {v: k for k, v in FIELD_TYPE.items()}
FORM_TEMPLATES = ("skill_shapeshift", "skill_buffselftoggled", "skill_buffradiustoggled")


@dataclass(frozen=True)
class Finding:
    rule: str
    status: str             # ERROR | WARN | OK | INFO
    text: str
    basis: str = "fact"     # fact | community
    confidence: str = "EXACT"


@dataclass
class Design:
    spec: Resolved
    findings: list = field(default_factory=list)
    ranks: dict = field(default_factory=dict)          # skill record -> (points, bonus, rank, ultimate)
    bonus_sources: dict = field(default_factory=dict)  # skill record -> [(source, n)]
    set_pieces: dict = field(default_factory=dict)     # set record -> pieces equipped
    devotion_steps: list = field(default_factory=list) # (step, action, name, points, affinity, ok, why)
    final_devotions: list = field(default_factory=list)  # what the steps really place (stops at a failed step)
    target_devotions: list = field(default_factory=list)  # the final set the steps describe (add/refund replayed)
    order_ok: bool = True                              # False: the step order is invalid (one ERROR, rest on target)
    affinity: dict = field(default_factory=dict)
    proposed_order: list | None = None
    bindings: list = field(default_factory=list)       # dicts per power
    composition: dict = field(default_factory=dict)    # skill record -> {type: share}
    rr: dict = field(default_factory=dict)             # {"minus": [(src, v)], "reduced": [...], "flat": [...]}
    budgets: dict = field(default_factory=dict)
    attribute_rows: list = field(default_factory=list) # (attribute, health, DA/OA, energy, damage text)
    data_note: str = ""                                # design-data file used / not matching this game data

    def add(self, *a, **k):
        self.findings.append(Finding(*a, **k))

    def devotions_in_use(self) -> list:
        """The constellations the other rules work with: the placed ones; with an invalid order the target set."""
        return self.final_devotions if self.order_ok else self.target_devotions


# ------------------------------------------------------------------------------------------ helpers
def _list(v):
    return v if isinstance(v, list) else ([v] if v not in (None, "") else [])


def _at(v, rank):
    if isinstance(v, list):
        return v[min(max(rank, 1), len(v)) - 1] if v else 0.0
    return v or 0.0


def item_bonuses(cat: Catalog, items) -> tuple[dict, dict, float, dict]:
    """(skill bonus {rec: [(src, n)]}, mastery bonus {mastery: [(src, n)]}, all skills, set pieces)."""
    skill, mastery, allsk, pieces = {}, {}, 0.0, {}
    counted: set = set()
    for it in items:
        r = cat.rec(it)
        name = cat.display(it)
        for i in range(1, 6):
            s, n = r.get(f"augmentSkillName{i}"), r.get(f"augmentSkillLevel{i}")
            if s and n:
                skill.setdefault(str(s), []).append((name, float(n)))
            m, n = r.get(f"augmentMasteryName{i}"), r.get(f"augmentMasteryLevel{i}")
            if m and n:
                mastery.setdefault(cat.mastery_of(m), []).append((name, float(n)))
        allsk += float(r.get("augmentAllLevel") or 0)
        st = cat.item_set(it)
        if st and it not in counted:           # a second copy of a set item is no second piece
            counted.add(it)
            pieces[st] = pieces.get(st, 0) + 1
    for st, n in pieces.items():
        s = cat.sets.get(st) or {}
        for e in s.get("mastery") or []:
            v = _at(e["ranks"], n)                 # beyond the array the last value counts
            if v:
                mastery.setdefault(cat.mastery_of(e["mastery"]), []).append((f"set {s.get('name', st)} ({n} pieces)", float(v)))
        for e in s.get("skills") or []:
            v = _at(e["ranks"], n)
            if v:
                skill.setdefault(e["skill"], []).append((f"set {s.get('name', st)} ({n} pieces)", float(v)))
    return skill, mastery, allsk, pieces


# ------------------------------------------------------------------------------------------ rules
@rule("design_names", Confidence.EXACT, "game index: display names of masteries, skills, items, devotions")
def check_names(d: Design, cat: Catalog) -> None:
    for fld, msg in d.spec.problems:
        d.add("names", "ERROR", f"{fld}: {msg}")
    for fld, msg in d.spec.notes:
        d.add("names", "INFO", f"{fld}: {msg}")


@rule("design_items", Confidence.EXACT, "item classes of the game database: one item per slot, two rings, "
      "two hands (a two-handed weapon takes both)")
def check_items(d: Design, cat: Catalog) -> None:
    slots: dict = {}
    hands = 0
    for it in d.spec.items:
        slot, wtype = cat.item_slot(it)
        if slot == "weapon":
            hands += 2 if wtype in TWO_HANDED else 1
        elif slot != "other":
            slots.setdefault(slot, []).append(cat.display(it))
    for slot, names in slots.items():
        if len(names) > SLOT_LIMIT.get(slot, 1):
            d.add("items", "ERROR", f"{len(names)} items for the {slot} slot ({', '.join(names)}) - "
                  f"{SLOT_LIMIT.get(slot, 1)} fit")
    if hands > 2:
        d.add("items", "ERROR", f"weapons, shields and off-hands need {hands} hands - two exist")


@rule("design_skill_tree", Confidence.EXACT,
      "skill records (skillMaxLevel, skillTier, exclusiveSkill, skillDependancy) and gameengine.dbr "
      "skillMasteryTierLevel; base skill of a modifier from the skill window (DERIVED, checked on all masteries)")
def check_skill_tree(d: Design, cat: Catalog) -> None:
    s = d.spec
    tiers = cat.tier_levels()
    for m, bar in s.masteries.items():
        mx = int(cat.rec(m).get("skillMaxLevel") or 50)
        if bar > mx or bar < 1:
            d.add("skill_tree", "ERROR", f"{s.mastery_names[m]}: mastery bar {bar} (1-{mx})")
    exclusive = [r for r in s.skills if (cat.skills.get(r) or {}).get("exclusive") and s.skills[r] > 0]
    if len(exclusive) > 1:
        d.add("skill_tree", "WARN", f"exclusive skills {', '.join(cat.display(r) for r in exclusive)}: points in "
              "all of them are allowed, but only one can be active at a time")
    for r, pts in s.skills.items():
        meta = cat.meta(r)
        name = cat.display(r)
        mx = meta.get("skillMaxLevel")
        if mx and pts > int(mx):
            d.add("skill_tree", "ERROR", f"{name}: {pts} points, the maximum is {int(mx)}")
        tier = meta.get("skillTier") or (cat.skills.get(r) or {}).get("tier")
        mrec = next((m for m in s.masteries if cat.mastery_of(m) == cat.mastery_of(r)), None)
        if tier and tiers and mrec:
            need = tiers[min(int(tier), len(tiers)) - 1]
            if s.masteries[mrec] < need:
                d.add("skill_tree", "ERROR", f"{name}: tier {int(tier)} needs {s.mastery_names[mrec]} {need} "
                      f"(bar {s.masteries[mrec]})")
        parents, need_all, src = cat.parents(r)
        if parents and pts > 0:
            have = [p for p in parents if s.skills.get(p, 0) > 0]
            if (need_all and len(have) < len(parents)) or not have:
                d.add("skill_tree", "ERROR", f"{name} needs a point in "
                      + (" and " if need_all else " or ").join(cat.display(p) for p in parents),
                      confidence="EXACT" if src == "skillDependancy" else "DERIVED")


@rule("design_ranks", Confidence.TESTED,
      "rank = points + item/set skill bonuses, capped at skillUltimateLevel (game database); bonuses need "
      "at least one point in the skill (Game Guide, Skills); skills granted by a form use the form's rank "
      "(mechanics page, Cpt Wolf golden test)")
def check_ranks(d: Design, cat: Catalog) -> None:
    s = d.spec
    skill_b, mastery_b, allsk, pieces = item_bonuses(cat, s.items)
    d.set_pieces = pieces
    for r, pts in s.skills.items():
        src = list(skill_b.get(r, [])) + list(mastery_b.get(cat.mastery_of(r), []))
        if allsk:
            src.append(("+all skills", allsk))
        bonus = sum(n for _, n in src)
        meta = cat.meta(r)
        ult = int(meta.get("skillUltimateLevel") or meta.get("skillMaxLevel") or 0) or None
        raw = pts + bonus if pts > 0 else 0
        rank = min(raw, ult) if ult else raw
        d.ranks[r] = (pts, bonus if pts > 0 else 0, int(rank), ult)
        d.bonus_sources[r] = src
        if pts > 0 and ult and raw > ult and ult > 1:      # transmuters (ultimate 1) take no bonus anyway
            d.add("ranks", "WARN", f"{cat.display(r)}: {pts} + {bonus:g} = {raw:g} above the ultimate rank {ult} - "
                  f"{raw - ult:g} wasted")
    for r, src in skill_b.items():
        if s.skills.get(r, 0) <= 0 and r in cat.skills:
            d.add("ranks", "WARN", f"+{sum(n for _, n in src):g} {cat.display(r)} from "
                  f"{', '.join(n for n, _ in src)} has no effect without a point in {cat.display(r)}",
                  basis="community", confidence="TESTED")
    for r in list(s.skills):
        for g in cat.granted(r):
            pts, bonus, rank, ult = d.ranks.get(r, (0, 0, 0, None))
            d.ranks[g] = (0, 0, rank, ult)


QUEST_NOTE = "over all three difficulties (community count, unconfirmed)"


@rule("design_budget", Confidence.EXACT,
      "playerlevels.dbr: skillModifierPoints per level, characterModifierPoints, maxDevotionPoints; quest "
      "rewards from ruleset 'design' (community, ASSUMED, not bound to the level: up to about N over all "
      "three difficulties)")
def check_budget(d: Design, cat: Catalog, ruleset: dict) -> None:
    s = d.spec
    pl = cat.player_levels()
    per = list(pl.get("skillModifierPoints") or [])
    lvl = max(1, min(s.level, int(pl.get("maxPlayerLevel") or 100)))
    level_sp = int(pl.get("initialSkillPoints") or 0) + int(sum(per[:lvl - 1]))
    level_ap = int(pl.get("characterModifierPoints") or 1) * (lvl - 1)
    q = (ruleset or {}).get("design") or {}
    quest_sp, quest_ap = int(q.get("quest_skill_points") or 0), int(q.get("quest_attribute_points") or 0)
    used_sp = sum(s.masteries.values()) + sum(s.skills.values())
    used_ap = sum(s.attributes.values())
    d.budgets = {"skill": (used_sp, level_sp, quest_sp), "attribute": (used_ap, level_ap, quest_ap),
                 "devotion": (None, int(pl.get("maxDevotionPoints") or 55), 0)}
    for kind, used, lv, qu in (("skill", used_sp, level_sp, quest_sp), ("attribute", used_ap, level_ap, quest_ap)):
        up_to = f"up to about {qu} {QUEST_NOTE}"
        if used > lv + qu:
            d.add("budget", "ERROR", f"{used} {kind} points used - level {lvl} gives {lv}, quests {up_to} more",
                  basis="community" if qu else "fact", confidence="ASSUMED" if qu else "EXACT")
        elif used > lv:
            d.add("budget", "WARN", f"{used} {kind} points used - level {lvl} gives {lv}, so {used - lv} must come from "
                  f"quests - up to about {qu} {QUEST_NOTE}", basis="community", confidence="ASSUMED")
        else:
            d.add("budget", "OK", f"{used} {kind} points used of {lv} (level {lvl})" + (f" + {up_to} from quests" if qu else ""),
                  basis="community" if qu else "fact", confidence="ASSUMED" if qu else "EXACT")


@rule("design_devotion_order", Confidence.EXACT,
      "Crate Game Guide, Devotion: affinity only from complete constellations, a constellation is placed "
      "when the others cover its need, a refund must leave every constellation covered; "
      "playerlevels.dbr maxDevotionPoints; analysis/generators/devotions.py (order search)")
def check_devotions(d: Design, cat: Catalog) -> None:
    cons = cat.constellations()
    budget = d.budgets.get("devotion", (None, 55, 0))[1]
    placed: list = []
    ok_all = True
    for i, (action, key) in enumerate(d.spec.steps, 1):
        c = cons[key]
        why = ""
        if action == "+":
            have = DV.affinity_sum(placed, cons)
            if key in placed:
                why = "already placed"
            elif not DV.covered(c["needs"], have):
                short = {a: n - have.get(a, 0) for a, n in c["needs"].items() if have.get(a, 0) < n}
                why = "needs " + ", ".join(f"{a} {n} more" for a, n in sorted(short.items()))
            elif sum(cons[k]["points"] for k in placed) + c["points"] > budget:
                why = f"{sum(cons[k]['points'] for k in placed) + c['points']} points - more than {budget}"
            else:
                placed.append(key)
        else:
            if key not in placed:
                why = "not placed"
            else:
                rest = [k for k in placed if k != key]
                if not DV.state_valid(rest, cons):
                    why = "the others still need its affinity"
                else:
                    placed = rest
        points = sum(cons[k]["points"] for k in placed)
        ok_all = ok_all and not why
        d.devotion_steps.append((i, action, c["name"], points, DV.affinity_sum(placed, cons), not why, why))
    d.final_devotions = placed
    d.affinity = DV.affinity_sum(placed, cons)
    target: dict = {}
    for a, k in d.spec.steps:                     # the final set the steps describe
        if a == "+":
            target[k] = True
        else:
            target.pop(k, None)
    d.target_devotions = list(target)
    if not d.spec.steps:
        return
    if ok_all and DV.state_valid(placed, cons):
        d.add("devotion_order", "OK", f"{len(d.spec.steps)} steps valid, {sum(cons[k]['points'] for k in placed)} "
              f"of {budget} points, final affinity " + ", ".join(f"{a} {n}" for a, n in sorted(d.affinity.items())))
        return
    d.order_ok = False
    bad = [s for s in d.devotion_steps if not s[5]]
    if bad:                                       # exactly one ERROR: the first failed step is the cause
        st = bad[0]
        later = f" ({len(bad) - 1} later steps fail as a consequence)" if len(bad) > 1 else ""
        d.add("devotion_order", "ERROR", f"step {st[0]} {'+' if st[1] == '+' else '-'}{st[2]}: {st[6]}{later}")
    level = "INFO" if bad else "ERROR"            # without a failed step the final state itself is the error
    need = sum(cons[k]["points"] for k in target)
    if need > budget:
        d.add("devotion_order", level, f"the final constellations need {need} points - only {budget} exist")
        return
    order = DV.find_order(set(target), cons, budget)
    if order:
        d.proposed_order = [(a, cons[k]["name"]) for a, k, _v, _n in order]
        d.add("devotion_order", "INFO", "an order that works for the same final set: "
              + ", ".join(("-" if a == "-" else "") + n for a, n in d.proposed_order),
              confidence="DERIVED")
    else:
        d.add("devotion_order", level, "no build order found for this final set within the search limit "
              "(not proof that none exists)", confidence="DERIVED")


@rule("design_star_weapons", Confidence.EXACT,
      "devotion star records name the weapon types they need (Ranged1h, Axe2h, Shield, ...); without one "
      "the star gives nothing (Game Guide, Devotion; e.g. Hydra = ranged)")
def check_star_weapons(d: Design, cat: Catalog) -> None:
    for k in d.final_devotions:
        need = cat.weapon_needs(k)
        if not need:
            continue
        name = cat.constellations()[k]["name"]
        if not d.spec.weapons:
            d.add("star_weapons", "WARN", f"{name} needs {' or '.join(need)} - no weapons given in the build")
        elif not set(need) & set(d.spec.weapons):
            d.add("star_weapons", "ERROR", f"{name} needs {' or '.join(need)} - with {', '.join(d.spec.weapons)} "
                  "its stars give nothing (only the affinity counts)")


def _order_note(d: Design, rule: str, what: str) -> None:
    """With an invalid devotion order the rules that depend on the devotions work on the target set (the final
    set the steps describe) - one INFO says so instead of follow-up ERRORs."""
    if not d.order_ok:
        d.add(rule, "INFO", f"{what}: computed for the final set of the devotion steps (the steps that fail "
              "are not replayed) - follows from the invalid order above", confidence="DERIVED")


def _host_ok(cat: Catalog, power: dict, host: str) -> str:
    t = cat.template(host)
    if host in (power.get("blacklist") or []):
        return "on the black list of this power"
    if power.get("templates") and t not in power["templates"]:
        return f"skill type '{t}' cannot hold this power"
    return ""


def _item_modifiers(d: Design, cat: Catalog) -> list:
    """(modifier record, modified skill, label) of the items and of the active set bonuses."""
    out = []
    for it in d.spec.items:
        r = cat.rec(it)
        for i in range(1, 6):
            mod, target = r.get(f"modifierSkillName{i}"), r.get(f"modifiedSkillName{i}")
            if mod and target:
                out.append((str(mod), str(target), cat.display(it)))
    for st, n in d.set_pieces.items():
        for e in (cat.sets.get(st) or {}).get("skill_modifiers") or []:
            if n >= e.get("pieces", 99):
                out.append((e["modifier"], e["modified"], f"set {(cat.sets[st] or {}).get('name', st)}"))
    return out


def host_cooldown(d: Design, cat: Catalog, host: str) -> tuple[float, list]:
    """(cooldown at rank incl. learned modifiers/transmuters and item modifiers of the host, changes)."""
    rank = d.ranks.get(host, (0, 0, 0, None))[2]
    cd = float(_at(cat.meta(host).get("skillCooldownTime"), rank) or 0)
    changes = []
    for r, pts in d.spec.skills.items():
        if pts > 0 and r != host and host in cat.parents(r)[0]:
            delta = _at((cat.mods.get(r) or {}).get("fields", {}).get("skillCooldownTime"), d.ranks.get(r, (0, 0, 1, None))[2])
            if delta:
                changes.append((cat.display(r), float(delta)))
    for mod, target, label in _item_modifiers(d, cat):
        if target == host:
            delta = (cat.mods.get(mod) or {}).get("fields", {}).get("skillCooldownTime") or cat.rec(mod).get("skillCooldownTime")
            if delta:
                changes.append((label, float(_at(delta, 1))))
    cd = max(0.0, cd + sum(v for _, v in changes))      # a transmuter can give a host a cooldown of its own (A4)
    return cd, changes


def refreshers(d: Design, cat: Catalog, host: str) -> list:
    """(label, chance %, seconds, trigger) of every learned modifier/transmuter and item skill modifier that
    cuts the cooldown of `host` (refreshCooldown*, game database): the same source the combat check
    uses (combat.actions._cooldown_refresh), so Designer and .gdc check cannot disagree."""
    from types import SimpleNamespace as NS

    from ...combat import actions as ACT
    states = [NS(record=r, label=cat.display(r), points=pts, level=d.ranks.get(r, (0, 0, 1, None))[2])
              for r, pts in d.spec.skills.items() if pts > 0]
    mods = [NS(modifier_record=m, target_skill=t, source_id=label) for m, t, label in _item_modifiers(d, cat)
            if d.ranks.get(str(t), (0, 0, 0, None))[2] > 0]
    sources = NS(modifiers=mods, source=lambda sid: NS(label=sid))
    return [(e[0], e[1] * 100, e[2], e[3]) for e in ACT._cooldown_refresh(host, states, sources, cat.index,
                                                                         cat.cooldown_refresh())]


@rule("design_bindings", Confidence.EXACT,
      "celestial power records: templateAutoCast (triggerType, chanceToRun, triggerParam), skillCooldownTime, "
      "skillTemplates, skillBlackList; one power per skill (forum); cooldown comparison DERIVED")
def check_bindings(d: Design, cat: Catalog) -> None:
    cons = cat.constellations()
    final_powers = {cons[k]["power"]: k for k in d.devotions_in_use() if cons[k]["power"]}
    _order_note(d, "bindings", "celestial power bindings")
    hosts: dict = {}
    for p, host in d.spec.bindings.items():
        pw = cat.powers.get(p) or {}
        name = cat.display(p)
        rank = d.ranks.get(host, (0, 0, 0, None))[2]
        host_cd, changes = host_cooldown(d, cat, host)
        cds = pw.get("cooldown")
        cd_lo, cd_top = float(_at(cds, 1) or 0), float(_at(cds, 999) or 0)
        top = len(cds) if isinstance(cds, list) else 1
        row = {"power": name, "trigger": pw.get("trigger", "?"), "chance": pw.get("chance"), "param": pw.get("param"),
               "cooldown": cd_top, "cooldown_level1": cd_lo, "power_level": top, "host": cat.display(host),
               "host_cd": host_cd, "host_cd_changes": changes, "note": ""}
        d.bindings.append(row)
        if p not in final_powers:
            d.add("bindings", "ERROR", f"{name}: its constellation is not in the final devotions")
        if rank <= 0:
            d.add("bindings", "ERROR", f"{name} on {cat.display(host)}: the host has no point (never used)")
        why = _host_ok(cat, pw, host)
        if why:
            d.add("bindings", "ERROR", f"{name} on {cat.display(host)}: {why}")
        if host in hosts:
            d.add("bindings", "ERROR", f"{cat.display(host)} holds two powers ({hosts[host]}, {name}) - one per skill")
        hosts[host] = name
        if pw.get("trigger", "").startswith("Attack") and host_cd and cd_top and host_cd < cd_top:
            row["note"] = f"cooldown {cd_top:g} s > host cooldown {host_cd:g} s"
            lvl = f" at power level {top}" if isinstance(cds, list) and cd_lo != cd_top else ""
            d.add("bindings", "WARN", f"{name} ({cd_top:g} s cooldown{lvl}) on {cat.display(host)} "
                  f"({host_cd:g} s): casts in between cannot trigger it - a host with a longer cooldown or none "
                  "loses nothing", confidence="DERIVED")
        elif pw.get("trigger", "").startswith("Attack") and host_cd and cd_top:
            refs = refreshers(d, cat, host)
            if refs:
                row["note"] = "host cooldown is an upper bound (cut by " + ", ".join(sorted({x[0] for x in refs})) + ")"
            cut = [x for x in refs if host_cd - x[2] <= cd_top + 1e-6]
            if cut:
                label, chance, secs, trig = max(cut, key=lambda x: x[2])
                when = {"AttackEnemy": "on attack", "AttackEnemyCrit": "on crit"}.get(trig, trig)
                d.add("bindings", "WARN", f"{name} ({cd_top:g} s cooldown) on {cat.display(host)} ({host_cd:g} s): {label} "
                      f"({chance:g}% {when}) can cut the host cooldown by {secs:g} s to {round(host_cd - secs, 2):g} s - "
                      f"not longer than the power's cooldown, so some casts cannot trigger it (the host cooldown is "
                      "an upper bound)", confidence="DERIVED")
    for p, k in final_powers.items():
        if p not in d.spec.bindings:
            d.add("bindings", "WARN", f"{cat.display(p)} ({cons[k]['name']}) is not bound to a skill - it never triggers")


@rule("design_form", Confidence.EXACT,
      "Werewolf/Wereraven tooltips: in the form default attacks and weapon pool skills (Onslaught too) cannot "
      "be used; the form grants its own attacks (records grantedSkills)")
def check_form(d: Design, cat: Catalog) -> None:
    forms = [r for r, pts in d.spec.skills.items() if pts > 0 and cat.template(r) == "skill_shapeshift"]
    if not forms:
        return
    for r, pts in d.spec.skills.items():
        t = cat.template(r)
        if pts > 0 and (t.startswith(("skill_wpattack", "skill_weaponpool"))):
            d.add("form", "WARN", f"{cat.display(r)} is a weapon pool skill - it cannot be used in "
                  f"{', '.join(cat.display(f) for f in forms)} (only outside the form)")


def _damage_types(cat: Catalog, rec: str, rank: int) -> list:
    """Direct damage types a skill deals at a rank (weapon damage counts as Physical)."""
    m = cat.meta(rec)
    out = set()
    if m.get("weaponDamagePct"):
        out.add("Physical")
    for f, t in FIELD_TYPE.items():
        if _at(m.get(f"offensive{f}Min"), rank) or _at(m.get(f"offensive{f}Max"), rank):
            out.add(t)
    if _at(m.get("offensiveElementalMin"), rank):
        out |= set(ELEMENTAL)
    return sorted(out)


def _affected(src: str, fate: dict) -> list:
    """The damage types of a skill (keys of `fate`) a conversion from `src` acts on: Elemental stands for Fire,
    Cold and Lightning (B(a))."""
    return [t for t in (ELEMENTAL if src == "Elemental" else (src,)) if t in fate]


def conversion_rules(d: Design, cat: Catalog, rec: str):
    """(skill rules, global rules, sources) for one attack skill. Skill stage: modifiers/transmuters of the
    skill or of the form granting it and item/set modifiers of them (Game Guide). Global stage: gear and
    toggles; for a skill the form does not grant, the form's modifiers act globally while it is active
    (ASSUMED)."""
    s = d.spec
    owners = {rec} | {r for r in s.skills if rec in cat.granted(r)}
    skill_r, glob_r, src = [], [], []

    def add(rules, conv, label, kind):
        for a, b, p in conv:
            x, y = FIELD_TYPE.get(a, a), FIELD_TYPE.get(b, b)
            rules.append((x, y, p))
            src.append((label, x, y, p, kind + (", ASSUMED" if y == "Elemental" and "ASSUMED" not in kind else "")))
    for r, pts in s.skills.items():
        if pts <= 0:
            continue
        conv = (cat.mods.get(r) or {}).get("conversions") or (cat.skills.get(r) or {}).get("conversions") or []
        if not conv:
            continue
        parents = cat.parents(r)[0]
        sub = (cat.skills.get(r) or {}).get("sub_skill")
        if sub and sub != rec:
            continue
        if r in owners or set(parents) & owners or sub == rec:
            add(skill_r, conv, cat.display(r), "skill")
        elif cat.template(r) in FORM_TEMPLATES:
            add(glob_r, conv, cat.display(r), "global")
        elif any(cat.template(x) in FORM_TEMPLATES and s.skills.get(x, 0) > 0 for x in parents):
            add(glob_r, conv, cat.display(r) + f" (on {cat.display(parents[0])})", "global, ASSUMED")
    for mod, target, label in _item_modifiers(d, cat):
        conv = (cat.mods.get(mod) or {}).get("conversions") or []
        if not conv or s.skills.get(target, 0) <= 0 and not any(target in cat.granted(o) for o in s.skills):
            continue
        if target in owners or target == rec:
            add(skill_r, conv, label, "skill")
        elif cat.template(target) in FORM_TEMPLATES:
            add(glob_r, conv, label + f" (on {cat.display(target)})", "global, ASSUMED")
    for it in s.items:
        add(glob_r, record_conversions(cat.rec(it)), cat.display(it), "global")
    return skill_r, glob_r, src


def type_fate(types, skill_r, glob_r) -> dict:
    """{source type: {final type: share}} - where each damage type of a skill ends (amounts unknown, so
    every type is followed on its own)."""
    out = {}
    for t in types:
        res, _c = CV.convert({t: 1.0}, skill_r, glob_r)
        tot = sum(res.values()) or 1.0
        out[t] = {k: v / tot for k, v in res.items() if v > 1e-9}
    return out


@rule("design_conversion", Confidence.EXACT,
      "Game Guide Combat: base skill > skill modifiers > conversion on modifiers/transmuters > global "
      "conversion; >100% split pro rata (TESTED); modifiers on a form/toggle act globally (ASSUMED)")
def check_conversion(d: Design, cat: Catalog) -> None:
    s = d.spec
    if s.main_skill and "spawnpet" in cat.template(s.main_skill):
        d.add("conversion", "INFO", f"{cat.display(s.main_skill)} (main skill) is a pet skill: pets use their own "
              "conversion; celestial powers bound to it are triggered by the pet", confidence="DERIVED")
    target = s.damage_type
    if not target:
        return
    attacks = []
    for r, (pts, bonus, rank, ult) in d.ranks.items():
        if rank > 0 and _damage_types(cat, r, rank) and cat.template(r).startswith(("skill_attack", "skill_wpattack",
                                                                                     "skill_weaponpool")):
            attacks.append(r)
    if s.main_skill and s.main_skill not in attacks:
        attacks.insert(0, s.main_skill)
    for r in attacks:
        rank = d.ranks.get(r, (0, 0, 1, None))[2] or 1
        types = _damage_types(cat, r, rank)
        if not types:
            continue
        skill_r, glob_r, src = conversion_rules(d, cat, r)
        fate = type_fate(types, skill_r, glob_r)
        d.composition[r] = {"fate": fate, "sources": src}
        for label, a, b, p, kind in src:
            if b == target:
                continue
            for t in _affected(a, fate):
                sk = [x for x in skill_r if x != (a, b, p)] if kind.startswith("skill") else skill_r
                gl = [x for x in glob_r if x != (a, b, p)] if not kind.startswith("skill") else glob_r
                with_it = fate[t].get(target, 0.0)
                without = type_fate([t], sk, gl)[t].get(target, 0.0)
                if without > with_it + 0.01:
                    assumed = "ASSUMED" in kind
                    d.add("conversion", "WARN" if assumed else "ERROR",
                          f"{cat.display(r)}: {label} converts {a} to {b} - of its {t} damage {with_it:.0%} ends as "
                          f"{target} instead of {without:.0%}", basis="community" if assumed else "fact",
                          confidence="ASSUMED" if assumed else "EXACT")
        if r == s.main_skill:
            short = {t: f for t, f in fate.items() if f.get(target, 0.0) < 0.99}
            if short:
                d.add("conversion", "WARN", f"{cat.display(r)} (main skill): " + "; ".join(
                    f"{t} damage ends {', '.join(f'{k} {v:.0%}' for k, v in sorted(f.items(), key=lambda x: -x[1]))}"
                    for t, f in short.items()))
        if any("ASSUMED" in x[4] for x in src):
            d.add("conversion", "INFO", f"{cat.display(r)}: {', '.join(sorted({x[0] for x in src if 'ASSUMED' in x[4]}))} "
                  "counted as global while the form is active", basis="community", confidence="ASSUMED")


@rule("design_rr", Confidence.TESTED,
      "rr_chain (Crate forum 47174): '-X%' add up, 'X% Reduced' strongest only and only on positive "
      "resistance, 'Reduced by X' strongest only; sources from skill, item modifier, star and power records")
def check_rr(d: Design, cat: Catalog) -> None:
    s = d.spec
    t = s.damage_type
    if not t:
        return
    f = TYPE_FIELD.get(t, t)
    minus, reduced, flat = [], [], []

    def scan(rec_fields: dict, label: str, rank: int = 1):
        v = _at(rec_fields.get(f"defensive{f}"), rank)
        if t in ELEMENTAL:
            v += _at(rec_fields.get("defensiveElementalResistance"), rank)
        if v < 0:
            minus.append((label, -v))
        for key, lst in (("ReductionPercentMin", reduced), ("ReductionAbsoluteMin", flat)):
            vals = [_at(rec_fields.get(f"offensive{f}Resistance{key}"), rank),
                    _at(rec_fields.get(f"offensiveTotalResistance{key}"), rank)]
            if t in ELEMENTAL:
                vals.append(_at(rec_fields.get(f"offensiveElementalResistance{key}"), rank))
            if max(vals) > 0:
                lst.append((label, max(vals)))
    def debuff(r) -> bool:
        """An enemy debuff (its own or its buff record is SkillBuff_Debuf*/Contageous) or a modifier of one -
        negative resistances elsewhere are penalties on the player, not RR."""
        recs = [cat.rec(r), cat.rec((cat.skills.get(r) or {}).get("buff") or cat.rec(r).get("buffSkillName") or "")]
        if any(str(x.get("Class", "")).startswith(("SkillBuff_Debuf", "SkillBuff_Contageous")) for x in recs):
            return True
        return any(debuff(p) for p in cat.parents(r)[0] if p != r)
    for r, (pts, bonus, rank, ult) in d.ranks.items():
        if rank <= 0:
            continue
        m = cat.meta(r)
        if debuff(r):
            scan(m, f"{cat.display(r)} (rank {rank})", rank)
        else:
            scan({k: v for k, v in m.items() if "ResistanceReduction" in str(k)}, f"{cat.display(r)} (rank {rank})", rank)
    for mod, target, label in _item_modifiers(d, cat):
        if d.ranks.get(str(target), (0, 0, 0, None))[2] > 0:
            fields = {**cat.rec(mod), **((cat.mods.get(mod) or {}).get("fields") or {})}
            scan(fields, f"{label} (modifies {cat.display(target)})")
    for it in dict.fromkeys(s.items):              # the item's own RR (e.g. Mindwarp), once per record
        scan({k: v for k, v in cat.rec(it).items() if "ResistanceReduction" in str(k)}, f"{cat.display(it)} (item)")
    cons = cat.constellations()
    for k in d.devotions_in_use():
        need = cat.weapon_needs(k)
        if need and d.spec.weapons and not set(need) & set(d.spec.weapons):
            continue                               # stars without their weapon give nothing
        for star in cons[k]["stars"]:
            if star == cons[k]["power"]:
                if star in d.spec.bindings:
                    sr = cat.rec(star)
                    both = {**sr, **cat.rec(sr.get("buffSkillName") or "")}
                    top = max((len(v) for v in both.values() if isinstance(v, list)), default=1)
                    scan(both, f"{cat.display(star)} (celestial power at its top level {top}, proc)", top)
            else:
                scan(cat.rec(star), f"{cons[k]['name']} (star)")
    def always(lst):
        return [(n, v) for n, v in lst if "celestial power" not in n]

    def ends(sel, res):
        return RS.rr_chain(res, [v for _, v in sel(minus)], [v for _, v in sel(reduced)], [v for _, v in sel(flat)])
    d.rr = {"minus": minus, "reduced": reduced, "flat": flat,
            "examples": {res: ends(always, res) for res in (0, 30, 60, 100)},
            "examples_with_procs": {res: ends(lambda x: x, res) for res in (0, 30, 60, 100)}}
    if reduced and minus:
        perm = [(n, v) for n, v in minus if "celestial power" not in n]     # always up; procs only now and then
        proc = [(n, v) for n, v in minus if "celestial power" in n]
        s1 = sum(v for _, v in perm)
        extra = ""
        if proc:
            names = " and ".join(n.split(" (")[0].split(" - ")[-1] for n, _ in proc)
            if s1:
                extra = f" (+{sum(v for _, v in proc):g} while {names} is up)"
            else:                                  # type 1 comes from procs only
                s1, extra = sum(v for _, v in proc), f" (only while {names} is up)"
        d.add("rr", "WARN", f"'% Reduced' {t} resistance ({', '.join(n for n, _ in reduced)}) acts only on resistance "
              f"left after the -{s1:g}% of type 1{extra} - against enemies below {s1:g}% {t} resistance it adds nothing",
              basis="community", confidence="TESTED")
    _order_note(d, "rr", f"{t} RR")
    procs = [n for n, _ in minus + reduced + flat if "celestial power" in n]
    perm = (sum(v for _, v in always(minus)), max((v for _, v in always(reduced)), default=0),
            max((v for _, v in always(flat)), default=0))
    text = (f"{t} RR{' always on' if procs else ''}: -{perm[0]:g}% (stacking), {perm[1]:g}% reduced, {perm[2]:g} flat - "
            f"an enemy with 30% {t} resistance ends at {d.rr['examples'][30]:g}%")
    if procs:
        text += (f"; while celestial powers are up: -{sum(v for _, v in minus):g}%, "
                 f"{max((v for _, v in reduced), default=0):g}% reduced, {max((v for _, v in flat), default=0):g} flat - "
                 f"ends at {d.rr['examples_with_procs'][30]:g}%")
    d.add("rr", "INFO", text, basis="community", confidence="TESTED")


@rule("design_sub_skill", Confidence.ASSUMED,
      "subSkillName of a modifier names the one granted skill it changes (Voracity -> Feral Claws); "
      "not confirmed by a game test - the sheet never shows Voracity (KG-11)")
def check_sub_skill(d: Design, cat: Catalog) -> None:
    for r, pts in d.spec.skills.items():
        sub = (cat.skills.get(r) or {}).get("sub_skill")
        if pts > 0 and sub:
            d.add("sub_skill", "INFO", f"{cat.display(r)} changes only {cat.display(sub)} (subSkillName) - "
                  f"do not count it for other skills", basis="fact", confidence="ASSUMED")


@rule("design_attribute_values", Confidence.EXACT,
      "playerlevels.dbr (8 per point, lifeIncrement*, manaIncrement) and combatformulas.dbr "
      "(OA/DA 0.5 per attribute; damage +1% per 2.45 Cunning physical/pierce, 2.15 bleeding/trauma, "
      "2.15 Spirit magic, 2.00 Spirit magic DoT)")
def attribute_values(d: Design, cat: Catalog) -> None:
    """What one attribute point gives (before % bonuses) - the player decides the split."""
    pl = cat.player_levels()
    per = float(pl.get("strengthIncrement") or 8)
    t = d.spec.damage_type
    magic = t not in ("Physical", "Pierce", "Bleeding", "")
    direct = DM.attribute_share("direct_magic", 0.0, per)
    dot = DM.attribute_share("dot_magic", 0.0, per)
    d.attribute_rows = [
        ("Physique", float(pl.get("lifeIncrement") or 20), f"+{per * 0.5:g} DA", 0.0, "-"),
        ("Cunning", float(pl.get("lifeIncrementDexterity") or 8), f"+{per * 0.5:g} OA", 0.0,
         f"+{DM.attribute_share('direct_phys', per, 0.0):.2f}% Physical/Pierce, "
         f"+{DM.attribute_share('dot_phys', per, 0.0):.2f}% Bleeding/Internal Trauma"
         + (f" (none for {t} after conversion)" if magic else "")),
        ("Spirit", float(pl.get("lifeIncrementIntelligence") or 12), "-", float(pl.get("manaIncrement") or 16),
         (f"+{direct:.2f}% magic damage (incl. {t})" if magic else f"+{direct:.2f}% magic damage")
         + f", +{dot:.2f}% magic damage over time"),
    ]


def run_all(spec: Resolved, cat: Catalog, ruleset: dict) -> Design:
    d = Design(spec=spec)
    check_names(d, cat)
    check_items(d, cat)
    check_skill_tree(d, cat)
    check_ranks(d, cat)
    check_budget(d, cat, ruleset)
    check_devotions(d, cat)
    check_star_weapons(d, cat)
    check_bindings(d, cat)
    check_form(d, cat)
    check_conversion(d, cat)
    check_rr(d, cat)
    check_sub_skill(d, cat)
    attribute_values(d, cat)
    return d
