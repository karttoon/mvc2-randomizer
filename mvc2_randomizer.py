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
    adjust_luminance, TOTAL_PALETTE_COUNT,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(SCRIPT_DIR, "randomizer_config.json")
DEFAULT_SKINS = os.path.join(SCRIPT_DIR, "skins")
LAST_RUN_LOG = os.path.join(SCRIPT_DIR, "last_run.txt")
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
    "seed": None,
}

CONFIG_DESCRIPTIONS = {
    "skins_path": "Path to skins folder (null = ./skins next to this script)",
    "game_path": "Game install directory (null = default Steam path)",
    "seed": "Fixed random seed for reproducible results (null = random each run)",
}

DEFAULT_LOCKS = os.path.join(SCRIPT_DIR, "skin_locks.txt")


def generate_default_config(config_path):
    """Create a default config file with descriptive comments."""
    lines = ["{"]
    keys = list(DEFAULT_CONFIG_CONTENT.keys())
    for i, key in enumerate(keys):
        val = DEFAULT_CONFIG_CONTENT[key]
        val_str = json.dumps(val)
        comma = "," if i < len(keys) - 1 else ""
        desc = CONFIG_DESCRIPTIONS.get(key, "")
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
    return {k: v for k, v in data.items() if not k.startswith("_")}


def generate_skin_locks(locks_path):
    """Create skin_locks.txt with every character and button set to null."""
    lines = [
        "# MvC2 Palette Randomizer - Skin Locks",
        "#",
        "# Each line is: Character_Name BUTTON=filename.png",
        "# Set a filename to lock that skin to that button slot.",
        "# Leave as \"null\" to randomize that slot each run.",
        "#",
        "# Filenames are case-insensitive and matched from the character's",
        "# skins folder. The character folder names listed here are the exact",
        "# folder names expected in your skins directory.",
        "#",
        "# Example:",
        "#   Akuma LP=Akuma_abc12345_cool-skin.png",
        "#   Akuma HP=Akuma_9bc20b73_AccurateMix.png",
        "#   Storm A1=null",
        "#",
        "",
    ]
    folders = sorted(CHAR_ID_TO_FOLDER.values(), key=str.lower)
    for folder in folders:
        for btn_name in BUTTON_NAMES:
            lines.append(f"{folder} {btn_name}=null")
        lines.append("")
    with open(locks_path, "w") as f:
        f.write("\n".join(lines))


def load_skin_locks(locks_path):
    """Load skin_locks.txt, creating it if it doesn't exist.

    Returns dict: {(folder_name, button_name): filename_or_none}
    """
    if not os.path.isfile(locks_path):
        generate_skin_locks(locks_path)
        print(f"Created skin locks: {locks_path}")
        print("  Edit this file to lock specific skins to button slots.\n")

    locks = {}
    with open(locks_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Format: "Character_Name BUTTON=value"
            if "=" not in line:
                continue
            left, value = line.split("=", 1)
            left = left.strip()
            value = value.strip()
            # Split "Character_Name BUTTON" on last space
            parts = left.rsplit(" ", 1)
            if len(parts) != 2:
                continue
            folder, btn = parts
            btn_upper = btn.upper()
            if btn_upper not in BUTTON_NAMES:
                continue
            if value.lower() == "null" or not value:
                locks[(folder, btn_upper)] = None
            else:
                locks[(folder, btn_upper)] = value
    return locks


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


def load_rejected_skins():
    """Load rejected skins from gallery_verdicts.json (verdict == 'delete')."""
    verdicts_file = os.path.join(SCRIPT_DIR, "gallery_verdicts.json")
    rejected = set()
    if os.path.isfile(verdicts_file):
        with open(verdicts_file, "r") as f:
            verdicts = json.load(f)
        for key, verdict in verdicts.items():
            if verdict == "delete":
                # key is "Character/filename.png" — extract filename
                filename = key.split("/", 1)[-1] if "/" in key else key
                rejected.add(filename.lower())
    return rejected


def do_gallery_download(skins_dir):
    """Download the skins gallery from GitHub and merge into existing collection.

    Only adds new files — existing skins (including user's own custom palettes)
    are preserved. Previously rejected skins (in gallery_verdicts.json) are skipped.
    Safe to run repeatedly to pick up gallery updates.
    """
    print("=" * 60)
    print("MvC2 Skins Gallery Download")
    print("=" * 60)
    print(f"Source: {SKINS_REPO_ZIP}")
    print(f"Output: {skins_dir}")
    print()

    skip_list = load_rejected_skins()
    if skip_list:
        print(f"Rejected skins: {len(skip_list)} (from gallery verdicts)")

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
    existed = 0
    rejected = 0

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
            # Check skip list (filename only, case-insensitive)
            filename = os.path.basename(rel_path)
            if filename.lower() in skip_list:
                rejected += 1
                continue
            dest = os.path.join(skins_dir, rel_path)
            if os.path.isfile(dest):
                existed += 1
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as dst:
                dst.write(src.read())
            added += 1

    print(f"Added {added} new skins ({existed} already existed, {rejected} skipped from reject list)")
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
                   help="List all valid character names")
    return p


def find_skin_file(skin_folder, filename):
    """Find a skin file by name, case-insensitive.

    Returns the actual filename on disk, or None if not found.
    """
    target = filename.lower()
    for f in os.listdir(skin_folder):
        if f.lower() == target:
            return f
    return None


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

    # Load skin locks (auto-generates skin_locks.txt if missing)
    locks = load_skin_locks(DEFAULT_LOCKS)

    # Set random seed
    seed = args.seed if args.seed is not None else config.get("seed")
    if seed is not None:
        random.seed(seed)
        print(f"Using seed: {seed}")

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
    print()

    # Read and decompress ROM — always from backup for clean state
    if not args.dry_run:
        print("Reading game archive...")
        rom = read_arc(backup_path)
        validate_rom(rom)
    else:
        rom = None

    total_assigned = 0
    total_locked = 0
    run_log = []  # collected for last_run.txt

    for cid in sorted(PLAYABLE_CHARS, key=lambda c: CHARACTERS[c]):
        char_name = CHARACTERS[cid]
        sname = safe_name(char_name)
        folder_name = CHAR_ID_TO_FOLDER.get(cid, sname)

        # Apply character filter
        if char_filter_id is not None and cid != char_filter_id:
            continue

        # Find skins folder
        skin_folder = os.path.join(skins_dir, folder_name)
        if not os.path.isdir(skin_folder):
            continue

        # Collect PNG files
        pngs = sorted(f for f in os.listdir(skin_folder) if f.lower().endswith(".png"))
        if not pngs:
            continue

        # Check which buttons are locked vs randomizable
        locked_buttons = {}   # btn_idx → filename
        random_buttons = []   # btn_idx values to randomize
        for btn_idx, btn_name in enumerate(BUTTON_NAMES):
            lock_val = locks.get((folder_name, btn_name))
            if lock_val is not None:
                # Locked — find the file case-insensitively
                actual = find_skin_file(skin_folder, lock_val)
                if actual:
                    locked_buttons[btn_idx] = actual
                else:
                    # Locked file not found — warn and randomize instead
                    print(f"  Warning: locked skin not found: {lock_val}")
                    print(f"    ({folder_name}/{lock_val} — will randomize instead)")
                    random_buttons.append(btn_idx)
            else:
                random_buttons.append(btn_idx)

        # Assign random skins to unlocked buttons
        random_assignments = assign_skins(pngs, len(random_buttons)) if random_buttons else []

        btn_log = []
        any_applied = False

        for btn_idx, btn_name in enumerate(BUTTON_NAMES):
            if btn_idx in locked_buttons:
                skin_file = locked_buttons[btn_idx]
                skin_path = os.path.join(skin_folder, skin_file)
                if args.dry_run:
                    btn_log.append(f"  {btn_name}: {skin_file} [locked]")
                else:
                    if apply_skin(rom, cid, btn_idx, skin_path):
                        btn_log.append(f"  {btn_name}: {skin_file} [locked]")
                        any_applied = True
                    else:
                        btn_log.append(f"  {btn_name}: [failed] {skin_file}")
                total_locked += 1
            elif random_buttons and btn_idx in random_buttons:
                idx = random_buttons.index(btn_idx)
                if idx < len(random_assignments):
                    skin_file = random_assignments[idx]
                    skin_path = os.path.join(skin_folder, skin_file)
                    if args.dry_run:
                        btn_log.append(f"  {btn_name}: {skin_file}")
                    else:
                        if apply_skin(rom, cid, btn_idx, skin_path):
                            btn_log.append(f"  {btn_name}: {skin_file}")
                            any_applied = True
                        else:
                            btn_log.append(f"  {btn_name}: [failed] {skin_file}")

        if btn_log:
            print(f"{char_name}")
            run_log.append(char_name)
            for line in btn_log:
                print(line)
                run_log.append(line)
            run_log.append("")
            total_assigned += 1

            # Write body palettes to extras animation frame entries
            if any_applied and not args.dry_run and cid in EXTRAS_BODY_ENTRIES:
                btn_body_cache = {}
                lum_cache = {}
                for entry in EXTRAS_BODY_ENTRIES[cid]:
                    entry_idx, btn_idx = entry[0], entry[1]
                    lum = entry[2] if len(entry) > 2 else None
                    # Shared entries (btn_idx=None) use LP (button 0) body palette
                    pal_btn = btn_idx if btn_idx is not None else 0
                    if pal_btn not in btn_body_cache:
                        btn_body_cache[pal_btn] = read_palette(rom, cid, pal_btn, 0)
                    colors = btn_body_cache[pal_btn]
                    if lum:
                        cache_key = (pal_btn, lum)
                        if cache_key not in lum_cache:
                            lum_cache[cache_key] = adjust_luminance(colors, lum)
                        colors = lum_cache[cache_key]
                    write_palette_at(rom, cid, entry_idx, colors)

    print()

    if args.dry_run:
        print(f"[dry-run] Would randomize {total_assigned} characters"
              f" ({total_locked} slots locked)")
        print("No files were modified.")
    else:
        print(f"Writing modified archive...")
        write_arc(arc_path, rom)
        # Touch backup so its mtime stays newer than the modified arc.
        # Without this, the next run would see arc newer than backup and
        # overwrite the clean backup with randomized data.
        os.utime(backup_path)
        # Save assignment log so user can check what was applied
        with open(LAST_RUN_LOG, "w") as f:
            f.write("\n".join(run_log) + "\n")
        print(f"Done! Randomized {total_assigned} characters"
              f" ({total_locked} slots locked)")
        print(f"Assignments saved to: {LAST_RUN_LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
