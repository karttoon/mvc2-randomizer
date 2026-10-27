"""Standalone applier for the CPU-AI pack (no dependencies beyond the stdlib) - copy this file into other tools.

Works on the decompressed game_50.arc ROM (the IBIS blob: 'IBIS' header, AFS archive at +0x40), i.e. exactly what
mvc2-randomizer's steam.read_arc() returns and steam.write_arc() takes.

    rom = steam.read_arc(arc)                      # bytearray
    rom = apply_ai_pack(rom, 'ai_pack')            # returns a NEW bytearray (the archive is re-laid out)
    off = translate(stock_off, rom, stock_toc)     # a fixed STOCK ROM offset -> where those bytes are now
    steam.write_arc(arc, rom)

Why translate(): the AI files grow, and AFS entries are stored back to back, so every entry after the first grown
AI file (AFS idx 504+, stock ROM offset >= ~0x3DF5040) moves. Anything that patches at fixed stock ROM offsets
(PalMod palette tables, FX sheet offsets) must translate them first or it writes into the wrong file.
"""
import hashlib, json, os, struct

AFS = 0x40                 # AFS archive offset inside the IBIS blob
DATA_START = 0x80000       # first entry's AFS-relative offset
ALIGN = 0x800


def parse_toc(rom):
    if rom[:4] != b'IBIS' or rom[AFS:AFS + 4] != b'AFS\x00':
        raise ValueError('not an IBIS/AFS game_50 ROM')
    n = struct.unpack_from('<I', rom, AFS + 4)[0]
    return [struct.unpack_from('<II', rom, AFS + 8 + 8 * i) for i in range(n)]


def build(rom, entries):
    """re-lay-out the archive with `entries` (list of bytes, index-aligned) - same layout rules as the game's own
    archive: index order, each entry 0x800-aligned from DATA_START, empty entries take the cursor without advancing"""
    head = bytearray(rom[:AFS + DATA_START])            # IBIS header + AFS header/TOC region (keeps padding/meta)
    struct.pack_into('<I', head, AFS + 4, len(entries))
    data, cur = bytearray(), DATA_START
    for i, b in enumerate(entries):
        struct.pack_into('<II', head, AFS + 8 + 8 * i, cur, len(b))
        if b:
            pos = cur - DATA_START
            data[len(data):] = b'\x00' * (pos - len(data))
            data += b
            cur += (len(b) + ALIGN - 1) // ALIGN * ALIGN
    data += b'\x00' * (cur - DATA_START - len(data))
    meta_size = struct.unpack_from('<I', head, AFS + DATA_START - 4)[0]
    struct.pack_into('<II', head, AFS + DATA_START - 8, cur, meta_size)    # (afs_size, filename-table size)
    struct.pack_into('<I', head, 0x0C, cur)                                  # IBIS: AFS payload size
    return bytearray(head) + data


def apply_ai_pack(rom, pack_dir, check_stock=True):
    """put the pack's AI entries into `rom`. With check_stock, every replaced entry must currently be stock or
    already the pack's version (so a different AI mod is never silently overwritten)."""
    pack = json.load(open(os.path.join(pack_dir, 'ai_pack.json')))
    toc = parse_toc(rom)
    entries = [bytes(rom[AFS + o:AFS + o + s]) if s else b'' for o, s in toc]
    for e in pack['entries']:
        cur = hashlib.md5(entries[e['idx']]).hexdigest()
        if check_stock and cur not in (e['stock_md5'], e['md5']):
            raise ValueError('AFS entry %d (%s) is neither stock nor this pack - another AI mod?' % (e['idx'], e['name']))
        entries[e['idx']] = open(os.path.join(pack_dir, e['file']), 'rb').read()
    return build(rom, entries)


def remove_ai_pack(rom, stock_rom):
    """undo: put the stock AI entries back (needs any stock ROM for the bytes)"""
    st = parse_toc(stock_rom)
    toc = parse_toc(rom)
    entries = [bytes(rom[AFS + o:AFS + o + s]) if s else b'' for o, s in toc]
    for i, (o, s) in enumerate(st):
        e = bytes(stock_rom[AFS + o:AFS + o + s]) if s else b''
        if i < len(entries) and 504 <= i < 504 + 59 * 5:
            entries[i] = e
    return build(rom, entries)


def translate(stock_off, rom, stock_toc):
    """stock ROM offset -> current ROM offset of the same byte (same AFS entry, same offset inside it).
    stock_toc = json.load(open('ai_pack/stock_toc.json')) ([[afs_offset, size], ...])."""
    rel = stock_off - AFS
    toc = parse_toc(rom)
    for i, (o, s) in enumerate(stock_toc):
        if s and o <= rel < o + s:
            if toc[i][1] != s:
                raise ValueError('offset %#x lies in AFS entry %d, whose size changed (%d -> %d)' % (
                    stock_off, i, s, toc[i][1]))
            return AFS + toc[i][0] + (rel - o)
    raise ValueError('offset %#x is not inside any AFS entry' % stock_off)
