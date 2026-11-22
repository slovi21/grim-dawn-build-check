"""Which shapeshift form counts (decision 67 of the user, M19) - the one place that decides it.

A character that has levelled a form (Werewolf, Wereraven) plays in that form: the check computes and shows the form, never
the human shape ("Nur die Werwolf Gestalt ... Als Mensch zu spielen macht keinen Sinn", 19.11.2026). With both forms levelled
the form with MORE SKILL POINTS counts (the user's answer of 20.11.2026: "Die mit mehr Punkten"). Points are the points put
into the form skill itself (block 8 of the save), not the rank with gear bonuses. On equal points the Werewolf counts - the
form of the user's decision - and the fixed order after it is the record name; the report says so (`FormChoice.tie`).

The loader switches the other form off (`Activity.FORM_ONLY`: its own values, its abilities and its modifiers give nothing);
the combat model leaves the human attacks out (default attack and weapon pool skills cannot be used in a form - tooltip of
the form: "kann keine Standardwaffenangriffe oder Angriffe aus dem Waffenpool nutzen"); the report names the form. All of
them ask `choose` / `choice_of`, none has a selection of its own."""
from __future__ import annotations

from dataclasses import dataclass

SHAPESHIFT_CLASS = "Skill_Shapeshift"
CLASS_SKILLS = "/playerclass"          # a form of a mastery (an item-granted form is no levelled form)
WEREWOLF = "records/skills/playerclass10/werewolf1.dbr"
WEAPON_POOL_CLASSES = ("Skill_WPAttack", "Skill_WeaponPool")     # weapon pool skills and the default weapon attack


@dataclass(frozen=True)
class FormChoice:
    """The form that counts. `record` and `points` are its own; `others` the other levelled forms, ((record, points), ...)
    with the most points first; `tie` is True when one of them has as many points (the fixed order then decided)."""
    record: str
    points: int
    others: tuple = ()
    tie: bool = False


def _order(item) -> tuple:
    record, points = item
    return -points, record != WEREWOLF, record


def choose(points) -> FormChoice | None:
    """The counting form of {form record: points}: the most points, on a tie the Werewolf, then the record name. Entries
    without a point are no levelled form; None when no form is levelled."""
    levelled = sorted(((r, int(p)) for r, p in dict(points).items() if int(p) >= 1), key=_order)
    if not levelled:
        return None
    record, best = levelled[0]
    return FormChoice(record, best, tuple(levelled[1:]), any(p == best for _r, p in levelled[1:]))


def levelled_forms(skills, index) -> dict:
    """{form record: points} of the class forms with points in {skill record: points} (the save's skill points)."""
    out = {}
    for record, pts in dict(skills).items():
        if int(pts or 0) >= 1 and CLASS_SKILLS in record \
                and str((index.record(record) or {}).get("Class", "")) == SHAPESHIFT_CLASS:
            out[record] = int(pts)
    return out


def choice_of(states) -> FormChoice | None:
    """The counting form of the SkillStates of a SourceSet (`choose` over its class forms with points). A stand-in state
    without a class is no form."""
    return choose({s.record: s.points for s in states
                   if getattr(s, "klass", "") == SHAPESHIFT_CLASS and s.points >= 1 and CLASS_SKILLS in s.record})


def is_weapon_pool(klass: str) -> bool:
    """The default weapon attack and the weapon pool skills of the masteries (a form cannot use them)."""
    return str(klass or "").startswith(WEAPON_POOL_CLASSES)
