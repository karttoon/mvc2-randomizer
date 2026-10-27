"""Game check, backup support and restore for the MvC2 game file.

check(rom, ...) compares a game file with the stock Steam game, part by part:
  palettes  character body colours (palette rows that aren't effects)
  fx        effect colours (the palette rows listed in fx_targets.json)
  stages    the 17 stage slots (AFS entries 801-834)
  cpu       the CPU AI files (the Arcade pack's entries)
  other     every other file in the archive (sprites, sound, code ...) - the
            things only another tool would change

restore_parts(rom, parts, original_rom=None) puts chosen parts back to stock
or to an original copy of the file. Pure functions on decompressed ROMs: the
caller reads/writes the arc and keeps state files in step.
"""
import hashlib
import json
import os
import struct
import zlib

from . import ai_pack_apply as A
from . import stages as S
from .characters import CHARACTERS
from .steam import STEAM_PALETTE_OFFSETS, TOTAL_PALETTE_COUNT, load_vanilla_palettes

HERE = os.path.dirname(os.path.abspath(__file__))
STOCK_ENTRIES = os.path.join(HERE, "stock_entries.json")
STOCK_STAGES = os.path.join(HERE, "stock_stages.bin")
FX_TARGETS = os.path.join(HERE, "fx_targets.json")
PARTS = ("palettes", "fx", "stages", "cpu")

SLOT_NAMES = {"00": "Airship (day)", "01": "Desert (orange sky)", "02": "Factory",
              "03": "Carnival (summer)", "04": "Swamp", "05": "Cave (water)",
              "06": "Clock Tower (clear)", "07": "River on Ice", "08": "Abyss",
              "09": "Airship (night)", "0A": "Desert (blue sky)", "0B": "Training",
              "0C": "Carnival (winter)", "0D": "Swamp (Asian)", "0E": "Cave (lava)",
              "0F": "Clock Tower (snowy)", "10": "River on Raft"}


def _md5(b):
    return hashlib.md5(b).hexdigest()


def load_stock_entries():
    with open(STOCK_ENTRIES) as f:
        return json.load(f)


def load_stock_stages():
    """{entry index: bytes} of the stock stage files."""
    with open(STOCK_STAGES, "rb") as f:
        blob = f.read()
    n = struct.unpack_from("<I", blob, 0)[0]
    index = json.loads(blob[4:4 + n])
    data = zlib.decompress(blob[4 + n:])
    return {int(i): data[o:o + s] for i, (o, s) in index.items()}


def fx_rows():
    """{char id: set(palette row indices that hold effect colours)}."""
    with open(FX_TARGETS, encoding="utf-8") as f:
        d = json.load(f)
    out = {}
    for v in d.values():
        rows = out.setdefault(v["uid"], set())
        for sh in v.get("sheets", []):
            rows.update(sh.get("entries", []))
    return out


def _ai_indices():
    try:
        from . import arcade_ai
        return [e["idx"] for e in arcade_ai.load_pack()["entries"]]
    except Exception:
        return []


def _entries(rom):
    return [bytes(rom[S.AFS_BASE + o:S.AFS_BASE + o + s]) for o, s in S.parse_toc(rom)]


def _rows(block):
    return [block[i:i + 32] for i in range(0, len(block), 32)]


def check(rom, vanilla=None, stock=None, fxr=None):
    """Compare a decompressed game ROM with stock. Returns a JSON-able report:
    {"palettes": {name: rows changed}, "fx": {name: rows changed},
     "stages": [slot ids], "cpu": status, "other": [entry indices],
     "layout_ok": bool, "stock": bool}"""
    vanilla = vanilla or load_vanilla_palettes()
    stock = stock or load_stock_entries()
    fxr = fx_rows() if fxr is None else fxr
    rep = {"palettes": {}, "fx": {}, "stages": [], "cpu": "stock", "other": [],
           "layout_ok": True}
    # palettes / fx, row by row against the stock block
    for cid, start in STEAM_PALETTE_OFFSETS.items():
        n = TOTAL_PALETTE_COUNT[cid] * 32
        cur, van = _rows(bytes(rom[start:start + n])), _rows(vanilla[cid])
        fx = fxr.get(cid, set())
        body = sum(1 for i, (a, b) in enumerate(zip(cur, van)) if a != b and i not in fx)
        eff = sum(1 for i, (a, b) in enumerate(zip(cur, van)) if a != b and i in fx)
        name = CHARACTERS.get(cid, str(cid))
        if body:
            rep["palettes"][name] = body
        if eff:
            rep["fx"][name] = eff
    ents = _entries(rom)
    if len(ents) != stock["count"]:
        rep["layout_ok"] = False
    # stages
    for slot in S.STAGE_SLOTS:
        p, t = S.slot_entries(slot)
        if p < len(ents) and (_md5(ents[p]) != stock["md5"][p] or _md5(ents[t]) != stock["md5"][t]):
            rep["stages"].append(slot)
    # cpu
    try:
        from . import arcade_ai
        rep["cpu"] = arcade_ai.status(rom)
    except Exception:
        rep["cpu"] = "unknown"
    # everything else. Entries holding palette blocks are compared with the
    # stock palettes laid back over them, so only non-palette edits count.
    skip = set(_ai_indices()) | set(range(S.STG_FIRST_INDEX,
                                          S.STG_FIRST_INDEX + 2 * len(S.STAGE_SLOTS)))
    toc = S.parse_toc(rom)
    pal_spans = [(STEAM_PALETTE_OFFSETS[c], vanilla[c]) for c in STEAM_PALETTE_OFFSETS]
    for i, e in enumerate(ents[:stock["count"]]):
        if i in skip:
            continue
        if _md5(e) == stock["md5"][i]:
            continue
        o = S.AFS_BASE + toc[i][0]
        patched = None
        for start, block in pal_spans:
            if o <= start < o + len(e):
                patched = patched or bytearray(e)
                patched[start - o:start - o + len(block)] = block
        if patched is not None and _md5(bytes(patched)) == stock["md5"][i]:
            continue
        rep["other"].append(i)
    rep["stock"] = not (rep["palettes"] or rep["fx"] or rep["stages"] or rep["other"]
                        or rep["cpu"] not in ("stock",))
    return rep


def summary_lines(rep):
    """Plain-language lines describing a check() report."""
    def pl(n, word):
        return f"{n} {word}{'' if n == 1 else 's'}"

    def names(d, k=3):
        ns = sorted(d)
        return ", ".join(ns[:k]) + (f" +{len(ns) - k}" if len(ns) > k else "")
    out = []
    out.append(("palettes", pl(len(rep['palettes']), 'character') + f": {names(rep['palettes'])}"
                if rep["palettes"] else None))
    out.append(("fx", pl(sum(rep['fx'].values()), 'effect colour') + " across "
                      + pl(len(rep['fx']), 'character') if rep["fx"] else None))
    out.append(("stages", pl(len(rep['stages']), 'slot') + ": "
                          + ", ".join(SLOT_NAMES.get(s, s) for s in rep["stages"][:3])
                          + (f" +{len(rep['stages']) - 3}" if len(rep["stages"]) > 3 else "")
                if rep["stages"] else None))
    cpu = {"stock": None, "installed": "Smarter CPU (Arcade) installed",
           "ours-old": "An older Smarter CPU version",
           "foreign": "CPU files changed by another mod"}.get(rep["cpu"], rep["cpu"])
    out.append(("cpu", cpu))
    out.append(("other", pl(len(rep['other']), 'other game file') + " changed by another tool"
                if rep["other"] else None))
    return out


def restore_parts(rom, parts, original=None, vanilla=None):
    """Return a new ROM with parts put back. parts = {part: "stock"|"original"}
    for part in PARTS; "original" needs original (a decompressed ROM)."""
    vanilla = vanilla or load_vanilla_palettes()
    fxr = fx_rows()
    ents = _entries(rom)
    orig_ents = _entries(original) if original is not None else None
    changed = False
    if parts.get("stages"):
        rng = range(S.STG_FIRST_INDEX, S.STG_FIRST_INDEX + 2 * len(S.STAGE_SLOTS))
        src = load_stock_stages() if parts["stages"] == "stock" else \
            {i: orig_ents[i] for i in rng}
        for i in rng:
            if ents[i] != src[i]:
                ents[i] = src[i]
                changed = True
    if parts.get("cpu"):
        idx = set(_ai_indices())
        if parts["cpu"] == "stock":
            from . import arcade_ai
            for i in idx:
                with open(os.path.join(arcade_ai.active_dir(), "stock", f"{i}.bin"), "rb") as f:
                    b = f.read()
                if ents[i] != b:
                    ents[i], changed = b, True
        else:
            for i in idx:
                if ents[i] != orig_ents[i]:
                    ents[i], changed = orig_ents[i], True
    out = A.build(rom, ents) if changed else bytearray(rom)
    # palette rows (absolute offsets, before any entry that can move)
    for part in ("palettes", "fx"):
        how = parts.get(part)
        if not how:
            continue
        for cid, start in STEAM_PALETTE_OFFSETS.items():
            fx = fxr.get(cid, set())
            src = vanilla[cid] if how == "stock" else \
                bytes(original[start:start + TOTAL_PALETTE_COUNT[cid] * 32])
            for r in range(TOTAL_PALETTE_COUNT[cid]):
                if (r in fx) == (part == "fx"):
                    out[start + r * 32:start + r * 32 + 32] = src[r * 32:r * 32 + 32]
    return out
