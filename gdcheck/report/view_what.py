"""Report v2: the short wordings of the generated changes (skill moves, devotion plans, attribute moves), shared by the steps of
the overview (`view_overview.step_of`, at most 12 words) and the suggestions of the improvements (`view_improve.what_of`, at
most 16). A change line of these kinds lists every constellation or move, which is far too long for a step: each function returns
the wordings longest first, `fit` takes the first one that fits the limit.

Words only: nothing here rates or chooses a proposal."""
from __future__ import annotations

from ..mechanics import uptime as UP
from . import common as CM

ATTRIBUTES = ("physique", "cunning", "spirit")


def words(text: str) -> int:
    return len(text.split())


def join(items) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def fit(variants, limit: int) -> str:
    """The first of `variants` (longest first) within `limit` words; if none fits, the last cut at a word boundary."""
    for v in variants:
        if v and words(v) <= limit:
            return v
    return CM.short_why(variants[-1], limit - 1)


def per_point(a) -> float:
    return float(a.ctx.attribute_constants.get("per_point", 8.0))


def extra_cdr(p) -> int:
    """The whole percent of Cooldown Reduction an uptime proposal asks for on the gear."""
    change = next((c for c in p.detail.get("apply") or () if c and c[0] == "cdr"), None)
    return int(change[1]) if change else 0


def uptime_after(p) -> float:
    """The uptime of the debuff with the extra cooldown reduction: duration over the base cooldown (the detail holds the one
    with the reduction in force) reduced by both. 100% when the detail lacks a duration or a cooldown. Block 1 (headline) and
    block 3 (gain) word the same figure."""
    d = p.detail
    cdr, duration, cooldown = float(d.get("cdr") or 0.0), d.get("duration"), d.get("cooldown")
    if not duration or not cooldown or cdr >= 100.0:
        return 1.0
    after = UP.uptime(float(duration), float(cooldown) / (1.0 - cdr / 100.0), cdr + extra_cdr(p))
    return 1.0 if after is None else after


def moves_what(p) -> list:
    moves = p.detail.get("moves") or []
    if not moves:
        return [p.change]
    m = moves[0]
    text = f"Move {plural(int(m['points']), 'point')} from {m['from']} to {m['to']}"
    rest = len(moves) - 1
    if not rest:
        return [text]
    # the counted form comes before the single move: a step that fits only the first move would hide the others
    counted = f"Move {sum(int(x['points']) for x in moves)} skill points in {len(moves)} moves"
    return [text + f", plus {plural(rest, 'more move')}", counted, text]


def plan_names(p, a) -> tuple[list, list]:
    plan = p.detail.get("plan") or {}
    names = a.ctx.extra.get("constellation_names") or {}
    nm = lambda k: names.get(k) or str(k).rsplit("/", 1)[-1]                                   # noqa: E731
    return [nm(k) for k in plan.get("out") or []], [nm(k) for k in plan.get("in") or []]


def plan_what(p, a) -> list:
    """The constellations of a plan, listed by name; counted instead when a name holds a comma ("Murmur, Mistress of Rumors"
    in a list would read as two names and a wrong count)."""
    out, in_ = plan_names(p, a)
    listed = not any("," in n for n in out + in_)
    if out and in_:
        count = f"Swap {plural(len(out), 'constellation')} for {plural(len(in_), 'other')}"
        return [f"Swap {join(out)} for {join(in_)}", f"Swap {plural(len(out), 'constellation')} for {join(in_)}",
                count] if listed else [count]
    if in_:
        counted = f"Add {plural(len(in_), 'constellation')}"
        return [f"Add {join(in_)}", counted] if listed else [counted]
    if out:
        counted = f"Remove {plural(len(out), 'constellation')}"
        return [f"Remove {join(out)}", counted] if listed else [counted]
    return [p.change]


def attribute_moves(p, a) -> tuple[list, list]:
    """([(points, Attribute)] taken away, [(points, Attribute)] added) of an attribute proposal: the new base values it
    applies against the character's, in points (`attribute_constants.per_point` raw value each)."""
    change = next((c for c in p.detail.get("apply") or () if c and c[0] == "attributes"), None)
    if change is None:
        return [], []
    new, old, per = dict(change[1]), a.ctx.character.attributes_base, per_point(a)
    delta = {k: round(((new.get(k) or 0) - (old.get(k) or 0)) / per) for k in ATTRIBUTES}
    down = sorted(((-d, k.capitalize()) for k, d in delta.items() if d < 0), key=lambda x: (-x[0], x[1]))
    up = sorted(((d, k.capitalize()) for k, d in delta.items() if d > 0), key=lambda x: (-x[0], x[1]))
    return down, up


def attributes_what(p, a) -> list:
    down, up = attribute_moves(p, a)
    if not down or not up:
        return ["Respread your attribute points"]
    return [f"Move {join(f'{n} {k}' for n, k in down)} points to {join(k for _n, k in up)}"]


def generated_what(p, a) -> list:
    """The wordings (longest first) of a skill, devotion or attribute change; [] for any other kind."""
    if p.kind == "skills":
        return moves_what(p)
    if p.kind == "devotion":
        return plan_what(p, a)
    if p.kind == "attributes":
        return attributes_what(p, a)
    return []
