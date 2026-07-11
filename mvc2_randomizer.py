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
import hashlib
import io
import json
import os
import random
import sys
import zipfile
import urllib.request

from PIL import Image

from mvc2_data.characters import (
    CHARACTERS, BUTTON_NAMES, PLAYABLE_CHARS,
    EXTRA_BODY_BUTTON_SLOTS, EXTRAS_BODY_ENTRIES, EXTRAS_SLOT_ENTRIES,
    palette_rows, palette_slot_map, safe_name,
)
from mvc2_data.steam import (
    read_arc, write_arc, validate_rom,
    read_palette, write_palette, write_palette_at,
    adjust_luminance, TOTAL_PALETTE_COUNT, STEAM_PALETTE_OFFSETS,
    load_vanilla_palettes,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(SCRIPT_DIR, "randomizer_config.json")
DEFAULT_SKINS = os.path.join(SCRIPT_DIR, "skins")
LAST_RUN_LOG = os.path.join(SCRIPT_DIR, "last_run.txt")
# Tracks what this tool wrote so external edits (e.g. PalMod) can be detected
# and protected instead of overwritten. See load_palette_state().
PALETTE_STATE = os.path.join(SCRIPT_DIR, "palette_state.json")
ARC_FILENAME = "game_50.arc"
ARC_SUBPATH = os.path.join("arc", "pc", ARC_FILENAME)

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
        raw = f.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        lines = raw.splitlines()
        print(f"\nError: Invalid JSON in config file: {config_path}")
        print(f"  {e.msg} at line {e.lineno}, column {e.colno}\n")
        if 1 <= e.lineno <= len(lines):
            problem_line = lines[e.lineno - 1]
            print(f"  Problem line: {problem_line.strip()}")
            print()
        print("  Common fixes:")
        print('    - Wrap path values in double quotes: "game_path": "C:/Games/MvC2"')
        print("    - Use forward slashes in paths:      C:/Games  not  C:\\Games")
        print("    - Don't add a comma after the last value before }")
        print(f"\n  You can delete {config_path} and re-run to regenerate it.\n")
        raise SystemExit(1)
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

    Handles PNGs with shifted palette indices (e.g. colors at 240-255
    instead of 0-15) by detecting all-black body palette and remapping
    from the actually-used indices.
    """
    img = Image.open(filepath)
    if img.mode != "P":
        img = img.convert("P", palette=Image.ADAPTIVE, colors=16)

    raw_palette = img.getpalette()
    if not raw_palette:
        return None

    n_colors = len(raw_palette) // 3

    # Check for shifted palette: body indices 1-15 all black but pixels
    # use higher indices. This happens with some PNGs saved by external
    # tools that place colors at the end of a 256-entry palette.
    pixels = img.getdata()
    max_idx = max(pixels) if pixels else 0
    body_all_black = (n_colors >= 16 and max_idx > 15 and
                      all(raw_palette[i * 3] == 0 and
                          raw_palette[i * 3 + 1] == 0 and
                          raw_palette[i * 3 + 2] == 0
                          for i in range(1, 16)))

    if body_all_black:
        # Remap: extract colors from the indices pixels actually use
        used = sorted(set(pixels) - {0})
        colors = [(0, 0, 0)]  # index 0 = transparent
        for idx in used:
            if idx < n_colors:
                i = idx
                colors.append((raw_palette[i * 3], raw_palette[i * 3 + 1],
                                raw_palette[i * 3 + 2]))
            else:
                colors.append((0, 0, 0))
        img.close()
        return colors

    # Normal path: sequential palette
    colors = []
    for i in range(n_colors):
        colors.append((raw_palette[i * 3], raw_palette[i * 3 + 1],
                        raw_palette[i * 3 + 2]))

    img.close()
    return colors


def assign_skins(png_files, num_buttons=6):
    """Pick skins for button slots via shuffle.

    Returns up to num_buttons filenames with no repeats. If fewer skins than
    buttons exist, only that many are returned — the caller restores vanilla
    first, so the leftover buttons keep their stock palettes instead of
    duplicating the small pool.
    """
    pool = list(png_files)
    random.shuffle(pool)
    return pool[:num_buttons]


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



# --------------------------------------------------------------------------
# Palette state: protecting external edits (e.g. PalMod)
#
# palette_state.json records a hash of each character's palette block as this
# tool last wrote it. On every run, a character whose block matches neither
# vanilla nor our last write must have been edited by something else - that
# character is "protected": skipped entirely (not randomized, not reset)
# until the user unlocks it or resets it to vanilla.
# --------------------------------------------------------------------------

def load_palette_state():
    state = {}
    try:
        if os.path.isfile(PALETTE_STATE):
            with open(PALETTE_STATE, "r") as f:
                state = json.load(f)
    except Exception:
        state = {}
    state.setdefault("written", {})     # folder -> hash we last wrote
    state.setdefault("protected", [])   # folders with detected external edits
    state.setdefault("notified", [])    # protections the GUI already announced
    return state


def save_palette_state(state):
    with open(PALETTE_STATE, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)


def block_hash(rom, cid):
    """Hash of one character's palette block in the ROM."""
    start = STEAM_PALETTE_OFFSETS[cid]
    n = TOTAL_PALETTE_COUNT[cid] * 32
    return hashlib.sha1(bytes(rom[start:start + n])).hexdigest()


def vanilla_hashes(vanilla):
    return {cid: hashlib.sha1(bytes(block)).hexdigest()
            for cid, block in vanilla.items()}


def reset_palettes(arc_path, char_id=None):
    """Reset character palettes to their stock (vanilla) values - one
    character if char_id is given, otherwise all of them.

    Uses the small bundled vanilla_palettes.bin - no per-user game_50.arc backup
    is needed, because the randomizer only ever changes palette bytes. Each
    character's palettes are a contiguous ROM block (STEAM_PALETTE_OFFSETS[cid]
    for TOTAL_PALETTE_COUNT[cid] * 32 bytes), written straight into the live arc.
    Everything else in the arc (stage mods, other edits) is left untouched.
    """
    try:
        vanilla = load_vanilla_palettes()
    except Exception as e:
        print(f"Error: bundled vanilla palette data unavailable ({e}).")
        return False
    targets = ({char_id: STEAM_PALETTE_OFFSETS[char_id]} if char_id is not None
               else STEAM_PALETTE_OFFSETS)
    who = CHARACTERS[char_id] if char_id is not None else "all characters"
    print(f"Resetting palettes to vanilla for {who} (other game data preserved)...")
    rom = read_arc(arc_path)          # live file - keeps non-palette changes
    validate_rom(rom)
    for cid, start in targets.items():
        block = vanilla[cid]
        rom[start:start + len(block)] = block
    write_arc(arc_path, rom)
    # Reset characters are vanilla again: clear their protection + write record.
    state = load_palette_state()
    for cid in targets:
        folder = CHAR_ID_TO_FOLDER.get(cid, safe_name(CHARACTERS[cid]))
        state["written"].pop(folder, None)
        if folder in state["protected"]:
            state["protected"].remove(folder)
        if folder in state["notified"]:
            state["notified"].remove(folder)
    save_palette_state(state)
    print(f"Reset palettes for {len(targets)} character(s).")
    return True


def unprotect_palettes(arc_path, char_id=None):
    """Allow the randomizer to overwrite protected characters again.

    Adopts each character's CURRENT palettes as if this tool wrote them, so
    the next run randomizes right over the external edits. One character if
    char_id is given, otherwise every currently-protected character.
    """
    state = load_palette_state()
    if char_id is not None:
        folders = [CHAR_ID_TO_FOLDER.get(char_id, safe_name(CHARACTERS[char_id]))]
    else:
        folders = list(state["protected"])
    if not folders:
        print("No protected characters - nothing to unlock.")
        return True
    rom = read_arc(arc_path)
    validate_rom(rom)
    folder_to_cid = {v: k for k, v in CHAR_ID_TO_FOLDER.items()}
    for folder in folders:
        cid = folder_to_cid.get(folder)
        if cid is None:
            continue
        state["written"][folder] = block_hash(rom, cid)
        if folder in state["protected"]:
            state["protected"].remove(folder)
        if folder in state["notified"]:
            state["notified"].remove(folder)
        print(f"Unlocked {folder} - next randomize may overwrite its palettes.")
    save_palette_state(state)
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

    print("Downloading archive...")
    try:
        response = urllib.request.urlopen(SKINS_REPO_ZIP)
        total = int(response.headers.get("Content-Length", 0))
        chunk_size = 256 * 1024  # 256 KB chunks
        chunks = []
        downloaded = 0
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            chunks.append(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded / total * 100
                bar_len = 40
                filled = int(bar_len * downloaded // total)
                bar = "█" * filled + "░" * (bar_len - filled)
                print(f"\r  [{bar}] {pct:5.1f}% — {downloaded / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB", end="", flush=True)
            else:
                print(f"\r  Downloaded {downloaded / 1024 / 1024:.1f} MB...", end="", flush=True)
        print()  # newline after progress bar
        zip_data = b"".join(chunks)
    except Exception as e:
        print(f"\nError downloading: {e}")
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
    p.add_argument("--reset-palettes", "--restore", dest="reset_palettes",
                   action="store_true",
                   help="Reset character palettes to vanilla (keeps other game data); "
                        "combine with --character to reset just one")
    p.add_argument("--unprotect", action="store_true",
                   help="Unlock protected characters (ones with external edits, e.g. "
                        "PalMod) so the randomizer may overwrite them; combine with "
                        "--character to unlock just one")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress the per-character assignment list (still written to last_run.txt)")
    p.add_argument("--gallery-download", action="store_true",
                   help="Download/update skins from gallery (merges new, keeps existing)")
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

    # Resolve --character filter (also scopes --reset-palettes / --unprotect)
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

    # Handle --restore
    if args.reset_palettes:
        return 0 if reset_palettes(arc_path, char_filter_id) else 1

    # Handle --unprotect
    if args.unprotect:
        return 0 if unprotect_palettes(arc_path, char_filter_id) else 1

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

    print("=" * 60)
    print("MvC2 Palette Randomizer")
    print("=" * 60)
    print(f"Game:  {arc_path}")
    print(f"Skins: {skins_dir}")
    if char_filter:
        print(f"Filter: {char_filter}")
    print()

    # Read ROM from the live arc so non-palette mods (stages etc.) are preserved.
    # Palette bytes are overwritten at absolute offsets, so prior randomization
    # doesn't affect the result.
    if not args.dry_run:
        print("Reading game archive...")
        rom = read_arc(arc_path)
        validate_rom(rom)
    else:
        rom = None

    total_assigned = 0
    total_locked = 0
    run_log = []  # collected for last_run.txt

    rejected = load_rejected_skins()   # palettes the user marked 'delete' — skip them

    # Vanilla palette data, used when a character has fewer palettes than
    # buttons: their block is reset to stock first so leftover slots show the
    # default colors instead of accumulating past runs' assignments.
    try:
        vanilla = load_vanilla_palettes()
    except Exception:
        vanilla = None

    # External-edit protection (PalMod etc.): a character whose current block
    # matches neither vanilla nor what we last wrote was edited by something
    # else - leave it completely alone and tell the user.
    state = load_palette_state()
    van_hash = vanilla_hashes(vanilla) if vanilla else {}
    protected_kept = []     # previously-protected characters skipped this run
    newly_protected = []    # external edits detected on this run

    for cid in sorted(PLAYABLE_CHARS, key=lambda c: CHARACTERS[c]):
        char_name = CHARACTERS[cid]
        sname = safe_name(char_name)
        folder_name = CHAR_ID_TO_FOLDER.get(cid, sname)

        # Apply character filter
        if char_filter_id is not None and cid != char_filter_id:
            continue

        # Skip characters with protected (externally edited) palettes
        if folder_name in state["protected"]:
            protected_kept.append(folder_name)
            run_log.append(char_name)
            run_log.append("  (protected - existing edits kept)")
            run_log.append("")
            continue
        if rom is not None and cid in van_hash:
            cur = block_hash(rom, cid)
            if cur != van_hash[cid] and cur != state["written"].get(folder_name):
                state["protected"].append(folder_name)
                newly_protected.append(folder_name)
                run_log.append(char_name)
                run_log.append("  (existing edits detected - now protected, kept as-is)")
                run_log.append("")
                continue

        # Collect PNG files (excluding palettes the user rejected)
        skin_folder = os.path.join(skins_dir, folder_name)
        pngs = []
        if os.path.isdir(skin_folder):
            pngs = sorted(f for f in os.listdir(skin_folder)
                          if f.lower().endswith(".png") and f.lower() not in rejected)

        # Fewer palettes than buttons (possibly none): reset this character to
        # vanilla first, then fill what we can.
        if len(pngs) < len(BUTTON_NAMES):
            if not args.dry_run and vanilla and cid in vanilla:
                start = STEAM_PALETTE_OFFSETS[cid]
                block = vanilla[cid]
                rom[start:start + len(block)] = block
                state["written"].pop(folder_name, None)   # vanilla = clean slate
            if not pngs:
                run_log.append(char_name)
                run_log.append("  (no palettes - reset to vanilla)")
                run_log.append("")
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

        # Assign random skins to unlocked buttons. With a short pool, shuffle
        # which buttons get them so the vanilla slots vary run to run.
        if len(pngs) < len(random_buttons):
            random.shuffle(random_buttons)
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
                else:
                    # Short pool — this button keeps its vanilla palette.
                    btn_log.append(f"  {btn_name}: (vanilla)")

        if btn_log:
            if not args.quiet:
                print(f"{char_name}")
            run_log.append(char_name)
            for line in btn_log:
                if not args.quiet:
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

            # Write extras derived from specific palette slots (e.g. rockets)
            if any_applied and not args.dry_run and cid in EXTRAS_SLOT_ENTRIES:
                slot_cache = {}
                for entry_idx, btn_idx, segments in EXTRAS_SLOT_ENTRIES[cid]:
                    pal_btn = btn_idx if btn_idx is not None else 0
                    colors = [(0, 0, 0)] * 16
                    for seg_start, seg_end, src_slot in segments:
                        key = (pal_btn, src_slot)
                        if key not in slot_cache:
                            slot_cache[key] = read_palette(
                                rom, cid, pal_btn, src_slot)
                        colors[seg_start:seg_end] = (
                            slot_cache[key][seg_start:seg_end])
                    write_palette_at(rom, cid, entry_idx, colors)

            # Remember what we wrote so later external edits can be detected.
            if not args.dry_run:
                state["written"][folder_name] = block_hash(rom, cid)

    print()

    if newly_protected:
        print("NOTICE: Existing palette edits (PalMod or similar) detected for:")
        print("  " + ", ".join(newly_protected))
        print("  These characters are now PROTECTED - the randomizer will leave")
        print("  them alone. To include them again, reset them to vanilla or")
        print("  unlock them (app: Randomize tab / CLI: --unprotect).")
        print()
    if protected_kept:
        print(f"Protected characters kept as-is: {', '.join(protected_kept)}")
        print()

    if args.dry_run:
        print(f"[dry-run] Would randomize {total_assigned} characters"
              f" ({total_locked} slots locked)")
        print("No files were modified.")
    else:
        print(f"Writing modified archive...")
        write_arc(arc_path, rom)
        save_palette_state(state)
        # Save assignment log so user can check what was applied
        with open(LAST_RUN_LOG, "w") as f:
            f.write("\n".join(run_log) + "\n")
        print(f"Done! Randomized {total_assigned} characters"
              f" ({total_locked} slots locked)")
        print(f"Assignments saved to: {LAST_RUN_LOG}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
