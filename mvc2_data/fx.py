"""FX (effect palette) randomization for MvC2.

Recolors the named move/projectile/super/intro effect palettes catalogued in
mvc2_data/fx_targets.json. Each sheet carries a curation verdict (accept/reject/
exclude), a skin-accessory flag (fromPalette), and a default_on flag
(accept AND not skin-driven). A global MODE decides how every enabled sheet is
transformed; the HSL math mirrors the FX Lab prototype exactly.

Per-button sheets (slot 0-7) are recoloured in all six button blocks
(entry = button*8 + slot), each from its own vanilla colours. Shared/EXTRAS
sheets (slot >= 8) are a single entry. Alpha is preserved per colour, so
mid-palette transparent entries stay transparent. Source colours always come
from the bundled vanilla block, so FX is deterministic and independent of
whatever skin landed on the character this run.
"""

import functools
import json
import math
import os
import struct
import zlib

from .steam import STEAM_PALETTE_OFFSETS, load_vanilla_palettes, read_palette
from .characters import safe_name

FX_MODES = ["Default", "Stock", "Shades", "Opposite", "Rainbow", "Rainbow Rev",
            "Grayscale", "Invert", "Chaos", "Thermal", "Night Vision", "Neon"]
# "Default" = no FX recolor (effects keep whatever the skins left there).
# "Stock"   = actively write the game's original effect colors (as a lock this
#             pins one effect to stock even while a global mode is active).
# "Thermal" / "Night Vision" are fixed looks (fire / green phosphor ramps);
# "Neon" rebuilds each colour group as a neon-tube glow (see NEON_COLORS).

# Modes that read the character's core colour (vs. palette-independent globals).
FX_AWARE_MODES = {"Shades"}

# Retired mode names still found in older configs/locks -> their replacement.
_LEGACY_MODES = {"Match": "Shades"}

# Gradient boost strengths (post-pass that re-spreads perceived lightness).
GRADIENT_LEVELS = {
    "Off": None,
    "Normal": {"lo": 14.0, "hi": 96.0, "mix": 0.7, "drift": 26.0},
    "Strong": {"lo": 8.0, "hi": 97.0, "mix": 0.9, "drift": 32.0},
}
GRADIENT_DEFAULT = "Normal"

# Neon tube hues. "Auto" snaps each colour group to the nearest one.
NEON_COLORS = {"Auto": None, "Pink": 328, "Blue": 192, "Lime": 98,
               "Yellow": 56, "Orange": 24, "Purple": 278, "Red": 352}


def canon_mode(m):
    """Map a retired mode name to its current equivalent (Match -> Shades)."""
    return _LEGACY_MODES.get(m, m)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FX_TARGETS_FILE = os.path.join(_ROOT, "mvc2_data", "fx_targets.json")
FX_SPRITES_FILE = os.path.join(_ROOT, "mvc2_data", "fx_sprites.bin")
FX_BODYSPRITES_FILE = os.path.join(_ROOT, "mvc2_data", "fx_bodysprites.bin")
_CORE_COLORS_FILE = os.path.join(_ROOT, "randgui", "core_colors.json")


def load_fx_targets(path=FX_TARGETS_FILE):
    """Load the per-character FX target catalogue, or {} if missing."""
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def load_core_colors(path=_CORE_COLORS_FILE):
    """Load core_colors.json ({safe_name: {primary, secondary}}), or {}."""
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def load_fx_sprites(path=FX_SPRITES_FILE):
    """Load the bundled effect-sprite blob -> {spr_key: (w, h, index_bytes)}.

    index_bytes is one palette index (0-15) per pixel, row-major. Empty {} if
    the blob is missing (the FX tab then falls back to color swatches).
    """
    out = {}
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"FXS1":
                return out
            (n,) = struct.unpack("<I", f.read(4))
            for _ in range(n):
                (kl,) = struct.unpack("<H", f.read(2))
                key = f.read(kl).decode("utf-8")
                w, h = struct.unpack("<HH", f.read(4))
                (dl,) = struct.unpack("<I", f.read(4))
                idx = zlib.decompress(f.read(dl))
                out[key] = (w, h, idx)
    except (OSError, ValueError, struct.error, zlib.error):
        pass
    return out


def load_fx_bodysprites(path=FX_BODYSPRITES_FILE):
    """Bundled character body sprites -> {uid(int): (w, h, index_bytes)}."""
    out = {}
    for k, v in load_fx_sprites(path).items():
        try:
            out[int(k)] = v
        except (TypeError, ValueError):
            pass
    return out


# --- HSL helpers (ported 1:1 from the FX Lab prototype) --------------------

def _rgb2hsl(r, g, b):
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(r, g, b), min(r, g, b)
    l = (mx + mn) / 2.0
    if mx == mn:
        return (0.0, 0.0, l)
    d = mx - mn
    s = d / (2.0 - mx - mn) if l > 0.5 else d / (mx + mn)
    if mx == r:
        h = (g - b) / d + (6.0 if g < b else 0.0)
    elif mx == g:
        h = (b - r) / d + 2.0
    else:
        h = (r - g) / d + 4.0
    return (h * 60.0, s, l)


def _hue(p, q, t):
    if t < 0:
        t += 1
    if t > 1:
        t -= 1
    if t < 1 / 6:
        return p + (q - p) * 6 * t
    if t < 1 / 2:
        return q
    if t < 2 / 3:
        return p + (q - p) * (2 / 3 - t) * 6
    return p


def _hsl2rgb(h, s, l):
    h = (((h % 360) + 360) % 360) / 360.0
    if s == 0:
        r = g = b = l
    else:
        q = l * (1 + s) if l < 0.5 else l + s - l * s
        p = 2 * l - q
        r = _hue(p, q, h + 1 / 3)
        g = _hue(p, q, h)
        b = _hue(p, q, h - 1 / 3)
    return (round(r * 255), round(g * 255), round(b * 255))


def _seeded(n):
    x = math.sin(n * 127.1 + 311.7) * 43758.5453
    return x - math.floor(x)


def _base_hsl(body, primary):
    """Core-colour hue/sat for palette-aware modes.

    body: list of 16 (r, g, b). Uses the curated primary index when it's
    saturated enough, else falls back to the most vivid body colour.
    """
    if 0 <= primary < len(body):
        h = _rgb2hsl(*body[primary])
        if h[1] >= 0.08:
            return h
    best, best_score = (0.0, 0.0, 0.5), -1.0
    for c in body:
        h = _rgb2hsl(*c)
        score = h[1] * (1 - abs(h[2] - 0.5))
        if score > best_score:
            best_score, best = score, h
    return best


# --- palette byte helpers (ARGB4444 LE, alpha preserved) -------------------

def _read_entry(block, entry):
    """16 (r, g, b, a) tuples from a 32-byte palette entry in a vanilla block."""
    off = entry * 32
    out = []
    for i in range(16):
        v = block[off + i * 2] | (block[off + i * 2 + 1] << 8)
        a = (v >> 12) & 0xF
        r = ((v >> 8) & 0xF) * 17
        g = ((v >> 4) & 0xF) * 17
        b = (v & 0xF) * 17
        out.append((r, g, b, a))
    return out


def _write_entry(rom, base, entry, cols):
    off = base + entry * 32
    for i, (r, g, b, a) in enumerate(cols):
        v = ((a & 0xF) << 12) | ((r // 17) << 8) | ((g // 17) << 4) | (b // 17)
        rom[off + i * 2] = v & 0xFF
        rom[off + i * 2 + 1] = (v >> 8) & 0xFF


def _transform(cols, mode, base_h, base_s, char_seed, chaos_seed=0):
    """Base recolour of one palette (list of (r,g,b,a)); transparent colours
    untouched. This is the raw per-colour step - recolor() adds the gradient
    boost and the fixed looks (Thermal / Night Vision / Neon) on top."""
    mode = canon_mode(mode)
    opaque = [i for i, c in enumerate(cols) if c[3] > 0]
    n = len(opaque)
    out = []
    for idx, (r, g, b, a) in enumerate(cols):
        if a == 0:
            out.append((r, g, b, a))
            continue
        h, s, l = _rgb2hsl(r, g, b)
        if mode == "Shades":
            # bold single-colour ramp centred on the base: hue drifts with
            # lightness and contrast is expanded, so the gradient is obvious.
            drift = (l - 0.5) * 50.0
            cl = max(0.0, min(1.0, 0.5 + (l - 0.5) * 1.35))
            nr = _hsl2rgb(base_h + drift, max(base_s, 0.55), cl)
        elif mode == "Opposite":
            nr = _hsl2rgb(h + 180, s, l)
        elif mode == "Rainbow":
            f = opaque.index(idx) / (n - 1) if n > 1 else 0
            nr = _hsl2rgb(f * 330, max(s, 0.55), l)
        elif mode == "Rainbow Rev":
            f = 1 - (opaque.index(idx) / (n - 1)) if n > 1 else 0
            nr = _hsl2rgb(f * 330, max(s, 0.55), l)
        elif mode == "Grayscale":
            nr = _hsl2rgb(0, 0, l)
        elif mode == "Invert":
            nr = (255 - r, 255 - g, 255 - b)
        elif mode == "Chaos":
            nr = _hsl2rgb(_seeded(char_seed + chaos_seed * 131.1 + idx * 7.3) * 360,
                          max(s, 0.4), l)
        else:                                   # Stock (and unknown): unchanged
            nr = (r, g, b)
        out.append((nr[0], nr[1], nr[2], a))
    return out


# --- perceptual lightness (CIE L*) ------------------------------------------
# HSL lightness is not what the eye sees (green/yellow read far brighter than
# blue at the same L), which is why recolours used to wash out. The gradient
# boost spaces colours evenly in CIE L* instead.

def _lin(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


@functools.lru_cache(maxsize=65536)
def _lstar(r, g, b):
    y = 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)
    return 116 * (y ** (1 / 3)) - 16 if y > 0.008856 else 903.3 * y


def _solve_l(h, s, target):
    """HSL lightness giving perceived lightness L* == target (bisection)."""
    lo, hi = 0.0, 1.0
    for _ in range(18):
        mid = (lo + hi) / 2
        if _lstar(*_hsl2rgb(h, s, mid)) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _cluster_refs(cols, gap=50.0, min_s=0.15):
    """Group a palette's chromatic colours into hue clusters (split where the
    circular gap between neighbouring hues exceeds `gap`). One palette can hold
    two effects (e.g. Cable: blue beam + yellow flash). Returns {index: the
    cluster's saturation-weighted mean hue}; whites/greys are left out."""
    pts = []
    for i, c in enumerate(cols):
        if c[3] > 0:
            h, s, _l = _rgb2hsl(c[0], c[1], c[2])
            if s >= min_s:
                pts.append((h, s, i))
    if not pts:
        return {}
    pts.sort()
    gaps = [((pts[(k + 1) % len(pts)][0] - pts[k][0]) % 360, k)
            for k in range(len(pts))]
    big = [k for g, k in gaps if g > gap] or [max(gaps)[1]]
    refs = {}
    for n, k in enumerate(big):
        end = big[(n + 1) % len(big)]
        grp, j = [], (k + 1) % len(pts)
        while True:
            grp.append(pts[j])
            if j == end:
                break
            j = (j + 1) % len(pts)
        sx = sum((p[1] + .05) * math.cos(math.radians(p[0])) for p in grp)
        sy = sum((p[1] + .05) * math.sin(math.radians(p[0])) for p in grp)
        m = math.degrees(math.atan2(sy, sx)) % 360
        for p in grp:
            refs[p[2]] = m
    return refs


def _ranks(stock, out):
    """Perceptual rank 0..1 per opaque colour, within its stock hue cluster.
    Ordered by the OUTPUT's lightness (stock lightness breaks ties), so a mode
    that reverses order (Invert) stays reversed. Neutrals rank globally."""
    idxs = [i for i, c in enumerate(stock) if c[3] > 0]
    refs = _cluster_refs(stock)
    groups = {}
    for i in idxs:
        groups.setdefault(round(refs[i], 1) if i in refs else None, []).append(i)
    key = {i: (round(_lstar(*out[i][:3])), round(_lstar(*stock[i][:3])))
           for i in idxs}
    allk = sorted(set(key.values()))
    t = {}
    for g, mem in groups.items():
        ks = allk if g is None else sorted({key[i] for i in mem})
        n = max(1, len(ks) - 1)
        for i in mem:
            t[i] = ks.index(key[i]) / n if len(ks) > 1 else 0.6
    return t


def _boost(stock, out, lo, hi, mix, drift):
    """Gradient boost: re-spread perceived lightness by rank (blended `mix` with
    the mode's own), drift hue across the ramp and soften highlights - the cues
    pixel art uses to read as a gradient instead of one flat tone."""
    t = _ranks(stock, out)
    res = list(out)
    for i, tt in t.items():
        r, g, b, a = out[i]
        h, s, _l = _rgb2hsl(r, g, b)
        tgt = mix * (lo + (hi - lo) * tt) + (1 - mix) * _lstar(r, g, b)
        if s >= 0.08:
            h += drift * (tt - 0.5)
            if tt > 0.75:
                s *= 1 - 0.45 * (tt - 0.75) / 0.25
        c = _hsl2rgb(h, s, _solve_l(h, s, tgt))
        res[i] = (c[0], c[1], c[2], a)
    return res


_THERMAL = [(0, (0, 0, 0)), (.22, (70, 0, 110)), (.42, (200, 15, 30)),
            (.62, (255, 120, 0)), (.82, (255, 225, 50)), (1, (255, 255, 235))]
_NIGHT_VISION = [(0, (0, 8, 0)), (.3, (6, 70, 12)), (.6, (40, 175, 45)),
                 (.85, (150, 255, 120)), (1, (225, 255, 215))]
# neon glow by rank: (t, HSL lightness, saturation) - deep edge -> tube -> core
_NEON_GLOW = [(0.0, .14, 1.0), (.35, .42, 1.0), (.65, .56, 1.0),
              (.86, .80, .9), (1.0, .96, .55)]


def _ramp(stops, t):
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if t <= b:
            u = (t - a) / (b - a) if b > a else 0
            return tuple(round(ca[k] + (cb[k] - ca[k]) * u) for k in range(3))
    return stops[-1][1]


def _themed(stock, stops):
    """Fixed-palette look: each colour's perceptual rank mapped onto a ramp."""
    out = list(stock)
    for i, tt in _ranks(stock, stock).items():
        out[i] = _ramp(stops, tt) + (stock[i][3],)
    return out


def _neon_hue(h):
    hues = [v for v in NEON_COLORS.values() if v is not None]
    return min(hues, key=lambda n: min(abs(h - n) % 360, 360 - abs(h - n) % 360))


def _neon(stock, force_hue=None):
    """Neon tube glow: each colour group snaps to the nearest neon hue (or the
    forced one) and is rebuilt deep edge -> vivid tube -> white-hot core."""
    refs = _cluster_refs(stock)
    out = list(stock)
    for i, tt in _ranks(stock, stock).items():
        r, g, b, a = stock[i]
        h, s, l = _rgb2hsl(r, g, b)
        if i not in refs and s < 0.15:              # whites/greys -> white core
            c = _hsl2rgb(0, 0, max(l, 0.9) if l > 0.6 else l)
        else:
            ref = refs.get(i, h)
            nh = force_hue if force_hue is not None else _neon_hue(ref)
            ll = ss = None
            for (a0, l0, s0), (b0, l1, s1) in zip(_NEON_GLOW, _NEON_GLOW[1:]):
                if tt <= b0:
                    u = (tt - a0) / (b0 - a0) if b0 > a0 else 0
                    ll, ss = l0 + (l1 - l0) * u, s0 + (s1 - s0) * u
                    break
            if ll is None:
                ll, ss = _NEON_GLOW[-1][1:]
            c = _hsl2rgb(nh + (h - ref) * 0.25, ss, ll)
        out[i] = (c[0], c[1], c[2], a)
    return out


@functools.lru_cache(maxsize=8192)
def _recolor_cached(cols, mode, base_h, base_s, char_seed, seed, gradient, neon):
    cols = list(cols)
    if mode == "Thermal":
        return tuple(_themed(cols, _THERMAL))
    if mode == "Night Vision":
        return tuple(_themed(cols, _NIGHT_VISION))
    if mode == "Neon":
        return tuple(_neon(cols, NEON_COLORS.get(neon)))
    out = _transform(cols, mode, base_h, base_s, char_seed, seed)
    lvl = GRADIENT_LEVELS.get(gradient)
    if lvl and mode not in ("Default", "Stock"):
        out = _boost(cols, out, **lvl)
    return tuple(out)


def recolor(cols, mode, base_h=0.0, base_s=0.0, char_seed=0, seed=0,
            gradient=GRADIENT_DEFAULT, neon="Auto"):
    """Full recolour of one palette for `mode`: the base transform plus the
    gradient boost, or a fixed look (Thermal / Night Vision / Neon). Used by the
    randomizer and both GUI previews so what you see is what lands in game."""
    mode = canon_mode(mode)
    if mode in (None, "Default", "Stock"):
        return list(cols)
    if gradient not in GRADIENT_LEVELS:
        gradient = GRADIENT_DEFAULT
    return list(_recolor_cached(tuple(tuple(c) for c in cols), mode,
                                round(base_h, 3), round(base_s, 3), char_seed,
                                seed, gradient, neon or "Auto"))


def _char_seed(name):
    return len(name) + sum(ord(ch) for ch in name)


def parse_color(c):
    """'#rrggbb' (or an (r, g, b) tuple) -> (r, g, b), else None."""
    if not c:
        return None
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        return tuple(int(v) for v in c[:3])
    try:
        s = str(c).lstrip("#")
        if len(s) != 6:
            return None
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return None


def color_hsl(c):
    """HSL anchor for a global base color (None when unset/invalid)."""
    rgb = parse_color(c)
    return _rgb2hsl(*rgb) if rgb else None


def _lock_setting(st, char_base_idx):
    """(mode, base_idx, seed) from one lock-settings dict, or None if it's not a
    real recolor (missing / Default / Follow global)."""
    m = canon_mode((st or {}).get("mode"))
    if not m or m in ("Default", "Follow global") or m not in FX_MODES:
        return None
    base = st.get("base")
    return (m, char_base_idx if base is None else base, int(st.get("seed", 0) or 0))


def _resolve_settings(lk, global_mode, global_seed, enabled, s, char_base_idx):
    """Effective (mode, base_idx, seed) for one sheet, or None to leave it
    stock. A per-effect lock wins; else the global mode (subject to the on/off
    enabled set)."""
    if lk and lk.get("mode"):
        return _lock_setting(lk, char_base_idx)       # locked (or locked off)
    global_mode = canon_mode(global_mode)
    if global_mode in (None, "Default") or global_mode not in FX_MODES:
        return None
    on = (s["id"] in enabled) if enabled is not None else s["default_on"]
    if not on:
        return None
    return (global_mode, char_base_idx, global_seed)


def apply_fx(rom, mode, targets=None, enabled=None, chaos_seed=0,
             only_uid=None, skip_uids=None, locks=None, applied_out=None, log=None, base_color=None,
             gradient=GRADIENT_DEFAULT, neon="Auto"):
    """Recolour FX palettes in the ROM in place.

    mode:       global mode for UNLOCKED sheets; "Default"/None leaves them stock.
    targets:    fx_targets dict (loaded from data/ if None).
    enabled:    optional set of sheet ids on for the GLOBAL mode (else default_on).
    chaos_seed: global Chaos reroll index (unlocked sheets).
    only_uid/skip_uids: character filters (CLI --character / protected palettes).
    locks:      {sheet_id: {"mode", "base"?, "seed"?}} - per-palette pinned
                settings that win over the global mode and are always applied.
    applied_out: optional dict filled {uid: [entry,...]} with entries written.
    base_color: optional global color ('#rrggbb'). When set, Shades anchors to
                it for every character instead of a palette index (a lock with
                its own explicit base index still wins).
    gradient:   gradient boost strength (GRADIENT_LEVELS: Off/Normal/Strong).
    neon:       Neon tube colour (NEON_COLORS key; "Auto" snaps per group).

    Returns the number of sheets changed.
    """
    locks = locks or {}
    mode = canon_mode(mode)
    global_hsl = color_hsl(base_color)
    if mode in (None, "Default") and not locks:
        return 0
    targets = targets if targets is not None else load_fx_targets()
    core = load_core_colors()
    vanilla = load_vanilla_palettes()
    skip_uids = skip_uids or set()
    count = 0

    for name, d in targets.items():
        uid = d["uid"]
        if only_uid is not None and uid != only_uid:
            continue
        if uid in skip_uids:
            continue
        if uid not in vanilla or uid not in STEAM_PALETTE_OFFSETS:
            continue
        base = STEAM_PALETTE_OFFSETS[uid]
        block = vanilla[uid]
        nentries = len(block) // 32
        cseed = _char_seed(name)

        # The character's base-colour index. It's read PER BUTTON from the
        # post-skin ROM so each button's FX matches its own applied skin; a
        # lock may override the index per sheet ("Lock base color").
        char_base = d.get("base_idx")
        if char_base is None:
            char_base = core.get(safe_name(name), {}).get("primary", 0)
        _base_cache = {}

        def button_base(btn, bidx):
            k = (btn, bidx)
            if k not in _base_cache:
                _base_cache[k] = _base_hsl(read_palette(rom, uid, btn, 0), bidx)
            return _base_cache[k]

        for s in d["sheets"]:
            lk = locks.get(s["id"])
            slot = s["slot"]
            sheet_per_btn = (s["tag"] == "per-button" and slot < 8)
            # Recolor every ROM entry that shares this effect's sprite image: its
            # per-button copies (core, <48) plus EXTRAS siblings/frames (>=48). A
            # core entry's button = entry//8; EXTRAS entries are shared (button 0).
            entries = s.get("entries")
            if not entries:
                entries = ([slot + b * 8 for b in range(6)] if sheet_per_btn
                           else [slot])
            wrote = False
            for e in entries:
                if not (0 <= e < nentries):
                    continue
                is_core = e < 48
                btn = (e // 8) if is_core else 0
                res = _resolve_settings(lk, mode, chaos_seed, enabled, s,
                                        char_base)
                if res is None:                 # this entry stays stock
                    continue
                smode, sbidx, sseed = res
                aware = smode in FX_AWARE_MODES
                lock_base = bool(lk and lk.get("mode") and lk.get("base") is not None)
                if not aware:
                    bh = (0.0, 0.0, 0.0)
                elif global_hsl and not lock_base:
                    bh = global_hsl
                else:
                    bh = button_base(btn, sbidx)
                cols = _read_entry(block, e)
                _write_entry(rom, base, e,
                             recolor(cols, smode, bh[0], bh[1], cseed, sseed,
                                     gradient, neon))
                wrote = True
                if applied_out is not None:
                    applied_out.setdefault(uid, []).append(e)
            if wrote:
                count += 1
                if log is not None:
                    tagtxt = f"{smode}{' (locked)' if lk else ''}"
                    log.append(f"  {name}: {s['label'][:46]} [{tagtxt}]")
    return count


def restore_entries(rom, applied, skip_uids=None, only_uid=None):
    """Write stock (vanilla) bytes back to the {uid: [entry, ...]} map.

    Used to revert a previous FX run before the next one (or before leaving FX
    off), so turning FX down doesn't strand last run's colours. Respects the
    same protected/character filters as apply_fx. Returns entries restored.
    """
    if not applied:
        return 0
    vanilla = load_vanilla_palettes()
    skip_uids = skip_uids or set()
    n = 0
    for uid, entries in applied.items():
        uid = int(uid)
        if uid in skip_uids or (only_uid is not None and uid != only_uid):
            continue
        if uid not in vanilla or uid not in STEAM_PALETTE_OFFSETS:
            continue
        base = STEAM_PALETTE_OFFSETS[uid]
        block = vanilla[uid]
        ne = len(block) // 32
        for e in entries:
            if 0 <= e < ne:
                rom[base + e * 32: base + e * 32 + 32] = block[e * 32:(e + 1) * 32]
                n += 1
    return n
