"""Constants from the game tables carried by the index (fallback: 1.3.0.8 values)."""
from __future__ import annotations

T_FORMULAS = "records/game/combatformulas.dbr"
T_LEVELS = "records/creatures/pc/playerlevels.dbr"
T_PC = "records/creatures/pc/malepc01.dbr"
T_DIFF_PLAYERS = "records/game/balancingadjustment_mp+difficulty_players01.dbr"
T_DIFF_ENEMIES = "records/game/balancingadjustment_mp+difficulty_enemies01.dbr"
T_ENGINE = "records/game/gameengine.dbr"

RESIST_FIELDS = ("defensiveFire", "defensiveCold", "defensiveLightning", "defensivePoison",
                 "defensivePierce", "defensiveBleeding", "defensiveLife", "defensiveAether",
                 "defensiveChaos")
LEECH_FIELD = "defensiveSlowLifeLeach"
MONSTER_FALLBACK = {
    "defensiveAether": [0, 0, 0, 0, 2, 5, 9, 12, 5, 8, 12, 15],
    "defensiveBleeding": [0, 0, 0, 0, 5, 7, 9, 12, 9, 11, 14, 17],
    "defensiveChaos": [0, 0, 0, 0, 2, 5, 9, 12, 5, 8, 12, 15],
    "defensiveCold": [0, 0, 0, 0, 2, 5, 9, 12, 5, 8, 12, 15],
    "defensiveFire": [0, 0, 0, 0, 4, 6, 8, 11, 8, 10, 13, 16],
    "defensiveLife": [0, 0, 0, 0, 6, 8, 10, 13, 12, 14, 17, 19],
    "defensiveLightning": [0, 0, 0, 0, 6, 8, 10, 13, 10, 12, 15, 18],
    "defensivePhysical": [0, 0, 0, 0, 0, 4, 6, 8, 2, 7, 10, 13],
    "defensivePierce": [0, 0, 0, 0, 2, 5, 9, 12, 5, 8, 12, 15],
    "defensivePoison": [0, 0, 0, 0, 6, 8, 10, 13, 10, 12, 15, 18],
    "characterDefensiveAbility": [35, 35, 35, 35, 60, 60, 60, 60, 75, 75, 75, 75],
    "characterDefensiveAbilityModifier": [-15, -15, -15, -15, -8, -8, -8, -8, -8, -8, -8, -8],
    "characterOffensiveAbility": [0, 0, 0, 0, 40, 40, 40, 40, 50, 50, 50, 50],
    "characterOffensiveAbilityModifier": [-12, -12, -12, -12, -8, -8, -8, -8, -8, -8, -8, -8],
    "defensiveAbsorptionModifier": [-20] * 12,
    "defensiveSlowLifeLeach": [35, 35, 35, 35, 45, 45, 45, 45, 65, 65, 65, 65],
}


def table(index, name) -> dict:
    return (getattr(index, "tabellen", None) or {}).get(name) or {}


def attribute_constants(index) -> dict:
    lv, pc = table(index, T_LEVELS), table(index, T_PC)
    inc = float(lv.get("strengthIncrement", 8))
    return {"per_point": inc,
            "life_per_physique": float(lv.get("lifeIncrement", 20)) / inc,
            "life_per_cunning": float(lv.get("lifeIncrementDexterity", 8)) / inc,
            "life_per_spirit": float(lv.get("lifeIncrementIntelligence", 12)) / inc,
            "mana_per_spirit": float(lv.get("manaIncrement", 16)) / inc,
            "base_attribute": float(pc.get("characterStrength", 50)),
            "base_life": float(pc.get("characterLife", 250)),
            "base_mana": float(pc.get("characterMana", 250)),
            "base_da": float(pc.get("characterDefensiveAbility", 65)),
            "base_oa": float(pc.get("characterOffensiveAbility", 65))}


def resist_penalty(index, difficulty: int) -> tuple[dict, str]:
    """Penalty per resistance field (positive number) for Normal/Elite/Ultimate, 1 player."""
    tab = table(index, T_DIFF_PLAYERS)
    out = {}
    for f in RESIST_FIELDS + (LEECH_FIELD,):
        arr = tab.get(f)
        if isinstance(arr, list) and len(arr) >= 12:
            out[f] = -float(arr[difficulty * 4])
    if all(f in out for f in RESIST_FIELDS):
        out.setdefault(LEECH_FIELD, 25.0 if difficulty == 2 else 0.0)
        return out, "game database"
    upper = ("defensiveFire", "defensiveCold", "defensiveLightning", "defensivePoison", "defensivePierce")
    out = {f: (25.0 * difficulty if f in upper else (25.0 if difficulty == 2 else 0.0)) for f in RESIST_FIELDS}
    out[LEECH_FIELD] = 25.0 if difficulty == 2 else 0.0
    return out, "wiki table (fallback)"


def speed_caps(index) -> dict:
    ge = table(index, T_ENGINE)
    return {"attack": float(ge.get("playerAttackSpeedCapMax", 200.0)),
            "cast": float(ge.get("playerSpellCastSpeedCapMax", 200.0)),
            "run": float(ge.get("playerRunSpeedCapMax", 135.0))}


def monster_adjustment(index, difficulty: int = 2, players: int = 1) -> dict:
    """Resistance/DA/leech bonus of monsters on a difficulty (schema 7 table)."""
    tab = table(index, T_DIFF_ENEMIES) or MONSTER_FALLBACK
    i = difficulty * 4 + max(1, min(players, 4)) - 1

    def val(f):
        v = tab.get(f, MONSTER_FALLBACK.get(f))
        if isinstance(v, list):
            return float(v[min(i, len(v) - 1)]) if v else 0.0
        return float(v or 0.0)
    names = {"Physical": "Physical", "Pierce": "Pierce", "Fire": "Fire", "Cold": "Cold",
             "Lightning": "Lightning", "Acid": "Poison", "Vitality": "Life", "Aether": "Aether",
             "Chaos": "Chaos", "Bleeding": "Bleeding"}
    return {"res": {n: val(f"defensive{raw}") for n, raw in names.items()},
            "da": val("characterDefensiveAbility"), "da_mod": val("characterDefensiveAbilityModifier"),
            "oa": val("characterOffensiveAbility"), "oa_mod": val("characterOffensiveAbilityModifier"),
            "absorption_mod": val("defensiveAbsorptionModifier"),
            "leech": val("defensiveSlowLifeLeach")}


def monster_life_adjustment(index, difficulty: int = 2, players: int = 1) -> tuple[float, float] | None:
    """(characterLifeModifier %, characterLifeMultModifier %) of the monsters on a difficulty and player count
    (enemies table, index diff*4 + players - 1; Ultimate solo = 8: +580 / 0). None without the table - the
    health of a benchmark is not guessed then (.72 N5)."""
    tab = table(index, T_DIFF_ENEMIES)
    i = difficulty * 4 + max(1, min(players, 4)) - 1
    out = []
    for f in ("characterLifeModifier", "characterLifeMultModifier"):
        v = tab.get(f)
        if not isinstance(v, list) or len(v) <= i:
            return None
        out.append(float(v[i]))
    return out[0], out[1]


def monster_adjustment_damage(index, difficulty: int = 2, players: int = 1) -> float:
    """Total damage % of monsters on a difficulty (offensiveTotalDamageModifier of the
    enemies table; Ultimate 1 player = 40 in 1.3.0.8)."""
    tab = table(index, T_DIFF_ENEMIES) or {}
    v = tab.get("offensiveTotalDamageModifier", 40.0 if difficulty == 2 else 0.0)
    i = difficulty * 4 + max(1, min(players, 4)) - 1
    if isinstance(v, list):
        return float(v[min(i, len(v) - 1)]) if v else 0.0
    return float(v or 0.0)
