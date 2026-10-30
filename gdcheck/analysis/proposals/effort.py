"""Report v2: how fast a player can do what a proposal says - the four effort tiers of the guide (spec 3.6).

  Now     a consumable in the bags, an item from the stash (`in_stock`), a bind
  Vendor  a consumable to craft or buy, a part to buy or craft ("buy: ..." with a rank that is enough, "craft or find")
  Respec  skill points, devotions, attributes, a refund, a devotion swap
  Farm    a purchase the faction rank does not allow yet (`needs_rank`), an Inventor reroll, more cooldown reduction

`detail["tier"]` wins over the rule, so a generator can say it outright (the fix proposals do)."""
from __future__ import annotations

TIERS = ("Now", "Vendor", "Respec", "Farm")
TIER_RANK = {"Now": 1, "Vendor": 2, "Respec": 3, "Farm": 4}

_RESPEC = ("skills", "devotion", "attributes", "refund", "devotion_swap")
_FARM = ("reroll", "uptime")


def tier(p) -> str:
    """The effort tier of a proposal. An unknown kind is never faster than a purchase (Vendor)."""
    said = p.detail.get("tier")
    if said in TIER_RANK:
        return said
    if p.detail.get("needs_rank") or p.kind in _FARM:
        return "Farm"
    if p.kind in _RESPEC:
        return "Respec"
    if p.kind == "bind":
        return "Now"
    return "Now" if p.in_stock else "Vendor"
