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
    0x09: {2},       # Iceman — slot 2 (Stance Frame)
    0x0A: {4, 5},    # Rogue — slots 4-5 (Dash Shadows)
    0x2F: {1},       # Silver Samurai — slot 1 (Shadow Frame)
    0x32: {3},       # Colossus — slot 3 (Stance Frame Main Color)
}

# Extras entries (index 56+) that use the character body sprite, keyed by
# button index. These are animation frame palettes (shine, charging, stance,
# power dive, etc.) that the game reads during specials/supers/intros.
# Format: char_id → list of (entry_index, button_index) or
#                            (entry_index, button_index, luminance)
# Parsed from PalMod's MVC2_A_DEF.h and MVC2_SUPP supplemental data.
# Button index None = shared across all buttons (use LP body palette).
# Luminance = PalMod MOD_LUM delta (e.g. 7 → +0.07 lightness in HLS).
EXTRAS_BODY_ENTRIES = {
    0x01: [  # Zangief — FAB Effect (3 per button, 3-stride; body copy approx for tint)
        *[(70 + b*3 + i, b) for b in range(6) for i in range(3)],
    ],
    0x03: [  # Morrigan — Join pose + Intro + Taunt
        # Join Morrigan (1 per button, 9-stride; body copy)
        *[(124 + b*9, b) for b in range(6)],
        *[(125 + b*9, b) for b in range(6)],   # join+white (approx body copy)
        # Intro phase-in (4 per button, 9-stride; lum 20/13/7/copy)
        *[(127 + b*9, b, 20) for b in range(6)],
        *[(128 + b*9, b, 13) for b in range(6)],
        *[(129 + b*9, b, 7) for b in range(6)],
        *[(130 + b*9, b) for b in range(6)],
        # Taunt (1 per button, 1-stride; body copy approx for sleeve mods)
        *[(176 + b, b) for b in range(6)],
    ],
    0x06: [  # Cyclops — Intro (8 per button, 8-stride; lum -3→-39) + MOB Stance (2)
        *[(88 + b*8, b, -3) for b in range(6)],
        *[(89 + b*8, b, -7) for b in range(6)],
        *[(90 + b*8, b, -11) for b in range(6)],
        *[(91 + b*8, b, -17) for b in range(6)],
        *[(92 + b*8, b, -24) for b in range(6)],
        *[(93 + b*8, b, -30) for b in range(6)],
        *[(94 + b*8, b, -33) for b in range(6)],
        *[(95 + b*8, b, -39) for b in range(6)],
        *[(136 + b*2, b) for b in range(6)],
        *[(137 + b*2, b) for b in range(6)],
    ],
    0x09: [  # Iceman — Shine Frames (7 per button, 7-stride)
        *[(i, b) for b in range(6) for i in range(80 + b*7, 87 + b*7)],
    ],
    0x0A: [  # Rogue — Dash Shadows (2 per button, 2-stride; lum -8/-12)
        *[(64 + b*2, b, -8) for b in range(6)],
        *[(65 + b*2, b, -12) for b in range(6)],
    ],
    0x0C: [  # Spider-Man — Intro (8 per button, 16-stride; lum -29→-3)
        *[(56 + b*16, b, -29) for b in range(6)],
        *[(57 + b*16, b, -23) for b in range(6)],
        *[(58 + b*16, b, -17) for b in range(6)],
        *[(59 + b*16, b, -12) for b in range(6)],
        *[(60 + b*16, b, -8) for b in range(6)],
        *[(61 + b*16, b, -7) for b in range(6)],
        *[(62 + b*16, b, -5) for b in range(6)],
        *[(63 + b*16, b, -3) for b in range(6)],
    ],
    0x0F: [  # Doctor Doom — Intro (1 per button, 28-stride)
        (56, 0), (84, 1), (112, 2), (140, 3), (168, 4), (196, 5),
    ],
    0x1C: [  # Mega Man — Intro (9; lum), Charging (9), Teleport (1), Hyper Armor (9), Mag Shockwave (1)
        *[e for b in range(6) for e in [
            (58+b*87, b, -25), (59+b*87, b, -7), (60+b*87, b, -5), (61+b*87, b),
            (62+b*87, b, 5), (63+b*87, b, 10), (64+b*87, b, 16), (65+b*87, b, 22), (66+b*87, b),
        ]],
        *[(94 + b*87 + i, b) for b in range(6) for i in range(9)],     # Charging
        *[(112 + b*87, b) for b in range(6)],                           # Teleport
        *[(114 + b*87 + i, b) for b in range(6) for i in range(9)],    # Hyper Armor
        *[(142 + b*87, b) for b in range(5)],                           # Mag Shockwave (LP-A1)
    ],
    0x1D: [  # Roll — Intro (9; lum), Charging (9), Hyper Roll (9; lum)
        *[e for b in range(6) for e in [
            (58+b*87, b, -21), (59+b*87, b, -13), (60+b*87, b, -5), (61+b*87, b),
            (62+b*87, b, 5), (63+b*87, b, 13), (64+b*87, b, 21), (65+b*87, b, 35), (66+b*87, b),
        ]],
        *[(94 + b*87 + i, b) for b in range(6) for i in range(9)],     # Charging
        *[e for b in range(6) for e in [                                # Hyper Roll
            (123+b*87, b, -25), (124+b*87, b, -7), (125+b*87, b, -5), (126+b*87, b),
            (127+b*87, b, 5), (128+b*87, b, 10), (129+b*87, b, 16), (130+b*87, b, 22), (131+b*87, b),
        ]],
    ],
    0x22: [  # Sakura — Evil Sakura (1 per button)
        (76, 0), (77, 1), (78, 2), (79, 3), (80, 4), (81, 5),
    ],
    0x24: [  # Cammy — Counterflash (9 per button, 9-stride; body copy approx for tint)
        *[(56 + b*9 + i, b) for b in range(6) for i in range(9)],
    ],
    0x25: [  # Dhalsim — Teleport (5 per button, 5-stride; lum 15/27/42/65/copy)
        *[(56 + b*5, b, 15) for b in range(6)],
        *[(57 + b*5, b, 27) for b in range(6)],
        *[(58 + b*5, b, 42) for b in range(6)],
        *[(59 + b*5, b, 65) for b in range(6)],
        *[(60 + b*5, b) for b in range(6)],
    ],
    0x28: [  # Gambit — Royal Flush/Cajun Explosion (5 per button, 5-stride; lum 10/5/0/-5/-10)
        *[(56 + b*5, b, 10) for b in range(6)],
        *[(57 + b*5, b, 5) for b in range(6)],
        *[(58 + b*5, b) for b in range(6)],
        *[(59 + b*5, b, -5) for b in range(6)],
        *[(60 + b*5, b, -10) for b in range(6)],
    ],
    0x29: [  # Juggernaut — Headcrush (2, copy) + Power-Up (8, 10-stride; lum 6→4→copy)
        *[(56 + b*10, b) for b in range(6)],
        *[(57 + b*10, b) for b in range(6)],
        *[(58 + b*10, b, 6) for b in range(6)],
        *[(59 + b*10, b, 12) for b in range(6)],
        *[(60 + b*10, b, 15) for b in range(6)],
        *[(61 + b*10, b, 18) for b in range(6)],
        *[(62 + b*10, b, 12) for b in range(6)],
        *[(63 + b*10, b, 7) for b in range(6)],
        *[(64 + b*10, b, 4) for b in range(6)],
        *[(65 + b*10, b) for b in range(6)],
    ],
    0x2A: [  # Storm — Lightning Effect (3 per button, 3-stride; copy/lum+7/lum+17)
        # Shared super/lightning ball entries (identical to per-button lum variants)
        (56, None),       # Lightning Storm Frame 1 (body copy, shared)
        (66, None, 17),   # Lightning Ball/Super Frame 1 (lum+17, shared)
        (69, None, 7),    # Lightning Ball/Super Frame 4 (lum+7, shared)
        # Per-button lightning effect entries
        *[(72 + b*3, b) for b in range(6)],
        *[(73 + b*3, b, 7) for b in range(6)],
        *[(74 + b*3, b, 17) for b in range(6)],
    ],
    0x2D: [  # Shuma-Gorath — body-derived extras (48-stride per button)
        # Stance after FP: 5 frames per button (SUPP copies colors 2-8)
        *[(64 + i + b*48, b) for b in range(6) for i in range(5)],
        # Stone Drop (d+HK): frame 1 copies colors 9-11; frames 2-4 full
        # body copy + lum 5 (SUPP also applies saturation we can't replicate);
        # frame 5 is a straight body copy
        *[(69 + b*48, b) for b in range(6)],
        *[(70 + i + b*48, b, 5) for b in range(6) for i in range(3)],
        *[(73 + b*48, b) for b in range(6)],
        # Unknown 1-4 / HP Flash / Winpose / Unknown 5 (SUPP copies colors 2-8)
        *[(94 + i + b*48, b) for b in range(6) for i in range(7)],
        # Chaos Dimension: 5 paired body+dash frames (SUPP copies from body
        # with sat/lum mods; we write plain body as approximation)
        *[(101 + i + b*48, b) for b in range(6) for i in range(10)],
    ],
    0x2F: [  # Silver Samurai — Super Armor shine/stance (7 per button, 8-stride; last frame lum -5)
        *[e for b in range(6) for e in [
            *[(56 + b*8 + i, b) for i in range(6)],
            (62 + b*8, b, -5),
        ]],
    ],
    0x30: [  # Omega Red — Intro (4 per button, 4-stride; lum -25/-12/-9/-5)
        *[(56 + b*4, b, -25) for b in range(6)],
        *[(57 + b*4, b, -12) for b in range(6)],
        *[(58 + b*4, b, -9) for b in range(6)],
        *[(59 + b*4, b, -5) for b in range(6)],
    ],
    0x31: [  # Spiral — Power-Up Enhance (6 per button, 28-stride)
        *[(92 + b*28 + i, b) for b in range(6) for i in range(6)],
        # Speed-Up Enhance (6 per button, 28-stride; lum 0/5/10/13/23/40)
        *[(98 + b*28, b) for b in range(6)],
        *[(99 + b*28, b, 5) for b in range(6)],
        *[(100 + b*28, b, 10) for b in range(6)],
        *[(101 + b*28, b, 13) for b in range(6)],
        *[(102 + b*28, b, 23) for b in range(6)],
        *[(103 + b*28, b, 40) for b in range(6)],
    ],
    0x32: [  # Colossus — Shine/Stance/Power Dive (32 per button; Power Dive has lum)
        *[
            (56 + b*32 + i, b, lum) if lum else (56 + b*32 + i, b)
            for b in range(6) for i in range(32)
            for lum in [{15: 31, 16: 45, 18: -18, 19: -13, 20: -6, 22: -5, 23: 25}.get(i)]
        ],
    ],
    0x35: [  # Blackheart — Intro (shared, 1 entry)
        (74, None),
    ],
    0x37: [  # Jin — Special Armor (6) + Power-Up Flash (6, 6-stride; lum 32/25/16/10)
        *[(56 + b*6 + i, b) for b in range(6) for i in range(6)],
        *[e for b in range(6) for e in [
            (92 + b*6, b), (93 + b*6, b, 32), (94 + b*6, b, 25),
            (95 + b*6, b, 16), (96 + b*6, b, 10), (97 + b*6, b),
        ]],
    ],
    0x38: [  # Captain Commando — Laser Intro (4 shared) + Intro (1 per button)
        *[(i, None) for i in range(56, 60)],
        (61, 0), (63, 1), (65, 2), (67, 3), (69, 4), (71, 5),
    ],
    0x3A: [  # Servbot — King Kobun (4 per button, 5-stride; lum 0/23/32/40)
        *[(56 + b*5, b) for b in range(6)],
        *[(57 + b*5, b, 23) for b in range(6)],
        *[(58 + b*5, b, 32) for b in range(6)],
        *[(59 + b*5, b, 40) for b in range(6)],
    ],
}


# Extras entries derived from specific palette slots (not body slot 0).
# Shared entries (btn_idx=None) use LP (button 0) as source.
# Format: char_id → list of (entry_index, button_idx_or_None,
#                             [(start_color, end_color, source_slot), ...])
# Each segment copies colors[start:end] from the given source slot.
EXTRAS_SLOT_ENTRIES = {
    0x34: [  # Sentinel — shared flame/fly effect palettes
        (65, None, [(0, 16, 3)]),  # Launcher/RP flames → LP rockets palette
        (66, None, [(0, 16, 3)]),  # Flying effects → LP rockets palette
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
