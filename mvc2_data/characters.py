"""MvC2 character ID/name mappings and constants."""

# Full MvC2 roster — CPS2 unit IDs map 1:1 to character IDs
CHARACTERS = {
    0x00: "Ryu", 0x01: "Zangief", 0x02: "Guile", 0x03: "Morrigan",
    0x04: "Anakaris", 0x05: "Strider", 0x06: "Cyclops", 0x07: "Wolverine",
    0x08: "Psylocke", 0x09: "Iceman", 0x0A: "Rogue", 0x0B: "Captain America",
    0x0C: "Spider-Man", 0x0D: "Hulk", 0x0E: "Venom", 0x0F: "Doctor Doom",
    0x10: "Tron Bonne", 0x11: "Jill", 0x12: "Hayato", 0x13: "Ruby Heart",
    0x14: "SonSon", 0x15: "Amingo", 0x16: "Marrow", 0x17: "Cable",
    0x18: "Abyss (Form 1)", 0x19: "Abyss (Form 2)", 0x1A: "Abyss (Form 3)",
    0x1B: "Chun-Li", 0x1C: "Mega Man", 0x1D: "Roll", 0x1E: "Akuma",
    0x1F: "BB Hood", 0x20: "Felicia", 0x21: "Charlie Nash", 0x22: "Sakura",
    0x23: "Dan", 0x24: "Cammy", 0x25: "Dhalsim", 0x26: "M. Bison",
    0x27: "Ken", 0x28: "Gambit", 0x29: "Juggernaut", 0x2A: "Storm",
    0x2B: "Sabretooth", 0x2C: "Magneto", 0x2D: "Shuma-Gorath",
    0x2E: "War Machine", 0x2F: "Silver Samurai", 0x30: "Omega Red",
    0x31: "Spiral", 0x32: "Colossus", 0x33: "Iron Man", 0x34: "Sentinel",
    0x35: "Blackheart", 0x36: "Thanos", 0x37: "Jin",
    0x38: "Captain Commando", 0x39: "Wolverine (Bone Claw)", 0x3A: "Servbot",
}

BUTTON_NAMES = ["LP", "LK", "HP", "HK", "A1", "A2"]

# Non-boss playable characters (excludes Abyss forms)
PLAYABLE_CHARS = [c for c in sorted(CHARACTERS.keys()) if c not in (0x18, 0x19, 0x1A)]

# Number of palette rows used in the composite sprite.
# Most characters use 1 row (body only). Characters with accessories
# (shields, projectiles, animals, etc.) use additional rows.
PALETTE_ROWS = {
    0x05: 3,   # Strider — Ouroboros orbs, tiger, eagle
    0x06: 2,   # Cyclops — optic blast
    0x07: 2,   # Wolverine — claws/effects
    0x0B: 2,   # Captain America — shield
    0x0C: 2,   # Spider-Man — web
    0x0E: 2,   # Venom — symbiote effects
    0x10: 4,   # Tron Bonne — Servbot helpers
    0x11: 2,   # Jill — zombie/effects
    0x12: 3,   # Hayato — plasma sword/motorcycle
    0x14: 7,   # SonSon — staff/monkey
    0x17: 2,   # Cable — viper beam
    0x1B: 2,   # Chun-Li — ki effects
    0x2B: 3,   # Sabretooth — effects
    0x2D: 2,   # Shuma-Gorath — eye/tentacles
    0x34: 3,   # Sentinel — drones/rockets
    0x39: 2,   # Wolverine (Bone Claw) — claws/effects
    0x3A: 7,   # Servbot — servbot army
}

# Which PalMod slots (within each button's 8-slot block) map to which pixel row.
# Index in list = pixel row in composite sprite.
# Value = slot offset within the button's 8-slot block.
PALETTE_SLOT_MAP = {
    0x17: [0, 3],           # Cable — main/04
    0x0B: [0, 1],           # Captain America — main/02
    0x1B: [0, 1],           # Chun-Li — main/02
    0x06: [0, 1],           # Cyclops — main/02
    0x12: [0, 1, 4],        # Hayato — main/02/05
    0x11: [0, 1],           # Jill — main/02
    0x2B: [0, 1, 2],        # Sabretooth — main/02/03
    0x34: [0, 1, 3],        # Sentinel — main/02/04
    0x3A: [0, 1, 2, 3, 4, 5, 6],  # Servbot — main/02-07
    0x2D: [0, 1],           # Shuma-Gorath — main/02
    0x14: [0, 1, 2, 3, 4, 5, 6],  # SonSon — main/02-07
    0x0C: [0, 2],           # Spider-Man — main/03
    0x05: [0, 1, 2],        # Strider — main/02/03
    0x10: [0, 1, 3, 4],     # Tron Bonne — main/02/04/05
    0x0E: [0, 1],           # Venom — main/02
    0x07: [0, 1],           # Wolverine — main/02
    0x39: [0, 1],           # Wolverine (Bone Claw) — main/02
}


# Additional per-button slots (0-7) that use the body sprite but aren't in
# PALETTE_SLOT_MAP. Derived from PalMod's indexCPS2Sprites with sprite index 0.
# Only characters with non-standard body slots beyond {0, 6, 7} are listed.
EXTRA_BODY_BUTTON_SLOTS = {
    0x2F: {1},    # Silver Samurai — slot 1 (Shadow Frame)
    0x32: {3},    # Colossus — slot 3 (Stance Frame Main Color)
}

# Extras entries (index 56+) that use the character body sprite, keyed by
# button index. These are animation frame palettes (shine, charging, stance,
# power dive, etc.) that the game reads during specials/supers.
# Format: char_id → list of (entry_index, button_index)
# Parsed from PalMod's MVC2_A_DEF.h — only entries with sprite index 0
# referencing the character's own sprite sheet are included.
_B = {"LP": 0, "LK": 1, "HP": 2, "HK": 3, "A1": 4, "A2": 5}
EXTRAS_BODY_ENTRIES = {
    0x09: [  # Iceman — Shine Frames (7 per button)
        *[(i, b) for b in range(6) for i in range(80 + b*7, 87 + b*7)],
    ],
    0x1C: [  # Mega Man — Intro (9), Charging (9), Magnetic Shockwave (1) per button
        *[(i, 0) for i in [58,59,60,61,62,63,64,65,66, 94,95,96,97,98,99,100,101,102, 142]],
        *[(i, 1) for i in [145,146,147,148,149,150,151,152,153, 181,182,183,184,185,186,187,188,189, 229]],
        *[(i, 2) for i in [232,233,234,235,236,237,238,239,240, 268,269,270,271,272,273,274,275,276, 316]],
        *[(i, 3) for i in [319,320,321,322,323,324,325,326,327, 355,356,357,358,359,360,361,362,363, 403]],
        *[(i, 4) for i in [406,407,408,409,410,411,412,413,414, 442,443,444,445,446,447,448,449,450, 490]],
        *[(i, 5) for i in [493,494,495,496,497,498,499,500,501, 529,530,531,532,533,534,535,536,537]],
    ],
    0x1D: [  # Roll — Intro (9), Charging (9), Hyper Roll (9) per button
        *[(i, 0) for i in [58,59,60,61,62,63,64,65,66, 94,95,96,97,98,99,100,101,102, 123,124,125,126,127,128,129,130,131]],
        *[(i, 1) for i in [145,146,147,148,149,150,151,152,153, 181,182,183,184,185,186,187,188,189, 210,211,212,213,214,215,216,217,218]],
        *[(i, 2) for i in [232,233,234,235,236,237,238,239,240, 268,269,270,271,272,273,274,275,276, 297,298,299,300,301,302,303,304,305]],
        *[(i, 3) for i in [319,320,321,322,323,324,325,326,327, 355,356,357,358,359,360,361,362,363, 384,385,386,387,388,389,390,391,392]],
        *[(i, 4) for i in [406,407,408,409,410,411,412,413,414, 442,443,444,445,446,447,448,449,450, 471,472,473,474,475,476,477,478,479]],
        *[(i, 5) for i in [493,494,495,496,497,498,499,500,501, 529,530,531,532,533,534,535,536,537, 558,559,560,561,562,563,564,565,566]],
    ],
    0x22: [  # Sakura — Evil Sakura (1 per button)
        (76, 0), (77, 1), (78, 2), (79, 3), (80, 4), (81, 5),
    ],
    0x28: [  # Gambit — Royal Flush/Cajun Explosion shine (5 per button)
        *[(i, b) for b in range(6) for i in range(56 + b*5, 61 + b*5)],
    ],
    0x29: [  # Juggernaut — Juggernaut Punch shine (2 per button)
        *[(56 + b*10, b) for b in range(6)],
        *[(57 + b*10, b) for b in range(6)],
    ],
    0x2F: [  # Silver Samurai — Super Armor shine/stance (7 per button, gaps)
        *[(i, 0) for i in [56,57,58,59,60,61,62]],
        *[(i, 1) for i in [64,65,66,67,68,69,70]],
        *[(i, 2) for i in [72,73,74,75,76,77,78]],
        *[(i, 3) for i in [80,81,82,83,84,85,86]],
        *[(i, 4) for i in [88,89,90,91,92,93,94]],
        *[(i, 5) for i in [96,97,98,99,100,101,102]],
    ],
    0x32: [  # Colossus — Shine/Stance/Power Dive frames (32 per button)
        *[(i, b) for b in range(6) for i in range(56 + b*32, 88 + b*32)],
    ],
    0x37: [  # Jin — Special Armor (6 per button)
        *[(i, b) for b in range(6) for i in range(56 + b*6, 62 + b*6)],
    ],
}
del _B


def palette_slot_map(char_id):
    """Get the palette slot mapping for a character."""
    if char_id in PALETTE_SLOT_MAP:
        return PALETTE_SLOT_MAP[char_id]
    return list(range(palette_rows(char_id)))


def palette_rows(char_id):
    """Get the number of palette rows for a character (1 = body only)."""
    return PALETTE_ROWS.get(char_id, 1)


def safe_name(name):
    """Convert character name to filesystem-safe format.

    Preserves hyphens (Chun-Li, Spider-Man, Shuma-Gorath).
    """
    return (name.replace(" ", "_").replace(".", "")
            .replace("(", "").replace(")", ""))
