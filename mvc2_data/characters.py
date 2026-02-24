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
# power dive, etc.) that the game reads during specials/supers/intros.
# Format: char_id → list of (entry_index, button_index)
# Parsed from PalMod's MVC2_A_DEF.h — includes entries at sprite index 0
# (shine, armor, stance) and non-zero sprite indices for intro/stance animations.
# Button index None = shared across all buttons (use LP body palette).
EXTRAS_BODY_ENTRIES = {
    0x03: [  # Morrigan — Intro (4 per button, sprite 13)
        *[(i, 0) for i in range(127, 131)],
        *[(i, 1) for i in range(136, 140)],
        *[(i, 2) for i in range(145, 149)],
        *[(i, 3) for i in range(154, 158)],
        *[(i, 4) for i in range(163, 167)],
        *[(i, 5) for i in range(172, 176)],
    ],
    0x06: [  # Cyclops — Intro (8 per button, sprite 12) + Mega Optic Blast Stance (2 per button, sprite 13)
        *[(i, b) for b in range(6) for i in range(88 + b*8, 96 + b*8)],
        *[(136 + b*2, b) for b in range(6)],
        *[(137 + b*2, b) for b in range(6)],
    ],
    0x09: [  # Iceman — Shine Frames (7 per button)
        *[(i, b) for b in range(6) for i in range(80 + b*7, 87 + b*7)],
    ],
    0x0C: [  # Spider-Man — Intro (8 per button, sprite 11, 16-stride)
        *[(i, 0) for i in range(56, 64)],
        *[(i, 1) for i in range(72, 80)],
        *[(i, 2) for i in range(88, 96)],
        *[(i, 3) for i in range(104, 112)],
        *[(i, 4) for i in range(120, 128)],
        *[(i, 5) for i in range(136, 144)],
    ],
    0x0F: [  # Doctor Doom — Intro (1 per button, sprite 11, 28-stride)
        (56, 0), (84, 1), (112, 2), (140, 3), (168, 4), (196, 5),
    ],
    0x1C: [  # Mega Man — Intro (9), Charging (9), Teleport (1), Hyper Armor (9), Mag Shockwave (1) per btn
        *[(i, 0) for i in [*range(58, 67), *range(94, 103), 112, *range(114, 123), 142]],
        *[(i, 1) for i in [*range(145, 154), *range(181, 190), 199, *range(201, 210), 229]],
        *[(i, 2) for i in [*range(232, 241), *range(268, 277), 286, *range(288, 297), 316]],
        *[(i, 3) for i in [*range(319, 328), *range(355, 364), 373, *range(375, 384), 403]],
        *[(i, 4) for i in [*range(406, 415), *range(442, 451), 460, *range(462, 471), 490]],
        *[(i, 5) for i in [*range(493, 502), *range(529, 538), 547, *range(549, 558)]],
    ],
    0x1D: [  # Roll — Intro (9), Charging (9), Hyper Roll (9) per button
        *[(i, 0) for i in [*range(58, 67), *range(94, 103), *range(123, 132)]],
        *[(i, 1) for i in [*range(145, 154), *range(181, 190), *range(210, 219)]],
        *[(i, 2) for i in [*range(232, 241), *range(268, 277), *range(297, 306)]],
        *[(i, 3) for i in [*range(319, 328), *range(355, 364), *range(384, 393)]],
        *[(i, 4) for i in [*range(406, 415), *range(442, 451), *range(471, 480)]],
        *[(i, 5) for i in [*range(493, 502), *range(529, 538), *range(558, 567)]],
    ],
    0x22: [  # Sakura — Evil Sakura (1 per button)
        (76, 0), (77, 1), (78, 2), (79, 3), (80, 4), (81, 5),
    ],
    0x25: [  # Dhalsim — Teleport (5 per button, sprite 11)
        *[(i, b) for b in range(6) for i in range(56 + b*5, 61 + b*5)],
    ],
    0x28: [  # Gambit — Royal Flush/Cajun Explosion shine (5 per button)
        *[(i, b) for b in range(6) for i in range(56 + b*5, 61 + b*5)],
    ],
    0x29: [  # Juggernaut — Headcrush (2) + Power-Up (8) per button, 10-stride
        *[(i, b) for b in range(6) for i in range(56 + b*10, 66 + b*10)],
    ],
    0x2D: [  # Shuma-Gorath — Stance (1 per button, 48-stride)
        (90, 0), (138, 1), (186, 2), (234, 3), (282, 4), (330, 5),
    ],
    0x2F: [  # Silver Samurai — Super Armor shine/stance (7 per button, gaps)
        *[(i, 0) for i in range(56, 63)],
        *[(i, 1) for i in range(64, 71)],
        *[(i, 2) for i in range(72, 79)],
        *[(i, 3) for i in range(80, 87)],
        *[(i, 4) for i in range(88, 95)],
        *[(i, 5) for i in range(96, 103)],
    ],
    0x30: [  # Omega Red — Intro (4 per button, sprite 11; LP has 8 entries)
        *[(i, 0) for i in [*range(56, 60), *range(68, 72)]],
        *[(i, 1) for i in range(60, 64)],
        *[(i, 2) for i in range(64, 68)],
        *[(i, 4) for i in range(72, 76)],
        *[(i, 5) for i in range(76, 80)],
    ],
    0x31: [  # Spiral — Power-Up Enhance (6 per button, sprite 11, 28-stride)
        *[(i, 0) for i in range(92, 98)],
        *[(i, 1) for i in range(120, 126)],
        *[(i, 2) for i in range(148, 154)],
        *[(i, 3) for i in range(176, 182)],
        *[(i, 4) for i in range(204, 210)],
        *[(i, 5) for i in range(232, 238)],
    ],
    0x32: [  # Colossus — Shine/Stance/Power Dive frames (32 per button)
        *[(i, b) for b in range(6) for i in range(56 + b*32, 88 + b*32)],
    ],
    0x35: [  # Blackheart — Intro (shared, 1 entry)
        (74, None),
    ],
    0x37: [  # Jin — Special Armor (6) + Power-Up Flash (6) per button
        *[(i, b) for b in range(6) for i in range(56 + b*6, 62 + b*6)],
        *[(i, b) for b in range(6) for i in range(92 + b*6, 98 + b*6)],
    ],
    0x38: [  # Captain Commando — Laser Intro (4 shared) + Intro (1 per button)
        *[(i, None) for i in range(56, 60)],
        (61, 0), (63, 1), (65, 2), (67, 3), (69, 4), (71, 5),
    ],
}


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
