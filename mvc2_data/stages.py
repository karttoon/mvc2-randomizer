"""AFS stage-file handling for the MvC2 Steam ROM.

The decompressed ROM inside game_50.arc is an IBIS wrapper (0x40-byte header,
with the archive size at offset 0x0C) around a standard AFS archive:

    AFS_BASE:  "AFS\\0", uint32 file count, then count x (offset, size)
               uint32 pairs (offsets relative to AFS_BASE). No name table.

Layout facts (verified against the live arc): TOC index order equals physical
order, every entry starts at align(prev_end, 0x800), padding is zeros, and the
archive ends exactly at the ROM end. Rebuilding with the same rules therefore
round-trips byte-for-byte.

The 17 fighting-stage slots STG00..STG10 are TOC entries 801-834 (one POL+TEX
pair per slot, in slot order - the same numbering mvccModManager's F-names
use). All character palette data lives physically BEFORE entry 801, so
resizing stage entries never moves the palette offsets this tool writes to.
"""
import hashlib
import struct

AFS_BASE = 0x40
AFS_ALIGN = 0x800
IBIS_SIZE_OFFSET = 0x0C          # uint32: total AFS size (ROM size - 0x40)

# Stage slot ids in TOC order; slot j's POL/TEX are entries 801+2j / 802+2j.
STAGE_SLOTS = ["00", "01", "02", "03", "04", "05", "06", "07", "08",
               "09", "0A", "0B", "0C", "0D", "0E", "0F", "10"]
STG_FIRST_INDEX = 801
TRAINING_SLOT = "0B"             # the only slot safe for POL (geometry) swaps


def slot_entries(slot):
    """(pol_index, tex_index) TOC entries for a stage slot id like '0B'."""
    j = STAGE_SLOTS.index(slot.upper())
    return STG_FIRST_INDEX + 2 * j, STG_FIRST_INDEX + 2 * j + 1


def parse_toc(rom):
    """[(offset, size), ...] for every AFS entry (offsets AFS-relative)."""
    if rom[AFS_BASE:AFS_BASE + 4] != b"AFS\x00":
        raise ValueError("AFS archive not found in ROM (bad magic)")
    count = struct.unpack_from("<I", rom, AFS_BASE + 4)[0]
    return [struct.unpack_from("<II", rom, AFS_BASE + 8 + i * 8)
            for i in range(count)]


def read_entry(rom, index, entries=None):
    off, size = (entries or parse_toc(rom))[index]
    return bytes(rom[AFS_BASE + off:AFS_BASE + off + size])


def entry_hash(rom, index, entries=None):
    return hashlib.md5(read_entry(rom, index, entries)).hexdigest()


def replace_entries(rom, replacements):
    """Rebuild the ROM with some AFS entries replaced.

    replacements: {toc_index: new_bytes}. Returns a new bytearray; the input
    is not modified. Later entries shift as needed (0x800-aligned, zero
    padding) and the IBIS size field is updated. With an empty replacements
    dict the result is byte-identical to the input.

    Only entries >= STG_FIRST_INDEX may be replaced: earlier entries hold the
    game binary whose palette data this tool addresses by absolute offset.
    """
    for idx in replacements:
        if idx < STG_FIRST_INDEX:
            raise ValueError(f"refusing to replace entry {idx}: "
                             f"only stage entries ({STG_FIRST_INDEX}+) are safe")
    entries = parse_toc(rom)
    data_start = entries[0][0]

    # New layout: same order, minimal aligned padding.
    new_entries = []
    payload_at = []                       # (new_offset, bytes-or-None, old)
    cur = data_start
    for i, (off, size) in enumerate(entries):
        data = replacements.get(i)
        length = len(data) if data is not None else size
        new_entries.append((cur, length))
        payload_at.append((cur, data, (off, size)))
        cur = (cur + length + AFS_ALIGN - 1) & ~(AFS_ALIGN - 1)
    total = cur

    out = bytearray(AFS_BASE + total)
    out[:AFS_BASE + data_start] = rom[:AFS_BASE + data_start]   # headers + TOC pad
    for i, (off, size) in enumerate(new_entries):
        struct.pack_into("<II", out, AFS_BASE + 8 + i * 8, off, size)
    struct.pack_into("<I", out, IBIS_SIZE_OFFSET, total)
    for (new_off, data, (old_off, old_size)) in payload_at:
        if data is None:
            data = rom[AFS_BASE + old_off:AFS_BASE + old_off + old_size]
        out[AFS_BASE + new_off:AFS_BASE + new_off + len(data)] = data
    return out
