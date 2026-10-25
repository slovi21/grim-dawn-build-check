"""ReportModel - plain data for every renderer (F-001, ADR-3). Renderers only
format; they never compute game mechanics (F-008)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Area:
    name: str
    status: str            # OK / RISK / GAP / INFO
    text: str
    value: int | None = None   # .70: how far the area's targets are met, 0-100 (None = no bar)


@dataclass
class PlayerIssue:
    text: str
    why: str = ""
    kind: str = ""          # self_penalty / error / must / recommended / warning
    target: str | None = None      # the target id of a must / recommended issue (the order is testable)


@dataclass
class PlayerAction:
    what: str
    gain: str
    cost: str
    tags: list = field(default_factory=list)     # "in your stash", "consumable 15 min", where to get
    priority: str = ""
    effort: str = ""


@dataclass
class PlayerBoss:
    name: str
    risk: str              # LOW / MEDIUM / HIGH
    reason: str
    prepare: str = ""


@dataclass
class Table:
    key: str               # E1 ... / D1 ...
    title: str
    columns: list
    rows: list             # list of lists of strings
    notes: list = field(default_factory=list)
    cells: list = field(default_factory=list)   # .56: per row {column id: provenance} (E7; JSON only)


@dataclass
class Report:
    meta: dict
    header: dict
    readiness: dict
    warnings: list
    areas: list
    issues: list
    actions: list
    bosses: list
    expert: list           # Tables E1-E6
    detail: list           # Tables D1-...
    chat_hint: str = ""
    boss_legend: str = ""   # one line under the boss lines: what HIGH / MEDIUM / LOW mean (B2)

    def to_dict(self) -> dict:
        d = asdict(self)
        for group in ("expert", "detail"):          # 'cells' only where a table has them (E7)
            for t in d.get(group) or ():
                if not t.get("cells"):
                    t.pop("cells", None)
        return d
