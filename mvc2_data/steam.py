"""Steam ARC file handling and ROM palette read/write for MvC2."""

import struct
import zlib

# Steam palette base offsets — from PalMod's baseSteamShiftTable (Game_MVC2_A.cpp)
# Each value is the ROM offset of the LP "Main" palette for that character.
STEAM_PALETTE_OFFSETS = {
    0x00: 0x82CC60,    # Ryu
    0x01: 0x9049A0,    # Zangief
    0x02: 0x997A20,    # Guile
    0x03: 0xA5D320,    # Morrigan
    0x04: 0xB77B60,    # Anakaris
    0x05: 0xC4FE20,    # Strider
    0x06: 0xD36F80,    # Cyclops
    0x07: 0xE32D20,    # Wolverine
    0x08: 0xF34940,    # Psylocke
    0x09: 0x10201E0,   # Iceman
    0x0A: 0x1107480,   # Rogue
    0x0B: 0x11F58A0,   # Captain America
    0x0C: 0x12D44C0,   # Spider-Man
    0x0D: 0x13EEF80,   # Hulk
    0x0E: 0x1512E20,   # Venom
    0x0F: 0x1625A40,   # Doctor Doom
    0x10: 0x173D7C0,   # Tron Bonne
    0x11: 0x1819B40,   # Jill
    0x12: 0x19176C0,   # Hayato
    0x13: 0x1A20D00,   # Ruby Heart
    0x14: 0x1B3A8C0,   # SonSon
    0x15: 0x1C53DE0,   # Amingo
    0x16: 0x1D48E20,   # Marrow
    0x17: 0x1E55220,   # Cable
    0x18: 0x1F41800,   # Abyss (Form 1)
    0x19: 0x1FCED80,   # Abyss (Form 2)
    0x1A: 0x20A8DA0,   # Abyss (Form 3)
    0x1B: 0x2129520,   # Chun-Li
    0x1C: 0x21BC920,   # Mega Man
    0x1D: 0x2230DE0,   # Roll
    0x1E: 0x22BA5A0,   # Akuma
    0x1F: 0x23D4AA0,   # BB Hood
    0x20: 0x24FC100,   # Felicia
    0x21: 0x25691A0,   # Charlie Nash
    0x22: 0x2630380,   # Sakura
    0x23: 0x267EB60,   # Dan
    0x24: 0x271EBE0,   # Cammy
    0x25: 0x27D5F20,   # Dhalsim
    0x26: 0x285B0A0,   # M. Bison
    0x27: 0x28E1AE0,   # Ken
    0x28: 0x29CB740,   # Gambit
    0x29: 0x2AF8860,   # Juggernaut
    0x2A: 0x2C070E0,   # Storm
    0x2B: 0x2D089E0,   # Sabretooth
    0x2C: 0x2E1FF80,   # Magneto
    0x2D: 0x2F08560,   # Shuma-Gorath
    0x2E: 0x30091A0,   # War Machine
    0x2F: 0x3124EC0,   # Silver Samurai
    0x30: 0x3223B60,   # Omega Red
    0x31: 0x3339480,   # Spiral
    0x32: 0x34652C0,   # Colossus
    0x33: 0x35683A0,   # Iron Man
    0x34: 0x368E840,   # Sentinel
    0x35: 0x37B5EE0,   # Blackheart
    0x36: 0x38A4C80,   # Thanos
    0x37: 0x3989460,   # Jin
    0x38: 0x3A793A0,   # Captain Commando
    0x39: 0x3B78B00,   # Wolverine (Bone Claw)
    0x3A: 0x3BDEB20,   # Servbot
}

# Total palette entries per character (from PalMod MVC2_A_DEF.h)
# All entries are contiguous 32-byte ARGB4444 palettes.
# Core = 6 buttons x 8 slots = 48; Status effects = 8 (or 16);
# Extras = variable (assists, morphs, effects, weapon palettes, etc.)
TOTAL_PALETTE_COUNT = {
    0x00:   56,  # Ryu
    0x01:  106,  # Zangief
    0x02:   56,  # Guile
    0x03:  182,  # Morrigan
    0x04:   56,  # Anakaris
    0x05:   56,  # Strider
    0x06:  148,  # Cyclops
    0x07:   65,  # Wolverine
    0x08:   56,  # Psylocke
    0x09:  122,  # Iceman
    0x0A:   82,  # Rogue
    0x0B:   64,  # Captain America
    0x0C:  148,  # Spider-Man
    0x0D:   58,  # Hulk
    0x0E:   56,  # Venom
    0x0F:  200,  # Doctor Doom
    0x10:   69,  # Tron Bonne
    0x11:   59,  # Jill
    0x12:   71,  # Hayato
    0x13:   64,  # Ruby Heart
    0x14:   88,  # SonSon
    0x15:   59,  # Amingo
    0x16:   56,  # Marrow
    0x17:   56,  # Cable
    0x18:   57,  # Abyss (Form 1)
    0x19:   57,  # Abyss (Form 2)
    0x1A:   56,  # Abyss (Form 3)
    0x1B:   56,  # Chun-Li
    0x1C:  577,  # Mega Man
    0x1D:  577,  # Roll
    0x1E:   56,  # Akuma
    0x1F:   68,  # BB Hood
    0x20:   56,  # Felicia
    0x21:   56,  # Charlie Nash
    0x22:   82,  # Sakura
    0x23:   56,  # Dan
    0x24:  110,  # Cammy
    0x25:   86,  # Dhalsim
    0x26:   68,  # M. Bison
    0x27:   56,  # Ken
    0x28:   86,  # Gambit
    0x29:  116,  # Juggernaut
    0x2A:   90,  # Storm
    0x2B:   56,  # Sabretooth
    0x2C:   58,  # Magneto
    0x2D:  352,  # Shuma-Gorath
    0x2E:   56,  # War Machine
    0x2F:  103,  # Silver Samurai
    0x30:   80,  # Omega Red
    0x31:  258,  # Spiral
    0x32:  248,  # Colossus
    0x33:   56,  # Iron Man
    0x34:   67,  # Sentinel
    0x35:   75,  # Blackheart
    0x36:   79,  # Thanos
    0x37:  182,  # Jin
    0x38:   73,  # Captain Commando
    0x39:   64,  # Wolverine (Bone Claw)
    0x3A:   86,  # Servbot
}

# ARC file constants
ARC_MAGIC = b"ARC\x00"
ARC_VERSION = 7
ARC_DATA_OFFSET = 0x8000
ARC_ENTRY_OFFSET = 0x08
ARC_FILENAME = b"bin\\mvsc2"
ARC_TYPE_HASH = 0x21D3D8A7

# ROM validation
IBIS_MAGIC = b"IBIS"
EXPECTED_ROM_SIZE = 112_635_968


def read_arc(arc_path):
    """Read a game_50.arc file and decompress the ROM.

    Returns a mutable bytearray of the decompressed ROM data.
    """
    with open(arc_path, "rb") as f:
        header = f.read(8)

    magic = header[:4]
    if magic != ARC_MAGIC:
        raise ValueError(f"Not an ARC file (magic: {magic!r}, expected {ARC_MAGIC!r})")

    version = struct.unpack_from("<H", header, 4)[0]
    if version != ARC_VERSION:
        raise ValueError(f"Unexpected ARC version {version} (expected {ARC_VERSION})")

    with open(arc_path, "rb") as f:
        f.seek(ARC_DATA_OFFSET)
        compressed = f.read()

    rom = bytearray(zlib.decompress(compressed))
    return rom


def write_arc(arc_path, rom_data):
    """Compress ROM data and write it as a game_50.arc file.

    Rebuilds the full ARC header with correct sizes.
    """
    compressed = zlib.compress(bytes(rom_data))
    compressed_size = len(compressed)
    uncompressed_size = len(rom_data)

    # flags (top 3 bits) + uncompressed_size (bottom 29 bits)
    # Original flags were 0x2 (bit 30 set) based on binary inspection
    flags_and_size = (0x2 << 29) | (uncompressed_size & 0x1FFFFFFF)

    # Build the full file
    buf = bytearray(ARC_DATA_OFFSET + compressed_size)

    # ARC header
    buf[0:4] = ARC_MAGIC
    struct.pack_into("<H", buf, 4, ARC_VERSION)
    struct.pack_into("<H", buf, 6, 1)  # file_count = 1

    # File entry at offset 0x08
    filename_padded = ARC_FILENAME.ljust(64, b"\x00")
    buf[ARC_ENTRY_OFFSET:ARC_ENTRY_OFFSET + 64] = filename_padded
    struct.pack_into("<I", buf, ARC_ENTRY_OFFSET + 64, ARC_TYPE_HASH)
    struct.pack_into("<I", buf, ARC_ENTRY_OFFSET + 68, compressed_size)
    struct.pack_into("<I", buf, ARC_ENTRY_OFFSET + 72, flags_and_size)
    struct.pack_into("<I", buf, ARC_ENTRY_OFFSET + 76, ARC_DATA_OFFSET)  # data offset

    # Compressed data at 0x8000
    buf[ARC_DATA_OFFSET:ARC_DATA_OFFSET + compressed_size] = compressed

    with open(arc_path, "wb") as f:
        f.write(buf)


def validate_rom(rom):
    """Check that decompressed ROM data looks correct."""
    if rom[:4] != IBIS_MAGIC:
        raise ValueError(f"ROM missing IBIS header (got {rom[:4]!r})")
    if len(rom) != EXPECTED_ROM_SIZE:
        raise ValueError(
            f"Unexpected ROM size {len(rom):,} (expected {EXPECTED_ROM_SIZE:,})"
        )


def palette_offset(char_id, button_idx, slot):
    """Calculate the ROM offset for a specific palette.

    Args:
        char_id: Character ID (0x00-0x3A)
        button_idx: Button index (0=LP, 1=LK, 2=HP, 3=HK, 4=A1, 5=A2)
        slot: Slot within the button's 8-slot block (0-7)

    Returns:
        ROM byte offset for the start of the 32-byte palette.
    """
    base = STEAM_PALETTE_OFFSETS[char_id]
    return base + (button_idx * 8 + slot) * 32


def read_palette(rom, char_id, button_idx, slot):
    """Read a 16-color palette from the ROM.

    Returns list of 16 (R, G, B) tuples (8-bit values).
    """
    offset = palette_offset(char_id, button_idx, slot)
    colors = []
    for i in range(16):
        c16 = struct.unpack_from("<H", rom, offset + i * 2)[0]
        r = ((c16 >> 8) & 0xF) * 17
        g = ((c16 >> 4) & 0xF) * 17
        b = (c16 & 0xF) * 17
        colors.append((r, g, b))
    return colors


def write_palette(rom, char_id, button_idx, slot, colors):
    """Write a 16-color palette to the ROM.

    Args:
        rom: Mutable bytearray of ROM data
        colors: List of 16 (R, G, B) tuples (8-bit values).
                Index 0 is always written as transparent.
    """
    offset = palette_offset(char_id, button_idx, slot)
    for i, (r, g, b) in enumerate(colors):
        if i == 0:
            # Index 0 = transparent
            c16 = 0x0000
        else:
            c16 = rgb_to_argb4444(r, g, b)
        struct.pack_into("<H", rom, offset + i * 2, c16)


def write_palette_at(rom, char_id, entry_index, colors):
    """Write a 16-color palette at an arbitrary entry index.

    Entry indices 0-47 are the core button palettes (6 buttons x 8 slots).
    Entry 48+ are status effects and extras.
    """
    base = STEAM_PALETTE_OFFSETS[char_id]
    offset = base + entry_index * 32
    for i, (r, g, b) in enumerate(colors):
        if i == 0:
            c16 = 0x0000
        else:
            c16 = rgb_to_argb4444(r, g, b)
        struct.pack_into("<H", rom, offset + i * 2, c16)


def rgb_to_argb4444(r, g, b):
    """Convert 8-bit RGB to ARGB4444 little-endian uint16.

    Alpha is always 0xF (fully opaque).
    Each channel is quantized from 8-bit to 4-bit (divide by 17).
    """
    return (0xF << 12) | ((r // 17) << 8) | ((g // 17) << 4) | (b // 17)
