"""Choose the actions for the Player View (max 3) and the lists per category."""
from __future__ import annotations


def _by_target(props) -> dict:
    groups: dict = {}
    for p in sorted(props, key=lambda p: p.sort_key()):
        groups.setdefault(p.detail.get("target") or p.id, []).append(p)
    return groups


def slot_clash(slot_part: str, used) -> bool:
    """True when a change of `slot_part` ("<slot>:<part>", e.g. "Amulet:augment_name") clashes with one already taken: the
    same part, or an item swap ("<slot>:item") and a part of that slot, whichever came first. Two different parts of one
    item (augment and component) do not clash."""
    slot, part = slot_part.rsplit(":", 1)
    return slot_part in used or f"{slot}:item" in used or (
        part == "item" and any(u.rsplit(":", 1)[0] == slot for u in used))


def best_per_target(props) -> list:
    """One proposal per addressed target (the best), then sorted by priority/effort."""
    return sorted((g[0] for g in _by_target(props).values()), key=lambda p: p.sort_key())


def _own_slot(p) -> str:
    return p.detail.get("slot_part") or ""


def player_actions(props, limit: int = 3, slot_of=_own_slot) -> list:
    """Max 3 actions, one per target, never two changes on the same item slot,
    never two ointments (only one works at a time) and never a purchase the character's
    faction rank does not allow yet (.66, detail 'needs_rank'), and never an expert-only
    proposal (.68, detail 'expert_only': Inventor rerolls - random result, rare materials). `slot_of` names the slot part a
    proposal takes (default: its own `slot_part`); the guide of report v2 passes a wider one, md and text do not."""
    groups = _by_target(props)
    order = sorted(groups, key=lambda t: groups[t][0].sort_key())
    chosen, used, ointment = [], set(), False
    for t in order:
        for p in groups[t]:
            if not (p.fixes or p.priority in ("HIGH", "MEDIUM")) or p.detail.get("needs_rank"):
                continue
            if p.detail.get("expert_only"):
                continue                 # .68: Inventor rerolls stay in E3/D4, never 'Do this next'
            if p.kind == "consumable" and p.priority not in ("HIGH", "MEDIUM"):
                continue                 # B4: a LOW consumable (EARLY) is never an action, even if it closes a gap
            sp = slot_of(p)
            if sp and slot_clash(sp, used):
                continue
            if p.kind == "consumable" and p.detail.get("ointment") and ointment:
                continue
            chosen.append(p)
            if sp:
                used.add(sp)
            ointment = ointment or bool(p.kind == "consumable" and p.detail.get("ointment"))
            break
        if len(chosen) >= limit:
            break
    return chosen


def by_category(props, per_category: int = 3) -> dict:
    out = {}
    for p in sorted(props, key=lambda p: p.sort_key()):
        lst = out.setdefault(p.category, [])
        if len(lst) < per_category:
            lst.append(p)
    return out
