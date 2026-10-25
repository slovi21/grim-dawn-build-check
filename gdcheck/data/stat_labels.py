"""Raw game database fields as readable bonus lines (port of the .35 gd_labels, Task 4a).

`offensiveSlowLifeModifier = 552` becomes `+552% Vitality Decay Damage`.

The translation table and the combination rules come from the StatTranslator of Grim Dawn
Item Assistant (https://github.com/marius00/iagd, MIT licence, (c) marius00:
StatTranslator/EnglishLanguage.cs and StatManager.cs). Taken over: the table of format
templates and three rules:

  1. The field name IS the localisation tag: `defensiveFire` looks up the tag
     `defensiveFire`, which gives `{0}% Fire Resistance`.
  2. Resistances are generated, not listed: each damage type yields `defensive<Type>`,
     `...Resistance` and `...MaxResist`.
  3. Damage fields belong together: `offensive<Type>Min`, `Max`, `Modifier`, `Chance` and
     `DurationMin` form ONE line.

With the game's own texts (index tags) those win (analysis.save_facts.stat_text); this table
is the fallback for fields Grim Dawn does not ship as a ready line. Pure tables, no state.

Old -> new: gd_labels.label -> label, conversion_line -> conversion_line,
damage_type_name -> damage_type_name, INTERNE_FELDER -> INTERNAL_FIELDS,
MINDERUNGEN -> REDUCTIONS, _tag -> _template, _num -> _number.
"""
from __future__ import annotations

# Damage types from which resistance and damage lines are generated.
BODY_DAMAGE_TYPES = [
    "SlowPoison", "SlowPhysical", "SlowBleeding", "Bleeding", "SlowLife",
    "SlowFire", "SlowCold", "SlowLightning", "Poison", "Chaos", "Fire",
    "Aether", "Cold", "Lightning", "Elemental", "Pierce", "Physical", "Life",
    "TotalDamage", "PercentCurrentLife",
]

TAGS = {
    "Resistance": "Resistance",
    "ResistanceMaxResist": "to Maximum ",
    "augmentAllLevel": "+{0} to All Skills",
    "augmentMastery1": "+{0} to All Skills in {3}",
    "augmentMastery2": "+{0} to All Skills in {3}",
    "augmentMastery3": "+{0} to All Skills in {3}",
    "augmentMastery4": "+{0} to All Skills in {3}",
    "augmentSkill1": "+{0} to {3}",
    "augmentSkill1Extras": "Tier {0} {3} skill",
    "augmentSkill2": "+{0} to {3}",
    "augmentSkill2Extras": "Tier {0} {3} skill",
    "augmentSkill3": "+{0} to {3}",
    "augmentSkill3Extras": "Tier {0} {3} skill",
    "augmentSkill4": "+{0} to {3}",
    "augmentSkill4Extras": "Tier {0} {3} skill",
    "blockRecoveryTime": "{0} second Block Recovery",
    "cast_@enemyonattack": "{0}% Chance on attack",
    "cast_@enemyonattackcrit": "{0}% Chance on a critical attack (target enemy)",
    "cast_@enemyonblock": "{0}% Chance when blocked",
    "cast_@enemyonhitcritical": "{0}% Chance when hit by a critical",
    "cast_@enemyonkill": "{0}% Chance on Enemy Death",
    "cast_@enemyonmeleehit": "{0}% Chance when hit by a melee attack",
    "cast_@enemyonprojectilehit": "{0}% NPSkill Proc",
    "cast_@selfat": "{0}% Chance at {1}% Health",
    "cast_@selfonanyhit": "{0}% Chance when hit",
    "cast_@selfonattack": "{0}% Chance on attacking",
    "cast_@selfonattackcrit": "{0}% Chance on a critical attack",
    "cast_@selfonblock": "{0}% Chance when blocking",
    "cast_@selfonhitcritica": "{0}% Chance when Hit by a Critical",
    "cast_@selfonkill": "{0}% Chance on Enemy Death",
    "cast_@selfonlowhealth": "{0}% Chance at 25% health",
    "cast_@selfonmeleehit": "{0}% Chance when Hit by Melee Attacks",
    "cast_@selfonprojectilehit": "{0}% Chance when Hit by Ranged Attacks",
    "characterAttackSpeed": "+{0}% Attack Speed",
    "characterAttackSpeedModifier": "+{0}% Attack Speed",
    "characterConstitutionModifier": "+{0}% Constitution",
    "characterDefensiveAbility": "+{0} Defensive Ability",
    "characterDefensiveAbilityModifier": "+{0}% Defensive Ability",
    "characterDefensiveBlockRecoveryReduction": "+{0}% Shield Recovery Time",
    "characterDeflectProjectile": "+{0}% Chance to Avoid Projectiles",
    "characterDexterity": "+{0} Cunning",
    "characterDexterityModifier": "+{0}% Cunning",
    "characterDodgePercent": "+{0}% Chance to Avoid Melee Attacks",
    "characterEnergyAbsorptionPercent": "{0}% Energy Absorption From Enemy Spells",
    "characterGlobalReqReduction": "{0}% Reduction to Attribute Requirements",
    "characterHuntingDexterityReqReduction": "-{0}% Cunning Req. for Ranged Weapons",
    "characterIncreasedExperience": "+{0}% Experience Gain",
    "characterIntelligence": "+{0} Spirit",
    "characterIntelligenceModifier": "+{0}% Spirit",
    "characterLife": "+{0} Health",
    "characterLifeModifier": "+{0}% Health",
    "characterLifeRegen": "+{0} Health regenerated per second",
    "characterLifeRegenModifier": "+{0}% Health regenerated per second",
    "characterLightRadius": "+{0}% Light Radius",
    "characterMana": "+{0} Energy",
    "characterManaModifier": "+{0}% Energy",
    "characterManaRegen": "+{0} Energy Regenerated per second",
    "characterManaRegenModifier": "+{0}% Energy Regenerated per second",
    "characterOffensiveAbility": "+{0} Offensive Ability",
    "characterOffensiveAbilityModifier": "+{0}% Offensive Ability",
    "characterRunSpeedModifier": "+{0}% Movement Speed",
    "characterSpellCastSpeedModifier": "+{0}% Casting Speed",
    "characterStrength": "+{0} Physique",
    "characterStrengthModifier": "+{0}% Physique",
    "characterTotalSpeedModifier": "+{0}% Total Speed",
    "customtag_013_retaliation": "{0}-{1} {3} Retaliation",
    "customtag_03_retaliation": "{0} {3} Retaliation",
    "customtag_block_01": "{0}% Chance to Block {1} Damage",
    "customtag_block_012": "{0}% Chance to Block {1} Damage ({2}% Absorption)",
    "customtag_damage_123": "{1}-{2} {3} Damage",
    "customtag_damage_13": "{1} {3} Damage",
    "customtag_damage_13%": "+{1}% {3} Damage",
    "customtag_damage_chanceof": "{0}% Chance of ",
    "customtag_damage_conversion": "{0}% {3} Damage converted to {5}",
    "customtag_damage_delay": " over {4} Seconds",
    "customtag_damage_racial": "+{0}% Damage to {3}",
    "customtag_damage_racial02": "+{0}% Damage to {3} & {5}",
    "customtag_faction_boost": "+{0}% faction gain with {3}",
    "customtag_resistance_reduction": "{0}% Chance of {1}% Reduced target's Resistance For {2} Seconds",
    "customtag_retaliation_delay": " over {4} Seconds",
    "customtag_slow_retaliation": "{0}% reduced attack speed for {1}s",
    "customtag_speed": "Speed: {3} ({0})",
    "customtag_xpac_modif_addProjectile1": "1 Projectile to {3}",
    "customtag_xpac_modif_addProjectileX": "{0} Projectiles to {3}",
    "customtag_xpac_modif_characterAttackSpeedModifier": "+{0}% Attack Speed to {3}",
    "customtag_xpac_modif_defense": "{0}% {5} Resistance to {3}",
    "customtag_xpac_modif_defensiveAbilityBuff": "+{0}% Defensive Ability to {3}",
    "customtag_xpac_modif_defensiveAbilityDebuff": "{0} Defensive Ability to {3}",
    "customtag_xpac_modif_defensiveAbilityDebuffForDuration": "{1} Reduced target's Defensive Ability for {0} Seconds to {3}",
    "customtag_xpac_modif_dmgConversion": "{5} Damage converted to {6} Damage to {3}",
    "customtag_xpac_modif_dmgConversionPerc": "100% {5} Damage converted to {6} Damage to {3}",
    "customtag_xpac_modif_offensiveAbilityBuff": "+{0}% Offensive Ability to {3}",
    "customtag_xpac_modif_offensiveCritDamageModifier": "+{0}% Crit Damage to {3}",
    "customtag_xpac_modif_offensiveDamageMin": "{0} {5} Damage to {3}",
    "customtag_xpac_modif_offensiveDamageMinMax": "{0}-{1} {5} Damage to {3}",
    "customtag_xpac_modif_offensiveDamageMultModifier": "Total Damage Modified by {0}% to {3}",
    "customtag_xpac_modif_offensiveTaunt": "Generate Additional Threat to {3}",
    "customtag_xpac_modif_offensiveTotalDamageReductionPercentDurationMin": "{0}% Reduced target's Damage for {1} Seconds to {3}",
    "customtag_xpac_modif_offensiveTotalResistanceReductionAbsoluteMin":
        "{0} Reduced target's Resistances for {1} Seconds to {3}",
    "customtag_xpac_modif_petLimit": "+{0} to Pet Limit to {3}",
    "customtag_xpac_modif_physicalResist": "{0} Reduced target's Physical Resistance to {3}",
    "customtag_xpac_modif_physicalResistDuration": "{0} Reduced target's Physical Resistance for {1} Seconds to {3}",
    "customtag_xpac_modif_retaliationTotalDamageModifier": "+{0}% to All Retaliation Damage to {3}",
    "customtag_xpac_modif_skillActiveDuration": "{0} Second Duration to {3}",
    "customtag_xpac_modif_skillCooldownReductionChance": "{0}% Chance of +{1}% Skill Cooldown Reduction to {3}",
    "customtag_xpac_modif_skillCooldownTime": "{0} Second Skill Recharge to {3}",
    "customtag_xpac_modif_skillLifePercent": "{0}% Health Restored to {3}",
    "customtag_xpac_modif_skillManaCostReduction": "-{0}% Skill Energy Cost to {3}",
    "customtag_xpac_modif_skillTargetAngle": "{0} Degree Attack Arc to {3}",
    "customtag_xpac_modif_skillTargetNumber": "{0} Target Maximum to {3}",
    "customtag_xpac_modif_skillTargetRadius": "{0} Meter Target Area to {3}",
    "customtag_xpac_modif_sparkChance": "{0}% Chance of affecting up to {1} targets to {3}",
    "customtag_xpac_modif_speedModifier": "-{0}% Total Speed to {3}",
    "customtag_xpac_modif_weaponDamagePct": "{0}% Weapon Damage to {3}",
    "defensiveAbsorptionModifier": " Increases Armor Absorption by {0}%",
    "defensiveAllMaxResist": "+{0} Maximum All Resistances",
    "defensiveBlockAmountModifier": "+{0}% Shield Damage Blocked",
    "defensiveBlockModifier": "Increases Shield Block Chance by {0}%",
    "defensiveFreeze": "{0}% Reduced Freeze Duration",
    "defensivePetrify": "{0}% Reduced Petrify Duration",
    "defensiveProtection": "{0} Armor",
    "defensiveProtectionModifier": "Increases Armor by {0}%",
    "defensiveReflect": "{0}% Damage Reflected",
    "defensiveStun": "{0}% Reduced Stun Duration",
    "defensiveTotalSpeedResistance": "{0}% Slow Resistance",
    "defensiveTrap": "{0}% Reduced Entrapment Duration",
    "iatag_damage_Aether": "Aether",
    "iatag_damage_BaseAether": "Aether",
    "iatag_damage_BaseBleeding": "Bleeding",
    "iatag_damage_BaseChaos": "Chaos",
    "iatag_damage_BaseCold": "Cold",
    "iatag_damage_BaseElemental": "Elemental",
    "iatag_damage_BaseFire": "Fire",
    "iatag_damage_BaseLife": "Vitality",
    "iatag_damage_BaseLightning": "Lightning",
    "iatag_damage_BasePhysical": "Physical",
    "iatag_damage_BasePierce": "Pierce",
    "iatag_damage_BasePoison": "Acid",
    "iatag_damage_BaseVitality": "Vitality",
    "iatag_damage_Bleeding": "Bleeding",
    "iatag_damage_BonusPhysical": "Bonus",
    "iatag_damage_Chaos": "Chaos",
    "iatag_damage_Cold": "Cold",
    "iatag_damage_Elemental": "Elemental",
    "iatag_damage_Fire": "Fire",
    "iatag_damage_Life": "Vitality",
    "iatag_damage_Lightning": "Lightning",
    "iatag_damage_PercentCurrentLife": "Life Reduction",
    "iatag_damage_Physical": "Physical",
    "iatag_damage_Pierce": "Pierce",
    "iatag_damage_Poison": "Acid",
    "iatag_damage_SlowBleeding": "Bleeding",
    "iatag_damage_SlowCold": "Frost",
    "iatag_damage_SlowFire": "Burn",
    "iatag_damage_SlowLife": "Vitality Decay",
    "iatag_damage_SlowLightning": "Electrocute",
    "iatag_damage_SlowPhysical": "Internal Trauma",
    "iatag_damage_SlowPoison": "Poison",
    "iatag_damage_SlowVitality": "Vitality Decay",
    "iatag_damage_TotalDamage": "to All",
    "iatag_damage_Vitality": "Vitality",
    "iatag_damage_iatag_ui_physical": "Physical",
    "iatag_feedback_duplicates_not_looted": "{0} items were not looted due to being possible duplicates (bugged items)",
    "iatag_feedback_quest_not_looted": "{0} quest reward items were not looted",
    "iatag_feedback_stacked_not_looted": "{0} items were not looted due to being stackable",
    "iatag_feedback_unknown_not_looted": "{0} items were not looted due to being unidentified",
    "iatag_html_augmentation_item": "You may be able to purchase this augment from {0}",
    "iatag_html_displaying": "Displaying {0}",
    "iatag_html_items_buddy_alsohasthisitem1": "«{0}» also has this item",
    "iatag_html_items_buddy_alsohasthisitem2": "These buddies also have this item: {0}",
    "iatag_html_items_buddy_alsohasthisitem4": "«{0}» has this item.",
    "iatag_html_items_level": "Level {0}",
    "iatag_html_levlerequirement": "Level Requirement: {0}",
    "iatag_invalid_deposit_stash_number": "You have configured IA to deposit to stash {0} but you only have {1} pages",
    "iatag_invalid_loot_stash_number": "You have configured IA to loot from stash {0} but you only have {1} pages",
    "iatag_items_found_self_and_buddy": "{0} matching items found (with an additional {1} from your friends)",
    "iatag_items_found_selfonly": "{0} matching items found",
    "iatag_looted_from_stash": "Looted {0} items from stash {1}",
    "iatag_not_enough_stash": "Transfer Stash only contains {0} pages. IA requires at least 2 pages to function properly.",
    "iatag_stash3_success": "Successfully deposited {0} items",
    "iatag_stash_might_be_full": "Some items not deposited, stash {0} might be full.",
    "iatag_ui_backup_loggedinas": "Logged in as {0}",
    "iatag_ui_language_by_author": "{0} by {1}",
    "offensiveCritDamageModifier": "+{0}% Crit Damage",
    "offensiveLifeLeechMin": "{0}% of Attack Damage converted to Health",
    "offensivePercentCurrentLifeMin": "{0}% Reduction to Enemy\"s Health",
    "offensivePierceRatioMin": "{0}% Armor Piercing",
    "offensiveStunMax": "Stun target for {0}-{1} Seconds",
    "offensiveStunMin": "Stun target for {0} Seconds",
    "offensiveTauntMin": "Taunt target for {0} Seconds",
    "offensiveXDurationModifier": "+{1}% {5} Damage with +{0}% Increased Duration to {3}",
    "petBurstSpawn": "+{0} Summons to {3}",
    "piercingProjectile": "{0}% Chance to pass through Enemies",
    "projectileExplosionRadius": "{0} Meter Radius",
    "projectilePiercingChance": "{0}% Chance to pass through Enemies",
    "racialBonusPercentDefense": "+{0}% Less Damage From {3}",
    "racialBonusPercentDefense02": "+{0}% Less Damage From {3} & {5}",
    "retaliationTotalDamageModifier": "+{0}% Total Retaliation Damage",
    "skillActiveDuration": "{0} Second Duration",
    "skillChanceWeight": "{0}% Chance to be Used",
    "skillCooldownReduction": "-{0}% Skill Cooldown Reduction",
    "skillCooldownTime": "{0} Seconds Skill Recharge",
    "skillLifeBonus": "{0} Health Restored",
    "skillLifePercent": "{0}% Health Restored",
    "skillManaCost": "{0} Energy Cost",
    "skillProjectileNumber": "{0} Projectile",
    "skillProjectileSpeedModifier": "+{0}% Increase in Projectile Speed",
    "skillTargetAngle": "{0} Targets in a {1}° Angle",
    "skillTargetRadius": "{0} Meter Radius",
    "sparkChance": "{0}% Chance of affecting up to {1} targets within {2} Meters",
    "weaponDamagePct": "+{0}% Weapon Damage",
}


def _template(name: str) -> str:
    return TAGS.get(name, "")


def damage_type_name(d: str) -> str:
    """Damage type in plain text (rule from StatManager.DamageTypeTranslation)."""
    d = d.replace("Modifier", "")
    return _template(f"iatag_damage_{d}") or _template(d) or d.replace("Base", "")


def _number(v: float) -> str:
    return f"{v:.0f}" if abs(v - round(v)) < 0.005 else f"{v:.2f}"


# Fields that steer game mechanics instead of describing a bonus - not part of a report.
INTERNAL_FIELDS = (
    "skillWeaponTint", "offensiveSlowBleedingXOR", "offensiveSlowPhysicalXOR",
    "offensiveSlowBleedingGlobal", "offensiveSlowPhysicalGlobal",
    "offensiveSlowLifeGlobal", "offensiveSlowPoisonGlobal",
    "skillProjectileTargetGroundOnly", "skillLocked",
)

# Fields with an "offensive" prefix that describe NO damage but a reduction on the enemy;
# the general damage rule would turn them into nonsense like
# "14 TotalDamageReductionPercent Damage".
REDUCTIONS = {
    "offensiveTotalDamageReductionPercentMin": "-{v}% Damage to enemies",
    "offensiveTotalDamageReductionAbsoluteMin": "-{v} Damage to enemies",
    "offensiveTotalResistanceReductionPercentMin": "-{v}% Enemy Resistances",
    "offensiveTotalResistanceReductionAbsoluteMin": "-{v} Enemy Resistances",
    "offensiveTotalDamageReductionPercentDurationMin":
        "Enemy Damage Reduction for {v} Seconds",
    "offensiveTotalResistanceReductionPercentDurationMin":
        "Enemy Resistance Reduction for {v} Seconds",
    "offensiveSlowDefensiveAbilityMin": "-{v} Defensive Ability to enemies",
    "offensiveSlowDefensiveAbilityDurationMin":
        "Enemy Defensive Ability reduced for {v} Seconds",
    "offensiveSlowOffensiveAbilityMin": "-{v} Offensive Ability to enemies",
    "offensiveDamageMultModifier": "+{v}% Damage (Multiplier)",
    "offensiveGlobalChance": "{v}% Chance of these bonuses",
    "defensiveDisruption": "{v}% Reduced Skill Disruption",
    "defensiveBonusProtection": "+{v} Armor (bonus)",
    "defensivePercentReflectionResistance": "{v}% Reflected Damage Reduction",
    "skillManaCostReduction": "-{v}% Skill Energy Cost",
    "characterManaLimitReserve": "{v} Energy Reserved",
    "characterHealIncreasePercent": "+{v}% Increases Health Restored",
    "characterAttackSpeedMaxModifier": "+{v}% Maximum Attack Speed",
    "characterSpellCastSpeedMaxModifier": "+{v}% Maximum Casting Speed",
    "characterRunSpeedMaxModifier": "+{v}% Maximum Movement Speed",
    "characterBaseAttackSpeed": "{v} Base Attack Speed Modifier",
    "racialBonusPercentDamage": "+{v}% Damage to a specific Race",
    "skillComboChargeSpendReduction": "-{v}% Charge Cost",
    "skillLifePercentBuffDuration": "{v} Seconds Duration",
    "skillActiveLifeCost": "{v} Health Cost while active",
    "skillProjectileMaximumNumber": "up to {v} Projectiles",
    "retaliationFearChance": "{v}% Chance of Fear Retaliation",
    "retaliationGlobalChance": "{v}% Chance of these Retaliation bonuses",
    "augmentSkillLevel1": "+{v} to a Skill (slot 1)",
    "augmentSkillLevel2": "+{v} to a Skill (slot 2)",
    "augmentSkillLevel3": "+{v} to a Skill (slot 3)",
    "augmentSkillLevel4": "+{v} to a Skill (slot 4)",
    "augmentSkillLevel5": "+{v} to a Skill (slot 5)",
}


def label(field: str, value: float) -> str | None:
    """One field as a readable line; "" for internal fields, None when unknown."""
    if field.startswith(INTERNAL_FIELDS):
        return ""
    if field in REDUCTIONS:
        return REDUCTIONS[field].replace("{v}", _number(value))
    # An exact table entry always wins - otherwise the general damage rule would print fields
    # like offensivePierceRatioMin ("Armor Piercing") as "PierceRatio Damage".
    template = _template(field)
    if template and "{0}" in template:
        return template.replace("{0}", _number(value)).replace("{3}", "").strip()

    # resistances (rule 2)
    for kind in BODY_DAMAGE_TYPES:
        name = damage_type_name(kind)
        if field == f"defensive{kind}" or field == f"defensive{kind}Resistance":
            return f"{_number(value)}% {name} {_template('Resistance')}".strip()
        if field == f"defensive{kind}MaxResist":
            return (f"{_number(value)}% {_template('ResistanceMaxResist')}"
                    f"{name} {_template('Resistance')}").strip()

    # damage fields (rule 3), one field at a time because sums are formed
    if field.startswith("offensive") or field.startswith("retaliation"):
        prefix = "offensive" if field.startswith("offensive") else "retaliation"
        rest = field[len(prefix):]
        if rest.endswith("DurationModifier"):
            kind = rest[:-len("DurationModifier")]
            return f"+{_number(value)}% {damage_type_name(kind)} Duration"
        if rest.endswith("DurationMin"):
            kind = rest[:-len("DurationMin")]
            return f"{_number(value)} Second {damage_type_name(kind)} Duration"
        if rest.endswith("Modifier"):
            kind = rest[:-len("Modifier")]
            template = _template("customtag_damage_13%")
            if template and kind:
                line = template.replace("{1}", _number(value)).replace("{3}", damage_type_name(kind))
                return line + (" Retaliation" if prefix == "retaliation" else "")
        for ending, extra in (("Min", ""), ("Max", " (max)")):
            if rest.endswith(ending):
                kind = rest[:-len(ending)]
                template = _template("customtag_damage_13")
                if template and kind:
                    line = template.replace("{1}", _number(value)).replace("{3}", damage_type_name(kind))
                    return line + extra + (" Retaliation" if prefix == "retaliation" else "")
    return None


def conversion_line(percent: float, source: str, target: str) -> str:
    template = _template("customtag_damage_conversion") or "{0}% {3} -> {5}"
    return (template.replace("{0}", _number(percent))
            .replace("{3}", damage_type_name(source))
            .replace("{5}", damage_type_name(target)))
