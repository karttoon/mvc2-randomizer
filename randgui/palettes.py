#!/usr/bin/env python3
"""palettes.py - palette-collection helpers for the gallery tab.

Reads/writes the same gallery_verdicts.json the CLI engine honours:
  { "Character/filename.png": "keep" | "delete" }
'delete' means the palette is excluded from randomization (and skipped by future
downloads). Files stay on disk until explicitly removed, so rejecting is
reversible. Saved with indent=1, sort_keys=True to match gallery.py.
"""
import os, json

from PIL import Image

import config

REJECT = "delete"
KEEP = "keep"


def load_verdicts():
    try:
        if os.path.exists(config.VERDICTS_JSON):
            with open(config.VERDICTS_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_verdicts(verdicts):
    with open(config.VERDICTS_JSON, "w", encoding="utf-8") as f:
        json.dump(verdicts, f, indent=1, sort_keys=True)


def key_for(char, filename):
    return f"{char}/{filename}"


def char_folders():
    """Character subfolders that actually contain palettes."""
    root = config.SKINS
    if not os.path.isdir(root):
        return []
    out = []
    for d in sorted(os.listdir(root)):
        full = os.path.join(root, d)
        if os.path.isdir(full) and any(
                f.lower().endswith(".png") for f in os.listdir(full)):
            out.append(d)
    return out


def list_palettes(char):
    d = os.path.join(config.SKINS, char)
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.lower().endswith(".png"))


def _load_rgba(char, filename):
    path = os.path.join(config.SKINS, char, filename)
    with Image.open(path) as im:
        return im.convert("RGBA")


def thumbnail(char, filename, width=180, bg=(45, 45, 45)):
    """A small RGB preview of a palette PNG, scaled to a target width."""
    im = _load_rgba(char, filename)
    scale = width / im.width
    im = im.resize((max(1, width), max(1, int(im.height * scale))), Image.NEAREST)
    canvas = Image.new("RGBA", im.size, bg + (255,))
    canvas.alpha_composite(im)
    return canvas.convert("RGB")


def load_scaled(char, filename, max_w, max_h, bg=(45, 45, 45)):
    """RGB preview scaled to fit within (max_w, max_h), preserving aspect.

    Upscales pixel art with nearest-neighbour so it grows to fill the space
    instead of staying tiny."""
    im = _load_rgba(char, filename)
    scale = min(max_w / im.width, max_h / im.height) if max_w > 0 and max_h > 0 else 1.0
    scale = max(scale, 0.02)
    im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))),
                   Image.NEAREST)
    canvas = Image.new("RGBA", im.size, bg + (255,))
    canvas.alpha_composite(im)
    return canvas.convert("RGB")


def list_filtered(char, unreviewed_only, verdicts):
    """Palettes for a character, optionally only the ones with no verdict yet."""
    files = list_palettes(char)
    if unreviewed_only:
        files = [f for f in files if verdicts.get(key_for(char, f)) is None]
    return files


def unreviewed_by_char(verdicts=None):
    """{character: count of palettes with no verdict yet} for every character."""
    v = verdicts if verdicts is not None else load_verdicts()
    return {c: sum(1 for f in list_palettes(c) if v.get(key_for(c, f)) is None)
            for c in char_folders()}


def counts():
    """(total_palettes, rejected_count) across the whole collection."""
    total = config.skins_count()
    v = load_verdicts()
    rejected = sum(1 for x in v.values() if x == REJECT)
    return total, rejected


def remove_rejected():
    """Delete files marked 'delete' from disk (keeps the verdict so re-download
    won't bring them back). Returns the number of files removed."""
    v = load_verdicts()
    removed = 0
    for key, verdict in v.items():
        if verdict != REJECT:
            continue
        path = os.path.join(config.SKINS, *key.split("/"))
        try:
            if os.path.isfile(path):
                os.remove(path)
                removed += 1
        except Exception:
            pass
    return removed
