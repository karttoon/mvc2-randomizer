#!/usr/bin/env python3
"""Rebuild mvc2_data/vanilla_palettes.bin from a STOCK game_50.arc.

The randomizer's "vanilla" data (Reset to vanilla, the stock fill for
characters with fewer palettes than buttons, FX source colors, edit
protection) all come from this file, so it must be built from an unmodified
game. The arc is checked first: its decompressed size must equal the stock
size and every stage slot must match the bundled stock stage hashes.

    py tools/build_vanilla_palettes.py "D:/path/to/stock/game_50.arc"
"""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mvc2_data.steam import (EXPECTED_ROM_SIZE, STEAM_PALETTE_OFFSETS,  # noqa: E402
                             TOTAL_PALETTE_COUNT, VANILLA_PALETTES_FILE, read_arc)
from mvc2_data import stages as stagelib  # noqa: E402


def main(arc_path):
    rom = read_arc(arc_path)
    if len(rom) != EXPECTED_ROM_SIZE:
        sys.exit(f"Not a stock arc: ROM is {len(rom):,} bytes, stock is "
                 f"{EXPECTED_ROM_SIZE:,}.")
    with open(os.path.join(ROOT, "mvc2_data", "vanilla_stage_hashes.json")) as f:
        stage_hashes = json.load(f)
    toc = stagelib.parse_toc(rom)
    for slot, h in stage_hashes.items():
        pol_i, tex_i = stagelib.slot_entries(slot)
        if (stagelib.entry_hash(rom, pol_i, toc) != h.get("pol")
                or stagelib.entry_hash(rom, tex_i, toc) != h.get("tex")):
            sys.exit(f"Not a stock arc: stage slot {slot} is modified.")
    blob = b"".join(bytes(rom[STEAM_PALETTE_OFFSETS[cid]:
                              STEAM_PALETTE_OFFSETS[cid] + TOTAL_PALETTE_COUNT[cid] * 32])
                    for cid in sorted(STEAM_PALETTE_OFFSETS))
    with open(VANILLA_PALETTES_FILE, "wb") as f:
        f.write(blob)
    print(f"Wrote {VANILLA_PALETTES_FILE} ({len(blob):,} bytes, "
          f"{len(STEAM_PALETTE_OFFSETS)} characters, "
          f"sha1 {hashlib.sha1(blob).hexdigest()[:12]}).")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
