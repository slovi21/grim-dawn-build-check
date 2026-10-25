"""Uniform proposal objects (F-006): every action carries gain, cost and the
explain path. Priority/effort rules (from .35, user 30.09.2026):
  priority HIGH   = fixes a self-penalty or a fact target gap (or a step towards one)
           MEDIUM = fixes a community target gap; a cost-free step towards one; an ointment that covers
                    half of a situational gap (Life Leech, Freeze, Stun, Slow)
           LOW    = fine-tuning, bonus values, costly steps towards a community gap (.64)
  Every gear proposal passes counterfactual.worth_it: score >= 2 or a closed fact target (.64).
  effort   low    = in stash / respec of skill points only
           medium = buy, craft, consumable, devotion respec, Inventor, attribute reset
           high   = another item, rerolls, rare stats
With equal benefit, what is already in the stash wins."""
from __future__ import annotations

from dataclasses import dataclass, field

PRIORITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
EFFORT_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class Delta:
    label: str
    now: float
    new: float
    unit: str = ""
    better_if_higher: bool = True

    @property
    def change(self) -> float:
        return self.new - self.now

    @property
    def good(self) -> bool:
        return (self.change > 0) == self.better_if_higher if self.change else True


@dataclass(frozen=True)
class Proposal:
    id: str
    kind: str                 # gear / consumable / reroll / skills / devotion / attributes
    category: str             # Defense / Offense / Gear / Skills / Devotions / Attributes
    title: str
    change: str               # what to do, one line
    gain: tuple               # Delta (good changes)
    cost: tuple               # Delta (bad changes) - empty means "none"
    fixes: tuple              # target ids moved to ok/tight
    breaks: tuple             # target ids that get worse status
    priority: str
    effort: str
    in_stock: bool = False
    consumable: str | None = None   # "15 min" etc.
    source: str | None = None       # where to get it (faction, craft)
    explain: tuple = ()
    score: float = 0.0              # ranking value (higher = better), not shown as a number
    detail: dict = field(default_factory=dict, compare=False)

    def sort_key(self):
        return (PRIORITY_RANK.get(self.priority, 9), EFFORT_RANK.get(self.effort, 9),
                not self.in_stock, -self.score, self.id)
