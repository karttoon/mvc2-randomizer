#!/usr/bin/env python3
"""
MvC2 Steam Palette Randomizer

Randomizes character palettes in the Steam version of Marvel vs. Capcom 2
using skin PNGs from a curated collection. Each game launch gets a fresh
set of random color schemes across all characters and buttons.

Usage:
    python mvc2_randomizer.py --skins ./skins --game "C:/path/to/game"
    python mvc2_randomizer.py --restore
    python mvc2_randomizer.py --gallery-download --skins ./skins

Use launch.bat as a Steam launch option for auto-randomize on every play.
See README.md for Steam setup instructions.
"""

import argparse
import io
import json
import os
import random
import shutil
import sys
import zipfile
import urllib.request

from PIL import Image

from mvc2_data.characters import (
    CHARACTERS, BUTTON_NAMES, PLAYABLE_CHARS,
    EXTRA_BODY_BUTTON_SLOTS, EXTRAS_BODY_ENTRIES,
    palette_rows, palette_slot_map, safe_name,
)
from mvc2_data.steam import (
    read_arc, write_arc, validate_rom,
    read_palette, write_palette, write_palette_at,
    TOTAL_PALETTE_COUNT,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(SCRIPT_DIR, "randomizer_config.json")
DEFAULT_SKINS = os.path.join(SCRIPT_DIR, "skins")
ARC_FILENAME = "game_50.arc"
ARC_SUBPATH = os.path.join("arc", "pc", ARC_FILENAME)
BACKUP_SUFFIX = ".bak"

# Default Steam install path
DEFAULT_STEAM_PATH = os.path.join(
    "C:\\", "Program Files (x86)", "Steam", "steamapps", "common",
    "MARVEL vs. CAPCOM Fighting Collection", "nativeDX11x64",
)

# GitHub skins download
SKINS_REPO_ZIP = "https://github.com/karttoon/mvc2-skins/archive/refs/heads/master.zip"
SKINS_ZIP_PREFIX = "mvc2-skins-master/skins/"

# Folder name → character ID (1:1 mapping via safe_name)
FOLDER_TO_CHAR_ID = {}
for _cid in PLAYABLE_CHARS:
    _sn = safe_name(CHARACTERS[_cid])
    FOLDER_TO_CHAR_ID[_sn] = _cid

# Reverse: char_id → folder name
CHAR_ID_TO_FOLDER = {v: k for k, v in FOLDER_TO_CHAR_ID.items()}


DEFAULT_CONFIG_CONTENT = {
    "skins_path": None,
    "game_path": None,
    "bypass_characters": [],
    "bypass_buttons": {},
    "only_defaults": False,
    "seed": None,
}

# Human-readable descriptions for each config key
CONFIG_DESCRIPTIONS = {
    "skins_path": "Path to skins folder (null = ./skins next to this script)",
    "game_path": "Game install directory (null = default Steam path)",
    "bypass_characters": "Character names to never randomize, e.g. [\"Ryu\", \"Storm\"]",
    "bypass_buttons": "Per-character button bypasses, e.g. {\"Ryu\": [\"LP\", \"HK\"]}",
    "only_defaults": "If true, only replace palettes that still match game defaults",
    "seed": "Fixed random seed for reproducible results (null = random each run)",
}


def generate_default_config(config_path):
    """Create a default config file with descriptive comments."""
    lines = ["{"]
    keys = list(DEFAULT_CONFIG_CONTENT.keys())
    for i, key in enumerate(keys):
        val = DEFAULT_CONFIG_CONTENT[key]
        val_str = json.dumps(val)
        comma = "," if i < len(keys) - 1 else ""
        desc = CONFIG_DESCRIPTIONS.get(key, "")
        # JSON doesn't support comments, so we use a _comment key
        # Use json.dumps for the description to escape any embedded quotes
        lines.append(f'    "_{key}_comment": {json.dumps(desc)},')
        lines.append(f'    "{key}": {val_str}{comma}')
    lines.append("}")
    with open(config_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def load_config(config_path):
    """Load JSON config file, creating a default one if it doesn't exist."""
    if not os.path.isfile(config_path):
        if config_path == DEFAULT_CONFIG:
            generate_default_config(config_path)
            print(f"Created default config: {config_path}")
            print("  Edit this file to customize settings.\n")
        return {}
    with open(config_path, "r") as f:
        data = json.load(f)
    # Strip _comment keys
    return {k: v for k, v in data.items() if not k.startswith("_")}


def resolve_character(query):
    """Resolve a character name from user input.

    Returns (char_id, canonical_name) or (None, error_message).
    Accepts: proper name, safe_name, or folder name (case-insensitive,
    separators ignored). On failure, suggests characters starting with
    the same first letter.
    """
    q = query.lower().replace(" ", "").replace("_", "").replace("-", "").replace(".", "")

    # Build lookup table
    candidates = []
    for cid in PLAYABLE_CHARS:
        name = CHARACTERS[cid]
        sname = safe_name(name)
        folder = CHAR_ID_TO_FOLDER.get(cid, sname)
        norm = name.lower().replace(" ", "").replace("-", "").replace(".", "")
        candidates.append((cid, name, norm))

    # Exact match (case-insensitive, ignoring separators)
    for cid, name, norm in candidates:
        if q == norm:
            return cid, name

    # No match — suggest by first letter
    first = q[0] if q else ""
    suggestions = [name for _, name, norm in candidates if norm.startswith(first)]

    msg = f"Unknown character \"{query}\"."
    if suggestions:
        msg += f"\n  Characters starting with '{first.upper()}': {', '.join(suggestions)}"
    msg += "\n  Use --list-characters to see all valid names."
    return None, msg


def list_characters():
    """Print all valid character names."""
    print("Valid character names (56 playable):")
    print("-" * 50)
    for cid in PLAYABLE_CHARS:
        name = CHARACTERS[cid]
        sname = safe_name(name)
        if sname != name.replace(" ", "_").replace(".", ""):
            print(f"  {name:<25} (also: {sname})")
        else:
            print(f"  {name}")
    print()
    print("Names are case-insensitive. Spaces, hyphens, and")
    print("underscores are interchangeable (e.g. \"doctor doom\",")
    print("\"Doctor_Doom\", \"doctordoom\" all work).")


def find_arc(game_path):
    """Locate game_50.arc within a game directory."""
    arc = os.path.join(game_path, ARC_SUBPATH)
    if os.path.isfile(arc):
        return arc
    # Maybe they pointed directly at the nativeDX11x64 dir or arc/pc dir
    for candidate in [
        os.path.join(game_path, ARC_FILENAME),
        os.path.join(game_path, "pc", ARC_FILENAME),
    ]:
        if os.path.isfile(candidate):
            return candidate
    return None


def extract_png_palette(filepath):
    """Extract palette colors from an indexed PNG skin.

    Returns a flat list of (R, G, B) tuples — 16 per palette row.
    For multi-row characters, the PNG contains indices 0-N*16 with
    colors stored consecutively in the palette.
    """
    img = Image.open(filepath)
    if img.mode != "P":
        img = img.convert("P", palette=Image.ADAPTIVE, colors=16)

    raw_palette = img.getpalette()
    if not raw_palette:
        return None

    # Determine how many colors are actually in the palette
    n_colors = len(raw_palette) // 3
    colors = []
    for i in range(n_colors):
        r = raw_palette[i * 3]
        g = raw_palette[i * 3 + 1]
        b = raw_palette[i * 3 + 2]
        colors.append((r, g, b))

    img.close()
    return colors


def assign_skins(png_files, num_buttons=6):
    """Pick skins for 6 button slots via shuffle.

    Returns list of 6 filenames. If fewer than 6 skins, cycles through.
    """
    if not png_files:
        return []
    pool = list(png_files)
    random.shuffle(pool)
    if len(pool) >= num_buttons:
        return pool[:num_buttons]
    # Cycle through available skins
    return [pool[i % len(pool)] for i in range(num_buttons)]


def apply_skin(rom, char_id, button_idx, skin_path):
    """Read a skin PNG and write its palette(s) into the ROM.

    Writes the mapped palette rows (body slot 0 + accessory slots for
    multi-row characters) plus super trail slots 6-7 with the body palette.
    Does not touch projectile/effect slots 1-5 or status effects.
    """
    colors = extract_png_palette(skin_path)
    if not colors:
        return False

    num_rows = palette_rows(char_id)
    slot_map = palette_slot_map(char_id)

    # Extract body palette (row 0) for reuse
    body_colors = colors[0:16]
    while len(body_colors) < 16:
        body_colors.append((0, 0, 0))

    for row in range(num_rows):
        start = row * 16
        end = start + 16
        row_colors = colors[start:end]

        while len(row_colors) < 16:
            row_colors.append((0, 0, 0))

        slot = slot_map[row]
        write_palette(rom, char_id, button_idx, slot, row_colors)

    # Write body palette to super trail slots (6-7) if not already used
    # These are "A-Groove/Super trail light/dark" — used for afterimages
    for trail_slot in (6, 7):
        if trail_slot not in slot_map:
            write_palette(rom, char_id, button_idx, trail_slot, body_colors)

    # Write body palette to character-specific extra body slots
    # (e.g., Colossus slot 3 = Stance Frame, Silver Samurai slot 1 = Shadow)
    for extra_slot in EXTRA_BODY_BUTTON_SLOTS.get(char_id, ()):
        if extra_slot not in slot_map:
            write_palette(rom, char_id, button_idx, extra_slot, body_colors)

    return True



def do_restore(arc_path):
    """Restore game_50.arc from backup."""
    backup = arc_path + BACKUP_SUFFIX
    if not os.path.isfile(backup):
        print(f"Error: No backup found at {backup}")
        return False
    shutil.copy2(backup, arc_path)
    print(f"Restored {arc_path} from backup")
    return True


def do_gallery_download(skins_dir):
    """Download the skins gallery from GitHub and merge into existing collection.

    Only adds new files — existing skins (including user's own custom palettes)
    are preserved. Safe to run repeatedly to pick up gallery updates.
    """
    print("=" * 60)
    print("MvC2 Skins Gallery Download")
    print("=" * 60)
    print(f"Source: {SKINS_REPO_ZIP}")
    print(f"Output: {skins_dir}")
    print()

    print("Downloading archive (this may take a while)...")
    try:
        response = urllib.request.urlopen(SKINS_REPO_ZIP)
        zip_data = response.read()
    except Exception as e:
        print(f"Error downloading: {e}")
        return False

    print(f"Downloaded {len(zip_data) / 1024 / 1024:.1f} MB")
    print("Merging skins...")

    os.makedirs(skins_dir, exist_ok=True)
    added = 0
    skipped = 0

    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if not info.filename.startswith(SKINS_ZIP_PREFIX):
                continue
            # Strip the prefix to get relative path within skins/
            rel_path = info.filename[len(SKINS_ZIP_PREFIX):]
            if not rel_path:
                continue
            dest = os.path.join(skins_dir, rel_path)
            if os.path.isfile(dest):
                skipped += 1
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as dst:
                dst.write(src.read())
            added += 1

    print(f"Added {added} new skins ({skipped} already existed)")
    return True


def build_parser():
    """Build the CLI argument parser."""
    p = argparse.ArgumentParser(
        description="Randomize MvC2 character palettes for Steam.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --skins ./skins\n"
            "  %(prog)s --character Ryu --seed 42\n"
            "  %(prog)s --restore\n"
            "  %(prog)s --gallery-download --skins ./skins\n"
        ),
    )
    p.add_argument("--skins", help="Path to skins collection folder")
    p.add_argument("--game", help="Game install directory (auto-detected if omitted)")
    p.add_argument("--config", default=DEFAULT_CONFIG,
                   help="Path to config JSON (default: randomizer_config.json)")
    p.add_argument("--bypass", help="Comma-separated character names to skip")
    p.add_argument("--bypass-buttons",
                   help="Character:button pairs to skip (e.g. Ryu:LP,HK;Storm:A2)")
    p.add_argument("--only-defaults", action="store_true",
                   help="Only replace palettes that still match game defaults")
    p.add_argument("--character", help="Only randomize a specific character")
    p.add_argument("--seed", type=int, help="Random seed for reproducible results")
    p.add_argument("--dry-run", action="store_true",
                   help="Show assignments without modifying files")
    p.add_argument("--restore", action="store_true",
                   help="Restore game_50.arc from backup")
    p.add_argument("--gallery-download", action="store_true",
                   help="Download/update skins from gallery (merges new, keeps existing)")
    p.add_argument("--force-backup", action="store_true",
                   help="Recreate backup from current game file (use after reinstall)")
    p.add_argument("--list-characters", action="store_true",
                   help="List all valid character names for --character/--bypass")
    return p


def parse_bypass_buttons(raw):
    """Parse --bypass-buttons string into dict.

    Format: "Ryu:LP,HK;Storm:A2" → {"Ryu": ["LP", "HK"], "Storm": ["A2"]}
    """
    if not raw:
        return {}
    result = {}
    for entry in raw.split(";"):
        entry = entry.strip()
        if ":" not in entry:
            continue
        char_part, btns_part = entry.split(":", 1)
        result[char_part.strip()] = [b.strip() for b in btns_part.split(",")]
    return result


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Load config (CLI args override config values)
    config = load_config(args.config)

    # Resolve skins path
    skins_dir = args.skins or config.get("skins_path") or DEFAULT_SKINS

    # Handle --list-characters (doesn't need game path)
    if args.list_characters:
        list_characters()
        return 0

    # Handle --gallery-download
    if args.gallery_download:
        if skins_dir == DEFAULT_SKINS and not args.skins and not config.get("skins_path"):
            print("Error: --gallery-download requires a skins path.")
            print("  Use --skins to specify where to download, e.g.:")
            print(f"    python {os.path.basename(__file__)} --gallery-download --skins C:/path/to/skins")
            print("  Or set skins_path in randomizer_config.json")
            return 1
        return 0 if do_gallery_download(skins_dir) else 1

    # Resolve game path
    game_path = args.game or config.get("game_path") or DEFAULT_STEAM_PATH

    # Find ARC file
    arc_path = find_arc(game_path)
    if not arc_path:
        print(f"Error: Could not find {ARC_FILENAME} in {game_path}")
        print("Use --game to specify the game install directory")
        return 1

    # Handle --restore
    if args.restore:
        return 0 if do_restore(arc_path) else 1

    # Validate skins directory
    if not os.path.isdir(skins_dir):
        print(f"Error: Skins directory not found: {skins_dir}")
        print("Use --skins to specify the path, or --gallery-download to download them")
        return 1

    # Set random seed
    seed = args.seed if args.seed is not None else config.get("seed")
    if seed is not None:
        random.seed(seed)
        print(f"Using seed: {seed}")

    # Build bypass lists
    bypass_chars = set()
    if args.bypass:
        bypass_chars = {c.strip() for c in args.bypass.split(",")}
    bypass_chars.update(config.get("bypass_characters", []))

    bypass_buttons = parse_bypass_buttons(args.bypass_buttons)
    for char_name, btns in config.get("bypass_buttons", {}).items():
        if char_name not in bypass_buttons:
            bypass_buttons[char_name] = btns

    only_defaults = args.only_defaults or config.get("only_defaults", False)

    # Resolve --character filter
    char_filter = args.character
    char_filter_id = None
    if char_filter:
        result = resolve_character(char_filter)
        if result[0] is None:
            print(f"Error: {result[1]}")
            return 1
        char_filter_id, resolved_name = result
        if resolved_name.lower() != char_filter.lower():
            print(f"Matched \"{char_filter}\" -> {resolved_name}")
        char_filter = resolved_name

    # Backup original ARC
    backup_path = arc_path + BACKUP_SUFFIX
    need_backup = not os.path.isfile(backup_path) or args.force_backup
    if not need_backup and os.path.isfile(backup_path):
        # Detect stale backup: if game was reinstalled, the arc will be
        # newer than the backup. Recreate backup from the fresh install.
        arc_mtime = os.path.getmtime(arc_path)
        bak_mtime = os.path.getmtime(backup_path)
        if arc_mtime > bak_mtime:
            need_backup = True
            print("Detected newer game file (reinstall?) — refreshing backup")
    if need_backup:
        if not args.dry_run:
            shutil.copy2(arc_path, backup_path)
            print(f"Backup created: {backup_path}")
            print(f"  (Unmodified copy of {ARC_FILENAME} — use --restore to revert)")
        else:
            print(f"[dry-run] Would backup {arc_path}")

    print("=" * 60)
    print("MvC2 Palette Randomizer")
    print("=" * 60)
    print(f"Game:  {arc_path}")
    print(f"Skins: {skins_dir}")
    if char_filter:
        print(f"Filter: {char_filter}")
    if bypass_chars:
        print(f"Bypass: {', '.join(sorted(bypass_chars))}")
    print()

    # Read and decompress ROM — always from backup for clean state
    if not args.dry_run:
        print("Reading game archive...")
        rom = read_arc(backup_path)
        validate_rom(rom)
    else:
        rom = None

    # Save default palettes for --only-defaults comparison
    default_palettes = {}
    if only_defaults and not args.dry_run:
        backup_rom = read_arc(backup_path)
        for cid in PLAYABLE_CHARS:
            for btn_idx in range(6):
                slot_map = palette_slot_map(cid)
                for row, slot in enumerate(slot_map):
                    key = (cid, btn_idx, slot)
                    default_palettes[key] = read_palette(backup_rom, cid, btn_idx, slot)
        del backup_rom

    total_assigned = 0
    total_skipped = 0

    for cid in PLAYABLE_CHARS:
        char_name = CHARACTERS[cid]
        sname = safe_name(char_name)
        folder_name = CHAR_ID_TO_FOLDER.get(cid, sname)

        # Apply character filter (already resolved to char_id)
        if char_filter_id is not None:
            if cid != char_filter_id:
                continue

        # Check bypass list
        if char_name in bypass_chars or sname in bypass_chars or folder_name in bypass_chars:
            total_skipped += 1
            continue

        # Find skins folder
        skin_folder = os.path.join(skins_dir, folder_name)
        if not os.path.isdir(skin_folder):
            continue

        # Collect PNG files
        pngs = sorted(f for f in os.listdir(skin_folder) if f.lower().endswith(".png"))
        if not pngs:
            continue

        # Assign skins to buttons
        assignments = assign_skins(pngs)
        btn_log = []

        for btn_idx, skin_file in enumerate(assignments):
            btn_name = BUTTON_NAMES[btn_idx]

            # Check button bypass
            if char_name in bypass_buttons and btn_name in bypass_buttons[char_name]:
                btn_log.append(f"  {btn_name}: [bypassed]")
                continue
            if sname in bypass_buttons and btn_name in bypass_buttons[sname]:
                btn_log.append(f"  {btn_name}: [bypassed]")
                continue

            # Check --only-defaults
            if only_defaults and not args.dry_run:
                slot_map = palette_slot_map(cid)
                current = read_palette(rom, cid, btn_idx, slot_map[0])
                default = default_palettes.get((cid, btn_idx, slot_map[0]))
                if default and current != default:
                    btn_log.append(f"  {btn_name}: [already modified, skipped]")
                    continue

            skin_path = os.path.join(skin_folder, skin_file)

            if args.dry_run:
                btn_log.append(f"  {btn_name}: {skin_file}")
            else:
                if apply_skin(rom, cid, btn_idx, skin_path):
                    btn_log.append(f"  {btn_name}: {skin_file}")
                else:
                    btn_log.append(f"  {btn_name}: [failed] {skin_file}")

        if btn_log:
            print(f"{char_name}")
            for line in btn_log:
                print(line)
            total_assigned += 1

            # Write body palettes to extras animation frame entries.
            # Status effects (entries 48-55) are intentionally left untouched:
            # they're shared across all buttons and contain pre-computed
            # dark/light transforms for burn/shock/charge visual effects.
            if not args.dry_run and cid in EXTRAS_BODY_ENTRIES:
                btn_body_cache = {}
                for entry_idx, btn_idx in EXTRAS_BODY_ENTRIES[cid]:
                    if btn_idx not in btn_body_cache:
                        btn_body_cache[btn_idx] = read_palette(rom, cid, btn_idx, 0)
                    write_palette_at(rom, cid, entry_idx, btn_body_cache[btn_idx])

    print()

    if args.dry_run:
        print(f"[dry-run] Would randomize {total_assigned} characters")
        print("No files were modified.")
    else:
        print(f"Writing modified archive...")
        write_arc(arc_path, rom)
        print(f"Done! Randomized {total_assigned} characters"
              f" ({total_skipped} bypassed)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
