"""Item roll - the values of a dropped item, rebuilt from its seed (pure, deterministic, standard library only).

Own implementation of the rolling procedure described in the project specification
`WUERFELUNG-SPEZIFIKATION` (sections in brackets below); no third-party code.  One stream of pseudo-random
numbers per item (Park-Miller "minimal standard", [1]) is consumed field by field over all parts of the item
(base, prefix, suffix, modifier; [4], [5]); the rolled values of the parts are added up and then scaled once
([3]).  This module delivers the totals; splitting them over the parts is the job of the caller.

    roll_item(base, seed, prefix=None, suffix=None, modifier=None) -> RolledItem

`base`, `prefix`, `suffix`, `modifier` are field dictionaries (field name -> number or text, i.e. the record of
the game index).  A part that is None does not exist; an empty dictionary exists.  Nothing is mutated.

Public helpers (all tested): `RollStream` / `draw_sequence` (the stream), `roll_standard` (A, [2.1]),
`roll_skill` (F, [2.2]), `roll_conversion` (U, [2.3]), `scale_value` (Sk, [3.2]), `to_float32`, `truncate`,
`round_half_away` and the tables `FIELD_GROUPS`.

Deviations from / additions to the interface of the task (the specification wins where both differ)
- `RolledItem` has two more members: `draw_log` (one `DrawEntry` per consumed draw in stream order: group, part,
  field, kind A/F/U; the protocol of [9.4]) and `conversions` (result field -> chosen (in type, out type) pair).
- `shares[part][field]` (requested by the coordinator) is the share of a part in `values[field]`; for every
  field of `values` the shares of the contributing parts add up exactly to the value.  Every part but the first
  contributing one (order base, prefix, suffix, modifier) gets its own value (scaled / rounded / truncated like
  the total: Sk of its own rolled value, R, or T); the first one gets the rest, i.e. the truncation rest of the
  common scaling.  Minimum: scaled own value.  Maximum of G02/G03: own minimum share plus the own rolled span
  (the span is not scaled), truncated; of G09: T(a + d).  Duration fields of G04 pairs are scaled fields like
  any other.  Fixed fields (4.5) belong completely to the part they were taken from.  Fractions: the rest is
  moved by a few ulps until the shares added one after the other give the value (whole numbers are exact).
  Proc lines are neither in `values` nor in `shares`.
- `raw[part]` holds, per summed field, the single rolled value; for the flat damage groups G02, G03, G09 the
  minimum is stored as `<f>Min` and the rolled span (Max - Min) as `<f>Span` (the maximum itself is never rolled).
  Proc lines, fixed fields and skipped sources are not in `raw`.
- `ProcLine.field` is the group field name (`offensiveFire`, `offensiveSlowFire`, `offensiveFireModifier`);
  `minimum` is the single value for the percent fields of G04; `maximum` is None when equal to the minimum.
- `values` holds the group results in group order, then the fixed fields (sorted by name, [4.5]).  The `Chance`
  fields of proc lines are fixed fields and therefore are in `values` as well; the proc line value is not.
- Values are floats; integral results (T, R, Sk) are whole floats.

`unsure` codes (sorted): U2 negative start value with a negative spread or an absurd result ([8] U2, formula kept
as specified), U3 fields of the item that the procedure does not know (listed in `unmodelled`; every further
draw of the game for them is missing), U6 G03 base skipped because `<f>DurationMin` is absent (presence, not value;
the index drops zero values), U8 seed whose stream is stuck at 0, U9 a prefix/suffix/modifier flat physical
damage dropped because the base is a weapon, U10 affix flat damage / damage over time dropped because of the
off-hand flag, U12 several different conversion pairs (only the first is output; also listed in `unmodelled`),
NO_CLASS the base has no `Class` text (weapon and off-hand test impossible; treated as neither).

Decisions where the specification is open (also in the report of the task)
- Weapon base damage: `offensivePhysicalMin/Max` of a `Weapon...` base are skipped by G02 ([4.4]) and are
  taken over as fixed fields ([6]: "Waffenbasisschaden"), value of the first part that has them (B, P, S, M).
- `defensive...MaxResist` are fixed fields (U3 hint of the specification).
- Proc separation of G04: the remaining (not proc) contributions are added in the order P, S, M, B ([4.3]).
- Text values (`Class`, conversion types) are compared after stripping surrounding blanks; a numeric string
  is a number, any other text is the number 0 (the field still exists, [7.1]); a non-finite number is 0.
- `to_float32` is written without `struct` (the layer rules of `mechanics` allow only math/dataclasses/typing).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

STREAM_MULTIPLIER = 16807
STREAM_MODULUS = 2147483647          # 2^31 - 1
BASE_JITTER_PERCENT = 20.0           # [5.3] fixed spread of the base record (U1)
PARTS = ("base", "prefix", "suffix", "modifier")
_PSMB = ("prefix", "suffix", "modifier", "base")
_AFFIXES = ("prefix", "suffix", "modifier")
_STUCK_SEEDS = (0, 2147483647, 4294967294)       # (16807 * seed) mod m == 0 [1.3]
_F32_MAX = 3.4028234663852886e38
_INT32 = 1 << 32
_UNSURE_ORDER = ("U2", "U3", "U6", "U8", "U9", "U10", "U12", "NO_CLASS")


# ---------------------------------------------------------------------------------------------------------
# Field tables ([4.4]); the order of every tuple is the order of the draws.  224 fields in 17 groups.
# ---------------------------------------------------------------------------------------------------------
_G01_FIELDS = (
    "characterStrength", "characterDexterity", "characterIntelligence", "characterLife", "characterMana",
    "characterStrengthModifier", "characterDexterityModifier", "characterIntelligenceModifier",
    "characterLifeModifier", "characterManaModifier", "characterLifeMultModifier",
    "characterOffensiveAbility", "characterDefensiveAbility", "characterOffensiveAbilityModifier",
    "characterDefensiveAbilityModifier", "characterLifeRegen", "characterLifeRegenModifier",
    "characterManaRegenModifier", "characterConstitutionModifier", "characterHealIncreasePercent",
    "characterTotalSpeedModifier", "characterAttackSpeedModifier", "characterAttackSpeedMaxModifier",
    "characterSpellCastSpeedModifier", "characterSpellCastSpeedMaxModifier", "characterRunSpeedModifier",
    "characterRunSpeedMaxModifier", "characterDefensiveBlockRecoveryReduction",
    "characterEnergyAbsorptionPercent", "characterDodgePercent", "characterDeflectProjectile",
    "characterManaLimitReserve", "characterManaLimitReserveModifier",
)

_G02_FIELDS = (
    "offensivePhysical", "offensiveBonusPhysical", "offensivePierce", "offensiveFire", "offensiveCold",
    "offensiveLightning", "offensivePoison", "offensiveLife", "offensiveAether", "offensiveChaos",
    "offensiveElemental",
)

_G03_FIELDS = (
    "offensiveSlowPhysical", "offensiveSlowBleeding", "offensiveSlowFire", "offensiveSlowCold",
    "offensiveSlowLightning", "offensiveSlowPoison", "offensiveSlowLife", "offensiveSlowAether",
    "offensiveSlowChaos", "offensiveSlowLifeLeach", "offensiveSlowManaLeach",
)

_G04_FIELDS = (
    "offensiveTotalDamageModifier", "offensiveCritDamageModifier", "offensivePhysicalModifier",
    "offensivePierceModifier", "offensiveFireModifier", "offensiveColdModifier", "offensiveLightningModifier",
    "offensivePoisonModifier", "offensiveLifeModifier", "offensiveAetherModifier", "offensiveChaosModifier",
    "offensiveElementalModifier", "offensiveSlowPhysicalModifier", "offensiveSlowPhysicalDurationModifier",
    "offensiveSlowBleedingModifier", "offensiveSlowBleedingDurationModifier", "offensiveSlowFireModifier",
    "offensiveSlowFireDurationModifier", "offensiveSlowColdModifier", "offensiveSlowColdDurationModifier",
    "offensiveSlowLightningModifier", "offensiveSlowLightningDurationModifier", "offensiveSlowPoisonModifier",
    "offensiveSlowPoisonDurationModifier", "offensiveSlowLifeModifier", "offensiveSlowLifeDurationModifier",
    "offensiveSlowAetherModifier", "offensiveSlowChaosModifier",
)

_G05_FIELDS = (
    "offensiveLifeLeech",
)

_G06_FIELDS = (
    "offensiveStun", "offensiveKnockdown", "offensiveSleep", "offensiveFreeze", "offensivePetrify",
)

_G07_FIELDS = (
    "offensiveSlowTotalSpeed", "offensiveSlowAttackSpeed", "offensiveSlowSpellCastSpeed",
    "offensiveSlowRunSpeed", "offensiveSlowOffensiveAbility", "offensiveSlowDefensiveAbility",
)

_G08_FIELDS = (
    "offensivePhysicalReductionPercent", "offensiveElementalReductionPercent",
    "offensiveTotalDamageReductionPercent", "offensiveTotalDamageReductionAbsolute",
    "offensiveTotalResistanceReductionPercent", "offensiveTotalResistanceReductionAbsolute",
    "offensivePhysicalResistanceReductionPercent", "offensivePhysicalResistanceReductionAbsolute",
    "offensiveElementalResistanceReductionPercent", "offensiveElementalResistanceReductionAbsolute",
)

_G09_FIELDS = (
    "retaliationPhysical", "retaliationPierce", "retaliationFire", "retaliationCold", "retaliationLightning",
    "retaliationPoison", "retaliationLife", "retaliationAether", "retaliationChaos", "retaliationElemental",
)

_G10_FIELDS = (
    "retaliationSlowPhysical", "retaliationSlowPierce", "retaliationSlowFire", "retaliationSlowCold",
    "retaliationSlowLightning", "retaliationSlowPoison", "retaliationSlowLife", "retaliationSlowAether",
    "retaliationSlowChaos", "retaliationSlowBleeding",
)

_G11_FIELDS = (
    "retaliationTotalDamageModifier", "retaliationPhysicalModifier", "retaliationPierceModifier",
    "retaliationFireModifier", "retaliationColdModifier", "retaliationLightningModifier",
    "retaliationPoisonModifier", "retaliationLifeModifier", "retaliationAetherModifier",
    "retaliationChaosModifier", "retaliationElementalModifier",
)

_G12_FIELDS = (
    "retaliationSlowAttackSpeed", "retaliationSlowRunSpeed",
)

_G13_FIELDS = (
    "retaliationStun", "retaliationFreeze", "retaliationConfusion",
)

_G14_FIELDS = (
    "defensiveBlockModifier", "defensiveBlockAmountModifier", "defensiveProtectionModifier",
    "defensiveAbsorptionModifier", "defensivePhysical", "defensivePierce", "defensiveFire", "defensiveCold",
    "defensiveLightning", "defensivePoison", "defensiveLife", "defensiveAether", "defensiveChaos",
    "defensiveElementalResistance", "defensiveBleeding", "defensiveSlowLifeLeach", "defensiveSlowManaLeach",
    "defensiveManaBurn", "defensiveAllResistance", "defensivePhysicalModifier", "defensivePierceModifier",
    "defensiveFireModifier", "defensiveColdModifier", "defensiveLightningModifier", "defensivePoisonModifier",
    "defensiveLifeModifier", "defensiveAetherModifier", "defensiveChaosModifier",
    "defensiveElementalModifier", "defensiveBleedingModifier", "defensiveSlowLifeLeachModifier",
    "defensiveSlowManaLeachModifier", "defensivePhysicalDuration", "defensiveFireDuration",
    "defensiveColdDuration", "defensiveLightningDuration", "defensivePoisonDuration", "defensiveLifeDuration",
    "defensiveAetherDuration", "defensiveChaosDuration", "defensiveBleedingDuration",
    "defensiveSlowLifeLeachDuration", "defensiveSlowManaLeachDuration", "defensivePhysicalDurationModifier",
    "defensiveFireDurationModifier", "defensiveColdDurationModifier", "defensiveLightningDurationModifier",
    "defensivePoisonDurationModifier", "defensiveLifeDurationModifier", "defensiveAetherDurationModifier",
    "defensiveChaosDurationModifier", "defensiveBleedingDurationModifier",
    "defensiveSlowLifeLeachDurationModifier", "defensiveSlowManaLeachDurationModifier", "defensiveDisruption",
    "defensiveStun", "defensiveStunModifier", "defensiveFreeze", "defensiveTrap", "defensivePetrify",
    "defensiveSleep", "defensiveSleepModifier", "defensiveKnockdown", "defensiveKnockdownModifier",
    "defensiveTaunt", "defensiveFear", "defensiveConfusion", "defensiveConvert",
    "defensiveTotalSpeedResistance", "defensiveCrowdControl", "defensiveReflect", "defensiveReflectModifier",
    "defensivePercentCurrentLife", "defensivePercentReflectionResistance",
)

_G15_FIELDS = (
    "retaliationDamageMultModifier",
)

_G17_FIELDS = (
    "skillCooldownReduction", "skillManaCostReduction", "skillComboChargeSpendReduction",
    "skillProjectileSpeedModifier", "skillCooldownReductionModifier", "skillManaCostReductionModifier",
)
_F0_FIELDS = ("skillCooldownReduction", "skillManaCostReduction")
_G07_MODES = ("scale", "scale", "scale", "scale", "round", "round")
_G04_UNSCALED = frozenset(("offensiveTotalDamageModifier", "offensiveCritDamageModifier"))
_G04_PROC = frozenset(_G04_FIELDS[2:12])                      # fields 3 to 12 can be split into proc lines
_G04_DURATION = frozenset(f for f in _G04_FIELDS if f.endswith("DurationModifier"))
# value field -> its duration partner (rolled together with it, [4.4] G04 type 2)
_G04_PAIR = {f: f[: -len("Modifier")] + "DurationModifier" for f in _G04_FIELDS
             if f.startswith("offensiveSlow") and f.endswith("Modifier") and not f.endswith("DurationModifier")}
_CONVERSION_SLOTS = (("conversionPercentage", "conversionInType", "conversionOutType"),
                     ("conversionPercentage2", "conversionInType2", "conversionOutType2"))

FIELD_GROUPS = {
    "G01": _G01_FIELDS, "G02": _G02_FIELDS, "G03": _G03_FIELDS, "G04": _G04_FIELDS, "G05": _G05_FIELDS,
    "G06": _G06_FIELDS, "G07": _G07_FIELDS, "G08": _G08_FIELDS, "G09": _G09_FIELDS, "G10": _G10_FIELDS,
    "G11": _G11_FIELDS, "G12": _G12_FIELDS, "G13": _G13_FIELDS, "G14": _G14_FIELDS, "G15": _G15_FIELDS,
    "G16": ("conversionPercentage", "conversionPercentage2"), "G17": _G17_FIELDS,
}

_STAT_PREFIXES = ("offensive", "defensive", "retaliation", "character", "skill", "conversion",
                  "blockAbsorption", "blockRecovery")
_COSMETIC_PREFIXES = (
    "augment", "modif", "item", "drop", "physics", "mesh", "bitmap", "baseTexture", "bumpTexture", "glowTexture",
    "shader", "weaponTrail", "hitSound", "swipeSound", "blockSound", "attackEffect", "basicProjectile", "actor",
    "casts", "maxTransparency", "outline", "scale", "templateName", "Class", "FileDescription", "levelRequirement",
    "itemLevel", "armorClassification", "characterBaseAttackSpeedTag", "armorFemaleMesh", "armorMaleMesh",
    "decoration", "attributeScalePercent", "petBonusName")
_FIXED_EXACT = frozenset((
    "characterBaseAttackSpeed", "characterManaRegen", "characterConstitution", "characterAttackSpeed",
    "characterSpellCastSpeed", "characterRunSpeed", "characterIncreasedExperience", "characterIncreasedGold",
    "characterLightRadius", "characterGlobalReqReduction", "characterLevelReqReduction", "characterModifierPoints",
    "defensiveProtection", "defensiveBlock", "defensiveBlockChance", "blockAbsorption", "blockRecoveryTime",
    "offensiveGlobalChance", "retaliationGlobalChance"))
_COMPONENTS = ("Min", "Max", "DurationMin", "Chance")


def _known_names() -> frozenset:
    """Every name that belongs to a group (field plus the components named there): never 'unmodelled'."""
    names: set = set()
    for group in ("G01", "G11", "G14", "G15"):
        names.update(FIELD_GROUPS[group])
    for group in ("G04", "G17"):
        for f in FIELD_GROUPS[group]:
            names.update((f, f + "Chance"))
    for group in ("G02", "G03", "G05", "G06", "G07", "G08", "G09", "G10", "G12", "G13"):
        for f in FIELD_GROUPS[group]:
            names.update(f + c for c in _COMPONENTS)
    return frozenset(names)


_KNOWN_NAMES = _known_names()


def _fixed_by_rule(name: str) -> bool:
    """The nine rules of [4.5] (plus `defensive...MaxResist`, U3 hint); the caller checked the stat prefix."""
    if name in _FIXED_EXACT:
        return True
    if name.startswith("character") and name.endswith("ReqReduction"):
        return True
    if name.startswith("offensiveBase") and name.endswith(("Min", "Max")):
        return True
    if name.startswith("offensiveSlow") and name.endswith("DurationMin"):
        return True
    if name.startswith("offensive") and name.endswith("RatioMin"):
        return True
    if name.endswith("Chance") and not name.endswith("GlobalChance") \
            and name.startswith(("offensive", "retaliation", "skill")):
        return True
    if name.startswith(("offensive", "retaliation")) and name.endswith("Global"):
        return True
    return name.startswith("defensive") and name.endswith("MaxResist")


def _is_unmodelled(name: str) -> bool:
    """[4.5]: a stat field that no group knows, that is neither fixed nor cosmetic nor a conversion."""
    return (name.startswith(_STAT_PREFIXES) and not name.startswith(_COSMETIC_PREFIXES)
            and name not in _KNOWN_NAMES and not name.startswith("conversion") and not _fixed_by_rule(name))


# ---------------------------------------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------------------------------------
def truncate(x: float) -> int:
    """T(x): drop the fraction towards zero ([0.2])."""
    return math.trunc(x)


def round_half_away(x: float) -> int:
    """R(x): round to a whole number, halves away from zero (2.5 -> 3, -2.5 -> -3) ([0.2]); exact, no x + 0.5."""
    if x < 0:
        return -round_half_away(-x)
    whole = math.floor(x)
    return whole + 1 if x - whole >= 0.5 else whole


def to_float32(x: float) -> float:
    """fl32(x): the nearest IEEE-754 single (ties to even), returned as a double ([0.2], [3.2]).

    Written with frexp/ldexp because the layer rules of `mechanics` do not allow `struct`."""
    if x == 0.0 or not math.isfinite(x):
        return x
    exponent = max(math.frexp(x)[1], -125)            # 2^-126 is the smallest normal single; below it fixed steps
    step = math.ldexp(1.0, exponent - 24)
    rounded = float(round(x / step)) * step           # x / step is exact (power of two); round() ties to even
    if abs(rounded) > _F32_MAX:
        return math.copysign(math.inf, x)
    return rounded


def scale_value(x: float, scale_percent: float) -> int:
    """Sk(x, q) ([3.2]): x * (100 + q) / 100 in 32-bit floats, then cut off.  Always cuts, also for q = 0."""
    a = to_float32(x)
    c = to_float32(100.0 + scale_percent)
    n = to_float32(a * c)                             # the product of two singles is exact in a double
    d = to_float32(n / 100.0)
    if not math.isfinite(d):
        raise ValueError("scaled value is not finite: %r" % (d,))
    return math.trunc(d)


def _num(value) -> float:
    """W: the number of a field value ([7.1]): numbers as they are, numeric text parsed, anything else 0."""
    if isinstance(value, (bool, int, float)):
        x = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text or "_" in text:
            return 0.0
        try:
            x = float(text)
        except ValueError:
            return 0.0
    else:
        return 0.0
    return x if math.isfinite(x) else 0.0


def _int32(n: int) -> int:
    return ((n + (1 << 31)) % _INT32) - (1 << 31)


# ---------------------------------------------------------------------------------------------------------
# Stream ([1])
# ---------------------------------------------------------------------------------------------------------
class RollStream:
    """The stream of one item: Z0 = 16807 * seed mod m (run-up without a draw), draw n is Z_n = 16807 * Z_(n-1) mod m.

    `seed` is a 32-bit unsigned number.  `draws` counts the draws taken so far.  Not shared between items."""

    __slots__ = ("seed", "state", "draws")

    def __init__(self, seed: int) -> None:
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 0xFFFFFFFF:
            raise ValueError("seed must be an integer from 0 to 4294967295, got %r" % (seed,))
        self.seed = seed
        self.state = (STREAM_MULTIPLIER * seed) % STREAM_MODULUS
        self.draws = 0

    def draw(self) -> int:
        """The next number, 1 .. m - 1 (0 forever for the stuck seeds of U8)."""
        self.state = (STREAM_MULTIPLIER * self.state) % STREAM_MODULUS
        self.draws += 1
        return self.state


def draw_sequence(seed: int, count: int) -> list[int]:
    """The first `count` draws of a fresh stream."""
    stream = RollStream(seed)
    return [stream.draw() for _ in range(count)]


# ---------------------------------------------------------------------------------------------------------
# Single rolls ([2]); every roll takes at most one draw
# ---------------------------------------------------------------------------------------------------------
def _spread_roll(stream: RollStream, value: float, percent: float, minimum_one: bool) -> tuple[float, bool]:
    """Common part of A and F.  Returns (result, doubtful) - doubtful is the U2 mark."""
    b = _int32(math.trunc(value * percent * 0.01))
    if b == 0 and minimum_one:
        b = 1
    s = stream.draw()
    n = (2 * b + 1) & 0xFFFFFFFF                      # read as unsigned 32 bit: negative b gives a huge modulus
    raw = (s % n) - b + value
    result = value if abs(raw) < 1 else raw
    return result, b < 0 or abs(result) > 10.0 * abs(value) + 1000.0


def _standard_roll(stream: RollStream, value: float, percent: float) -> tuple[float, bool]:
    if value == 0 or percent == 0:
        return value, False
    return _spread_roll(stream, value, percent, True)


def _skill_roll(stream: RollStream, value: float, percent: float) -> tuple[float, bool]:
    if value == 0:
        return value, False
    return _spread_roll(stream, value, percent, False)


def roll_standard(stream: RollStream, value: float, percent: float) -> float:
    """A(v, p) ([2.1]): v +- T(v * p * 0.01) (at least 1) in whole steps; no draw for v = 0 or p = 0."""
    return _standard_roll(stream, float(value), float(percent))[0]


def roll_skill(stream: RollStream, value: float, percent: float) -> float:
    """F(v, p) ([2.2]): like A, but p = 0 still draws (v != 0) and the spread may be 0."""
    return _skill_roll(stream, float(value), float(percent))[0]


def roll_conversion(stream: RollStream, value: float, percent: float) -> float:
    """U(w, p) ([2.3]): w * f with a continuous single-precision factor f around 1, kept in 0 .. 100; none for p <= 0."""
    if percent <= 0:
        return value
    j = percent * 0.01
    s = stream.draw()
    factor = to_float32((s * 2.0 ** -31) * (2.0 * j) + (1.0 - j))
    result = value * factor
    if result < 0:
        return 0.0
    return 100.0 if result > 100 else result


# ---------------------------------------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ProcLine:
    """A contribution with its own proc chance, kept apart from the sums ([4.4] G02, G03, G04)."""
    part: str
    field: str
    minimum: float
    maximum: float | None
    duration: float | None
    chance: float


@dataclass(frozen=True)
class DrawEntry:
    """One consumed draw: group (G01 .. G17, F0), part, field (`<f>Span` is the span draw), kind A/F/U."""
    group: str
    part: str
    field: str
    kind: str


@dataclass(frozen=True)
class RolledItem:
    values: dict[str, float]
    raw: dict[str, dict[str, float]]
    shares: dict[str, dict[str, float]]
    scale_percent: float
    proc_lines: tuple[ProcLine, ...]
    draws: int
    draws_by_part: dict[str, int]
    unsure: tuple[str, ...]
    unmodelled: tuple[str, ...]
    draw_log: tuple[DrawEntry, ...]
    conversions: dict[str, tuple[str, str]]


# ---------------------------------------------------------------------------------------------------------
# Shares of the parts in a total (the split of `values` over base, prefix, suffix, modifier)
# ---------------------------------------------------------------------------------------------------------
def _chain(first: float, rest: list[float]) -> float:
    """first + rest[0] + rest[1] ... added one after the other (the order base, prefix, suffix, modifier)."""
    total = first
    for r in rest:
        total = total + r
    return total


def _balance(total: float, rest: list[float]) -> float:
    """The share of the first part: `total` minus the shares of the others.

    Whole numbers are exact.  For fractions the exact remainder is moved by a few ulps until adding the shares
    one after the other (and, if possible, `math.fsum` too) gives `total` exactly."""
    guess = math.fsum([total] + [-r for r in rest])
    if _chain(guess, rest) == total and math.fsum([guess] + rest) == total:
        return guess
    candidates = []
    up = down = guess
    for _ in range(4):
        up = math.nextafter(up, math.inf)
        down = math.nextafter(down, -math.inf)
        candidates += [up, down]
    for c in candidates:
        if _chain(c, rest) == total and math.fsum([c] + rest) == total:
            return c
    for c in candidates:
        if _chain(c, rest) == total:
            return c
    return guess


def _own(mode: str, x: float, scale_percent: float) -> float:
    """The own value of one part in a total that is built with `mode` (plain, round, scale, trunc)."""
    if mode == "round":
        return float(round_half_away(x))
    if mode == "scale":
        return float(scale_value(x, scale_percent))
    if mode == "trunc":
        return float(truncate(x))
    return x


# ---------------------------------------------------------------------------------------------------------
# The roll of one item ([4])
# ---------------------------------------------------------------------------------------------------------
class _Run:
    def __init__(self, base: dict, seed: int, prefix: dict | None, suffix: dict | None, modifier: dict | None) -> None:
        if base is None:
            raise TypeError("the base part is required")
        for rec in (base, prefix, suffix, modifier):
            if rec is not None and not isinstance(rec, dict):
                raise TypeError("a part must be a dict (field name -> number or text) or None")
        self.stream = RollStream(seed)
        self.src = {"base": base, "prefix": prefix, "suffix": suffix, "modifier": modifier}
        self.affix_context = any(self.src[p] is not None for p in _AFFIXES)
        self.pct = {"base": BASE_JITTER_PERCENT}
        q = _num(base.get("attributeScalePercent"))
        for part in _AFFIXES:
            rec = self.src[part]
            self.pct[part] = _num(rec.get("lootRandomizerJitter")) if rec is not None else 0.0
            q = q + (_num(rec.get("lootRandomizerScale")) if rec is not None else 0.0)
        self.q = q
        cls = base.get("Class")
        self.cls = cls if isinstance(cls, str) else ""
        self.offhand = self.cls == "WeaponArmor_Offhand"
        self.weapon = self.cls.startswith("Weapon")
        self.values: dict[str, float] = {}
        self.raw: dict[str, dict[str, float]] = {p: {} for p in PARTS}
        self.shares: dict[str, dict[str, float]] = {p: {} for p in PARTS}
        self.procs: list[ProcLine] = []
        self.draws_by_part = {p: 0 for p in PARTS}
        self.log: list[DrawEntry] = []
        self.unsure: set[str] = set()
        self.unmodelled: list[str] = []
        self.conversions: dict[str, tuple[str, str]] = {}
        self.early: dict[str, float] = {}              # F0: field -> sum of the prefix and suffix rolls
        self.early_by: dict[str, dict[str, float]] = {}    # F0: field -> part -> its roll
        self.early_done = False
        if not self.cls:
            self.unsure.add("NO_CLASS")
        if self.stream.state == 0:
            self.unsure.add("U8")

    # -- access ------------------------------------------------------------------------------------------
    def _has(self, part: str, name: str) -> bool:
        rec = self.src[part]
        return rec is not None and name in rec

    def _w(self, part: str, name: str) -> float:
        rec = self.src[part]
        return _num(rec[name]) if rec is not None and name in rec else 0.0

    def _any_has(self, *names: str) -> bool:
        return any(self._has(part, name) for part in PARTS for name in names)

    def _text(self, part: str, name: str) -> str:
        rec = self.src[part]
        return str(rec[name]).strip() if rec is not None and rec.get(name) is not None else ""

    def _set_fixed(self, name: str) -> None:
        """A fixed field: the value of the first part (B, P, S, M) that has it; that part owns the whole share."""
        for part in PARTS:
            if self._has(part, name):
                self.values[name] = self._w(part, name)
                self.shares[part][name] = self.values[name]
                return

    def _scale(self, x: float) -> float:
        return float(scale_value(x, self.q))

    def _split(self, name: str, own: dict[str, float], total: float) -> None:
        """shares[part][name]: every part but the first contributing one (B, P, S, M) keeps its own value, the first
        one gets the rest (truncation rest of the common scaling, float rest) so that the shares add up to `total`."""
        order = [p for p in PARTS if p in own]
        if not order:
            return
        self.shares[order[0]][name] = _balance(total, [own[p] for p in order[1:]])
        for part in order[1:]:
            self.shares[part][name] = own[part]

    def _set(self, name: str, total: float, contributions: dict[str, float], mode: str = "plain") -> None:
        """Result field `name` = `total`, shared out over the parts of `contributions` (their rolled values)."""
        self.values[name] = total
        self._split(name, {p: _own(mode, x, self.q) for p, x in contributions.items()}, total)

    # -- rolling -----------------------------------------------------------------------------------------
    def _roll(self, kind: str, group: str, part: str, field: str, value: float) -> float:
        before = self.stream.draws
        percent = self.pct[part]
        doubtful = False
        if kind == "A":
            result, doubtful = _standard_roll(self.stream, value, percent)
        elif kind == "F":
            result, doubtful = _skill_roll(self.stream, value, percent)
        else:
            result = roll_conversion(self.stream, value, percent)
        if self.stream.draws != before:
            self.draws_by_part[part] += 1
            self.log.append(DrawEntry(group, part, field, kind))
        if doubtful:
            self.unsure.add("U2")
        return result

    def _rolls(self, kind: str, group: str, name: str, order: tuple[str, ...]) -> dict[str, float]:
        """One roll per existing part that has the field, in `order`; part -> rolled value."""
        out: dict[str, float] = {}
        for part in order:
            if self._has(part, name):
                out[part] = self._roll(kind, group, part, name, self._w(part, name))
        return out

    @staticmethod
    def _sum_psmb(contributions: dict[str, float]) -> float:
        """((P' + S') + M') + B' - the fixed order of the sums of single-value fields ([4.3])."""
        total = 0.0
        for part in _PSMB:
            if part in contributions:
                total = total + contributions[part]
        return total

    def _keep(self, name: str, contributions: dict[str, float]) -> None:
        for part, x in contributions.items():
            self.raw[part][name] = x

    # -- the groups --------------------------------------------------------------------------------------
    def _single_group(self, group: str, fields: tuple[str, ...], kind: str, order: tuple[str, ...]) -> None:
        """G01, G11, G14, G15: one roll per part and field, summed P, S, M, B, no scaling."""
        for f in fields:
            if self._any_has(f):
                c = self._rolls(kind, group, f, order)
                self._keep(f, c)
                self._set(f, self._sum_psmb(c), c)

    def _early_skills(self) -> None:
        """F0: the prefix and suffix skill values are rolled right after G01 ([4.4])."""
        self.early_done = any(self._has(p, f) for p in ("prefix", "suffix") for f in _F0_FIELDS)
        if not self.early_done:
            return
        self.early = {f: 0.0 for f in _F0_FIELDS}
        self.early_by = {f: {} for f in _F0_FIELDS}
        for part in ("prefix", "suffix"):
            for f in _F0_FIELDS:
                if self._has(part, f):
                    x = self._roll("F", "F0", part, f, self._w(part, f))
                    self.early[f] = self.early[f] + x
                    self.early_by[f][part] = x
                    self.raw[part][f] = x

    def _span_rolls(self, group: str, f: str, parts: tuple[str, ...]) -> list[tuple[str, float, float]]:
        """Min and span of every listed part that has `<f>Min` or `<f>Max`: (part, a, d), draw order min then span."""
        out = []
        for part in parts:
            if not (self._has(part, f + "Min") or self._has(part, f + "Max")):
                continue
            mn = self._w(part, f + "Min")
            mx = self._w(part, f + "Max")
            a = self._roll("A", group, part, f + "Min", mn)
            d = self._roll("A", group, part, f + "Span", max(0.0, mx - mn))
            out.append((part, a, d))
        return out

    def _flat_group(self, group: str, f: str, parts: tuple[str, ...], with_duration: bool) -> None:
        """G02 / G03 for one field: proc lines apart, the rest summed, minimum scaled, maximum = minimum + span."""
        a_sum = d_sum = 0.0
        a_by: dict[str, float] = {}
        d_by: dict[str, float] = {}
        for part, a, d in self._span_rolls(group, f, parts):
            if self.affix_context and self._has(part, f + "Chance"):
                mn = self._scale(a)
                mx = float(truncate(mn + d))
                has_duration = with_duration and self._has(part, f + "DurationMin")
                duration = self._w(part, f + "DurationMin") if has_duration else None
                chance = self._w(part, f + "Chance")
                self.procs.append(ProcLine(part, f, mn, None if mx == mn else mx, duration, chance))
            else:
                a_sum = a_sum + a
                d_sum = d_sum + d
                a_by[part] = a
                d_by[part] = d
                self.raw[part][f + "Min"] = a
                self.raw[part][f + "Span"] = d
        if a_by:
            mn = self._scale(a_sum)
            self._set(f + "Min", mn, a_by, "scale")
            # the span is not scaled: the share in the maximum is the share in the minimum plus the own span
            self._set(f + "Max", float(truncate(mn + d_sum)),
                      {p: self.shares[p][f + "Min"] + d_by[p] for p in a_by}, "trunc")

    def _affix_has_flat(self, f: str) -> bool:
        return any(self._has(p, f + "Min") or self._has(p, f + "Max") for p in _AFFIXES)

    def _g02(self) -> None:
        parts = ("base",) if self.offhand else PARTS
        for f in _G02_FIELDS:
            if f == "offensivePhysical" and self.weapon:
                if self._affix_has_flat(f):
                    self.unsure.add("U9")
                continue
            if not self._any_has(f + "Min", f + "Max"):
                continue
            if self.offhand and self._affix_has_flat(f):
                self.unsure.add("U10")
            self._flat_group("G02", f, parts, False)

    def _g03(self) -> None:
        for f in _G03_FIELDS:
            if self.offhand:
                if self._affix_has_flat(f):
                    self.unsure.add("U10")
                continue
            if self._has("base", f + "Min") and not self._has("base", f + "DurationMin"):
                self.unsure.add("U6")                  # presence, not value: the index drops zeros (7.2)
                continue
            if self._any_has(f + "Min", f + "Max"):
                self._flat_group("G03", f, PARTS, True)

    def _g04(self) -> None:
        paired: set[str] = set()
        for f in _G04_FIELDS:
            if f in _G04_DURATION and f in paired:
                continue
            if not self._any_has(f):
                continue
            scaled = f not in _G04_UNSCALED
            d = _G04_PAIR.get(f)
            if d is not None and self._any_has(d):                    # type 2: value, then duration, per part
                v_sum = u_sum = 0.0
                v_by: dict[str, float] = {}
                u_by: dict[str, float] = {}
                for part in PARTS:
                    if self.src[part] is None:
                        continue
                    v = self._roll("A", "G04", part, f, self._w(part, f))
                    u = self._roll("A", "G04", part, d, self._w(part, d))
                    v_sum = v_sum + v
                    u_sum = u_sum + u
                    if self._has(part, f):
                        v_by[part] = v
                    if self._has(part, d):
                        u_by[part] = u
                self._keep(f, v_by)
                self._keep(d, u_by)
                self._set(f, self._scale(v_sum), v_by, "scale")
                self._set(d, self._scale(u_sum), u_by, "scale")
                paired.add(d)
                continue
            contributions = self._rolls("A", "G04", f, PARTS)         # type 1
            kept = contributions
            if self.affix_context and f in _G04_PROC:
                kept = {}
                for part in PARTS:
                    if part not in contributions:
                        continue
                    chance = self._w(part, f + "Chance")
                    if chance > 0:
                        self.procs.append(ProcLine(part, f, self._scale(contributions[part]), None, None, chance))
                    else:
                        kept[part] = contributions[part]
            if kept:
                self._keep(f, kept)
                total = self._sum_psmb(kept)
                self._set(f, self._scale(total) if scaled else total, kept, "scale" if scaled else "plain")

    def _min_group(self, group: str, fields: tuple[str, ...], modes, extras: tuple[str, ...]) -> None:
        """G05 - G08, G10, G12, G13: `<f>Min` summed B, P, S, M; mode none/round/scale; fixed components copied."""
        if isinstance(modes, str):
            modes = (modes,) * len(fields)
        for f, mode in zip(fields, modes):
            name = f + "Min"
            if not self._any_has(name):
                continue
            total = 0.0
            c: dict[str, float] = {}
            for part in PARTS:
                if self._has(part, name):
                    c[part] = self._roll("A", group, part, name, self._w(part, name))
                    total = total + c[part]
            self._keep(name, c)
            if mode == "none":
                mode = "plain"
            self._set(name, _own(mode, total, self.q), c, mode)
            for suffix in extras:
                self._set_fixed(f + suffix)

    def _g09(self) -> None:
        for f in _G09_FIELDS:
            if not self._any_has(f + "Min", f + "Max"):
                continue
            a_sum = d_sum = 0.0
            a_by: dict[str, float] = {}
            m_by: dict[str, float] = {}
            for part, a, d in self._span_rolls("G09", f, PARTS):
                a_sum = a_sum + a
                d_sum = d_sum + d
                a_by[part] = a
                m_by[part] = a + d
                self.raw[part][f + "Min"] = a
                self.raw[part][f + "Span"] = d
            self._set(f + "Min", float(truncate(a_sum)), a_by, "trunc")
            self._set(f + "Max", float(truncate(a_sum + d_sum)), m_by, "trunc")

    def _g16(self) -> None:
        for pct_field, in_field, out_field in _CONVERSION_SLOTS:
            sums: dict[tuple[str, str], float] = {}
            parts_of: dict[tuple[str, str], list[tuple[str, float]]] = {}
            for part in PARTS:
                if self.src[part] is None:
                    continue
                w = self._w(part, pct_field)
                kind_in = self._text(part, in_field)
                if not kind_in or w == 0:
                    continue
                x = self._roll("U", "G16", part, pct_field, w)
                key = (kind_in, self._text(part, out_field))
                sums[key] = sums.get(key, 0.0) + x
                parts_of.setdefault(key, []).append((part, x))
            if not sums:
                continue
            first = next(iter(sums))
            contributions = dict(parts_of[first])
            self._keep(pct_field, contributions)
            self._set(pct_field, sums[first], contributions)
            self.conversions[pct_field] = first
            if len(sums) > 1:
                self.unmodelled.append(pct_field + " (several different conversion pairs)")
                self.unsure.add("U12")

    def _g17(self) -> None:
        for f in _G17_FIELDS:
            if not self._any_has(f):
                continue
            if self.early_done and f in _F0_FIELDS:                   # prefix and suffix were rolled in F0
                total = self.early[f]
                c = dict(self.early_by[f])
                for part in ("modifier", "base"):
                    if self._has(part, f):
                        c[part] = self._roll("F", "G17", part, f, self._w(part, f))
                        self.raw[part][f] = c[part]
                        total = total + c[part]
                self._set(f, total, c)
            else:
                c = self._rolls("F", "G17", f, _PSMB)
                self._keep(f, c)
                self._set(f, self._sum_psmb(c), c)

    def _finish(self) -> None:
        """[4.5]: fixed fields are taken over, unknown stat fields are reported."""
        names = sorted({n for p in PARTS if self.src[p] is not None for n in self.src[p]})
        unknown = []
        for name in names:
            if name in self.values:
                continue
            weapon_base = self.weapon and name in ("offensivePhysicalMin", "offensivePhysicalMax")
            if weapon_base or (name.startswith(_STAT_PREFIXES) and _fixed_by_rule(name)):
                self._set_fixed(name)
            elif _is_unmodelled(name):
                unknown.append(name)
        if unknown:
            self.unsure.add("U3")
        self.unmodelled = unknown + self.unmodelled

    def execute(self) -> RolledItem:
        self._single_group("G01", _G01_FIELDS, "A", _PSMB)
        self._early_skills()
        self._g02()
        self._g03()
        self._g04()
        self._min_group("G05", _G05_FIELDS, "none", ())
        self._min_group("G06", _G06_FIELDS, "round", ("Chance",))
        self._min_group("G07", _G07_FIELDS, _G07_MODES, ("DurationMin", "Chance"))
        self._min_group("G08", _G08_FIELDS, "none", ("DurationMin",))
        self._g09()
        self._min_group("G10", _G10_FIELDS, "round", ("DurationMin", "Chance"))
        self._single_group("G11", _G11_FIELDS, "A", PARTS)
        self._min_group("G12", _G12_FIELDS, "round", ("DurationMin", "Chance"))
        self._min_group("G13", _G13_FIELDS, "none", ("Chance",))
        self._single_group("G14", _G14_FIELDS, "A", PARTS)
        self._single_group("G15", _G15_FIELDS, "A", PARTS)
        self._g16()
        self._g17()
        self._finish()
        return RolledItem(
            values=dict(self.values),
            raw={p: dict(v) for p, v in self.raw.items()},
            shares={p: dict(v) for p, v in self.shares.items()},
            scale_percent=self.q,
            proc_lines=tuple(self.procs),
            draws=self.stream.draws,
            draws_by_part=dict(self.draws_by_part),
            unsure=tuple(c for c in _UNSURE_ORDER if c in self.unsure),
            unmodelled=tuple(self.unmodelled),
            draw_log=tuple(self.log),
            conversions=dict(self.conversions),
        )


def roll_item(base: dict, seed: int, prefix: dict | None = None, suffix: dict | None = None,
              modifier: dict | None = None) -> RolledItem:
    """Roll the values of one item from its seed.

    `base` is the base record (required), `prefix`, `suffix`, `modifier` the affix records that exist (None = no such
    part).  `seed` is the 32-bit unsigned item seed.  Pure: the same input always gives the same result."""
    return _Run(base, seed, prefix, suffix, modifier).execute()
