""".arc and .arz readers (game installation) - taken over unchanged from the
.35 extractor (parts 3a/3b). Used by the PC tools (index build, record view).

LZ4 blocks are unpacked by the optional C module `lz4` when it is installed (GD-Tools Extras 7 -
minutes faster for the index build), else by the pure-Python decoder of data/savefile.py. Both give
the same bytes (tests/unit/test_arz_lz4.py); on any error of the module the pure decoder decides,
so corrupt data fails with the same GDFormatError. This guarded import is the one documented
exception of tests/architecture/test_layers.py::test_no_third_party_imports."""
from __future__ import annotations

import struct
from pathlib import Path

from .savefile import GDFormatError
from .savefile import lz4_decompress as _lz4_pure

try:                                    # optional C decoder, never required
    import lz4.block as _lz4_block
except Exception:                       # noqa: BLE001 - ImportError or a broken installation
    _lz4_block = None


def lz4_decompress(src: bytes, size: int) -> bytes:
    """One LZ4 block: the C module if installed (same bytes), otherwise or on its error pure Python."""
    if _lz4_block is not None and size:
        try:
            out = _lz4_block.decompress(src, uncompressed_size=size)
            if len(out) == size:
                return bytes(out)
        except Exception:               # noqa: BLE001 - the pure decoder raises the real GDFormatError
            pass
    return _lz4_pure(src, size)
# ===========================================================================
# Part 3a - .arc archive reader (localisation Text_XX.arc)
# ===========================================================================

""".arc archive reader - used here mainly for Text_EN.arc localization.

Layout (little-endian):
    header:  u32 magic 'ARC'(0x435241), u32 version(3),
             u32 file_entries, u32 data_records,
             u32 record_table_size, u32 string_table_size, u32 record_table_offset
    at record_table_offset: data_records x part {u32 offset, u32 csize, u32 dsize}
    then string table (filenames, raw ascii indexed by offset)
    then file_entries x record {u32 entry_type, u32 offset, u32 csize, u32 dsize,
             u32 adler32, u64 filetime, u32 parts, u32 first_part,
             u32 name_len, u32 name_offset}
    file bytes: concatenated parts, each LZ4 block-compressed unless csize==dsize
"""


ARC_MAGIC = 0x435241


class ArcFile:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        (magic, version, file_entries, data_records,
         rt_size, st_size, rt_offset) = struct.unpack_from("<IIIIIII", self.data, 0)
        if magic != ARC_MAGIC or version != 3:
            raise GDFormatError(f"{self.path.name}: not an ARC v3 archive")
        self.rt_offset = rt_offset
        self.rt_size = rt_size
        self.st_size = st_size

        pos = rt_offset + rt_size + st_size
        self.entries: dict[str, tuple] = {}
        for _ in range(file_entries):
            (entry_type, offset, csize, dsize, _adler, _ftime, parts,
             first_part, name_len, name_offset) = struct.unpack_from("<IIIIIQIIII", self.data, pos)
            pos += 44
            name_pos = rt_offset + rt_size + name_offset
            name = self.data[name_pos:name_pos + name_len].decode("latin-1")
            if name:
                self.entries[name] = (entry_type, offset, csize, dsize, parts, first_part)

    def names(self):
        return self.entries.keys()

    def read_file(self, name: str) -> bytes | None:
        e = self.entries.get(name)
        if e is None:
            return None
        entry_type, offset, csize, dsize, parts, first_part = e
        if entry_type == 1 and csize == dsize:
            return self.data[offset:offset + csize]
        out = bytearray()
        for i in range(parts):
            ppos = self.rt_offset + (first_part + i) * 12
            p_off, p_csize, p_dsize = struct.unpack_from("<III", self.data, ppos)
            chunk = self.data[p_off:p_off + p_csize]
            if p_csize != p_dsize:
                chunk = lz4_decompress(chunk, p_dsize)
            out += chunk
        return bytes(out)


class LazyArcFile:
    """ARC v3 reader that reads only the tables on open and single files on demand (large
    archives such as Creatures.arc are not loaded into memory). Same layout as ArcFile."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        with open(self.path, "rb") as f:
            head = f.read(28)
            if len(head) < 28:
                raise GDFormatError(f"{self.path.name}: not an ARC v3 archive")
            (magic, version, file_entries, _data_records,
             rt_size, st_size, rt_offset) = struct.unpack_from("<IIIIIII", head, 0)
            if magic != ARC_MAGIC or version != 3:
                raise GDFormatError(f"{self.path.name}: not an ARC v3 archive")
            f.seek(rt_offset)
            tables = f.read(rt_size + st_size + file_entries * 44)
        if len(tables) < rt_size + st_size + file_entries * 44:
            raise GDFormatError(f"{self.path.name}: truncated ARC tables")
        self._parts = tables[:rt_size]
        self.entries: dict[str, tuple] = {}
        pos = rt_size + st_size
        for _ in range(file_entries):
            (entry_type, offset, csize, dsize, _adler, _ftime, parts,
             first_part, name_len, name_offset) = struct.unpack_from("<IIIIIQIIII", tables, pos)
            pos += 44
            name = tables[rt_size + name_offset:rt_size + name_offset + name_len].decode("latin-1")
            if name:
                self.entries[name] = (entry_type, offset, csize, dsize, parts, first_part)

    def names(self):
        return self.entries.keys()

    def size(self, name: str) -> int | None:
        e = self.entries.get(name)
        return e[3] if e else None

    def read_file(self, name: str, first_part_only: bool = False) -> bytes | None:
        e = self.entries.get(name)
        if e is None:
            return None
        entry_type, offset, csize, dsize, parts, first_part = e
        with open(self.path, "rb") as f:
            if entry_type == 1 and csize == dsize:
                f.seek(offset)
                return f.read(csize)
            out = bytearray()
            for i in range(parts if not first_part_only else min(parts, 1)):
                p_off, p_csize, p_dsize = struct.unpack_from("<III", self._parts, (first_part + i) * 12)
                f.seek(p_off)
                chunk = f.read(p_csize)
                if p_csize != p_dsize:
                    chunk = lz4_decompress(chunk, p_dsize)
                out += chunk
            return bytes(out)


def load_localization(path: str | Path) -> dict[str, str]:
    """Load every tag=value line from all text files in a Text_*.arc archive."""
    arc = ArcFile(path)
    table: dict[str, str] = {}
    for name in arc.names():
        data = arc.read_file(name)
        if data is None:
            continue
        try:
            text = data.decode("utf-8-sig", errors="replace")
        except Exception:
            continue
        for line in text.splitlines():
            eq = line.find("=")
            if eq > 0:
                table[line[:eq].strip()] = line[eq + 1:].strip()
    return table


# ===========================================================================
# Part 3b - database.arz reader (game database)
# ===========================================================================

"""database.arz reader - the Grim Dawn game database.

Layout (little-endian):
    header:  u16 unknown, u16 version(3),
             u32 record_table_start, u32 record_table_size, u32 record_count,
             u32 string_table_start, u32 string_table_size
    string table: u32 count, then count x (u32 len + ascii bytes)
    record table: per record:
             u32 name_index (into string table)
             u32 type_len + ascii type (record class, e.g. "ArmorProtective_Head")
             u32 offset (data location minus 24)
             u32 compressed_size, u32 decompressed_size
             u32 unknown, u32 unknown2
    record data: LZ4 block-compressed field stream:
             repeat: u16 dtype, u16 count, u32 fieldname_index, count x u32
             dtype 1 = float, 2 = string index, else int
"""


class ArzRecordInfo:
    __slots__ = ("name", "rtype", "offset", "csize", "dsize")

    def __init__(self, name, rtype, offset, csize, dsize):
        self.name = name
        self.rtype = rtype
        self.offset = offset
        self.csize = csize
        self.dsize = dsize


class ArzFile:
    """Random-access reader over one .arz database file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        (unknown, version, rt_start, rt_size, rt_count,
         st_start, st_size) = struct.unpack_from("<HHIIIII", self.data, 0)
        if version != 3:
            raise GDFormatError(f"{self.path.name}: unsupported ARZ version {version}")

        # string table
        pos = st_start
        count = struct.unpack_from("<I", self.data, pos)[0]
        pos += 4
        strings = []
        for _ in range(count):
            n = struct.unpack_from("<I", self.data, pos)[0]
            pos += 4
            strings.append(self.data[pos:pos + n].decode("latin-1"))
            pos += n
        self.strings = strings

        # record table
        pos = rt_start
        records: dict[str, ArzRecordInfo] = {}
        for _ in range(rt_count):
            name_idx = struct.unpack_from("<I", self.data, pos)[0]
            pos += 4
            tlen = struct.unpack_from("<I", self.data, pos)[0]
            pos += 4
            rtype = self.data[pos:pos + tlen].decode("latin-1")
            pos += tlen
            offset, csize, dsize, _, _ = struct.unpack_from("<IIIII", self.data, pos)
            pos += 20
            name = strings[name_idx]
            records[name] = ArzRecordInfo(name, rtype, offset, csize, dsize)
        self.records = records

    def record_names(self):
        return self.records.keys()

    def record_type(self, name: str) -> str | None:
        info = self.records.get(name)
        return info.rtype if info else None

    def read_record(self, name: str) -> dict | None:
        """Decompress and decode a single record into {field: value}."""
        info = self.records.get(name)
        if info is None:
            return None
        raw = lz4_decompress(
            self.data[info.offset + 24: info.offset + 24 + info.csize],
            info.dsize)
        strings = self.strings
        rec: dict = {"_recordname": name, "_type": info.rtype}
        pos = 0
        n = len(raw)
        while pos < n:
            dtype, count, name_idx = struct.unpack_from("<HHI", raw, pos)
            pos += 8
            vals = struct.unpack_from(f"<{count}I", raw, pos)
            pos += 4 * count
            field = strings[name_idx]
            if dtype == 1:
                out = [struct.unpack("<f", struct.pack("<I", v))[0] for v in vals]
            elif dtype == 2:
                out = [strings[v] if v < len(strings) else "" for v in vals]
            else:
                out = list(vals)
            rec[field] = out[0] if count == 1 else out
        return rec



# .arz databases of a game folder (the index builder gdcheck.app.index_build uses this function;
# tests/unit/test_refs_extract.py keeps it equal to the copy in data/fingerprint.py). Survival mode
# databases are never merged into the game data.
ARZ_FLAT = ("database.arz", "GDX1.arz", "GDX2.arz", "GDX3.arz", "GDX4.arz")


def arz_paths(gd: Path) -> list[Path]:
    """All .arz databases in load order: base first, then the expansions (later overrides earlier)."""
    gd = Path(gd)
    out = []
    base = gd / "database" / "database.arz"
    if base.exists():
        out.append(base)
    for i in (1, 2, 3, 4):
        p = gd / f"gdx{i}" / "database" / f"GDX{i}.arz"
        if p.exists():
            out.append(p)
    return out


def arz_files(folder: Path) -> list[Path]:
    """The game's .arz files from an installation folder (arz_paths) or from a flat folder holding
    database.arz and GDX1-4.arz (copies, e.g. ../arz) - allowlist, never SurvivalMode*.arz."""
    found = arz_paths(folder)
    if found:
        return found
    return [Path(folder) / n for n in ARZ_FLAT if (Path(folder) / n).is_file()]
