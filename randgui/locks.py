#!/usr/bin/env python3
"""locks.py - read/write skin_locks.txt for the "Lock Palettes" tab.

The CLI engine (mvc2_randomizer.load_skin_locks) reads a plain-text file where
each line is "Folder BUTTON=filename.png" (or "=null" to randomize that slot).
The GUI edits one character's six slots at a time; we load the whole file, patch
that character, and write it all back in the same canonical layout the engine
generates - so hand edits to other characters are preserved.
"""
import os, sys

import config

BUTTONS = ["LP", "LK", "HP", "HK", "A1", "A2"]
# Friendly labels shown next to the raw button names in the UI.
BUTTON_LABELS = {
    "LP": "Light Punch", "LK": "Light Kick",
    "HP": "Heavy Punch", "HK": "Heavy Kick",
    "A1": "Assist 1", "A2": "Assist 2",
}
RANDOM = "(random)"     # UI label for an unlocked slot (stored as null)


def _all_folders():
    """Canonical character-folder list from the engine data (case-insensitive sort).

    Mirrors the CLI's folder derivation: safe_name(CHARACTERS[cid]) for every
    playable character.
    """
    for p in (config.resource_dir(), config.app_dir()):
        if p not in sys.path:
            sys.path.insert(0, p)
    from mvc2_data.characters import CHARACTERS, PLAYABLE_CHARS, safe_name
    return sorted((safe_name(CHARACTERS[cid]) for cid in PLAYABLE_CHARS), key=str.lower)


def load_locks():
    """Return {(folder, BUTTON): filename or None} parsed from skin_locks.txt."""
    locks = {}
    path = config.LOCKS_TXT
    if not os.path.isfile(path):
        return locks
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            left, value = line.split("=", 1)
            parts = left.strip().rsplit(" ", 1)
            if len(parts) != 2:
                continue
            folder, btn = parts[0], parts[1].upper()
            if btn not in BUTTONS:
                continue
            value = value.strip()
            locks[(folder, btn)] = None if (not value or value.lower() == "null") else value
    return locks


def char_locks(char, locks=None):
    """The six-slot mapping for one character: {BUTTON: filename or None}."""
    locks = load_locks() if locks is None else locks
    return {b: locks.get((char, b)) for b in BUTTONS}


def set_char_locks(char, mapping):
    """Patch one character's six slots (mapping is {BUTTON: filename or None})
    and rewrite the whole file; every other character is left untouched."""
    locks = load_locks()
    for b in BUTTONS:
        locks[(char, b)] = mapping.get(b)
    _write(locks)


def clear_char(char):
    set_char_locks(char, {b: None for b in BUTTONS})


def count_locked(char, locks=None):
    return sum(1 for b in BUTTONS if char_locks(char, locks)[b])


def _write(locks):
    folders = _all_folders()
    # Preserve any folder present in the file that isn't in the canonical list.
    extra = sorted({f for (f, _b) in locks} - set(folders), key=str.lower)
    lines = [
        "# MvC2 Palette Randomizer - Skin Locks",
        "#",
        "# Each line is: Character_Name BUTTON=filename.png",
        "# Set a filename to lock that palette to that button slot.",
        '# Leave as "null" to randomize that slot each run.',
        "#",
        "# Normally edited from the app's 'Lock Palettes' tab.",
        "",
    ]
    for folder in folders + extra:
        for btn in BUTTONS:
            val = locks.get((folder, btn))
            lines.append(f"{folder} {btn}={val if val else 'null'}")
        lines.append("")
    with open(config.LOCKS_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
