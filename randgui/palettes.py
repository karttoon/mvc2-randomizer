#!/usr/bin/env python3
"""palettes.py - palette-collection helpers for the gallery tab.

Reads/writes the same gallery_verdicts.json the CLI engine honours:
  { "Character/filename.png": "keep" | "delete" }
'delete' means the palette is excluded from randomization (and skipped by future
downloads). Files stay on disk until explicitly removed, so rejecting is
reversible. Saved with indent=1, sort_keys=True to match gallery.py.
"""
import os, json, math, colorsys

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


_color_key_cache = {}   # (char, filename) -> (mtime, key)
_core_colors = None     # hand-picked {char: {primary, secondary}} (lazy-loaded)

# Achromatic thresholds shared by both sort paths
_GRAY_S = 0.16
_GRAY_V = 0.14


def _load_core_colors():
    """Hand-curated primary/secondary palette index per character, picked with
    the (local, non-repo) core-color-picker tool. Missing file -> {}."""
    global _core_colors
    if _core_colors is None:
        _core_colors = {}
        for base in (os.path.dirname(os.path.abspath(__file__)),
                     config.resource_dir()):
            p = os.path.join(base, "core_colors.json")
            try:
                if os.path.isfile(p):
                    with open(p, "r", encoding="utf-8") as f:
                        _core_colors = json.load(f)
                    break
            except Exception:
                pass
    return _core_colors


def _hsv(pal, idx):
    r, g, b = pal[idx * 3: idx * 3 + 3]
    return colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)


def _subkey(h, s, v):
    """Continuous perceptual ordering: achromatics by lightness, then hues."""
    if s < _GRAY_S or v < _GRAY_V:
        return (0, 0, v, s)
    return (1, int(h * 16), v, s)


def color_sort_key(char, filename):
    """Perceptual sort key so similar-looking palettes end up adjacent.

    Sheets are index-standardized, so palette index N is the same body part in
    every file for a character. When the character has hand-picked core
    indices (core_colors.json), the PRIMARY color decides a coarse bucket
    (achromatic lightness tier, or one of 16 hue bands), the SECONDARY color
    orders files inside the bucket, and exact primary shade breaks ties -
    neighborhood / street / house number. Characters without a mapping fall
    back to the old most-pixels heuristic. Unreadable files sort last.
    """
    path = os.path.join(config.SKINS, char, filename)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return (2, 0, 0.0, 0.0)
    hit = _color_key_cache.get((char, filename))
    if hit and hit[0] == mtime:
        return hit[1]
    key = (2, 0, 0.0, 0.0)
    try:
        with Image.open(path) as im:
            is_p = im.mode == "P"
            if not is_p:
                im = im.convert("P", palette=Image.ADAPTIVE)
            hist = im.histogram()
            pal = im.getpalette() or []
        core = _load_core_colors().get(char) if is_p else None
        if core and len(pal) >= 48:
            hp, sp, vp = _hsv(pal, core["primary"])
            if sp < _GRAY_S or vp < _GRAY_V:
                bucket = (0, int(vp * 5))        # gray tier
            else:
                bucket = (1, int(hp * 16))       # hue band
            sec = core.get("secondary")
            seckey = _subkey(*_hsv(pal, sec)) if sec else (0, 0, 0.0, 0.0)
            key = bucket + seckey + (vp, sp, hp)
        else:
            counts = hist[1:16]                  # body-row indices only
            if any(counts) and len(pal) >= 48:
                dom = 1 + max(range(15), key=lambda i: counts[i])
                key = _subkey(*_hsv(pal, dom))
    except Exception:
        pass
    _color_key_cache[(char, filename)] = (mtime, key)
    return key


# ---------------------------------------------------------------------------
# Similarity-tour ordering ("Sort by color")
#
# Greedy nearest-neighbor chain over a perceptual whole-palette distance:
# every palette index the sprite uses, weighted by its pixel coverage,
# compared in CIELAB. Each palette in the ordering is the closest unvisited
# neighbor of the one before it, so similar-looking palettes form runs -
# regardless of which colors make them similar.
# ---------------------------------------------------------------------------

_tour_weights = {}    # char -> {palette index: pixel-coverage weight}
_tour_feats = {}      # (char, filename) -> (mtime, {index: (L, a, b)} | None)


def _srgb_to_lab(r, g, b):
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    rl, gl, bl = lin(r), lin(g), lin(b)
    x = (0.4124 * rl + 0.3576 * gl + 0.1805 * bl) / 0.95047
    y = 0.2126 * rl + 0.7152 * gl + 0.0722 * bl
    z = (0.0193 * rl + 0.1192 * gl + 0.9505 * bl) / 1.08883
    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def _char_weights(char, files):
    """Coverage weight per palette index - the sheets share one base layout,
    so the first readable file defines it for the character."""
    w = _tour_weights.get(char)
    if w is not None:
        return w
    for fn in files:
        try:
            with Image.open(os.path.join(config.SKINS, char, fn)) as im:
                if im.mode != "P":
                    continue
                hist = im.histogram()
        except Exception:
            continue
        w = {i: hist[i] for i in range(256) if hist[i] > 0 and i % 16 != 0}
        total = sum(w.values())
        if total:
            w = {i: c / total for i, c in w.items()}
            _tour_weights[char] = w
            return w
    return {}


def _tour_feat(char, fn, weights):
    path = os.path.join(config.SKINS, char, fn)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _tour_feats.get((char, fn))
    if hit and hit[0] == mtime:
        return hit[1]
    feat = None
    try:
        with Image.open(path) as im:
            pal = im.getpalette() if im.mode == "P" else None
        if pal:
            feat = {i: _srgb_to_lab(*pal[i * 3:i * 3 + 3]) for i in weights}
    except Exception:
        pass
    _tour_feats[(char, fn)] = (mtime, feat)
    return feat


def sort_similarity(char, files):
    """Order files as a similarity tour; unreadable files go last.
    Falls back to the key-based sort if features can't be built."""
    if len(files) < 3:
        return list(files)
    weights = _char_weights(char, files)
    if not weights:
        return sorted(files, key=lambda f: color_sort_key(char, f))
    witems = list(weights.items())
    feats = {fn: _tour_feat(char, fn, weights) for fn in files}
    good = [fn for fn in files if feats[fn]]
    bad = [fn for fn in files if not feats[fn]]
    if len(good) < 3:
        return good + bad

    def d2(a, b):                      # squared distance (ordering-equivalent)
        fa, fb = feats[a], feats[b]
        s = 0.0
        for i, w in witems:
            la, lb = fa[i], fb[i]
            s += w * ((la[0] - lb[0]) ** 2 + (la[1] - lb[1]) ** 2
                      + (la[2] - lb[2]) ** 2)
        return s

    pd = {}
    for i, a in enumerate(good):
        for b in good[i + 1:]:
            pd[(a, b)] = pd[(b, a)] = d2(a, b)
    # Start from the most isolated palette so outliers anchor the chain ends.
    start = max(good, key=lambda f: sum(math.sqrt(pd[(f, o)])
                                        for o in good if o != f))
    left = set(good)
    left.discard(start)
    tour = [start]
    while left:
        nxt = min(left, key=lambda f: pd[(tour[-1], f)])
        tour.append(nxt)
        left.discard(nxt)
    return tour + bad


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
