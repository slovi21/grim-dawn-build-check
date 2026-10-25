"""Readable description of a parsed savefile (port of the .35 extractor's
analyse(), without the calculated part)."""
from __future__ import annotations

import re

from .savefile import has_unsafe_extras
from .text import (DIFFICULTIES, EQUIP_SLOTS, MASTERY_NAMES, gender_text, ist_wertname,
                   reputation_tier, short)


def hhmmss(seconds):
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m {s:02d}s"


def _affix(db, dbr):
    """Affix anzeigen: Name, wenn es einen gibt, sonst Kurzname plus Bonuszeile."""
    if not dbr:
        return ""
    text = db.display(dbr)
    kurz = short(dbr)
    if not text:
        return kurz
    if ist_wertname(text):
        return f"{kurz} ({text})"
    return text


def describe_item(item, db, slot=None):
    """Ein Item als flaches, vollständiges dict."""
    if not item or not item.get("basename"):
        return None
    out = {
        "slot": slot,
        "name": db.item_name(item),
        "record": item.get("basename"),
        "kurz": short(item.get("basename")),
        "mythical": "/upgraded/" in item.get("basename", ""),
        "praefix": _affix(db, item.get("prefix_name")),
        "suffix": _affix(db, item.get("suffix_name")),
        "modifier": _affix(db, item.get("modifier_name")),
        "transmuter": _affix(db, item.get("transmute_name")),
        "komponente": _affix(db, item.get("relic_name")),
        "komponenten_bonus": _affix(db, item.get("relic_bonus")),
        "komponente_fertig": bool(item.get("relic_completion_level")),
        "augment": _affix(db, item.get("augment_name")),
        "ascended": _affix(db, item.get("ascended_name")),
        "ascended_2h": _affix(db, item.get("ascended_2h_name")),
        "rerolls": item.get("rerolls_used", 0) or None,
        "seed": item.get("seed"),
        "stack": item.get("stack_count", 1),
    }
    if "x" in item:
        out["pos"] = [item.get("x"), item.get("y")]
    return {k: v for k, v in out.items() if v not in ("", None)}


def _statistik(b16, db):
    """Statistikfelder uebernehmen und die Monsternamen aufloesen.

    Im Savefile stehen dort Lokalisierungs-Tags wie tagBossLoghorrean01,
    keine Klartextnamen."""
    out = {k: v for k, v in b16.items()
           if k not in ("id", "version", "skills_map", "_extra")}
    gm = out.get("greatest_monster_killed")
    if isinstance(gm, list):
        neu = []
        for entry in gm:
            if isinstance(entry, dict):
                entry = dict(entry)
                for feld in ("name", "last_monster_hit", "last_monster_hit_by"):
                    tag = entry.get(feld)
                    if tag:
                        klar = db.tag(tag) if hasattr(db, "tag") else ""
                        if klar:
                            entry[feld + "_klartext"] = klar
            neu.append(entry)
        out["greatest_monster_killed"] = neu
    return out


def _skill_name(rec_name, db):
    """Anzeigename eines Skills, notfalls ueber den verlinkten Buff-Record.

    Auren tragen ihren Namen nicht selbst, sondern im <name>_buff.dbr -
    deshalb standen Eintraege wie "rallyingcry1" vorher unuebersetzt da."""
    name = db.display(rec_name)
    if name:
        return name
    rec = db.record(rec_name) if hasattr(db, "record") else None
    ziel = (rec or {}).get("buffSkillName")
    if isinstance(ziel, str) and ziel:
        name = db.display(ziel)
        if name:
            return name
    return short(rec_name)


skill_name = _skill_name        # public name (character text report: bound skill of a celestial power)


def describe(doc, db):
    """Readable description of a parsed save (names, items, progress). No values
    are calculated here - that is the job of domain/stats."""
    B = {b["id"]: b for b in doc["blocks"] if "id" in b}
    h = doc["header"]
    b1, b2, b3 = B.get(1, {}), B.get(2, {}), B.get(3, {})
    b8, b13, b16 = B.get(8, {}), B.get(13, {}), B.get(16, {})

    # --- Skills / Masteries / Devotionen trennen -------------------------
    masteries, class_skills, devotions, item_skills, default_skills = {}, [], [], [], []
    for s in b8.get("skills", []):
        rec = s["skill_name"]
        entry = {
            "record": rec,
            "kurz": short(rec),
            "name": _skill_name(rec, db),
            "level": s["level"],
            "aktiv": s.get("enabled", True),
        }
        m = re.search(r"playerclass(\d+)", rec)
        if m:
            num = int(m.group(1))
            # Mastery-Balken: .../playerclassNN/_classtraining_classNN.dbr
            if short(rec).startswith("_classtraining") or short(rec) == f"playerclass{m.group(1)}":
                masteries[num] = {
                    "nummer": num,
                    "name": entry["name"] or MASTERY_NAMES.get(num, f"Class {num}"),
                    "balken": s["level"],
                }
            else:
                entry["klasse"] = num
                class_skills.append(entry)
        elif "/devotion/" in rec:
            entry["devotion_level"] = s.get("devotion_level", 0)
            entry["devotion_exp"] = s.get("devotion_experience", 0)
            devotions.append(entry)
        elif "/itemskills" in rec:
            # Trank-Controller sind keine Item-Skills im gemeinten Sinn.
            if "potion" not in rec and "container" not in rec:
                item_skills.append(entry)
        else:
            default_skills.append(entry)

    for num, mst in masteries.items():
        if (not mst["name"] or mst["name"].startswith("playerclass")
                or mst["name"].startswith("_classtraining")):
            mst["name"] = MASTERY_NAMES.get(num, f"Class {num}")
    for e in class_skills:
        e["klasse_name"] = masteries.get(e["klasse"], {}).get(
            "name", MASTERY_NAMES.get(e["klasse"], f"Class {e['klasse']}"))

    dev_taken = [d for d in devotions if d["level"] > 0]
    # Nur tatsaechlich belegte Knoten. Ohne die Levelpruefung tauchten hier
    # auch Kraefte auf, die gar nicht geskillt sind - bei Cpt Wolf etwa
    # "Huntress - Rend" mit level 0, ohne einen einzigen Huntress-Knoten.
    celestial = [d for d in devotions if d["level"] > 0
                 and (d.get("devotion_exp", 0) > 0 or d.get("devotion_level", 0) > 1)]

    # --- Ausrüstung ------------------------------------------------------
    equipment = []
    for i, it in enumerate(b3.get("equipment", [])):
        d = describe_item(it, db, EQUIP_SLOTS[i] if i < len(EQUIP_SLOTS) else f"Slot {i}")
        if d:
            equipment.append(d)

    weapon_sets = []
    for si, ws in enumerate(b3.get("weapon_sets", [])):
        items = [describe_item(it, db, ["Main hand", "Off hand"][j] if j < 2 else f"W{j}")
                 for j, it in enumerate(ws.get("items", []))]
        weapon_sets.append({
            "set": si + 1,
            "aktiv": not ws.get("unused", False),
            "items": [i for i in items if i],
        })

    sacks = []
    for i, sack in enumerate(b3.get("sacks", [])):
        entries = [describe_item(it, db) for it in sack.get("items", [])]
        sacks.append({"tasche": i, "items": [e for e in entries if e]})

    stash = []
    for i, page in enumerate(B.get(4, {}).get("pages", [])):
        entries = [describe_item(it, db) for it in page.get("items", [])]
        stash.append({
            "seite": i + 1,
            "breite": page.get("width"), "hoehe": page.get("height"),
            "items": [e for e in entries if e],
        })

    # --- Fraktionen ------------------------------------------------------
    factions = []
    for i, f in enumerate(b13.get("factions", [])):
        if not f.get("unlocked") and f.get("value", 0) == 0:
            continue
        factions.append({
            "slot": i,
            "name": db.faction_name(i),
            "wert": round(f.get("value", 0.0), 1),
            "stand": reputation_tier(f.get("value", 0.0)),
            "freigeschaltet": f.get("unlocked", False),
        })

    # --- Schwierigkeit ---------------------------------------------------
    last = b1.get("last_difficulty", 0)
    greatest = b1.get("greatest_difficulty", 0)
    diff = {
        "zuletzt_gespielt_roh": last,
        "zuletzt_gespielt": DIFFICULTIES[last & 0x0F] if (last & 0x0F) < 3 else f"?{last}",
        "hoechste_freigeschaltet": DIFFICULTIES[min(greatest, 2)],
        "hoechste_roh": greatest,
        "veteran_flag": bool(last & 0x10),
        "weitere_bits": f"0x{last & 0xE0:02X}" if last & 0xE0 else "none",
        "hinweis": "Only the lower 2 bits (difficulty) are certain. Bit 0x10 "
                   "is read as Veteran, higher bits are unknown - "
                   "verify in game.",
    }

    return {
        "datei": doc.get("_source_path", ""),
        "format": {
            "preamble_version": doc["preamble_version"],
            "data_version": doc["data_version"],
            "block_versionen": {b["id"]: b.get("version") for b in doc["blocks"]
                                if "id" in b},
            "unbekannte_bloecke": [b["id"] for b in doc["blocks"] if "_opaque" in b],
            "nicht_zugeordnete_restfelder": {
                b["id"]: (f"{len(b['_extra']['values'])} x "
                          f"{'u32' if b['_extra']['kind'] == 'ints' else 'Byte'}")
                for b in doc["blocks"] if b.get("_extra")},
            "unbekannte_restfelder": has_unsafe_extras(doc),
            "namensaufloesung": hasattr(db, "records"),
        },
        "charakter": {
            "name": h["character_name"],
            "level": h["character_level"],
            "maennlich": h["male"],
            "hardcore": h["hardcore"],
            "klassen_tag": h["player_class_tag"],
            "klasse": gender_text(db.tag(h["player_class_tag"]) if hasattr(db, "tag") else "",
                                  h.get("male", True)),
            "expansion_status": h["expansion_status"],
            "spielzeit": hhmmss(b16.get("playtime_seconds", 0)),
        },
        "attribute_basis": {
            "hinweis": "Base values from the savefile (without gear/skill/devotion bonuses). "
                       "The final values shown in game are NOT in the file.",
            "physique": b2.get("physique"),
            "cunning": b2.get("cunning"),
            "spirit": b2.get("spirit"),
            "health": b2.get("health"),
            "energy": b2.get("energy"),
        },
        "fortschritt": {
            "erfahrung": b2.get("experience"),
            "level_bio": b2.get("level_in_bio"),
            "attributpunkte_frei": b2.get("attribute_points"),
            "skillpunkte_frei": b2.get("skill_points"),
            "devotionspunkte_frei": b2.get("devotion_points"),
            "devotionspunkte_gesamt": b2.get("total_devotion_unlocked"),
            "skillpunkte_zurueckgeholt": b8.get("skill_points_reclaimed"),
            "devotionspunkte_zurueckgeholt": b8.get("devotion_points_reclaimed"),
            "iron": b1.get("iron"),
            "tributes": b1.get("tributes"),
            "in_hauptquest": b1.get("in_main_quest"),
            "schwierigkeit": diff,
            # Diese Listen sind pro Schwierigkeitsgrad gefuehrt (Block 5/6/7:
            # je 3 Listen; Block 17: 6 Listen).
            "riftgates_pro_schwierigkeit": [
                len(x) for x in B.get(6, {}).get("teleporter_points", [])],
            "respawnpunkte_pro_schwierigkeit": [
                len(x) for x in B.get(5, {}).get("spawn_points", [])],
            "kartenmarker_pro_schwierigkeit": [
                len(x) for x in B.get(7, {}).get("markers", [])],
            "shrine_listen": [len(x) for x in B.get(17, {}).get("shrines", [])],
            "lore_notizen": len(B.get(12, {}).get("lore_item_names", [])),
            "quest_tokens_pro_schwierigkeit": [
                len(t) for t in B.get(10, {}).get("tokens_per_difficulty", [])],
            "hinweis_quests": "Quest tokens in player.gdc are only a small "
                              "story part; quest progress is in the "
                              "quests.gdd files of each difficulty.",
        },
        "masteries": [masteries[k] for k in sorted(masteries)],
        "skills": sorted(class_skills, key=lambda e: (e["klasse"], -e["level"], e["kurz"])),
        "devotionen": {
            "belegte_knoten": len(dev_taken),
            "knoten": sorted(dev_taken, key=lambda e: e["kurz"]),
            "himmlische_kraefte": celestial,
        },
        "item_skills": item_skills,
        "gewaehrte_item_skills": [{
            "skill": db.display(e["skill_name"]) or short(e["skill_name"]),
            "record": e["skill_name"],
            "von_item_skill": db.display(e.get("unknown")) or short(e.get("unknown")),
        } for e in b8.get("item_skills", [])],
        "ausruestung": equipment,
        "waffensets": weapon_sets,
        "taschen": sacks,
        "stash": stash,
        "fraktionen": factions,
        "statistik": _statistik(b16, db),


    }
