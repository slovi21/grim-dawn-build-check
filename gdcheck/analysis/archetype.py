"""Build archetype: melee / ranged / caster / pet (review 01.10.2026: the weapon class
alone made a scepter caster 'melee'; 02.10.2026: a weapon-damage AoE such as Ring of Steel made a
Blademaster 'caster' - the main attack and its weapon damage decide). Decides which Health target
applies."""
from __future__ import annotations

PET_TOKENS = ("summon", "raiseskeleton", "pets/", "_pet")
PET_SHARE = 0.6                # a build with this share of its points in summons is a pet build


def pet_share_of(points, index=None) -> float:
    """Share of skill points in summons of summons + own attacks (incl. modifiers) over (record, points)
    pairs: 0 = no pet, 1 = pure pet build. Without an index only the summon names count (no own attacks
    are recognised). The one implementation for pet_share (context) and damage_types.pet_share (list)."""
    pet = own = 0
    for rn, pts in points:
        n = rn.lower()
        if pts < 1 or "/devotion/" in n or "_classtraining" in n or "default" in n:
            continue
        if any(t in n for t in PET_TOKENS):
            pet += pts
            continue
        cls = str(((index.record(rn) if index is not None else None) or {}).get("Class", ""))
        if "Attack" in cls or ("Modifier" in cls and "Pet" not in cls):
            own += pts
    return pet / (pet + own) if (pet + own) else 0.0


def pet_share(ctx) -> float:
    return pet_share_of(ctx.character.skills.items(), ctx.index)


def is_pet(ctx) -> bool:
    """The one pet threshold: the archetype, the overview facts and damage_types.is_pet_build use it."""
    return pet_share(ctx) >= PET_SHARE


WEAPON_TOKENS = ("Weapon", "WPAttack", "PathCharge")


def main_action(actions):
    """The attack the character mostly uses (review 02.10.2026: the strongest action per use was a
    cooldown skill - Ring of Steel for a Cadence Blademaster): strongest action without cooldown,
    else the strongest action; never the default weapon attack when there is another one."""
    own = [a for a in actions if "defaultweaponattack" not in a.action_id and getattr(a, "proc", None) is None]
    fill = [a for a in own if not a.cooldown]
    if fill:
        return max(fill, key=lambda a: (a.direct + a.dot, a.action_id))
    return own[0] if own else None


def weapon_attack(ctx, action) -> bool:
    """Weapon attack = a weapon skill class or any weapon damage (Ring of Steel 322%, Leap 281% are
    Skill_AttackRadius*); spells (cast speed) are never weapon attacks."""
    if action.kind == "spell":
        return False
    cls = str((ctx.index.record(action.action_id) or {}).get("Class", ""))
    return any(t in cls for t in WEAPON_TOKENS) or (action.wd_pct or 0) > 0


def archetype(ctx, snap, actions) -> str:
    if is_pet(ctx):
        return "pet"
    top = main_action(actions)
    if top is not None and not weapon_attack(ctx, top):
        return "caster"
    if snap.melee:
        return "melee"
    return "ranged" if snap.weapon_types and snap.weapon_types[0].startswith("WeaponHunting") else "caster"
