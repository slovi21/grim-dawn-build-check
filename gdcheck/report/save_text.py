"""Character text report of a save (port of the .35 extractor's render_text, gd_extract part 7):
CHARACTER, ATTRIBUTES, PROGRESS, EQUIPMENT, WEAPON SETS, SKILLS, DEVOTIONS, SKILLS GRANTED BY ITEMS,
FACTIONS, STORED ITEMS, CALCULATED VALUES, STATISTICS, FORMAT / COMPLETENESS.

Input is the save document of analysis.save_facts (legacy keys, data/describe.py + "berechnet").
One change against .35: the heading "Celestial powers (bound to a skill)" listed every power with
devotion experience, bound or not - now "Celestial powers", each line names the skill it is bound
to (block 8 skills + item_skills, domain.character bindings) or says "not bound"."""
from __future__ import annotations

from ..analysis.save_facts import stat_text
from ..data import stat_labels
from ..data.describe import skill_name
from ..data.text import DIFFICULTIES
from ..domain.character import from_doc

__all__ = ["GROUPS", "FIELD_LABEL", "GROUP_ORDER", "group_stats", "celestial_bindings", "render_text",
           "line"]

# Display groups of the summed bonus fields. Fields without a label land in "Other" under their
# DBR name - nothing is lost when a patch adds fields.
GROUPS = [
    ("Resistances", [
        ("defensiveFire", "Fire"), ("defensiveCold", "Cold"),
        ("defensiveLightning", "Lightning"), ("defensivePoison", "Acid/Poison"),
        ("defensivePierce", "Pierce"), ("defensiveBleeding", "Bleeding"),
        ("defensiveAether", "Aether"), ("defensiveChaos", "Chaos"),
        ("defensiveLife", "Vitality"), ("defensiveElementalResistance", "Elemental (all 3)"),
        ("defensivePhysical", "Physical"),
        ("defensiveSlowLifeLeach", "Life Leech"),
        ("defensiveTotalSpeedResistance", "Slow"),
        ("defensiveStun", "Stun"), ("defensiveFreeze", "Freeze"),
        ("defensiveSleep", "Sleep"), ("defensivePetrify", "Petrify"),
        ("defensiveTrap", "Trap"), ("defensiveDisruption", "Disruption"),
        ("defensiveConfusion", "Confusion"), ("defensiveFear", "Fear"),
        ("defensiveConvert", "Convert"), ("defensiveKnockdown", "Knockdown"),
        ("defensiveTaunt", "Taunt"), ("defensiveManaBurnRatio", "Energy Burn"),
        ("defensiveReflect", "Reflect"),
    ]),
    ("Defensive stats", [
        ("defensiveProtection", "Armor (flat)"),
        ("defensiveProtectionModifier", "Armor %"),
        ("defensiveAbsorptionModifier", "Armor Absorption %"),
        ("defensiveAbsorption", "Damage Absorption (flat)"),
        ("characterDefensiveAbility", "Defensive Ability (flat)"),
        ("characterDefensiveAbilityModifier", "Defensive Ability %"),
        ("characterLife", "Health (flat)"), ("characterLifeModifier", "Health %"),
        ("characterLifeRegen", "Health Regeneration"),
        ("characterLifeRegenModifier", "Health Regeneration %"),
        ("defensiveBlock", "Block Value"), ("defensiveBlockChance", "Block Chance"),
        ("defensiveBlockAmountModifier", "Block Value %"),
        ("defensiveBlockModifier", "Block Chance %"),
        ("defensiveDodge", "Dodge (melee)"),
        ("defensiveProjectile", "Deflect (projectiles)"),
    ]),
    ("Offensive stats", [
        ("characterOffensiveAbility", "Offensive Ability (flat)"),
        ("characterOffensiveAbilityModifier", "Offensive Ability %"),
        ("characterAttackSpeedModifier", "Attack Speed %"),
        ("characterSpellCastSpeedModifier", "Cast Speed %"),
        ("characterRunSpeedModifier", "Movement Speed %"),
        ("characterTotalSpeedModifier", "Total Speed %"),
        ("characterHuntingShoutModifier", "Total Damage %"),
        ("offensivePhysicalMin", "Physical (min)"), ("offensivePhysicalMax", "Physical (max)"),
        ("offensivePhysicalModifier", "Physical %"),
        ("offensivePierceRatioMin", "Armor Piercing %"),
        ("offensivePierceMin", "Pierce (min)"), ("offensivePierceMax", "Pierce (max)"),
        ("offensivePierceModifier", "Pierce %"),
        ("offensiveFireMin", "Fire (min)"), ("offensiveFireMax", "Fire (max)"),
        ("offensiveFireModifier", "Fire %"),
        ("offensiveColdMin", "Cold (min)"), ("offensiveColdModifier", "Cold %"),
        ("offensiveLightningMin", "Lightning (min)"), ("offensiveLightningModifier", "Lightning %"),
        ("offensivePoisonMin", "Acid (min)"), ("offensivePoisonModifier", "Acid %"),
        ("offensiveLifeMin", "Vitality (min)"), ("offensiveLifeModifier", "Vitality %"),
        ("offensiveAetherMin", "Aether (min)"), ("offensiveAetherModifier", "Aether %"),
        ("offensiveChaosMin", "Chaos (min)"), ("offensiveChaosModifier", "Chaos %"),
        ("offensiveSlowPhysicalMin", "Internal Trauma (min)"),
        ("offensiveSlowPhysicalModifier", "Internal Trauma %"),
        ("offensiveSlowPhysicalDurationModifier", "Internal Trauma Duration %"),
        ("offensiveSlowBleedingMin", "Bleeding (min)"),
        ("offensiveSlowBleedingModifier", "Bleeding %"),
        ("offensiveSlowBleedingDurationModifier", "Bleeding Duration %"),
        ("offensiveLifeLeechMin", "Life Leech (flat)"),
        ("offensiveLifeLeechModifier", "Life Leech %"),
        ("offensivePercentCurrentLifeMin", "% Current Life"),
        ("offensiveTotalDamageReductionPercentMin", "Damage Reduction %"),
        ("offensiveCritDamageModifier", "Crit Damage %"),
    ]),
    ("Attributes", [
        ("characterStrength", "Physique (flat)"),
        ("characterStrengthModifier", "Physique %"),
        ("characterDexterity", "Cunning (flat)"),
        ("characterDexterityModifier", "Cunning %"),
        ("characterIntelligence", "Spirit (flat)"),
        ("characterIntelligenceModifier", "Spirit %"),
        ("characterMana", "Energy (flat)"), ("characterManaModifier", "Energy %"),
        ("characterManaRegen", "Energy Regeneration"),
        ("characterManaRegenModifier", "Energy Regeneration %"),
        ("characterIncreasedExperience", "Experience %"),
    ]),
]

FIELD_LABEL = {f: (grp, lbl) for grp, fields in GROUPS for f, lbl in fields}
GROUP_ORDER = [g for g, _ in GROUPS] + ["Other"]

STAT_LABELS = {
    "death_count": "Deaths", "kill_count": "Kills", "hero_kills": "Hero kills",
    "champion_kills": "Champion kills", "boss_kills": "Boss kills",
    "greatest_damage_done": "Greatest damage dealt",
    "greatest_damage_received": "Greatest damage taken",
    "crits_inflicted": "Crits dealt", "crits_received": "Crits taken",
    "hits_received": "Hits taken", "hits_inflicted": "Hits dealt",
    "health_potions_used": "Health potions", "items_crafted": "Items crafted",
    "devotion_shrines_unlocked": "Shrines unlocked",
    "lore_notes_collected": "Lore notes", "max_level": "Highest level",
    "experience_from_kills": "XP from kills",
}


def group_stats(totals: dict, names=None) -> dict:
    """Raw fields -> display groups [{feld, label, wert, text?}] (internal fields left out)."""
    grouped: dict = {g: [] for g in GROUP_ORDER}
    for field, value in sorted(totals.items()):
        grp, lbl = FIELD_LABEL.get(field, ("Other", field))
        row = {"feld": field, "label": lbl, "wert": round(value, 2)}
        text = stat_text(field, value, names)
        if text == "":
            continue                    # internal control fields are not shown
        if text:
            row["text"] = text
        grouped[grp].append(row)
    return {g: rows for g, rows in grouped.items() if rows}


def celestial_bindings(doc: dict, names) -> dict:
    """Celestial power record -> display name of the skill it is bound to (block 8 skills and
    item_skills: form abilities such as Feral Claws are only in item_skills)."""
    if names is None:
        return {}
    bindings = from_doc(doc, names.display, names.tag).bindings
    return {power: skill_name(skill, names) for power, (skill, _controller) in bindings.items()}


def line(k, v, w=32) -> str:
    return f"  {k:<{w}} {v}"


def _num(v):
    return f"{v:,}" if isinstance(v, int) else v


def _item_label(it) -> str:
    """'Mythical Reaver's Claw  [d011_sword]' or just 'd011_sword'."""
    name = it.get("name") or it["kurz"]
    myth = "Mythical " if it.get("mythical") and not name.lower().startswith("mythical") else ""
    label = f"{myth}{name}"
    return label if name == it["kurz"] else f"{label}  [{it['kurz']}]"


def _item_details(it, indent) -> list:
    out = []
    for label, key in (("Prefix", "praefix"), ("Suffix", "suffix"),
                       ("Modifier", "modifier"), ("Transmuter", "transmuter"),
                       ("Component", "komponente"),
                       ("Component bonus", "komponenten_bonus"),
                       ("Augment", "augment"),
                       ("Ascended", "ascended"),
                       ("Ascended (2H)", "ascended_2h"),
                       ("Rerolls", "rerolls")):
        if it.get(key):
            out.append(f"{indent}{label}: {it[key]}")
    return out


def _head(o, rep) -> None:
    A = o.append
    c = rep["charakter"]
    A("=" * 78)
    A(f"  {c['name']}  -  Level {c['level']}  -  {c['klasse'] or c['klassen_tag']}")
    A("=" * 78)
    A("")
    A("CHARACTER")
    A(line("Masteries", " + ".join(f"{m['name']} (bar {m['balken']})" for m in rep["masteries"]) or "-"))
    A(line("Hardcore", "yes" if c["hardcore"] else "no"))
    A(line("Playtime", c["spielzeit"]))
    d = rep["fortschritt"]["schwierigkeit"]
    unknown = d.get("weitere_bits") not in (None, "keine", "none")
    A(line("Last played",
           f"{d['zuletzt_gespielt']}"
           f"{' (Veteran bit set)' if d['veteran_flag'] else ''}"
           f"  [raw {d['zuletzt_gespielt_roh']}"
           f"{', unknown bits ' + d['weitere_bits'] if unknown else ''}]"))
    A(line("Highest unlocked", d["hoechste_freigeschaltet"]))
    A("")

    a = rep["attribute_basis"]
    A("ATTRIBUTES (base, without bonuses - final values are not in the savefile)")
    A(line("Physique / Cunning / Spirit", f"{a['physique']:.0f} / {a['cunning']:.0f} / {a['spirit']:.0f}"))
    A(line("Health / Energy", f"{a['health']:.0f} / {a['energy']:.0f}"))
    A("")

    p = rep["fortschritt"]
    A("PROGRESS")
    A(line("Experience", f"{p['erfahrung']:,}"))
    A(line("Unspent points (Attr/Skill/Dev)",
           f"{p['attributpunkte_frei']} / {p['skillpunkte_frei']} / {p['devotionspunkte_frei']}"))
    A(line("Devotion points total", p["devotionspunkte_gesamt"]))
    A(line("Respec (Skill/Devotion)", f"{p['skillpunkte_zurueckgeholt']} / {p['devotionspunkte_zurueckgeholt']}"))
    A(line("Iron Bits", f"{p['iron']:,}"))
    A(line("Riftgates (N/E/U)", " / ".join(str(x) for x in p["riftgates_pro_schwierigkeit"])))
    A(line("Respawn points (N/E/U)", " / ".join(str(x) for x in p["respawnpunkte_pro_schwierigkeit"])))
    sl = p["shrine_listen"]
    if len(sl) == 6:
        A(line("Shrines (N/E/U)", " / ".join(f"{sl[2*i]}+{sl[2*i+1]}" for i in range(3))
               + "   (two lists per difficulty;"))
        A(line("", f"the first ones add up to {sl[0]+sl[2]+sl[4]} = statistic 'shrines unlocked')"))
    else:
        A(line("Shrine lists", " / ".join(str(x) for x in sl)))
    A(line("Lore notes", p["lore_notizen"]))
    A(line("Quest tokens (N/E/U)", " / ".join(str(x) for x in p["quest_tokens_pro_schwierigkeit"])
           + "   (story tokens only, the rest is in quests.gdd)"))
    A("")


def _gear_and_skills(o, rep, verbose) -> None:
    A = o.append
    A("EQUIPMENT")
    for it in rep["ausruestung"]:
        A(f"  {it['slot']:<12} {_item_label(it)}")
        o.extend(_item_details(it, " " * 17))
    A("")

    A("WEAPON SETS")
    for ws in rep["waffensets"]:
        A(f"  Set {ws['set']}{'  (active)' if ws['aktiv'] else ''}")
        for it in ws["items"]:
            A(f"    {it['slot']:<12} {_item_label(it)}")
            o.extend(_item_details(it, " " * 19))
    A("")

    A("SKILLS")
    by_class: dict = {}
    for s in rep["skills"]:
        by_class.setdefault(s["klasse_name"], []).append(s)
    for cls, skills in by_class.items():
        taken = [s for s in skills if s["level"] > 0]
        A(f"  {cls}  ({len(taken)} with points)")
        for s in sorted(taken, key=lambda x: -x["level"]):
            A(f"    {s['level']:>3}  {s['name']}")
        untaken = [s for s in skills if s["level"] == 0]
        if untaken and verbose:
            A(f"    (0 points: {', '.join(s['name'] for s in untaken)})")
    A("")


def _devotions(o, rep, verbose, bindings) -> None:
    A = o.append
    dv = rep["devotionen"]
    A(f"DEVOTIONS  ({dv['belegte_knoten']} nodes taken)")
    if dv["himmlische_kraefte"]:
        A("    Celestial powers:")
        for d in dv["himmlische_kraefte"]:
            bound = bindings.get(d["record"])
            A(f"      {d['name']}   [raw values {d['devotion_level']} / {_num(d['devotion_exp'])}]"
              f"   {'bound to ' + bound if bound else 'not bound'}")
        A("      (Both raw values are identical for several powers, so they are not")
        A("       a property of the single power. Meaning unclear, left unlabeled.)")
    if verbose:
        groups: dict = {}
        for d in dv["knoten"]:
            groups[d["name"]] = groups.get(d["name"], 0) + 1
        A("    Nodes taken per constellation:")
        for name, count in sorted(groups.items(), key=lambda x: (-x[1], x[0])):
            A(f"      {count:>2}x  {name}")
    A("")

    gis = rep.get("gewaehrte_item_skills") or []
    if gis:
        A(f"SKILLS GRANTED BY ITEMS ({len(gis)})")
        for e in gis:
            A(f"    {e['skill']}"
              f"{'   (from ' + e['von_item_skill'] + ')' if e.get('von_item_skill') else ''}")
        A("")
    if rep["item_skills"] and verbose:
        A(f"OTHER ITEM SKILL ENTRIES ({len(rep['item_skills'])})")
        for s in rep["item_skills"]:
            A(f"    {s['name']}")
        A("")


def _storage(o, rep, verbose) -> None:
    A = o.append
    A("FACTIONS")
    for f in sorted(rep["fraktionen"], key=lambda x: -x["wert"]):
        A(f"  {f['name']:<28} {f['wert']:>10.0f}  {f['stand']}")
    A("")

    inv = sum(len(s["items"]) for s in rep["taschen"])
    st = sum(len(s["items"]) for s in rep["stash"])
    A(f"STORED ITEMS  ({inv} in {len(rep['taschen'])} bags, "
      f"{st} on {len(rep['stash'])} character stash pages)")
    A("  (The shared stash of all characters is in transfer.gst, not in this file.)")
    for title, groups, key in (("Bag", rep["taschen"], "tasche"), ("Stash page", rep["stash"], "seite")):
        for group in groups:
            if not group["items"]:
                continue
            A(f"  {title} {group[key]}  ({len(group['items'])} items):")
            for it in group["items"]:
                A(f"    {_item_label(it)}{' x' + str(it['stack']) if it.get('stack', 1) > 1 else ''}")
                if verbose:
                    o.extend(_item_details(it, " " * 8))
    A("")


def _calculated(o, rep, names) -> None:
    A = o.append
    calc = rep.get("berechnet", {})
    gear, skl = calc.get("ausruestung"), calc.get("skills_und_devotionen")
    A("CALCULATED VALUES")
    if gear is None:
        A("  Not available - this needs the game database.")
        A("  Run with  --gd-dir \"<Grim Dawn folder>\"  on the PC where Grim Dawn is installed.")
        A("  Otherwise, fully calculated values are only on grimtools.com/calc/")
        A("  (arrow icon top left -> upload player.gdc).")
        A("")
        return
    A(f"  Source: {gear['anzahl_items']} equipped items incl. components, augments and affixes.")
    if skl:
        A(f"          + {len(skl['beitragende'])} permanent skills and devotion nodes.")
        if skl.get("auren"):
            A(f"          + {len(skl['auren'])} auras/toggles/procs, listed separately: "
              + ", ".join(a["skill"] for a in skl["auren"]))
    A("  This is the sum of bonuses, NOT the final character sheet: base values")
    A("  from level/attribute points, multipliers and difficulty penalties are missing.")
    A("")
    aura_block = {"summe": skl["auren_summe"]} if skl and skl.get("auren_summe") else None
    for title, block in (("From equipment", gear),
                         ("From permanent skills/devotions", skl),
                         ("From auras, toggles and procs (ONLY while active or when triggered)", aura_block)):
        if not block or not block["summe"]:
            continue
        A(f"  --- {title} ---")
        if title.startswith("From auras"):
            A("      Note: negative resistance values in this block are enemy debuffs")
            A("      (resistance reduction), not penalties on your own resistances.")
        for grp, rows in group_stats(block["summe"], names).items():
            A(f"  {grp}")
            for r in rows:
                if r.get("text"):
                    A(f"      {r['text']:<48} [{r['feld']}]")
                else:
                    A(f"      {r['label']:<32} {r['wert']:>10.2f}   [{r['feld']}]")
        A("")
    conv = list(gear.get("umwandlungen") or [])
    if skl:
        conv += list(skl.get("umwandlungen") or [])
    if conv:
        A("  --- Damage conversion ---")
        A("  (NOT summed: conversions from the same source stack additively,")
        A("   skill conversion applies before gear conversion.)")
        for c in conv:
            A(f"      {stat_labels.conversion_line(c['prozent'], c['von'], c['nach']):<48} [{c['quelle']}]")
        A("")
    single = gear.get("nicht_summierbar") or {}
    if single:
        A("  --- Per item, not additive ---")
        A("      These bonuses apply per item to their own skill or condition.")
        A("      A sum of them would be meaningless.")
        for field in sorted(single):
            A(f"      {field}")
            for name, value in sorted(single[field], key=lambda x: -x[1]):
                A(f"          +{value:g}   {name}")
        A("")
    if gear.get("nicht_gefunden"):
        A(f"  ! {len(gear['nicht_gefunden'])} DBR paths not found in the database (mod or newer patch?)")
    if skl and skl.get("aktive_skills_ignoriert"):
        A(f"  Note: {len(skl['aktive_skills_ignoriert'])} active skills with values were deliberately not summed")
        A("  (they only apply when used, not permanently). Details in the JSON.")
    A("")


def _statistics(o, rep) -> None:
    A = o.append
    s = rep["statistik"]
    A("STATISTICS")
    A("  (Triple values are unlabeled in the savefile; the order Normal/Elite/")
    A("   Ultimate is the usual one but not strictly verified.)")
    for k, label in STAT_LABELS.items():
        if k not in s:
            continue
        v = s[k]
        if isinstance(v, list) and len(v) == 3:
            v = " / ".join(f"{DIFFICULTIES[i]}: {_num(x)}" for i, x in enumerate(v))
        elif isinstance(v, float):
            v = f"{v:,.0f}"
        else:
            v = _num(v)
        A(line(label, v))
    gm = s.get("greatest_monster_killed")
    if isinstance(gm, list):
        for i, entry in enumerate(gm):
            if isinstance(entry, dict) and entry.get("name"):
                A(line(f"Greatest kill ({DIFFICULTIES[i]})",
                       f"{entry.get('name_klartext') or entry['name']} (Lvl {entry.get('level')})"))
    A("")


def _format(o, rep) -> None:
    A = o.append
    fmt = rep["format"]
    A("FORMAT / COMPLETENESS")
    A(line("data_version", fmt["data_version"]))
    A(line("Block versions", ", ".join(f"{k}:v{v}" for k, v in sorted(fmt["block_versionen"].items()))))
    A(line("Name resolution (database.arz)", "active" if fmt["namensaufloesung"] else "OFF  (set --gd-dir)"))
    A(line("Unknown blocks", ", ".join(str(b) for b in fmt["unbekannte_bloecke"]) or "none"))
    A(line("Unassigned trailing fields",
           ", ".join(f"Block {k}: {v}" for k, v in fmt["nicht_zugeordnete_restfelder"].items()) or "none"))
    if fmt["nicht_zugeordnete_restfelder"] or fmt["unbekannte_bloecke"]:
        A("  ! This data was read and matches in the round trip, but is not yet")
        A("    assigned to a named field - probably a newer patch. Visible in the raw JSON.")


def render_text(rep, verbose: bool = False, names=None, bindings: dict | None = None) -> str:
    """The character text report. `names`: name source of the calculated lines (game tags),
    `bindings`: celestial power record -> bound skill name (celestial_bindings)."""
    o: list[str] = []
    _head(o, rep)
    _gear_and_skills(o, rep, verbose)
    _devotions(o, rep, verbose, bindings or {})
    _storage(o, rep, verbose)
    _calculated(o, rep, names)
    _statistics(o, rep)
    _format(o, rep)
    return "\n".join(o)

