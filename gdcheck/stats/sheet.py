"""Character sheet and combat snapshots (AP4.2/4.3).

    sheet   = permanent + toggles (what the in-game character sheet shows)
    combat  = sheet + kept-up buffs (duration / cooldown >= 95%, X-018; the HUD while fighting)
    purged  = after a monster purge: permanent effects + toggles/forms whose skill is notDispelable
              in the game database (werewolf1 ...); all other buffs, toggles and auras are removed
Partial uptimes never enter these values - they are reported separately.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..data import player_skills as PS
from ..data.gametables import LEECH_FIELD
from ..domain import activity as ACT
from ..domain.enums import ON_SHEET, Activity, SourceKind
from ..domain.fields import value_at
from ..mechanics import armor as ARM
from ..mechanics import attributes as ATT
from ..mechanics import resist as RES
from ..mechanics import speed as SPD
from ..mechanics import uptime as UP
from . import damage_mods as DM
from .aggregate import aggregate

ARMOR_SLOTS = ("Head", "Chest", "Legs", "Feet", "Hands", "Shoulders")
ARMOR_GLOBAL_SLOTS = ("Amulet", "Ring 1", "Ring 2", "Waist", "Medal", "Relic")
SPEEDS = {"attack": ("characterAttackSpeedModifier", "characterAttackSpeedMaxModifier"),
          "cast": ("characterSpellCastSpeedModifier", "characterSpellCastSpeedMaxModifier"),
          "run": ("characterRunSpeedModifier", "characterRunSpeedMaxModifier")}


@dataclass
class State:
    """One evaluated state (sheet, combat or purged)."""
    name: str
    fields: dict
    provenance: dict
    abilities: dict
    resists: dict           # name -> {raw, penalty, value, cap, shown}
    leech: dict
    absorption: float
    armor: dict             # slot -> armor value
    armor_weighted: float
    speeds: dict
    damage: dict            # per type
    main_type: str | None


@dataclass
class Snapshot:
    sheet: State
    combat: State
    purged: State
    buffs: list = field(default_factory=list)       # kept-up and partial buffs with uptime
    enemy_debuffs: list = field(default_factory=list)
    melee: bool = False
    weapon_types: tuple = ()
    two_handed: bool = False


def _armor(ctx, sources, effects_by_source, fields) -> dict:
    per_item: dict = {}
    global_flat = 0.0
    for s in sources.sources:
        v = sum(e.value for e in effects_by_source.get(s.id, ()) if e.key == "defensiveProtection"
                and e.activity in ON_SHEET)
        if not v:
            continue
        if s.kind in (SourceKind.SKILL, SourceKind.DEVOTION, SourceKind.GRANTED_SKILL,
                      SourceKind.ITEM_MODIFIER):
            global_flat += v
        elif s.slot in ARMOR_SLOTS:
            per_item[s.slot] = per_item.get(s.slot, 0.0) + v
        elif s.slot in ARMOR_GLOBAL_SLOTS:
            global_flat += v
    pct = fields.get("defensiveProtectionModifier", 0.0)
    worn_armor = {w.slot for w in ctx.character.worn if w.slot in ARMOR_SLOTS}
    return {slot: (per_item.get(slot, 0.0) + global_flat) * (1 + pct / 100) for slot in sorted(worn_armor)}


def _weapon_factor(ctx) -> float:
    """1 + characterBaseAttackSpeed of the weapons in hand (active set), two weapons averaged with the
    game engine's dual wield factor 0.5; no weapon (or a shield only): 1.0."""
    idx = ctx.index
    offs = []
    for w in getattr(ctx.character, "worn", ()) or ():
        if w.slot not in ("Main hand", "Off hand"):
            continue
        r = idx.record(w.item.get("basename", "")) or {}
        f = r.get("f") or r
        if str(r.get("Class") or f.get("Class") or "").startswith(("WeaponMelee", "WeaponHunting")):
            offs.append(float(f.get("characterBaseAttackSpeed") or 0.0))
    off = (offs[0] + offs[1]) * 0.5 if len(offs) >= 2 else (offs[0] if offs else 0.0)
    return 1.0 + off


def evaluate(name, ctx, sources, fields, prov, effects_by_source) -> State:
    ch = ctx.character
    ab = ATT.abilities(ch.attributes_base, fields, ch.level, ctx.attribute_constants)
    rs = ctx.ruleset
    resists = {}
    for n in RES.RESISTS + ("Physical",):
        raw = RES.raw_resist(fields, n)
        pen = ctx.penalty.get(RES.FIELD[n], 0.0) if n != "Physical" else 0.0
        cap = RES.own_cap(fields, n, rs.get("resist_base_cap", 80.0), rs.get("resist_hard_cap", 95.0))
        val = raw - pen
        resists[n] = {"raw": raw, "penalty": pen, "value": val, "cap": cap, "shown": min(val, cap)}
    leech_raw = fields.get(LEECH_FIELD, 0.0)
    leech_pen = ctx.penalty.get(LEECH_FIELD, 0.0)
    absorption = ARM.absorption(fields.get("defensiveAbsorptionModifier", 0.0),
                                rs.get("absorption_base", 70.0), rs.get("absorption_max", 100.0))
    armor = _armor(ctx, sources, effects_by_source, fields)
    speeds = {}
    wf = _weapon_factor(ctx)
    for kind, (f, fmax) in SPEEDS.items():
        speeds[kind] = SPD.speed(fields.get(f, 0.0), fields.get("characterTotalSpeedModifier", 0.0),
                                 ctx.speed_caps[kind], fields.get(fmax, 0.0),
                                 wf if kind == "attack" else 1.0)
    dmg = DM.per_type(fields, ab["cunning"], ab["spirit"])
    return State(name, fields, prov, ab, resists,
                 {"raw": leech_raw, "penalty": leech_pen, "value": leech_raw - leech_pen},
                 absorption, armor, ARM.armor_weighted(armor, rs.get("hit_zones", {})),
                 speeds, dmg, DM.main_type(dmg))


def _skill_uptimes(ctx, sources, cdr) -> dict:
    """Uptime of temporary buffs / enemy debuffs from mastery skills on the hotbar.
    {base skill record: {"uptime", "duration", "cooldown", "label", "on_hotbar"}}"""
    idx = ctx.index
    out = {}
    for st in sources.skills:
        if st.activity not in (Activity.TEMPORARY, Activity.ENEMY_DEBUFF) or st.points < 1:
            continue
        if "/devotion/" in st.record or "/playerclass" not in st.record:
            continue
        base = st.parent or st.record
        if base in out:
            continue
        brec = idx.record(base) or {}
        buff = ACT.buff_record(brec, idx) or brec
        lvl = next((s.level for s in sources.skills if s.record == base), st.level)
        dur = value_at(buff.get("skillActiveDuration") or brec.get("skillActiveDuration"), lvl)
        cd = value_at(buff.get("skillCooldownTime") or brec.get("skillCooldownTime"), lvl)
        on_bar = base in ctx.character.hotbar or st.record in ctx.character.hotbar
        u = UP.uptime(dur, cd, cdr) if on_bar else 0.0
        out[base] = {"uptime": u, "duration": dur, "cooldown": (cd or 0.0) * (1 - cdr / 100) if cd else cd,
                     "label": idx.display(base) or base.rsplit("/", 1)[-1], "on_hotbar": on_bar}
    return out


def _base_of(e) -> str:
    return e.group[4:] if e.group.startswith("mod:") else e.group


def build(ctx, sources) -> Snapshot:
    by_src: dict = {}
    for e in sources.effects:
        by_src.setdefault(e.source_id, []).append(e)
    sheet_fields, sheet_prov = aggregate(sources.effects, lambda e: 1.0 if e.activity in ON_SHEET else 0.0)
    sheet = evaluate("sheet", ctx, sources, sheet_fields, sheet_prov, by_src)
    cdr = min(max(sheet_fields.get("skillCooldownReduction", 0.0), 0.0), 100.0)
    upt = _skill_uptimes(ctx, sources, cdr)
    threshold = ctx.ruleset.get("kept_up_threshold", UP.KEPT_UP)

    def kept(e):
        if e.activity in ON_SHEET:
            return 1.0
        if e.activity != Activity.TEMPORARY:
            return 0.0
        u = (upt.get(_base_of(e)) or {}).get("uptime")
        if not UP.kept_up(u, threshold):
            return 0.0
        k = e.key
        if k.startswith("offensive") and e.value <= 0:
            return 0.0
        if "Regen" in k or "RunSpeed" in k:
            return 0.0
        return 1.0
    combat_fields, combat_prov = aggregate(sources.effects, kept)
    combat = evaluate("combat", ctx, sources, combat_fields, combat_prov, by_src)
    keep = PS.not_dispelable(ctx.game_version or "1.3.0.8",
                             str((getattr(getattr(ctx, "index", None), "meta", None) or {}).get("fingerprint") or ""))
    parent = {s.record: (s.parent or s.record) for s in sources.skills}

    def survives_purge(e):
        """Monster purges (SkillBuff_DispelMagic) remove every buff, toggle and aura unless its
        skill is notDispelable in the game database (werewolf1, wereraven1, bladebarrier1)."""
        if e.activity == Activity.PERMANENT:
            return 1.0
        if e.activity != Activity.TOGGLE:
            return 0.0
        base = e.group.split(":", 1)[1] if e.group.startswith(("mod:", "granted:")) else e.group
        if not base.startswith("records/"):
            src = sources.source(e.source_id)
            base = parent.get(src.record_id, src.record_id) if src else ""
        base = parent.get(base, base)
        return 1.0 if base in keep else 0.0
    purged_fields, purged_prov = aggregate(sources.effects, survives_purge)
    purged = evaluate("purged", ctx, sources, purged_fields, purged_prov, by_src)

    buffs: list = []
    debuffs: list = []
    for base, u in sorted(upt.items()):
        effects = [e for e in sources.effects if _base_of(e) == base and e.activity in
                   (Activity.TEMPORARY, Activity.ENEMY_DEBUFF)]
        vals: dict = {}
        for e in effects:
            vals[e.key] = vals.get(e.key, 0.0) + e.value
        row = dict(u, record=base, kept_up=UP.kept_up(u["uptime"], threshold), values=vals)
        (debuffs if any(e.activity == Activity.ENEMY_DEBUFF for e in effects) else buffs).append(row)

    weapons = [w for w in ctx.character.worn if w.slot in ("Main hand", "Off hand")]
    types = tuple(str((ctx.index.record(w.basename) or {}).get("Class", "")) for w in weapons)
    melee = bool(types) and types[0].startswith("WeaponMelee")
    return Snapshot(sheet, combat, purged, buffs, debuffs, melee, types,
                    ACT.two_handed_active(weapons))
