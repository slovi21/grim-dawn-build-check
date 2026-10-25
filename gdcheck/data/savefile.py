"""Grim Dawn savefile (player.gdc) parser and writer - taken over UNCHANGED
from the .35 extractor (scripts/gd_extract.py, parts 1-2), byte-verified with a
round trip on every test character. Origin of the format layer:
github.com/kraken444444/grim-dawn-save-editor (MIT). The pure-Python LZ4 decoder
is used (no optional third-party module). Only change (.68): the item, skill and stash page
readers/writers are looked up through an explicit Hooks object (parse_character/write_character
`hooks=`) so the save diagnosis can try other layouts without swapping module functions."""
from __future__ import annotations

import struct

_lz4_block = None          # pure-Python decoder only (stdlib rule)


def lz4_decompress(src: bytes, size: int) -> bytes:
    """LZ4-Blockdekompression. Nutzt das Modul `lz4`, wenn vorhanden, sonst
    eine reine Python-Umsetzung - damit laeuft das Skript ohne Fremdpakete."""
    if _lz4_block is not None:
        return _lz4_block.decompress(src, uncompressed_size=size)
    out = bytearray()
    pos, n = 0, len(src)
    while pos < n:
        token = src[pos]
        pos += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[pos]
                pos += 1
                lit += b
                if b != 255:
                    break
        out += src[pos:pos + lit]
        pos += lit
        if pos >= n:                                  # letzte Sequenz: nur Literale
            break
        offset = src[pos] | (src[pos + 1] << 8)
        pos += 2
        match = token & 0x0F
        if match == 15:
            while True:
                b = src[pos]
                pos += 1
                match += b
                if b != 255:
                    break
        match += 4                                    # Mindestlaenge einer Referenz
        start = len(out) - offset
        if start < 0:
            raise GDFormatError("corrupt LZ4 block in the game database")
        for i in range(match):                        # ueberlappende Kopien erlaubt
            out.append(out[start + i])
    if size and len(out) != size:
        raise GDFormatError(
            f"LZ4: {len(out)} bytes unpacked, expected {size}")
    return bytes(out)


# ===========================================================================
# Part 1 - encryption (rolling XOR stream)
# ===========================================================================

"""Low-level codec for Grim Dawn save files (.gdc, .gdd, .gst).

Grim Dawn encrypts save files with a rolling XOR stream:

  - A 32-bit seed is stored in the first 4 bytes of the file, masked with
    0x55555555.
  - A 256-entry table is derived from the seed:
        val = seed
        for i in 0..255:  val = lo32(((val << 31) | (val >> 1)) * 39916801); table[i] = val
  - The stream state starts equal to the seed.  Every *ciphertext* byte b that
    passes through the stream (either direction) updates it:
        state = lo32(state ^ table[b])
  - A 32-bit read XORs the whole little-endian word against the current state
    *before* the state absorbs the word's 4 bytes (in file order); a single
    byte read XORs against the state and masks to the low byte.
  - Block payload lengths are XORed against the state but do NOT advance it.
  - Block checksums are the raw running state at that point in the stream,
    written unencrypted.

Because the state evolves from ciphertext, decrypt->encrypt with the same
starting state is always a byte-identical round trip regardless of how the
plaintext is chunked.  That property is used by the round-trip test suite to
verify parser completeness against real save files.
"""


SEED_MASK = 0x55555555
U32 = 0xFFFFFFFF


class GDFormatError(Exception):
    """Raised when a save file does not match the expected structure."""


def generate_table(seed: int) -> list[int]:
    table = []
    val = seed & U32
    for _ in range(256):
        val = (((val << 31) | (val >> 1)) * 39916801) & U32
        table.append(val)
    return table


class GDReader:
    """Decrypting reader over an in-memory save file."""

    def __init__(self, data: bytes):
        if len(data) < 8:
            raise GDFormatError("file too short to be a Grim Dawn save")
        self.data = data
        self.pos = 4
        self.seed = (struct.unpack_from("<I", data, 0)[0] ^ SEED_MASK) & U32
        self.state = self.seed
        self.table = generate_table(self.seed)

    # -- primitives ---------------------------------------------------------

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def _need(self, n: int) -> None:
        if self.pos + n > len(self.data):
            raise GDFormatError(
                f"unexpected end of file at offset {self.pos} (wanted {n} more bytes)")

    def read_u32(self) -> int:
        self._need(4)
        cipher = struct.unpack_from("<I", self.data, self.pos)[0]
        val = (cipher ^ self.state) & U32
        table = self.table
        state = self.state
        for b in self.data[self.pos:self.pos + 4]:
            state ^= table[b]
        self.state = state & U32
        self.pos += 4
        return val

    def read_i32(self) -> int:
        val = self.read_u32()
        return val - 0x100000000 if val >= 0x80000000 else val

    def read_float(self) -> float:
        return struct.unpack("<f", struct.pack("<I", self.read_u32()))[0]

    def read_byte(self) -> int:
        self._need(1)
        cipher = self.data[self.pos]
        val = (cipher ^ self.state) & 0xFF
        self.state = (self.state ^ self.table[cipher]) & U32
        self.pos += 1
        return val

    def read_bool(self) -> bool:
        return self.read_byte() == 1

    def read_bytes(self, n: int) -> bytes:
        self._need(n)
        out = bytearray(n)
        table = self.table
        state = self.state
        data = self.data
        pos = self.pos
        for i in range(n):
            cipher = data[pos + i]
            out[i] = (cipher ^ state) & 0xFF
            state ^= table[cipher]
        self.state = state & U32
        self.pos += n
        return bytes(out)

    def read_str(self) -> str:
        n = self.read_u32()
        if n > self.remaining():
            raise GDFormatError(
                f"string length {n} at offset {self.pos} exceeds file size")
        return self.read_bytes(n).decode("latin-1")

    def read_wstr(self) -> str:
        n = self.read_u32()  # number of UTF-16 code units
        if 2 * n > self.remaining():
            raise GDFormatError(
                f"wide string length {n} at offset {self.pos} exceeds file size")
        return self.read_bytes(2 * n).decode("utf-16-le")

    # -- block framing ------------------------------------------------------

    def read_block_length(self) -> int:
        """Read a 32-bit value XORed against the state WITHOUT advancing it."""
        self._need(4)
        cipher = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return (cipher ^ self.state) & U32

    def read_u32_no_update(self) -> int:
        """Alias for the odd fields (e.g. transfer stash) that skip the state update."""
        return self.read_block_length()

    def begin_block(self, expected_id: int | None = None) -> tuple[int, int]:
        """Read a block header. Returns (block_id, expected_end_position)."""
        block_id = self.read_u32()
        length = self.read_block_length()
        end = self.pos + length
        if end > len(self.data):
            raise GDFormatError(
                f"block {block_id} at offset {self.pos - 8} claims length {length}, "
                f"which runs past end of file")
        if expected_id is not None and block_id != expected_id:
            raise GDFormatError(
                f"expected block {expected_id} but found block {block_id} "
                f"at offset {self.pos - 8}")
        return block_id, end

    def end_block(self, block_id: int, expected_end: int) -> None:
        if self.pos != expected_end:
            raise GDFormatError(
                f"block {block_id}: parser stopped at offset {self.pos}, "
                f"expected {expected_end} (off by {expected_end - self.pos})")
        self._need(4)
        checksum = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        if checksum != self.state:
            raise GDFormatError(
                f"block {block_id}: checksum mismatch at offset {self.pos - 4} "
                f"(file {checksum:#010x}, computed {self.state:#010x})")

    def verify_checksum(self) -> None:
        """Verify a bare running-state checksum (used after the .gdc header)."""
        self._need(4)
        checksum = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        if checksum != self.state:
            raise GDFormatError(
                f"header checksum mismatch at offset {self.pos - 4} "
                f"(file {checksum:#010x}, computed {self.state:#010x})")


class GDWriter:
    """Encrypting writer producing an in-memory save file."""

    def __init__(self, seed: int):
        self.seed = seed & U32
        self.state = self.seed
        self.table = generate_table(self.seed)
        self.buf = bytearray(struct.pack("<I", (self.seed ^ SEED_MASK) & U32))
        self._block_stack: list[tuple[int, int, int]] = []  # (id, len_pos, len_state)

    def getvalue(self) -> bytes:
        if self._block_stack:
            raise GDFormatError("unclosed block on writer")
        return bytes(self.buf)

    # -- primitives ---------------------------------------------------------

    def write_u32(self, val: int) -> None:
        cipher = (val ^ self.state) & U32
        raw = struct.pack("<I", cipher)
        self.buf += raw
        table = self.table
        state = self.state
        for b in raw:
            state ^= table[b]
        self.state = state & U32

    def write_i32(self, val: int) -> None:
        self.write_u32(val & U32)

    def write_float(self, val: float) -> None:
        self.write_u32(struct.unpack("<I", struct.pack("<f", val))[0])

    def write_byte(self, val: int) -> None:
        cipher = (val ^ self.state) & 0xFF
        self.buf.append(cipher)
        self.state = (self.state ^ self.table[cipher]) & U32

    def write_bool(self, val: bool) -> None:
        self.write_byte(1 if val else 0)

    def write_bytes(self, data: bytes) -> None:
        table = self.table
        state = self.state
        for b in data:
            cipher = (b ^ state) & 0xFF
            self.buf.append(cipher)
            state ^= table[cipher]
        self.state = state & U32

    def write_str(self, s: str) -> None:
        b = s.encode("latin-1")
        self.write_u32(len(b))
        self.write_bytes(b)

    def write_wstr(self, s: str) -> None:
        b = s.encode("utf-16-le")
        self.write_u32(len(b) // 2)  # number of UTF-16 code units
        self.write_bytes(b)

    # -- block framing ------------------------------------------------------

    def write_u32_no_update(self, val: int) -> None:
        cipher = (val ^ self.state) & U32
        self.buf += struct.pack("<I", cipher)

    def begin_block(self, block_id: int) -> None:
        self.write_u32(block_id)
        self._block_stack.append((block_id, len(self.buf), self.state))
        self.buf += b"\x00\x00\x00\x00"  # length placeholder (patched later)

    def end_block(self) -> None:
        block_id, len_pos, len_state = self._block_stack.pop()
        payload_len = len(self.buf) - len_pos - 4
        struct.pack_into("<I", self.buf, len_pos, (payload_len ^ len_state) & U32)
        self.buf += struct.pack("<I", self.state)  # checksum = raw running state

    def write_checksum(self) -> None:
        """Write a bare running-state checksum (used after the .gdc header)."""
        self.buf += struct.pack("<I", self.state)


def magic_u32(tag: str) -> int:
    """Pack a 4-char tag the way the game stores file magics (little-endian)."""
    b = tag.encode("ascii")
    return b[0] | (b[1] << 8) | (b[2] << 16) | (b[3] << 24)


# ===========================================================================
# Part 2 - block parser for player.gdc (blocks 1-17)
# ===========================================================================

"""player.gdc - the Grim Dawn character file.

The file is a sequence of checksummed, encrypted blocks (see codec.py).
Each block has a numeric id and its own version field; newer game patches
bump block versions and append fields.

The parsed representation is a plain dict tree ("character document"):

    {
      "seed": int,                     # encryption seed (reused on write)
      "preamble_version": int,
      "header": {...},                 # name, class tag, level, hardcore...
      "data_version": int,             # 6/7/8 known
      "mystery": bytes(16),
      "blocks": [ {"id": N, ...}, ...] # in original file order
    }

Every block reader has a matching writer; writing an unmodified document
reproduces the original file byte for byte, which the test harness verifies
against real save files.

Unknown trailing data (a newer patch appending fields) is captured into
"_extra": {"kind": "ints"|"bytes", "values": [...]} so files still round-trip;
"ints" survives edits made earlier in the file, "bytes" is only safe on
otherwise untouched files (the editor refuses risky writes in that case).
Unknown whole blocks are captured the same way under "_opaque".
"""


GDC_MAGIC = magic_u32("GDCX")
KNOWN_DATA_VERSIONS = {6, 7, 8}

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _read_extra(r: GDReader, end: int) -> dict | None:
    """Capture unparsed trailing bytes of a block (newer-patch fields)."""
    rem = end - r.pos
    if rem == 0:
        return None
    if rem < 0:
        raise GDFormatError(f"parser overran block by {-rem} bytes")
    if rem % 4 == 0:
        return {"kind": "ints", "values": [r.read_u32() for _ in range(rem // 4)]}
    return {"kind": "bytes", "values": r.read_bytes(rem)}


def _write_extra(w: GDWriter, extra: dict | None) -> None:
    if not extra:
        return
    if extra["kind"] == "ints":
        for v in extra["values"]:
            w.write_u32(v)
    else:
        w.write_bytes(bytes(extra["values"]))


def has_unsafe_extras(doc: dict) -> list[str]:
    """Return descriptions of any byte-granularity opaque data in the document.

    Such data only survives a byte-identical prefix; the editor uses this to
    refuse writes after edits when present.
    """
    problems = []
    for block in doc["blocks"]:
        extra = block.get("_extra")
        if extra and extra["kind"] == "bytes":
            problems.append(f"block {block['id']} has {len(extra['values'])} unknown raw bytes")
        opaque = block.get("_opaque")
        if opaque and opaque["kind"] == "bytes":
            problems.append(f"unknown block {block['id']} stored as raw bytes")
    return problems


def _read_uid(r: GDReader) -> bytes:
    return r.read_bytes(16)


def _read_uid_list(r: GDReader) -> list[bytes]:
    return [_read_uid(r) for _ in range(r.read_u32())]


def _write_uid_list(w: GDWriter, uids: list[bytes]) -> None:
    w.write_u32(len(uids))
    for u in uids:
        w.write_bytes(u)


# ---------------------------------------------------------------------------
# items (shared with stash formats)
# ---------------------------------------------------------------------------

# Feldliste eines Item-Records, nach Containerversion gestaffelt.
# Quelle: eigenes Reverse Engineering an 1.3.0.8-Saves, abgeglichen mit der
# Formatbeschreibung von NavingLord/GrimBuildLoader. Deren Layout ist
# vollstaendiger als das urspruenglich verwendete und byte-verifiziert
# nachgerechnet:
#   ... augmentID, augmentLevel, augmentSeed,
#   [ab 8:  ascendantID, ascendant2hID],
#   var1 (relicCompletionLevel),
#   [ab 4:  stackCount], [ab 8: rerollsUsed], [ab 11: affixRerollsUsed]
#
# Wichtig: ES GIBT ZWEI Ascended-Felder. Das zweite gilt fuer Zweihandwaffen.
# Wer nur eines liest, laeuft bei der ersten Zweihandwaffe mit Ascended-Affix
# aus dem Tritt - genau der Fehler, der vorher schon beim ersten Feld auftrat.
ITEM_ASCENDED_MIN_VERSION = 8
ITEM_STACK_MIN_VERSION = 4
ITEM_REROLL_MIN_VERSION = 8
ITEM_AFFIX_REROLL_MIN_VERSION = 11


def read_item(r: GDReader, v2: bool = False, version: int | None = None) -> dict:
    """Ein Item lesen. `version` ist die Containerversion des umgebenden Blocks.

    `v2` bleibt aus Kompatibilitaet erhalten: True entspricht Version 11,
    False einer alten Version ohne Ascended- und Reroll-Felder."""
    ver = version if version is not None else (11 if v2 else 5)
    item = {
        "basename": r.read_str(),
        "prefix_name": r.read_str(),
        "suffix_name": r.read_str(),
        "modifier_name": r.read_str(),
        "transmute_name": r.read_str(),
        "seed": r.read_u32(),
        "relic_name": r.read_str(),
        "relic_bonus": r.read_str(),
        "relic_seed": r.read_u32(),
        "augment_name": r.read_str(),
        "augment_level": r.read_u32(),
        "augment_seed": r.read_u32(),
    }
    if ver >= ITEM_ASCENDED_MIN_VERSION:
        item["ascended_name"] = r.read_str()
        item["ascended_2h_name"] = r.read_str()
    item["relic_completion_level"] = r.read_u32()
    if ver >= ITEM_STACK_MIN_VERSION:
        item["stack_count"] = r.read_u32()
    if ver >= ITEM_REROLL_MIN_VERSION:
        item["rerolls_used"] = r.read_u32()
    if ver >= ITEM_AFFIX_REROLL_MIN_VERSION:
        item["affix_rerolls_used"] = r.read_u32()
    item["_version"] = ver
    return item


def write_item(w: GDWriter, item: dict, v2: bool = False, version: int | None = None) -> None:
    ver = version if version is not None else item.get(
        "_version", 11 if v2 else 5)
    for key in ("basename", "prefix_name", "suffix_name", "modifier_name",
                "transmute_name"):
        w.write_str(item[key])
    w.write_u32(item["seed"])
    w.write_str(item["relic_name"])
    w.write_str(item["relic_bonus"])
    w.write_u32(item["relic_seed"])
    w.write_str(item["augment_name"])
    w.write_u32(item.get("augment_level", item.get("unknown", 0)))
    w.write_u32(item["augment_seed"])
    if ver >= ITEM_ASCENDED_MIN_VERSION:
        w.write_str(item.get("ascended_name", ""))
        w.write_str(item.get("ascended_2h_name", ""))
    w.write_u32(item.get("relic_completion_level", 0))
    if ver >= ITEM_STACK_MIN_VERSION:
        w.write_u32(item.get("stack_count", 1))
    if ver >= ITEM_REROLL_MIN_VERSION:
        w.write_u32(item.get("rerolls_used", 0))
    if ver >= ITEM_AFFIX_REROLL_MIN_VERSION:
        w.write_u32(item.get("affix_rerolls_used", 0))


def new_item(basename: str, seed: int, **kwargs) -> dict:
    """A blank item dict with sensible defaults."""
    item = {
        "basename": basename,
        "prefix_name": "",
        "suffix_name": "",
        "modifier_name": "",
        "transmute_name": "",
        "seed": seed,
        "relic_name": "",
        "relic_bonus": "",
        "relic_seed": 0,
        "augment_name": "",
        "augment_seed": 0,
        "relic_completion_level": 0,
        "ascended_name": "",
        "ascended_2h_name": "",
        "augment_level": 0,
        "rerolls_used": 0,
        "affix_rerolls_used": 0,
        "stack_count": 1,
    }
    item.update(kwargs)
    return item


# ---------------------------------------------------------------------------
# block 1: character info
# ---------------------------------------------------------------------------

def _read_block1(r: GDReader, end: int) -> dict:
    b = {"version": r.read_u32()}
    b["in_main_quest"] = r.read_bool()
    b["has_been_in_game"] = r.read_bool()
    b["last_difficulty"] = r.read_byte()
    b["greatest_difficulty"] = r.read_byte()
    b["iron"] = r.read_u32()
    b["greatest_survival_difficulty"] = r.read_byte()
    b["tributes"] = r.read_u32()
    b["ui_compass_state"] = r.read_byte()
    if 2 <= b["version"] <= 4:
        b["always_show_loot"] = r.read_u32()
    b["show_skill_help"] = r.read_bool()
    b["alt_weapon_set"] = r.read_bool()
    b["alt_weapon_set_enabled"] = r.read_bool()
    b["player_texture"] = r.read_str()
    if b["version"] >= 5:
        n = r.read_u32()
        b["loot_filters"] = list(r.read_bytes(n))
    return b


def _write_block1(w: GDWriter, b: dict) -> None:
    w.write_u32(b["version"])
    w.write_bool(b["in_main_quest"])
    w.write_bool(b["has_been_in_game"])
    w.write_byte(b["last_difficulty"])
    w.write_byte(b["greatest_difficulty"])
    w.write_u32(b["iron"])
    w.write_byte(b["greatest_survival_difficulty"])
    w.write_u32(b["tributes"])
    w.write_byte(b["ui_compass_state"])
    if 2 <= b["version"] <= 4:
        w.write_u32(b["always_show_loot"])
    w.write_bool(b["show_skill_help"])
    w.write_bool(b["alt_weapon_set"])
    w.write_bool(b["alt_weapon_set_enabled"])
    w.write_str(b["player_texture"])
    if b["version"] >= 5:
        w.write_u32(len(b["loot_filters"]))
        w.write_bytes(bytes(b["loot_filters"]))


# ---------------------------------------------------------------------------
# block 2: bio / attributes
# ---------------------------------------------------------------------------

def _read_block2(r: GDReader, end: int) -> dict:
    return {
        "version": r.read_u32(),
        "level_in_bio": r.read_u32(),
        "experience": r.read_u32(),
        "attribute_points": r.read_u32(),
        "skill_points": r.read_u32(),
        "devotion_points": r.read_u32(),
        "total_devotion_unlocked": r.read_u32(),
        "physique": r.read_float(),
        "cunning": r.read_float(),
        "spirit": r.read_float(),
        "health": r.read_float(),
        "energy": r.read_float(),
    }


def _write_block2(w: GDWriter, b: dict) -> None:
    w.write_u32(b["version"])
    w.write_u32(b["level_in_bio"])
    w.write_u32(b["experience"])
    w.write_u32(b["attribute_points"])
    w.write_u32(b["skill_points"])
    w.write_u32(b["devotion_points"])
    w.write_u32(b["total_devotion_unlocked"])
    w.write_float(b["physique"])
    w.write_float(b["cunning"])
    w.write_float(b["spirit"])
    w.write_float(b["health"])
    w.write_float(b["energy"])


# ---------------------------------------------------------------------------
# block 3: inventory + equipment
# ---------------------------------------------------------------------------

# Container block version at which the FoA item layout (and stash page
# trailer fields) appear.
ITEM_V2_BLOCK_VERSION = 11


def _read_inv_item(r: GDReader, v2: bool) -> dict:
    item = r.hooks.read_item(r, v2)
    item["x"] = r.read_u32()
    item["y"] = r.read_u32()
    return item


def _write_inv_item(w: GDWriter, item: dict, v2: bool) -> None:
    w.hooks.write_item(w, item, v2)
    w.write_u32(item["x"])
    w.write_u32(item["y"])


def _read_equip_item(r: GDReader, v2: bool) -> dict:
    item = r.hooks.read_item(r, v2)
    item["attached"] = r.read_bool()
    return item


def _write_equip_item(w: GDWriter, item: dict, v2: bool) -> None:
    w.hooks.write_item(w, item, v2)
    w.write_bool(item["attached"])


def _read_block3(r: GDReader, end: int) -> dict:
    b = {"version": r.read_u32()}
    v2 = b["version"] >= ITEM_V2_BLOCK_VERSION
    b["has_data"] = r.read_bool()
    if not b["has_data"]:
        return b
    sack_count = r.read_u32()
    b["focused_sack"] = r.read_u32()
    b["selected_sack"] = r.read_u32()
    sacks = []
    for _ in range(sack_count):
        _, sub_end = r.begin_block(expected_id=0)
        sack = {"unused": r.read_bool(),
                "items": [_read_inv_item(r, v2) for _ in range(r.read_u32())]}
        r.end_block(0, sub_end)
        sacks.append(sack)
    b["sacks"] = sacks
    b["use_alt_weaponset"] = r.read_bool()
    b["equipment"] = [_read_equip_item(r, v2) for _ in range(12)]
    weapon_sets = []
    for _ in range(2):
        ws = {"unused": r.read_bool(),
              "items": [_read_equip_item(r, v2) for _ in range(2)]}
        weapon_sets.append(ws)
    b["weapon_sets"] = weapon_sets
    return b


def _write_block3(w: GDWriter, b: dict) -> None:
    w.write_u32(b["version"])
    v2 = b["version"] >= ITEM_V2_BLOCK_VERSION
    w.write_bool(b["has_data"])
    if not b["has_data"]:
        return
    w.write_u32(len(b["sacks"]))
    w.write_u32(b["focused_sack"])
    w.write_u32(b["selected_sack"])
    for sack in b["sacks"]:
        w.begin_block(0)
        w.write_bool(sack["unused"])
        w.write_u32(len(sack["items"]))
        for item in sack["items"]:
            _write_inv_item(w, item, v2)
        w.end_block()
    w.write_bool(b["use_alt_weaponset"])
    assert len(b["equipment"]) == 12
    for item in b["equipment"]:
        _write_equip_item(w, item, v2)
    for ws in b["weapon_sets"]:
        w.write_bool(ws["unused"])
        assert len(ws["items"]) == 2
        for item in ws["items"]:
            _write_equip_item(w, item, v2)


# ---------------------------------------------------------------------------
# block 4: personal (character) stash
# ---------------------------------------------------------------------------

def _read_stash_page(r: GDReader, v2: bool, float_xy: bool = False) -> dict:
    _, sub_end = r.begin_block(expected_id=0)
    page = {"width": r.read_u32(), "height": r.read_u32()}
    items = []
    for _ in range(r.read_u32()):
        item = r.hooks.read_item(r, v2)
        if float_xy:
            item["x"] = r.read_float()
            item["y"] = r.read_float()
        else:
            item["x"] = r.read_u32()
            item["y"] = r.read_u32()
        items.append(item)
    page["items"] = items
    if v2:
        # Since FoA: four trailing u32 fields (observed all-zero) followed by
        # the page name as a wide string. An unnamed page stores length 0,
        # which earlier versions of this parser misread as a fifth u32.
        page["extra"] = [r.read_u32() for _ in range(4)]
        page["name"] = r.read_wstr()
    r.end_block(0, sub_end)
    return page


def _write_stash_page(w: GDWriter, page: dict, v2: bool, float_xy: bool = False) -> None:
    w.begin_block(0)
    w.write_u32(page["width"])
    w.write_u32(page["height"])
    w.write_u32(len(page["items"]))
    for item in page["items"]:
        w.hooks.write_item(w, item, v2)
        if float_xy:
            w.write_float(item["x"])
            w.write_float(item["y"])
        else:
            w.write_u32(item["x"])
            w.write_u32(item["y"])
    if v2:
        for v in page.get("extra", [0, 0, 0, 0])[:4]:
            w.write_u32(v)
        w.write_wstr(page.get("name", ""))
    w.end_block()


def _read_block4(r: GDReader, end: int) -> dict:
    b = {"version": r.read_u32()}
    v2 = b["version"] >= ITEM_V2_BLOCK_VERSION
    # Stash page item coordinates are floats (verified against real saves),
    # unlike inventory sacks which use ints.
    b["pages"] = [r.hooks.read_stash_page(r, v2, float_xy=True) for _ in range(r.read_u32())]
    return b


def _write_block4(w: GDWriter, b: dict) -> None:
    w.write_u32(b["version"])
    v2 = b["version"] >= ITEM_V2_BLOCK_VERSION
    w.write_u32(len(b["pages"]))
    for page in b["pages"]:
        w.hooks.write_stash_page(w, page, v2, float_xy=True)


# ---------------------------------------------------------------------------
# blocks 5/6/7/17: respawn / teleporter / marker / shrine UID lists
# ---------------------------------------------------------------------------

def _read_block5(r: GDReader, end: int) -> dict:
    b = {"version": r.read_u32()}
    b["spawn_points"] = [_read_uid_list(r) for _ in range(3)]
    b["current_respawn"] = [_read_uid(r) for _ in range(3)]
    return b


def _write_block5(w: GDWriter, b: dict) -> None:
    w.write_u32(b["version"])
    for lst in b["spawn_points"]:
        _write_uid_list(w, lst)
    for uid in b["current_respawn"]:
        w.write_bytes(uid)


def _read_block6(r: GDReader, end: int) -> dict:
    b = {"version": r.read_u32()}
    b["teleporter_points"] = [_read_uid_list(r) for _ in range(3)]
    return b


def _write_block6(w: GDWriter, b: dict) -> None:
    w.write_u32(b["version"])
    for lst in b["teleporter_points"]:
        _write_uid_list(w, lst)


def _read_block7(r: GDReader, end: int) -> dict:
    b = {"version": r.read_u32()}
    b["markers"] = [_read_uid_list(r) for _ in range(3)]
    return b


def _write_block7(w: GDWriter, b: dict) -> None:
    w.write_u32(b["version"])
    for lst in b["markers"]:
        _write_uid_list(w, lst)


def _read_block17(r: GDReader, end: int) -> dict:
    b = {"version": r.read_u32()}
    b["shrines"] = [_read_uid_list(r) for _ in range(6)]
    return b


def _write_block17(w: GDWriter, b: dict) -> None:
    w.write_u32(b["version"])
    for lst in b["shrines"]:
        _write_uid_list(w, lst)


# ---------------------------------------------------------------------------
# block 8: skills
# ---------------------------------------------------------------------------

# Block 8 version at which skill entries gain an extra byte after `enabled`
# (Fangs of Asterkarn; observed only as zero).
SKILL_V2_BLOCK_VERSION = 8


def _read_skill(r: GDReader, v2: bool) -> dict:
    s = {
        "skill_name": r.read_str(),
        "level": r.read_u32(),
        "enabled": r.read_bool(),
    }
    if v2:
        s["unknown_byte"] = r.read_byte()
    s.update({
        "devotion_level": r.read_u32(),
        "devotion_experience": r.read_u32(),
        "sublevel": r.read_u32(),
        "skill_active": r.read_bool(),
        "skill_transition": r.read_bool(),
        "autocast_skill_name": r.read_str(),
        "autocast_controller_name": r.read_str(),
    })
    return s


def _write_skill(w: GDWriter, s: dict, v2: bool) -> None:
    w.write_str(s["skill_name"])
    w.write_u32(s["level"])
    w.write_bool(s["enabled"])
    if v2:
        w.write_byte(s.get("unknown_byte", 0))
    w.write_u32(s["devotion_level"])
    w.write_u32(s["devotion_experience"])
    w.write_u32(s["sublevel"])
    w.write_bool(s["skill_active"])
    w.write_bool(s["skill_transition"])
    w.write_str(s["autocast_skill_name"])
    w.write_str(s["autocast_controller_name"])


def blank_skill(skill_name: str, level: int = 1) -> dict:
    return {
        "skill_name": skill_name,
        "level": level,
        "enabled": True,
        "devotion_level": 0,
        "devotion_experience": 0,
        "sublevel": 0,
        "skill_active": False,
        "skill_transition": False,
        "autocast_skill_name": "",
        "autocast_controller_name": "",
    }


def _read_item_skill(r: GDReader, v2: bool = False) -> dict:
    s = {
        "skill_name": r.read_str(),
        "autocast_skill_name": r.read_str(),
        "autocast_controller_name": r.read_str(),
    }
    if not v2:
        s["unknown_bytes"] = r.read_bytes(4)
    s["unknown"] = r.read_str()
    return s


def _write_item_skill(w: GDWriter, s: dict, v2: bool = False) -> None:
    w.write_str(s["skill_name"])
    w.write_str(s["autocast_skill_name"])
    w.write_str(s["autocast_controller_name"])
    if not v2:
        w.write_bytes(bytes(s["unknown_bytes"]))
    w.write_str(s["unknown"])


def _read_block8(r: GDReader, end: int) -> dict:
    b = {"version": r.read_u32()}
    v2 = b["version"] >= SKILL_V2_BLOCK_VERSION
    b["skills"] = [r.hooks.read_skill(r, v2) for _ in range(r.read_u32())]
    b["masteries_allowed"] = r.read_u32()
    b["skill_points_reclaimed"] = r.read_u32()
    b["devotion_points_reclaimed"] = r.read_u32()
    if v2:
        # Blockversion 8: zwei Layouts beobachtet.
        #  "anzahl_zuerst": Anzahl, Eintraege MIT 4 Zusatzbytes, dann unk1 am
        #     Ende (wie vor Version 8). Byte-verifiziert an Skelleton Master
        #     (1.3.0.8, ein Item-Skill aus einer Rune).
        #  "unk1_zuerst": unk1, Anzahl, Eintraege OHNE Zusatzbytes (vier
        #     Strings). Byte-verifiziert am 25.09.2026 an einer echten
        #     1.3.0.8-Datei mit Item-Skills. Beide Layouts kommen also vor;
        #     bei 0 Item-Skills sind sie byte-gleich.
        snap = (r.pos, r.state)
        try:
            items = [_read_item_skill(r) for _ in range(r.read_u32())]
            if end - r.pos != 4:
                raise GDFormatError("layout count-first does not fit")
            b["item_skills"] = items
            b["unk1"] = r.read_u32()
            b["_item_skill_layout"] = "anzahl_zuerst"
            return b
        except GDFormatError:
            r.pos, r.state = snap
        b["unk1"] = r.read_u32()
        b["item_skills"] = [_read_item_skill(r, True) for _ in range(r.read_u32())]
        b["_item_skill_layout"] = "unk1_zuerst"
        return b
    b["item_skills"] = [_read_item_skill(r) for _ in range(r.read_u32())]
    if b["version"] >= 6:
        b["unk1"] = r.read_u32()
    return b


def _write_block8(w: GDWriter, b: dict) -> None:
    w.write_u32(b["version"])
    v2 = b["version"] >= SKILL_V2_BLOCK_VERSION
    w.write_u32(len(b["skills"]))
    for s in b["skills"]:
        w.hooks.write_skill(w, s, v2)
    w.write_u32(b["masteries_allowed"])
    w.write_u32(b["skill_points_reclaimed"])
    w.write_u32(b["devotion_points_reclaimed"])
    if v2 and b.get("_item_skill_layout", "unk1_zuerst") == "unk1_zuerst":
        w.write_u32(b["unk1"])
        w.write_u32(len(b["item_skills"]))
        for s in b["item_skills"]:
            _write_item_skill(w, s, True)
        return
    w.write_u32(len(b["item_skills"]))
    for s in b["item_skills"]:
        _write_item_skill(w, s)
    if b["version"] >= 6:
        w.write_u32(b["unk1"])


# ---------------------------------------------------------------------------
# block 10: quest tokens per difficulty
# ---------------------------------------------------------------------------

def _read_block10(r: GDReader, end: int) -> dict:
    b = {"version": r.read_u32()}
    b["tokens_per_difficulty"] = [
        [r.read_str() for _ in range(r.read_u32())] for _ in range(3)]
    return b


def _write_block10(w: GDWriter, b: dict) -> None:
    w.write_u32(b["version"])
    for tokens in b["tokens_per_difficulty"]:
        w.write_u32(len(tokens))
        for t in tokens:
            w.write_str(t)


# ---------------------------------------------------------------------------
# block 12: lore notes
# ---------------------------------------------------------------------------

def _read_block12(r: GDReader, end: int) -> dict:
    b = {"version": r.read_u32()}
    b["lore_item_names"] = [r.read_str() for _ in range(r.read_u32())]
    return b


def _write_block12(w: GDWriter, b: dict) -> None:
    w.write_u32(b["version"])
    w.write_u32(len(b["lore_item_names"]))
    for s in b["lore_item_names"]:
        w.write_str(s)


# ---------------------------------------------------------------------------
# block 13: factions
# ---------------------------------------------------------------------------

def _read_block13(r: GDReader, end: int) -> dict:
    b = {"version": r.read_u32()}
    b["my_faction"] = r.read_u32()
    factions = []
    for _ in range(r.read_u32()):
        factions.append({
            "changed": r.read_bool(),
            "unlocked": r.read_bool(),
            "value": r.read_float(),
            "positive_boost": r.read_float(),
            "negative_boost": r.read_float(),
        })
    b["factions"] = factions
    return b


def _write_block13(w: GDWriter, b: dict) -> None:
    w.write_u32(b["version"])
    w.write_u32(b["my_faction"])
    w.write_u32(len(b["factions"]))
    for f in b["factions"]:
        w.write_bool(f["changed"])
        w.write_bool(f["unlocked"])
        w.write_float(f["value"])
        w.write_float(f["positive_boost"])
        w.write_float(f["negative_boost"])


# ---------------------------------------------------------------------------
# block 14: UI state (hotbar etc.)
# ---------------------------------------------------------------------------

def _read_hotslot(r: GDReader) -> dict:
    slot_type = r.read_u32()
    slot = {"type": slot_type}
    if slot_type == 0:
        slot["skill_name"] = r.read_str()
        slot["is_item_skill"] = r.read_bool()
        slot["item_name"] = r.read_str()
        slot["item_equip_location"] = r.read_u32()
    elif slot_type == 4:
        slot["item_name"] = r.read_str()
        slot["bitmap_up"] = r.read_str()
        slot["bitmap_down"] = r.read_str()
        slot["default_text"] = r.read_wstr()
    return slot


def _write_hotslot(w: GDWriter, slot: dict) -> None:
    w.write_u32(slot["type"])
    if slot["type"] == 0:
        w.write_str(slot["skill_name"])
        w.write_bool(slot["is_item_skill"])
        w.write_str(slot["item_name"])
        w.write_u32(slot["item_equip_location"])
    elif slot["type"] == 4:
        w.write_str(slot["item_name"])
        w.write_str(slot["bitmap_up"])
        w.write_str(slot["bitmap_down"])
        w.write_wstr(slot["default_text"])


def _read_block14(r: GDReader, end: int) -> dict:
    b = {"version": r.read_u32()}
    b["equipment_selection"] = r.read_bool()
    b["skill_window_selection"] = r.read_u32()
    b["skill_setting_valid"] = r.read_bool()
    b["skill_sets"] = [{
        "primary_skill": r.read_str(),
        "secondary_skill": r.read_str(),
        "skill_active": r.read_bool(),
    } for _ in range(5)]
    if b["version"] >= 7:
        # Verified against real saves: bank-count and slots-per-bank
        # (a character with a transformation form has 2 hotbar banks, and the
        # second bank carries one extra slot). The only field after the slots
        # is the camera float, so read slots until exactly 4 bytes remain.
        b["hotslot_banks"] = r.read_u32()
        b["slots_per_bank"] = r.read_u32()
        b["unk4"] = r.read_u32()
        b["hotslots"] = []
        while end - r.pos > 4:
            b["hotslots"].append(_read_hotslot(r))
        b["camera_distance"] = r.read_float()
        return b
    slot_count = 36 if b["version"] == 4 else 46
    b["hotslots"] = [_read_hotslot(r) for _ in range(slot_count)]
    b["camera_distance"] = r.read_float()
    if b["version"] == 6:
        b["unk1"] = r.read_u32()
    return b


def _write_block14(w: GDWriter, b: dict) -> None:
    w.write_u32(b["version"])
    w.write_bool(b["equipment_selection"])
    w.write_u32(b["skill_window_selection"])
    w.write_bool(b["skill_setting_valid"])
    for ss in b["skill_sets"]:
        w.write_str(ss["primary_skill"])
        w.write_str(ss["secondary_skill"])
        w.write_bool(ss["skill_active"])
    if b["version"] >= 7:
        w.write_u32(b["hotslot_banks"])
        w.write_u32(b["slots_per_bank"])
        w.write_u32(b["unk4"])
    for slot in b["hotslots"]:
        _write_hotslot(w, slot)
    w.write_float(b["camera_distance"])
    if b["version"] == 6:
        w.write_u32(b["unk1"])


# ---------------------------------------------------------------------------
# block 15: tutorials
# ---------------------------------------------------------------------------

def _read_block15(r: GDReader, end: int) -> dict:
    b = {"version": r.read_u32()}
    b["tutorials_unlocked"] = [r.read_u32() for _ in range(r.read_u32())]
    return b


def _write_block15(w: GDWriter, b: dict) -> None:
    w.write_u32(b["version"])
    w.write_u32(len(b["tutorials_unlocked"]))
    for v in b["tutorials_unlocked"]:
        w.write_u32(v)


# ---------------------------------------------------------------------------
# block 16: play statistics
# ---------------------------------------------------------------------------

def _read_block16(r: GDReader, end: int) -> dict:
    b = {"version": r.read_u32()}
    b["playtime_seconds"] = r.read_u32()
    b["death_count"] = r.read_u32()
    b["kill_count"] = r.read_u32()
    b["experience_from_kills"] = r.read_u32()
    b["health_potions_used"] = r.read_u32()
    b["energy_potions_used"] = r.read_u32()
    b["max_level"] = r.read_u32()
    b["hits_received"] = r.read_u32()
    b["hits_inflicted"] = r.read_u32()
    b["crits_inflicted"] = r.read_u32()
    b["crits_received"] = r.read_u32()
    b["greatest_damage_done"] = r.read_float()
    b["greatest_monster_killed"] = [{
        "name": r.read_str(),
        "level": r.read_u32(),
        "life_mana": r.read_u32(),
        "last_monster_hit": r.read_str(),
        "last_monster_hit_by": r.read_str(),
    } for _ in range(3)]
    b["champion_kills"] = r.read_u32()
    b["last_monster_hit_DA"] = r.read_float()
    b["last_monster_hit_OA"] = r.read_float()
    b["greatest_damage_received"] = r.read_float()
    b["hero_kills"] = r.read_u32()
    b["items_crafted"] = r.read_u32()
    b["relics_crafted"] = r.read_u32()
    b["tier2_relics_crafted"] = r.read_u32()
    b["tier3_relics_crafted"] = r.read_u32()
    b["devotion_shrines_unlocked"] = r.read_u32()
    b["one_shot_chests_unlocked"] = r.read_u32()
    b["lore_notes_collected"] = r.read_u32()
    b["boss_kills"] = [r.read_u32() for _ in range(3)]
    b["survival_greatest_wave"] = r.read_u32()
    b["survival_greatest_score"] = r.read_u32()
    b["survival_defense_built"] = r.read_u32()
    b["survival_powerups_activated"] = r.read_u32()
    if b["version"] >= 11:
        b["skills_map"] = [{
            "skill_name": r.read_str(),
            "level": r.read_u32(),
        } for _ in range(r.read_u32())]
        b["endless_souls"] = r.read_u32()
        b["endless_essence"] = r.read_u32()
        b["difficulty_skip"] = r.read_byte()
    b["unique_items_found"] = r.read_u32()
    b["randomized_items_found"] = r.read_u32()
    if b["version"] >= 12:
        b["unk_v12_a"] = r.read_u32()
        b["unk_v12_b"] = r.read_u32()
    return b


def _write_block16(w: GDWriter, b: dict) -> None:
    w.write_u32(b["version"])
    w.write_u32(b["playtime_seconds"])
    w.write_u32(b["death_count"])
    w.write_u32(b["kill_count"])
    w.write_u32(b["experience_from_kills"])
    w.write_u32(b["health_potions_used"])
    w.write_u32(b["energy_potions_used"])
    w.write_u32(b["max_level"])
    w.write_u32(b["hits_received"])
    w.write_u32(b["hits_inflicted"])
    w.write_u32(b["crits_inflicted"])
    w.write_u32(b["crits_received"])
    w.write_float(b["greatest_damage_done"])
    for m in b["greatest_monster_killed"]:
        w.write_str(m["name"])
        w.write_u32(m["level"])
        w.write_u32(m["life_mana"])
        w.write_str(m["last_monster_hit"])
        w.write_str(m["last_monster_hit_by"])
    w.write_u32(b["champion_kills"])
    w.write_float(b["last_monster_hit_DA"])
    w.write_float(b["last_monster_hit_OA"])
    w.write_float(b["greatest_damage_received"])
    w.write_u32(b["hero_kills"])
    w.write_u32(b["items_crafted"])
    w.write_u32(b["relics_crafted"])
    w.write_u32(b["tier2_relics_crafted"])
    w.write_u32(b["tier3_relics_crafted"])
    w.write_u32(b["devotion_shrines_unlocked"])
    w.write_u32(b["one_shot_chests_unlocked"])
    w.write_u32(b["lore_notes_collected"])
    for v in b["boss_kills"]:
        w.write_u32(v)
    w.write_u32(b["survival_greatest_wave"])
    w.write_u32(b["survival_greatest_score"])
    w.write_u32(b["survival_defense_built"])
    w.write_u32(b["survival_powerups_activated"])
    if b["version"] >= 11:
        w.write_u32(len(b["skills_map"]))
        for s in b["skills_map"]:
            w.write_str(s["skill_name"])
            w.write_u32(s["level"])
        w.write_u32(b["endless_souls"])
        w.write_u32(b["endless_essence"])
        w.write_byte(b["difficulty_skip"])
    w.write_u32(b["unique_items_found"])
    w.write_u32(b["randomized_items_found"])
    if b["version"] >= 12:
        w.write_u32(b.get("unk_v12_a", 0))
        w.write_u32(b.get("unk_v12_b", 0))


# ---------------------------------------------------------------------------
# top level
# ---------------------------------------------------------------------------

_BLOCK_READERS = {
    1: _read_block1, 2: _read_block2, 3: _read_block3, 4: _read_block4,
    5: _read_block5, 6: _read_block6, 7: _read_block7, 8: _read_block8,
    10: _read_block10, 12: _read_block12, 13: _read_block13, 14: _read_block14,
    15: _read_block15, 16: _read_block16, 17: _read_block17,
}

_BLOCK_WRITERS = {
    1: _write_block1, 2: _write_block2, 3: _write_block3, 4: _write_block4,
    5: _write_block5, 6: _write_block6, 7: _write_block7, 8: _write_block8,
    10: _write_block10, 12: _write_block12, 13: _write_block13, 14: _write_block14,
    15: _write_block15, 16: _write_block16, 17: _write_block17,
}


class Hooks:
    """The replaceable item, skill and stash page readers/writers of one parse or write. Normally the
    functions of this module; gdcheck.app.save_diag passes variants to try other field layouts (the
    .35 diagnosis swapped the module functions instead - this keeps every other parse untouched)."""

    __slots__ = ("read_item", "write_item", "read_skill", "write_skill", "read_stash_page",
                 "write_stash_page")

    def __init__(self, **replace):
        unknown = set(replace) - set(self.__slots__)
        if unknown:
            raise TypeError(f"unknown hooks: {sorted(unknown)}")
        self.read_item = replace.get("read_item", read_item)
        self.write_item = replace.get("write_item", write_item)
        self.read_skill = replace.get("read_skill", _read_skill)
        self.write_skill = replace.get("write_skill", _write_skill)
        self.read_stash_page = replace.get("read_stash_page", _read_stash_page)
        self.write_stash_page = replace.get("write_stash_page", _write_stash_page)


DEFAULT_HOOKS = Hooks()
GDReader.hooks = DEFAULT_HOOKS          # readers/writers use these unless given their own
GDWriter.hooks = DEFAULT_HOOKS


def parse_character(data: bytes, hooks: Hooks | None = None) -> dict:
    r = GDReader(data)
    if hooks is not None:
        r.hooks = hooks
    doc: dict = {"seed": r.seed}

    magic = r.read_u32()
    if magic != GDC_MAGIC:
        raise GDFormatError(f"bad magic {magic:#010x} (not a player.gdc file)")
    doc["preamble_version"] = r.read_u32()

    doc["header"] = {
        "character_name": r.read_wstr(),
        "male": r.read_bool(),
        "player_class_tag": r.read_str(),
        "character_level": r.read_u32(),
        "hardcore": r.read_bool(),
        "expansion_status": r.read_byte(),
    }
    r.verify_checksum()

    doc["data_version"] = r.read_u32()
    doc["mystery"] = r.read_bytes(16)

    blocks = []
    while r.remaining() > 0:
        block_id, end = r.begin_block()
        reader = _BLOCK_READERS.get(block_id)
        if reader is not None:
            try:
                block = reader(r, end)
            except GDFormatError:
                raise
            if r.pos > end:
                raise GDFormatError(
                    f"block {block_id}: parser overran block end by {r.pos - end} bytes")
            block["_extra"] = _read_extra(r, end)
            if block["_extra"] is None:
                del block["_extra"]
        else:
            # Unknown block: keep it opaque but round-trippable.
            r.pos = r.pos  # (position already at payload start)
            rem = end - r.pos
            if rem % 4 == 0:
                block = {"_opaque": {"kind": "ints",
                                     "values": [r.read_u32() for _ in range(rem // 4)]}}
            else:
                block = {"_opaque": {"kind": "bytes", "values": r.read_bytes(rem)}}
        block["id"] = block_id
        r.end_block(block_id, end)
        blocks.append(block)
    doc["blocks"] = blocks
    return doc


def write_character(doc: dict, hooks: Hooks | None = None) -> bytes:
    w = GDWriter(doc["seed"])
    if hooks is not None:
        w.hooks = hooks
    w.write_u32(GDC_MAGIC)
    w.write_u32(doc["preamble_version"])

    h = doc["header"]
    w.write_wstr(h["character_name"])
    w.write_bool(h["male"])
    w.write_str(h["player_class_tag"])
    w.write_u32(h["character_level"])
    w.write_bool(h["hardcore"])
    w.write_byte(h["expansion_status"])
    w.write_checksum()

    w.write_u32(doc["data_version"])
    w.write_bytes(doc["mystery"])

    for block in doc["blocks"]:
        w.begin_block(block["id"])
        if "_opaque" in block:
            _write_extra(w, block["_opaque"])
        else:
            _BLOCK_WRITERS[block["id"]](w, block)
            _write_extra(w, block.get("_extra"))
        w.end_block()
    return w.getvalue()


def load_character(path) -> dict:
    with open(path, "rb") as f:
        doc = parse_character(f.read())
    doc["_source_path"] = str(path)
    return doc


def get_block(doc: dict, block_id: int) -> dict | None:
    for block in doc["blocks"]:
        if block["id"] == block_id:
            return block
    return None

