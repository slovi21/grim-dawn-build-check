"""Reading bonus fields from records (port of the .35 extractor, part 4)."""
from __future__ import annotations

STAT_PREFIXES = ("defensive", "offensive", "character", "retaliation",
                 "skill", "augment", "conversion", "racial", "pet")
STAT_SKIP = {
    "characterBaseAttackSpeedTag", "offensiveSkillCooldownReduction",
    "skillCooldownTime", "skillManaCost", "skillActiveDuration",
    "skillTargetRadius", "skillTargetNumber", "skillTargetAngle",
    "skillProjectileNumber", "skillWeaponScalePercent", "skillMaxLevel",
    "skillUltimateLevel", "skillTier", "skillConnectionOff",
}
# Per item, not summable over items (augmentSkillLevel1 = +N to THIS item's first skill)
PER_ITEM = ("augmentSkillLevel", "augmentMastery", "augmentSkillName",
            "racialBonusPercentDamage", "racialBonusPercentDefense",
            "offensiveGlobalChance", "retaliationGlobalChance")


def value_at(val, level):
    if isinstance(val, list):
        if not val or not isinstance(val[0], (int, float)):
            return None
        return float(val[min(max(level, 1), len(val)) - 1])
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    return None


def leveled_fields(rec, level: int = 1) -> dict:
    """Numeric bonus fields of a record at a rank (arrays are indexed by rank)."""
    out = {}
    if not rec or level < 1:
        return out
    for key, val in rec.items():
        if key.startswith("_") or key in STAT_SKIP or key.startswith("conversion"):
            continue
        if not key.startswith(STAT_PREFIXES):
            continue
        v = value_at(val, level)
        if v:
            out[key] = v
    return out


def conversions(rec, level=None) -> list:
    """[(from, to, pct)] - conversionInType/OutType/Percentage with suffixes 2..5."""
    out = []
    if not rec:
        return out
    for suffix in ("", "2", "3", "4", "5"):
        src = rec.get("conversionInType" + suffix)
        dst = rec.get("conversionOutType" + suffix)
        pct = rec.get("conversionPercentage" + suffix)
        if not (isinstance(src, str) and src and isinstance(dst, str) and dst):
            continue
        p = value_at(pct, level if level and level >= 1 else (len(pct) if isinstance(pct, list) else 1))
        if p:
            out.append((src, dst, round(p, 2)))
    return out


def per_item(key: str) -> bool:
    return key.startswith(PER_ITEM)
