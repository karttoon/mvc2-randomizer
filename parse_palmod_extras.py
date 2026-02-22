"""
Parse MVC2_A_DEF.h from PalMod source to build mappings of:
1. Which "extras" palette entries use the character body sprite (sprite index 0)
2. Which per-button slots (beyond slot 0) also use sprite index 0
3. Button association for button-specific extras entries

Output: Python dicts suitable for copy-pasting into source code.
"""

import re
import sys
from collections import defaultdict

HEADER_PATH = r"D:\Storage\MvC2Modding\PalMod_src\palmod\Game\MVC2_A_DEF.h"

# Character ID mapping (from CPS2 unit IDs)
CHAR_IDS = {
    "RYU": 0x00,
    "ZANGIEF": 0x01,
    "GUILE": 0x02,
    "MORRIGAN": 0x03,
    "ANAKARIS": 0x04,
    "STRIDER": 0x05,
    "CYCLOPS": 0x06,
    "WOLVERINE": 0x07,
    "PSYLOCKE": 0x08,
    "ICEMAN": 0x09,
    "ROGUE": 0x0A,
    "CAPTAINAMERICA": 0x0B,
    "SPIDERMAN": 0x0C,
    "HULK": 0x0D,
    "VENOM": 0x0E,
    "DRDOOM": 0x0F,
    "TRON": 0x10,
    "JILL": 0x11,
    "HAYATO": 0x12,
    "RUBY": 0x13,
    "SONSON": 0x14,
    "AMINGO": 0x15,
    "MARROW": 0x16,
    "CABLE": 0x17,
    # 0x18-0x1A are Abyss forms (skip)
    "CHUNLI": 0x1B,
    "MEGAMAN": 0x1C,
    "ROLL": 0x1D,
    "GOUKI": 0x1E,  # Akuma
    "BBHOOD": 0x1F,
    "FELICIA": 0x20,
    "CHARLIE": 0x21,  # Charlie Nash
    "SAKURA": 0x22,
    "DAN": 0x23,
    "CAMMY": 0x24,
    "DHALSIM": 0x25,
    "MBISON": 0x26,
    "KEN": 0x27,
    "GAMBIT": 0x28,
    "JUGGERNAUT": 0x29,
    "STORM": 0x2A,
    "SABRETOOTH": 0x2B,
    "MAGNETO": 0x2C,
    "SHUMA": 0x2D,
    "WARMACHINE": 0x2E,
    "SILVERSAMURAI": 0x2F,
    "OMEGARED": 0x30,
    "SPIRAL": 0x31,
    "COLOSSUS": 0x32,
    "IRONMAN": 0x33,
    "SENTINEL": 0x34,
    "BLACKHEART": 0x35,
    "THANOS": 0x36,
    "JIN": 0x37,
    "CAPTAINCOMMANDO": 0x38,
    "BONERINE": 0x39,  # Wolverine (Bone Claw)
    "KOBUN": 0x3A,  # Servbot
}

# Friendly names for output
CHAR_NAMES = {
    0x00: "Ryu",
    0x01: "Zangief",
    0x02: "Guile",
    0x03: "Morrigan",
    0x04: "Anakaris",
    0x05: "Strider",
    0x06: "Cyclops",
    0x07: "Wolverine",
    0x08: "Psylocke",
    0x09: "Iceman",
    0x0A: "Rogue",
    0x0B: "Captain America",
    0x0C: "Spider-Man",
    0x0D: "Hulk",
    0x0E: "Venom",
    0x0F: "Doctor Doom",
    0x10: "Tron Bonne",
    0x11: "Jill",
    0x12: "Hayato",
    0x13: "Ruby Heart",
    0x14: "SonSon",
    0x15: "Amingo",
    0x16: "Marrow",
    0x17: "Cable",
    0x1B: "Chun-Li",
    0x1C: "Mega Man",
    0x1D: "Roll",
    0x1E: "Akuma",
    0x1F: "BB Hood",
    0x20: "Felicia",
    0x21: "Charlie Nash",
    0x22: "Sakura",
    0x23: "Dan",
    0x24: "Cammy",
    0x25: "Dhalsim",
    0x26: "M. Bison",
    0x27: "Ken",
    0x28: "Gambit",
    0x29: "Juggernaut",
    0x2A: "Storm",
    0x2B: "Sabretooth",
    0x2C: "Magneto",
    0x2D: "Shuma-Gorath",
    0x2E: "War Machine",
    0x2F: "Silver Samurai",
    0x30: "Omega Red",
    0x31: "Spiral",
    0x32: "Colossus",
    0x33: "Iron Man",
    0x34: "Sentinel",
    0x35: "Blackheart",
    0x36: "Thanos",
    0x37: "Jin",
    0x38: "Captain Commando",
    0x39: "Wolverine (Bone Claw)",
    0x3A: "Servbot",
}

BUTTONS = ["LP", "LK", "HP", "HK", "A1", "A2"]

# Mapping from character key (as used in array names) to the expected
# indexCPS2Sprites_XXX reference for that character's OWN sprite.
# This is needed to distinguish "body sprite index 0 for THIS character"
# from "sprite index 0 for ANOTHER character" (e.g., Spiral's Metamorphosis).
CHAR_SPRITE_REFS = {
    "RYU": "indexCPS2Sprites_Ryu",
    "ZANGIEF": "indexCPS2Sprites_Zangief",
    "GUILE": "indexCPS2Sprites_Guile",
    "MORRIGAN": "indexCPS2Sprites_Morrigan",
    "ANAKARIS": "indexCPS2Sprites_Anakaris",
    "STRIDER": "indexCPS2Sprites_Strider",
    "CYCLOPS": "indexCPS2Sprites_Cyclops",
    "WOLVERINE": "indexCPS2Sprites_Wolverine",
    "PSYLOCKE": "indexCPS2Sprites_Psylocke",
    "ICEMAN": "indexCPS2Sprites_Iceman",
    "ROGUE": "indexCPS2Sprites_Rogue",
    "CAPTAINAMERICA": "indexCPS2Sprites_CapAm",
    "SPIDERMAN": "indexCPS2Sprites_Spidey",
    "HULK": "indexCPS2Sprites_Hulk",
    "VENOM": "indexCPS2Sprites_Venom",
    "DRDOOM": "indexCPS2Sprites_DrDoom",
    "TRON": "indexCPS2Sprites_Tron",
    "JILL": "indexCPS2Sprites_Jill",
    "HAYATO": "indexCPS2Sprites_Hayato",
    "RUBY": "indexCPS2Sprites_Ruby",
    "SONSON": "indexCPS2Sprites_SonSon",
    "AMINGO": "indexCPS2Sprites_Amingo",
    "MARROW": "indexCPS2Sprites_Marrow",
    "CABLE": "indexCPS2Sprites_Cable",
    "CHUNLI": "indexCPS2Sprites_ChunLi",
    "MEGAMAN": "indexCPS2Sprites_Megaman",
    "ROLL": "indexCPS2Sprites_Roll",
    "GOUKI": "indexCPS2Sprites_Akuma",
    "BBHOOD": "indexCPS2Sprites_BBHood",
    "FELICIA": "indexCPS2Sprites_Felicia",
    "CHARLIE": "indexCPS2Sprites_Charlie",
    "SAKURA": "indexCPS2Sprites_Sakura",
    "DAN": "indexCPS2Sprites_Dan",
    "CAMMY": "indexCPS2Sprites_Cammy",
    "DHALSIM": "indexCPS2Sprites_Dhalsim",
    "MBISON": "indexCPS2Sprites_Bison",
    "KEN": "indexCPS2Sprites_Ken",
    "GAMBIT": "indexCPS2Sprites_Gambit",
    "JUGGERNAUT": "indexCPS2Sprites_Juggy",
    "STORM": "indexCPS2Sprites_Storm",
    "SABRETOOTH": "indexCPS2Sprites_Sabretooth",
    "MAGNETO": "indexCPS2Sprites_Magneto",
    "SHUMA": "indexCPS2Sprites_Shuma",
    "WARMACHINE": "indexCPS2Sprites_WarMachine",
    "SILVERSAMURAI": "indexCPS2Sprites_SilverSamurai",
    "OMEGARED": "indexCPS2Sprites_OmegaRed",
    "SPIRAL": "indexCPS2Sprites_Spiral",
    "COLOSSUS": "indexCPS2Sprites_Colossus",
    "IRONMAN": "indexCPS2Sprites_IronMan",
    "SENTINEL": "indexCPS2Sprites_Sentinel",
    "BLACKHEART": "indexCPS2Sprites_Blackheart",
    "THANOS": "indexCPS2Sprites_Thanos",
    "JIN": "indexCPS2Sprites_Jin",
    "CAPTAINCOMMANDO": "indexCPS2Sprites_CapCom",
    "BONERINE": "indexCPS2Sprites_Bonerine",
    "KOBUN": "indexCPS2Sprites_Kobun",
}


def parse_palette_entry(line):
    """Parse a single palette entry line.

    Returns dict with: name, has_sprite, sprite_index, sprite_ref

    Formats:
      { L"name", 0xstart, 0xend }                                    -- 3 fields, no sprite
      { L"name", 0xstart, 0xend, indexCPS2Sprites_XXX, N }           -- 5 fields
      { L"name", 0xstart, 0xend, indexCPS2Sprites_XXX, N, &pairXXX } -- 6 fields
    """
    # Extract the content between { and }
    match = re.search(r'\{\s*L"([^"]*)"(.+?)\}', line)
    if not match:
        return None

    name = match.group(1)
    rest = match.group(2)

    # Split the rest by commas and strip whitespace
    parts = [p.strip() for p in rest.split(',') if p.strip()]

    # parts[0] and parts[1] are start/end offsets
    # If there are sprite references, parts[2] is indexCPS2Sprites_XXX, parts[3] is sprite_index
    # parts[4] if exists is &pairXXX

    result = {
        'name': name,
        'has_sprite': False,
        'sprite_index': None,
        'sprite_ref': None,
    }

    if len(parts) >= 4:
        sprite_ref = parts[2]
        # The sprite index - might have &pairXXX after it, handle that
        sprite_idx_str = parts[3]
        # Remove any trailing &pairXXX reference
        sprite_idx_str = sprite_idx_str.split('&')[0].strip()
        # Handle potential trailing }, etc.
        sprite_idx_str = sprite_idx_str.rstrip(' },')

        if sprite_ref.startswith('index'):
            result['has_sprite'] = True
            result['sprite_ref'] = sprite_ref
            try:
                result['sprite_index'] = int(sprite_idx_str)
            except ValueError:
                result['sprite_index'] = None

    return result


def extract_button_from_name(name):
    """Extract button label from an extras entry name.

    Examples:
      "21: LP - Shine Frame 1" -> "LP"
      "11: LP - Mecha Gief" -> "LP"
      "09: Icebeam Frame 1 (all buttons)" -> None (shared across all)
      "4b: LP button - Lilith Same as 01 LP (Extra - 02)" -> "LP"
      "50: LP - Intro Frame 1 LP color..." -> "LP"
    """
    # Strip the hex prefix like "09: " or "0a: "
    stripped = re.sub(r'^[0-9a-fA-F]+:\s*', '', name)

    # Check for "XX - " or "XX button" patterns at the start
    for btn in BUTTONS:
        # "LP - ...", "LP button - ..."
        if re.match(rf'^{btn}\s*[-\s]', stripped) or re.match(rf'^{btn}\s+button', stripped):
            return btn

    # Check for "XX Color" pattern in the name (like "LP Color" or "LK Color")
    for btn in BUTTONS:
        if f'{btn} Color' in name or f'{btn} color' in name:
            return btn

    return None


def parse_header(filepath):
    """Parse the entire MVC2_A_DEF.h file."""

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    lines = content.split('\n')

    # 1. Find all PALETTES_EXTRAS arrays and their entries
    # 2. Find all button PALETTES arrays (LP, LK, HP, HK, A1, A2) and their entries

    # Pattern for array declaration
    extras_pattern = re.compile(
        r'const\s+sGame_PaletteDataset\s+MVC2_A_(\w+)_PALETTES_EXTRAS\s*\[\]\s*='
    )
    button_pattern = re.compile(
        r'const\s+sGame_PaletteDataset\s+MVC2_A_(\w+)_PALETTES_(LP|LK|HP|HK|A1|A2)\s*\[\]\s*='
    )

    # Parse all arrays
    extras_arrays = {}  # char_key -> list of parsed entries
    button_arrays = {}  # (char_key, button) -> list of parsed entries

    i = 0
    while i < len(lines):
        line = lines[i]

        # Check for EXTRAS array
        em = extras_pattern.search(line)
        if em:
            char_key = em.group(1)
            entries = []
            i += 1  # skip to {
            while i < len(lines) and '{' not in lines[i]:
                i += 1
            i += 1  # skip opening {
            while i < len(lines):
                if '};' in lines[i]:
                    break
                entry = parse_palette_entry(lines[i])
                if entry:
                    entries.append(entry)
                i += 1
            extras_arrays[char_key] = entries
            continue

        # Check for button array
        bm = button_pattern.search(line)
        if bm:
            char_key = bm.group(1)
            button = bm.group(2)
            entries = []
            i += 1
            while i < len(lines) and '{' not in lines[i]:
                i += 1
            i += 1  # skip opening {
            while i < len(lines):
                if '};' in lines[i]:
                    break
                entry = parse_palette_entry(lines[i])
                if entry:
                    entries.append(entry)
                i += 1
            button_arrays[(char_key, button)] = entries
            continue

        i += 1

    return extras_arrays, button_arrays


def main():
    extras_arrays, button_arrays = parse_header(HEADER_PATH)

    # =========================================================================
    # PART 1: Extras entries that use sprite index 0 (body sprite)
    # =========================================================================
    print("=" * 80)
    print("PART 1: Extras entries with sprite index 0 (body sprite)")
    print("=" * 80)
    print()

    # Build the dict: char_id -> list of (extras_index_56+N, button_or_None, name)
    extras_body_sprite = {}
    # Also track entries that reference OTHER characters' sprite 0 (e.g. Spiral Metamorphosis)
    extras_other_char_sprite0 = {}

    for char_key, entries in sorted(extras_arrays.items()):
        if char_key not in CHAR_IDS:
            # Skip Abyss forms and other non-playable
            continue

        char_id = CHAR_IDS[char_key]
        own_sprite_ref = CHAR_SPRITE_REFS.get(char_key)
        body_entries = []
        other_entries = []

        for n, entry in enumerate(entries):
            slot_index = 56 + n
            if entry['has_sprite'] and entry['sprite_index'] == 0:
                # Check if this references the character's OWN sprite
                if own_sprite_ref and entry['sprite_ref'] == own_sprite_ref:
                    button = extract_button_from_name(entry['name'])
                    body_entries.append({
                        'slot': slot_index,
                        'button': button,
                        'name': entry['name'],
                    })
                else:
                    other_entries.append({
                        'slot': slot_index,
                        'sprite_ref': entry['sprite_ref'],
                        'name': entry['name'],
                    })

        if body_entries:
            extras_body_sprite[char_id] = body_entries
        if other_entries:
            extras_other_char_sprite0[char_id] = other_entries

    # Print detailed info
    for char_id in sorted(extras_body_sprite.keys()):
        entries = extras_body_sprite[char_id]
        name = CHAR_NAMES.get(char_id, f"Unknown 0x{char_id:02X}")
        print(f"  0x{char_id:02X} {name}: {len(entries)} body-sprite extras")
        for e in entries:
            btn_str = f" [{e['button']}]" if e['button'] else ""
            print(f"    slot {e['slot']}: {e['name']}{btn_str}")
        print()

    # Report entries that reference OTHER characters' sprite at index 0
    if extras_other_char_sprite0:
        print()
        print("  NOTE: Entries with sprite index 0 referencing OTHER characters' sprites:")
        print("  (These are NOT body sprite entries - they are transformation/metamorphosis palettes)")
        for char_id in sorted(extras_other_char_sprite0.keys()):
            name = CHAR_NAMES.get(char_id, f"0x{char_id:02X}")
            entries = extras_other_char_sprite0[char_id]
            print(f"    0x{char_id:02X} {name}: {len(entries)} entries referencing other characters")
            for e in entries:
                print(f"      slot {e['slot']}: {e['name']} (ref: {e['sprite_ref']})")
        print()

    # =========================================================================
    # PART 1a: Output dict - simple format (char_id -> list of slot indices)
    # =========================================================================
    print()
    print("=" * 80)
    print("OUTPUT: EXTRAS_BODY_SLOTS dict (char_id -> list of slot indices)")
    print("=" * 80)
    print()
    print("EXTRAS_BODY_SLOTS = {")
    for char_id in sorted(extras_body_sprite.keys()):
        entries = extras_body_sprite[char_id]
        slots = [e['slot'] for e in entries]
        name = CHAR_NAMES.get(char_id, f"0x{char_id:02X}")
        print(f"    0x{char_id:02X}: {slots},  # {name}")
    print("}")
    print()

    # =========================================================================
    # PART 1b: Output dict - with button mapping
    # Format: char_id -> list of (slot_index, button_or_None)
    # =========================================================================
    print()
    print("=" * 80)
    print("OUTPUT: EXTRAS_BODY_SLOTS_WITH_BUTTONS dict")
    print("  char_id -> list of (slot_index, button_or_None)")
    print("=" * 80)
    print()
    print("EXTRAS_BODY_SLOTS_WITH_BUTTONS = {")
    for char_id in sorted(extras_body_sprite.keys()):
        entries = extras_body_sprite[char_id]
        name = CHAR_NAMES.get(char_id, f"0x{char_id:02X}")
        tuples = [(e['slot'], e['button']) for e in entries]

        # Format nicely: if many entries, break across lines
        if len(tuples) <= 4:
            print(f"    0x{char_id:02X}: {tuples},  # {name}")
        else:
            print(f"    0x{char_id:02X}: [  # {name}")
            for t in tuples:
                print(f"        {t},")
            print(f"    ],")
    print("}")
    print()

    # =========================================================================
    # PART 2: Per-button slot analysis
    # Which slots besides slot 0 use sprite index 0 within LP/LK/HP/HK/A1/A2?
    # =========================================================================
    print()
    print("=" * 80)
    print("PART 2: Per-button slots (0-7) that use sprite index 0")
    print("  Slot 0 = Main Color (always body sprite)")
    print("  Slots 6-7 typically = A-Groove/Super trail (usually body sprite)")
    print("  Looking for anomalies in slots 1-5")
    print("=" * 80)
    print()

    # For each character, check all 6 buttons and report which slots use sprite 0
    # We're interested in patterns beyond {0, 6, 7}

    per_button_body_slots = {}  # char_key -> {button -> set of slot indices with sprite 0}
    anomalies = {}  # char_key -> {button -> slots_with_sprite0_in_1_5_range}

    for (char_key, button), entries in sorted(button_arrays.items()):
        if char_key not in CHAR_IDS:
            continue

        own_sprite_ref = CHAR_SPRITE_REFS.get(char_key)
        body_slots = set()
        for slot_idx, entry in enumerate(entries):
            if entry['has_sprite'] and entry['sprite_index'] == 0:
                # For per-button arrays, also verify it references own sprite
                if own_sprite_ref and entry['sprite_ref'] == own_sprite_ref:
                    body_slots.add(slot_idx)

        if char_key not in per_button_body_slots:
            per_button_body_slots[char_key] = {}
        per_button_body_slots[char_key][button] = body_slots

        # Check for anomalies: sprite 0 in slots 1-5
        anomaly_slots = body_slots & {1, 2, 3, 4, 5}
        if anomaly_slots:
            if char_key not in anomalies:
                anomalies[char_key] = {}
            anomalies[char_key][button] = anomaly_slots

    # Report the standard pattern
    standard_chars = []  # chars with exactly {0, 6, 7} pattern
    no_trail_chars = []  # chars where slots 6/7 are NOT sprite 0
    custom_chars = []    # chars with other patterns

    for char_key in sorted(per_button_body_slots.keys()):
        if char_key not in CHAR_IDS:
            continue
        char_id = CHAR_IDS[char_key]

        # Check LP as representative (all buttons usually same pattern)
        if 'LP' in per_button_body_slots[char_key]:
            lp_slots = per_button_body_slots[char_key]['LP']

            if lp_slots == {0, 6, 7}:
                standard_chars.append(char_key)
            elif lp_slots == {0}:
                no_trail_chars.append(char_key)
            else:
                custom_chars.append(char_key)

    print(f"  Standard pattern (slots 0, 6, 7 use sprite 0): {len(standard_chars)} characters")
    for ck in standard_chars:
        print(f"    0x{CHAR_IDS[ck]:02X} {CHAR_NAMES.get(CHAR_IDS[ck], ck)}")
    print()

    print(f"  Only slot 0 uses sprite 0 (no trail): {len(no_trail_chars)} characters")
    for ck in no_trail_chars:
        cid = CHAR_IDS[ck]
        slots = per_button_body_slots[ck].get('LP', set())
        print(f"    0x{cid:02X} {CHAR_NAMES.get(cid, ck)}: LP body slots = {sorted(slots)}")
    print()

    print(f"  Custom/unusual pattern: {len(custom_chars)} characters")
    for ck in custom_chars:
        cid = CHAR_IDS[ck]
        for btn in BUTTONS:
            if btn in per_button_body_slots[ck]:
                slots = per_button_body_slots[ck][btn]
                if slots != {0}:
                    print(f"    0x{cid:02X} {CHAR_NAMES.get(cid, ck)} {btn}: body slots = {sorted(slots)}")
        print()

    # Report anomalies (slots 1-5 with sprite 0)
    print()
    print("  ANOMALIES: Slots 1-5 with sprite index 0:")
    if anomalies:
        for ck in sorted(anomalies.keys()):
            if ck not in CHAR_IDS:
                continue
            cid = CHAR_IDS[ck]
            for btn in BUTTONS:
                if btn in anomalies[ck]:
                    slots = anomalies[ck][btn]
                    # Get the entry names for context
                    entries = button_arrays.get((ck, btn), [])
                    for s in sorted(slots):
                        entry_name = entries[s]['name'] if s < len(entries) else "?"
                        print(f"    0x{cid:02X} {CHAR_NAMES.get(cid, ck)} {btn} slot {s}: {entry_name}")
    else:
        print("    None found - no slots 1-5 use sprite index 0.")
    print()

    # =========================================================================
    # PART 2a: Output dict for per-button body slots
    # =========================================================================
    print()
    print("=" * 80)
    print("OUTPUT: PER_BUTTON_BODY_SLOTS dict")
    print("  char_id -> list of slot indices (0-based within each button)")
    print("  Only includes characters that differ from standard {0, 6, 7}")
    print("=" * 80)
    print()

    print("# Standard pattern for most characters: slots [0, 6, 7]")
    print("# Characters below deviate from this pattern")
    print("PER_BUTTON_BODY_SLOTS_OVERRIDE = {")
    for char_key in sorted(per_button_body_slots.keys()):
        if char_key not in CHAR_IDS:
            continue
        char_id = CHAR_IDS[char_key]

        # Check if all buttons have the same pattern
        all_slots = set()
        for btn in BUTTONS:
            if btn in per_button_body_slots[char_key]:
                all_slots = per_button_body_slots[char_key][btn]
                break

        consistent = True
        for btn in BUTTONS:
            if btn in per_button_body_slots[char_key]:
                if per_button_body_slots[char_key][btn] != all_slots:
                    consistent = False
                    break

        if all_slots == {0, 6, 7}:
            continue  # Standard, skip

        name = CHAR_NAMES.get(char_id, f"0x{char_id:02X}")
        if consistent:
            print(f"    0x{char_id:02X}: {sorted(all_slots)},  # {name}")
        else:
            # Per-button breakdown
            print(f"    0x{char_id:02X}: {{  # {name} (varies by button)")
            for btn in BUTTONS:
                if btn in per_button_body_slots[char_key]:
                    slots = per_button_body_slots[char_key][btn]
                    print(f"        # {btn}: {sorted(slots)}")
            # Use the LP pattern as the main one
            lp_slots = per_button_body_slots[char_key].get('LP', set())
            print(f"        # Using LP pattern: {sorted(lp_slots)}")
            print(f"    }},")
    print("}")
    print()

    # =========================================================================
    # PART 3: Full combined analysis - comprehensive per-character slot map
    # =========================================================================
    print()
    print("=" * 80)
    print("PART 3: Summary - characters with body sprite in extras")
    print("=" * 80)
    print()

    chars_with_extras = set(extras_body_sprite.keys())
    chars_without_extras = set()
    for char_key in CHAR_IDS:
        cid = CHAR_IDS[char_key]
        if cid not in extras_body_sprite and char_key in extras_arrays:
            chars_without_extras.add(cid)

    chars_no_extras_array = set()
    for char_key in CHAR_IDS:
        cid = CHAR_IDS[char_key]
        if char_key not in extras_arrays:
            chars_no_extras_array.add(cid)

    print(f"  Characters with body-sprite extras: {len(chars_with_extras)}")
    for cid in sorted(chars_with_extras):
        n = len(extras_body_sprite[cid])
        print(f"    0x{cid:02X} {CHAR_NAMES.get(cid, '?')}: {n} entries")

    print()
    print(f"  Characters with extras array but NO body-sprite entries: {len(chars_without_extras)}")
    for cid in sorted(chars_without_extras):
        print(f"    0x{cid:02X} {CHAR_NAMES.get(cid, '?')}")

    print()
    print(f"  Characters with no extras array at all: {len(chars_no_extras_array)}")
    for cid in sorted(chars_no_extras_array):
        print(f"    0x{cid:02X} {CHAR_NAMES.get(cid, '?')}")


if __name__ == '__main__':
    main()
