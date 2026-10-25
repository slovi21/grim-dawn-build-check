"""Second-level boss model from the game database (KG-08, AP-B2): the skill graph of a monster.

From the monster record the walk follows the references that make damage or change the fight:

    monster  skillNameN, specialAttackSkillNameN, attackSkillName, initialSkillName, dyingSkillName
    skill    autoCastSkill (+ autoCastController: chanceToRun, triggerType), buffSkillName,
             buffSelfSkillName, spawnObjects (monsters with skills of their own), augmentSkillName,
             chainInitialSkill, chainNextSkill, grantedSkills

with a depth limit and a cycle guard. Each node keeps: parent, edge, depth, the monster whose level
applies (owner), the skill level equation (skills of a monster: skillLevelN of that monster; skills
reached through another skill: the level of that skill, ASSUMED), autocast chance, spawn limits
(petLimit, petPeriod, spawnObjectsTimeToLive, cooldown) and the life threshold of enrage skills
(lifeMonitorPercent). Phases: monsters that spawn the boss on death (poolToSpawnOnDeath -> pool
nameN) and the ones the boss spawns, in fight order. Pure: `read(record) -> dict | None`.
"""
from __future__ import annotations

import re

MAX_DEPTH = 5
MONSTER_EDGES = ("skillName", "specialAttackSkillName", "attackSkillName", "initialSkillName", "dyingSkillName")
SKILL_EDGES = ("autoCastSkill", "buffSkillName", "buffSelfSkillName", "spawnObjects", "augmentSkillName",
               "chainInitialSkill", "chainNextSkill", "grantedSkills")
SPAWN_KEYS = ("petLimit", "petPeriod", "spawnObjectsTimeToLive", "skillCooldownTime", "petBurstSpawn")
SCHEMA = 3


def _refs(rec: dict, prefix: str):
    """(field, record) for every field `prefix` or `prefix<N>` with a .dbr value (lists flattened)."""
    for k in sorted(rec):
        if not re.fullmatch(re.escape(prefix) + r"\d*", k):
            continue
        vals = rec[k] if isinstance(rec[k], list) else [rec[k]]
        for v in vals:
            if isinstance(v, str) and v.lower().endswith(".dbr"):
                yield k, v


def _is_monster(rec: dict) -> bool:
    cls = str(rec.get("Class", ""))
    return cls in ("Monster", "Pet", "PetPlayerScaling") or cls.startswith("Monster")


def _first(v):
    return v[0] if isinstance(v, list) and v else v


def _monster_children(read, mrec: str):
    """(field, skill record, skill level equation) of a monster's skill slots (disabled slots skipped)."""
    r = read(mrec) or {}
    for field, srec in [x for e in MONSTER_EDGES for x in _refs(r, e)]:
        n = field[len(next(e for e in MONSTER_EDGES if field.startswith(e))):]
        eq = r.get(f"skillLevel{n}") if field.startswith("skillName") else None
        if field.startswith("skillName") and str(eq if eq is not None else "").strip() == "0":
            continue                                    # disabled slot
        yield field, srec, (str(eq) if eq not in (None, "") else None)


def walk(read, monster: str, max_depth: int = MAX_DEPTH) -> list[dict]:
    """Nodes of the skill graph below `monster` (the monster itself is not a node), breadth first so
    every record gets its shallowest depth. A node is unique per (record, owner); every node lists
    ALL its outgoing references in `edges` ([field, record]) - also those whose target was reached
    first through another path (e.g. a boss's '_secondary' skill that is also its own skill slot)."""
    from collections import deque
    nodes: list[dict] = []
    seen: set = set()
    queue: deque = deque()
    for field, srec, eq in _monster_children(read, monster):
        queue.append((srec, monster, field, 1, monster, eq))
    while queue:
        srec, parent, via, depth, owner, level_eq = queue.popleft()
        key = (srec, owner)
        if depth > max_depth or key in seen:
            continue
        seen.add(key)
        r = read(srec) or {}
        node = {"record": srec, "class": str(r.get("Class", "")), "parent": parent,
                "via": re.sub(r"\d+$", "", via), "depth": depth, "owner": owner, "level_eq": level_eq}
        nodes.append(node)
        if _is_monster(r):
            node["kind"] = "monster"
            node["level_eq"] = None                      # level: its charLevel (summoner level, ASSUMED)
            node["charLevel"] = r.get("charLevel")
            node["attack_speed"] = r.get("characterAttackSpeed")
            node["edges"] = []
            for field, child, eq in _monster_children(read, srec):
                node["edges"].append([re.sub(r"\d+$", "", field), child])
                queue.append((child, srec, field, depth + 1, srec, eq))
            continue
        node["kind"] = "skill"
        ctl = read(str(r.get("autoCastController") or "")) or {}
        if ctl:
            node["autocast"] = {"controller": r.get("autoCastController"), "chance": ctl.get("chanceToRun"),
                                "trigger": ctl.get("triggerType")}
        spawn = {k: r.get(k) for k in SPAWN_KEYS if r.get(k) not in (None, 0, 0.0, "")}
        if spawn and list(_refs(r, "spawnObjects")):
            node["spawn"] = spawn
        if r.get("lifeMonitorPercent"):
            node["life_pct"] = r.get("lifeMonitorPercent")
        node["edges"] = []
        for e in SKILL_EDGES:
            for field, child in _refs(r, e):
                if [e, child] not in node["edges"]:
                    node["edges"].append([e, child])
                queue.append((child, srec, field, depth + 1, owner, level_eq))
    return nodes


def phases(read, names, monster: str) -> list[dict]:
    """Fight phases around `monster`: [{'record', 'attack_speed', 'position'}] in order (the boss
    record has position 0, earlier phases negative). `names`: all record names of the database."""
    pools: dict = {}
    for n in names:
        if n.startswith("records/proxies/") and "deathspawn" in n:
            for _f, m in _refs(read(n) or {}, "name"):
                pools.setdefault(m, set()).add(n)
    spawned_by: dict = {}
    for n in names:
        if not n.startswith("records/creatures/"):
            continue
        r = read(n) or {}
        p = r.get("poolToSpawnOnDeath")
        if p:
            spawned_by.setdefault(str(p), set()).add(n)

    def before(rec):
        out = set()
        for pool in pools.get(rec, ()):
            out |= spawned_by.get(pool, set())
        return sorted(out)

    def after(rec):
        pool = (read(rec) or {}).get("poolToSpawnOnDeath")
        return [m for _f, m in _refs(read(str(pool)) or {}, "name")] if pool else []

    chain = [monster]
    cur = monster
    for _ in range(6):
        prev = [p for p in before(cur) if p not in chain]
        if len(prev) != 1:
            break
        chain.insert(0, prev[0])
        cur = prev[0]
    cur = monster
    for _ in range(6):
        nxt = [n for n in after(cur) if n not in chain]
        if len(nxt) != 1:
            break
        chain.append(nxt[0])
        cur = nxt[0]
    pos0 = chain.index(monster)
    return [{"record": r, "attack_speed": _first((read(r) or {}).get("characterAttackSpeed")),
             "position": i - pos0} for i, r in enumerate(chain)] if len(chain) > 1 else []
