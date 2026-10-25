"""Domain enums. StackingPolicy and Confidence live in mechanics.policy (pure layer)
and are re-exported here."""
from __future__ import annotations

from enum import Enum

from ..mechanics.policy import AUDIT_CLASS, Confidence, StackingPolicy  # noqa: F401


class SourceKind(str, Enum):
    ITEM = "item"                  # item base record
    AFFIX = "affix"                # prefix / suffix
    COMPONENT = "component"        # relic_name on an item
    COMPONENT_BONUS = "component_bonus"
    AUGMENT = "augment"
    ASCENDED = "ascended"
    TRANSMUTER = "transmuter"      # modifier_name / transmute_name of an item
    SKILL = "skill"                # mastery skill with points
    DEVOTION = "devotion"          # devotion node
    ITEM_MODIFIER = "item_modifier"  # modifierSkillName<n> of an item part
    GRANTED_SKILL = "granted_skill"  # itemSkillName of an item part


class Activity(str, Enum):
    PERMANENT = "permanent"
    TOGGLE = "toggle"
    TEMPORARY = "temporary"                       # buff with duration, recast needed
    PROC = "proc"
    SKILL_ONLY = "skill_only"                     # only for one attack/spell
    WEAPON_CONDITION_UNMET = "weapon_condition"
    ENEMY_DEBUFF = "enemy_debuff"
    FORM_ONLY = "form_only"                       # only while transformed


ON_SHEET = (Activity.PERMANENT, Activity.TOGGLE)


class ContentProfile(str, Enum):
    GENERAL = "general"
    SR = "sr"
    ASCENDANT = "ascendant"
    CELESTIAL = "celestial"


class Stage(str, Enum):
    EARLY = "EARLY"
    MID = "MID"
    ENDGAME = "ENDGAME"


class Difficulty(int, Enum):
    NORMAL = 0
    ELITE = 1
    ULTIMATE = 2

    @property
    def label(self) -> str:
        return ("Normal", "Elite", "Ultimate")[self.value]


class Basis(str, Enum):
    FACT = "fact"
    COMMUNITY = "community"
    SCENARIO = "scenario"


class LiveStatus(str, Enum):
    MATCHES_LIVE = "matches_live"
    MATCHES_INSTALLED = "matches_installed"
    MISMATCH = "mismatch"
    NOT_CHECKED = "not_checked"
