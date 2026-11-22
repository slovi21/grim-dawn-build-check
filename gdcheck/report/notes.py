"""The calm sentences of the report (M19, decisions 67 and 71): the form that counts, skill points the save has not spent, and
how the item values are made. Plain sentences in English, like the other hints of the report; the player view of the chat shows
them in one line (`render_md.player`), the text and the page next to the header, the Expert View in the notes of E1 and E2.
Only wording lives here - the choice of the form is `domain.forms`, the free points come from `analysis.build_quality`."""
from __future__ import annotations

from ..analysis import build_quality as BQ
from ..data.text import short
from ..domain import forms as FM
from ..domain import item_values as IV

ITEM_VALUES = "Item values are rolled from each item's seed as the game does it."


def points_text(n: int) -> str:
    return f"{n} point{'' if n == 1 else 's'}"


def name_of(index):
    """record -> the name the game shows ("Werewolf"), the file name without its suffix for an unknown record."""
    return lambda record: (index.display(record) if index is not None else "") or short(record)


def form_of(a) -> FM.FormChoice | None:
    """The form the report is calculated in, None for a build without a levelled form."""
    return FM.choice_of(a.sources.skills)


def form_label(choice, name) -> str:
    """"Werewolf form": the header line and the facts of the page."""
    return f"{name(choice.record)} form" if choice is not None else ""


def form_sentence(choice, name) -> str:
    """What the report is calculated in: one form (decision 67), or the form with more points of two (decision 67 (c): "the
    Werewolf form (12 points), Wereraven has 8"); a tie goes to the Werewolf (domain.forms)."""
    head = f"Calculated in {form_label(choice, name)} ({points_text(choice.points)})"
    if not choice.others:
        return f"{head}; the human form is not shown."
    rest = ", ".join(f"{name(r)} has {p}" for r, p in choice.others)
    if choice.tie:
        return f"{head}; {rest} too - on a tie the Werewolf counts."
    return f"{head}, the form with more points; {rest}."


def attack_sentence(choice, name) -> str:
    """The note under the damage table: why the default attack is not listed."""
    return (f"{form_label(choice, name)}: the default attack and weapon pool skills cannot be used in a form, so they are "
            "not listed.")


def unspent_sentence(free: int) -> str:
    """Skill points the save has not spent (block 2 of the save); "" when none are free."""
    if free < 1:
        return ""
    return f"{free} skill point{' is' if free == 1 else 's are'} still unspent; the values are for the build as it stands."


def unspent_skill_points(a) -> int:
    return BQ.unspent_points(a.ctx)[1]


def unrolled_sentence(n: int) -> str:
    """Worn items with a seed that could not be rolled (they show the values of the game data's base item); "" when none."""
    if n < 1:
        return ""
    return f"{n} worn item{' shows' if n == 1 else 's show'} base values: part not in the game data."


def unrolled_items(a) -> int:
    """How many worn items have a seed and could not be rolled (`item_values.unrolled`)."""
    index = getattr(a.ctx, "index", None)
    return 0 if index is None else sum(1 for w in a.ctx.character.worn if IV.unrolled(index, w.item))


def notes_of(a) -> list:
    """The sentences of the report in their order: form (only with a levelled form), unspent skill points (only when some
    are free), item values (always), and after it the worn items that could not be rolled (only when there are some)."""
    out = []
    choice = form_of(a)
    if choice is not None:
        out.append(form_sentence(choice, name_of(getattr(a.ctx, "index", None))))
    free = unspent_sentence(unspent_skill_points(a))
    if free:
        out.append(free)
    out.append(ITEM_VALUES)
    base_only = unrolled_sentence(unrolled_items(a))
    if base_only:
        out.append(base_only)
    return out
