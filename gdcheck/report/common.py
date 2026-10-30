"""Proposal and boss helpers shared by the Player View, the Expert View, the detail tables and the report v2 view
(view_overview and the modules after it). Moved unchanged out of build.py; build.py keeps the old names as aliases."""
from __future__ import annotations

import re
from pathlib import Path

from ..domain.enums import LiveStatus

ROOT = Path(__file__).resolve().parents[2]

def version_state(ctx) -> str:
    """How the game data stand to the live patch - the one condition behind the version line of build.py and the data line
    of the report v2 overview: "live" (matches the live patch), "installed" (matches the installed game), "unconfirmed"
    (the installed game, but its version label is not confirmed), "mismatch" or "unchecked"."""
    st = ctx.live_status
    if st == LiveStatus.MATCHES_LIVE:
        return "live"
    if st == LiveStatus.MATCHES_INSTALLED:
        return "unconfirmed" if ctx.options.version_unconfirmed else "installed"
    return "mismatch" if st == LiveStatus.MISMATCH else "unchecked"


PROFILE_NAME = {"general": "General Ultimate", "sr": "Shattered Realm", "ascendant": "Ascendant",
                "celestial": "Celestials"}


_NUMBER = ("zero", "one", "two", "three", "four", "five", "six")


def number_word(n: int) -> str:
    """The numbers 0 to 6 as a word, any other as digits (the texts of report v2)."""
    return _NUMBER[n] if 0 <= n < len(_NUMBER) else str(n)


_DRAWBACK = re.compile(r"^\s*drawback for you:\s*", re.I)


def penalty_name(title: str) -> str:
    """The name in the title of a self-penalty finding: "DRAWBACK FOR YOU: Absolute Zero (rank 1)" -> "Absolute Zero"."""
    return re.sub(r"\s*[(][^)]*[)]", "", _DRAWBACK.sub("", title)).strip()


def short_why(text: str, words: int = 25) -> str:
    """N-011: a reason of at most 25 words, cut at a word boundary (before: 160 characters, cut mid-word).
    A cut never leaves an open bracket or a trailing dash (.62, code review)."""
    w = str(text or "").split()
    if len(w) <= words:
        return " ".join(w)
    cut = " ".join(w[:words])
    if cut.count("(") > cut.count(")"):
        cut = cut[:cut.rfind("(")]
    return cut.rstrip(" ,;:-(") + " ..."


def display_name(label: str) -> str:
    """The label of a value as a page shows it: "Pierce Res" is "Pierce Resistance"."""
    return label[:-4] + " Resistance" if label.endswith(" Res") else label


def view_label(label: str) -> str:
    """`display_name` without the internal word "(weighted)" ("Armor (weighted)" is "Armor"): the one name of a value in the
    gains and costs of blocks 1 and 3 of the page. md and text keep the raw labels."""
    return display_name(label).replace(" (weighted)", "")


def signed(d, player: bool = False, name=None):
    """"Pierce Res -10%"; `name` turns the label into the one a page shows ("Pierce Resistance")."""
    label = name(d.label) if name else d.label
    unit = d.unit if d.unit == "%" else ""
    ch = d.change
    if player and abs(ch) < 0.5:              # B7: no decimals in the Player View - never '+0%'
        return f"{label} {'+' if ch > 0 else '-'}<1{unit}"
    num = f"{ch:,.1f}" if unit == "%" and abs(ch) < 1 and not player else f"{ch:,.0f}"
    return f"{label} {'+' if ch > 0 else ''}{num}{unit}"


def lost_grouped(lost) -> list[str]:
    """Player View (A4, actions <=2 lines): '+50% Aether Damage', '+50% Fire Damage' -> one
    'you lose +50% Aether and Fire Damage' - every drawback still named, only shorter."""
    groups: dict = {}
    other = []
    for x in lost:
        m = re.fullmatch(r"(\S+) (.+) Damage", str(x))
        if m:
            groups.setdefault(m.group(1), []).append(m.group(2))
        else:
            other.append(str(x))
    parts = [f"{v} " + (", ".join(n[:-1]) + " and " + n[-1] if len(n) > 1 else n[0]) + " Damage"
             for v, n in groups.items()]
    return ([f"you lose {', '.join(parts)}"] if parts else []) + [f"you lose {x}" for x in other]


def cost_text(p, player: bool = False, name=None) -> str:
    """Every drawback is named (N-006) - costs are never cut off; lost bound Celestial Powers
    count as a drawback (the counterfactual cannot see procs). `name`: see `signed` (the HTML view names its labels like its
    gains; md and text keep the raw ones)."""
    lost = [f"loses Celestial Power {n} (bound to {b}" + (", your low-health circuit breaker" if t == "LowHealth"
            else "") + ")" for n, b, t in (p.detail.get("lost_powers") or ())]
    raw = list(p.detail.get("lost") or ())
    lost += lost_grouped(raw) if player else [f"you lose {x}" for x in raw]
    if p.cost or lost:
        return ", ".join([signed(d, player, name) for d in p.cost] + lost)
    if p.kind == "consumable":
        if not p.detail.get("ointment"):
            return "none (temporary)"
        return "uses your ointment slot (one at a time)" if player else \
            "uses your ointment slot - only one ointment works at a time"
    return "none for your stats"


GAIN_FOR = {"health": ("Health",), "da": ("Defensive Ability",), "physical": ("Physical Res",),
             "absorption": ("Armor Absorption",), "armor": ("Armor (weighted)",), "leech": ("Life Leech Res",),
             "freeze": ("Freeze Res",), "stun": ("Stun Res",), "slow": ("Slow Res",), "pth": ("Offensive Ability",)}


def ordered_gains(p, a) -> list:
    """The gains of a proposal, those that touch an open target first (the order `gain_text` writes them in)."""
    open_ids = [r.id for r in a.results if r.status in ("gap", "tight")]
    below = set()
    for r in a.results:
        if r.id in ("res_ultimate", "res_own_cap") and r.status in ("gap", "tight") and "below: " in r.now_text:
            below |= {x.rsplit(" ", 1)[0] + " Res" for x in r.now_text.split("below: ")[-1].split(", ")}
        elif r.status in ("gap", "tight") and r.id.startswith(("ravager", "fire_", "lightning_")):
            below.add(r.label.split(" buffer")[0].split()[-1] + " Res")

    def touches(d) -> bool:
        if d.label in below:
            return True
        return any(d.label in labs for k, labs in GAIN_FOR.items() if any(i.startswith(k) for i in open_ids))
    return sorted(p.gain, key=lambda d: not touches(d))


def gain_text(p, a, n: int = 3, player: bool = True) -> str:
    """Gains that touch an open target first, then the rest; '+k more' instead of silently cutting
    (.62, product review: Cpt Cheat's Aether +18% that closes a gap was the 4th value and hidden). E3 uses
    it too (.67, Cpt Piercewolf: Aether Res +20% closed the target but was the 5th gain), with the
    expert number format (player=False)."""
    gains = ordered_gains(p, a)
    txt = ", ".join(signed(d, player) for d in gains[:n])
    return txt + (f" +{len(gains) - n} more" if len(gains) > n else "")


# what a proposal without an imperative verb (the generated skill, devotion and attribute changes) is called after "after"
_KIND_GERUND = {"skills": "moving the skill points", "devotion": "the devotion change",
                "attributes": "moving the attribute points", "reroll": "the reroll"}
_GERUND = {"Drink": "drinking", "Apply": "applying", "Put": "putting", "Wear": "wearing", "Refund": "refunding",
           "Bind": "binding", "Keep": "keeping", "Swap": "swapping", "Reroll": "rerolling", "Take": "taking"}


def _gear_parts(p) -> tuple[str, str, str] | None:
    """(kind, new item or part, slot) of a gear proposal read from its change line: kind "part" for an augment or a
    component ("<name> on <slot> (replaces <old>)" or "(free slot)"), "item" for a swap from the stash
    ("<name> instead of <old> (<slot>)"); None when the line has neither shape."""
    sp = p.detail.get("slot_part") or ""
    if p.kind != "gear" or not sp:
        return None
    if p.detail.get("name") and p.detail.get("slot"):          # the generators say it outright
        return ("item" if sp.endswith(":item") else "part"), p.detail["name"], p.detail["slot"]
    if sp.endswith(":item"):
        m = re.fullmatch(r"(.+?) instead of (.+) \(([^()]+)\)", p.change)
        return ("item", m.group(1), m.group(3)) if m else None
    m = re.fullmatch(r"(.+) on ([^()]+?) \((?:free slot|replaces .+)\)", p.change)
    return ("part", m.group(1), m.group(2)) if m else None


def _consumable_name(p) -> str:
    if p.detail.get("name"):
        return p.detail["name"]
    m = re.fullmatch(r"Use (.+?)(?: before hard fights)?", p.change)
    return m.group(1) if m else p.change


def action_text(p) -> str:
    """What a player does for a proposal, as an imperative (report v2, the steps of the overview): "Drink Royal Jelly
    Essence", "Apply Spiritbane Ointment", "Put Basilisk's Gaze on your amulet", "Wear <item> from your stash". Every
    other kind (refund, bind, uptime, swap, generated changes) already reads as an imperative: its change line."""
    if p.kind == "consumable":
        return f"{'Apply' if p.detail.get('ointment') else 'Drink'} {_consumable_name(p)}"
    parts = _gear_parts(p)
    if parts and parts[0] == "part":
        return f"Put {parts[1]} on your {parts[2].lower()}"
    if parts:
        return f"Wear {parts[1]} from your stash"
    return p.change


def action_gerund(p) -> str:
    """`action_text` as a gerund phrase for "14% after refunding Absolute Zero"; a refund names the skill, not the points."""
    text = action_text(p)
    verb, _, rest = text.partition(" ")
    if verb not in _GERUND or p.kind in _KIND_GERUND:
        return _KIND_GERUND.get(p.kind, "this change")
    if verb == "Refund":
        rest = re.sub(r"^the (?:\d+ )?points? in ", "", rest)
    return f"{_GERUND[verb]} {rest}"


def item_name(p) -> str:
    """The thing a proposal brings, without its verb, for "<name> closes it": a consumable's name, an augment's or a
    component's name, an item's name; otherwise the change line."""
    if p.kind == "consumable":
        return _consumable_name(p)
    parts = _gear_parts(p)
    return parts[1] if parts else p.change


def slot_phrase(p) -> str:
    """"on your amulet" for an augment or component, "" for everything else."""
    parts = _gear_parts(p)
    return f"on your {parts[2].lower()}" if parts and parts[0] == "part" else ""


def boss_fights(a) -> dict:
    """Boss name -> fight key (B2): forms and phases of one fight share the key. The link is the boss
    data's spawn-on-death chain ('phases', data.boss_graph), never the name - a test save: 'The Final
    Form ...' and 'The Actually Final Form ...' are one fight. Bosses without data: their own key."""
    try:
        from ..data import bosses as BD
        from ..data import versioned as V
        ver, fp = V.index_identity(a.ctx.index)
        data = (BD.combat_meta(ROOT, ver or "1.3.0.8", fp) or {}).get("bosses") or {}
    except (OSError, ValueError, AttributeError, TypeError):   # no boss data: every boss is its own fight
        data = {}
    parent: dict = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for b in data.values():
        recs = [b.get("record")] + [ph.get("record") for ph in b.get("phases") or ()]
        recs = [r for r in recs if r]
        for r in recs[1:]:
            parent[find(r)] = find(recs[0])
    return {name: find(b["record"]) for name, b in data.items() if b.get("record")}


def only_with_consumable(b) -> bool:
    """N-010: the named consumable closes every reason - the boss is prepared only with it. The DA-shred
    reason has no consumable field, so a boss with it is never 'with consumable'; nor is one that needs two
    ointments at once (only one works at a time)."""
    return bool(b.consumable) and bool(b.reasons) and not getattr(b, "ointments", ()) \
        and not any(r.startswith("lowers your DA") for r in b.reasons)
