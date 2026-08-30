#!/usr/bin/env python3
"""stagegal.py - stage-gallery data for the Stage Gallery tab.

Reads the downloaded stage manifest (stages/stages.json), preview JPGs
(stages/previews/) and payloads (stages/bins/). Verdicts live in
stage_verdicts.json: {variant_key: "delete"} excludes a variant from the
randomizer pool - everything is IN the pool by default. Keys match the
engine's pool keys: d_00 (default), m_00 (author edit), m_CV (custom),
c_04_68776038 / c_XX_a6920569 (community).
"""
import os, json

from PIL import Image

import config

# Slot ids in game order (mirrors mvc2_data.stages.STAGE_SLOTS)
SLOTS = ["00", "01", "02", "03", "04", "05", "06", "07", "08",
         "09", "0A", "0B", "0C", "0D", "0E", "0F", "10"]
TRAINING = "0B"

KIND_LABEL = {"default": "Default", "edit": "Edit", "retexture": "Retexture",
              "port": "Port", "original": "Custom"}


def manifest():
    try:
        with open(os.path.join(config.STAGES, "stages.json"),
                  encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_verdicts():
    try:
        with open(config.STAGE_VERDICTS_JSON, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_verdicts(v):
    with open(config.STAGE_VERDICTS_JSON, "w", encoding="utf-8") as f:
        json.dump(v, f, indent=1, sort_keys=True)


def toggle(key):
    """Flip a variant in/out of the pool; returns True if now in pool."""
    v = load_verdicts()
    if v.get(key) == "delete":
        del v[key]
        in_pool = True
    else:
        v[key] = "delete"
        in_pool = False
    save_verdicts(v)
    return in_pool


def _preview(name):
    if not name:
        return None
    p = os.path.join(config.STAGES, "previews", name)
    return p if os.path.isfile(p) else None


def _bin_exists(name):
    return bool(name) and os.path.isfile(os.path.join(config.STAGES, "bins", name))


def _variant(key, name, author, kind, media, tex, pol=None, game=None):
    media = media or {}
    return {
        "key": key, "name": name, "author": author, "kind": kind,
        "game": game,
        "thumb": _preview(media.get("thumb")),
        "views": {k: _preview(media.get(mk))
                  for k, mk in (("C", "c"), ("L", "l"), ("R", "r"))
                  if _preview(media.get(mk))},
        "has_bin": _bin_exists(tex) and (pol is None or _bin_exists(pol)),
    }


def sections():
    """[(title, [variant, ...]), ...] - one section per stage slot in game
    order, plus a final section with the slot-agnostic training-pool stages
    (ports / customs). Empty list when no stage data is downloaded."""
    m = manifest()
    if not m:
        return []
    by_slot = {s: [] for s in SLOTS}
    training_extra = []
    slot_titles = {}

    for e in m.get("stages", []):
        sid = e.get("id", "")
        name = e.get("n", sid)
        if sid in by_slot:
            slot_titles[sid] = name
            by_slot[sid].append(_variant(
                f"d_{sid}", "Default", "Capcom", "default",
                e.get("def"), f"d_{sid}_tex.BIN"))
            if e.get("tex"):
                by_slot[sid].append(_variant(
                    f"m_{sid}", name, e.get("author", "?"), "edit",
                    e.get("mod"), e.get("tex")))
        elif e.get("tex"):
            training_extra.append(_variant(
                f"m_{sid}", name, e.get("author", "?"), "original",
                e.get("mod"), e.get("tex"), pol=f"m_{sid}_pol.BIN"))

    for c in m.get("community", []):
        sid = c.get("stageId", "")
        v = _variant(f"c_{sid}_{c.get('sub', '')}",
                     c.get("name") or c.get("title") or c.get("sub", "?"),
                     c.get("author", "?"), c.get("kind", "retexture"),
                     c, c.get("tex"), pol=c.get("pol"), game=c.get("game"))
        if sid in by_slot:
            by_slot[sid].append(v)
        else:
            training_extra.append(v)

    # Custom & ported stages are part of the training slot's pool - show them
    # inside that section (flagged so the UI can divide them visually).
    for v in training_extra:
        v["xx"] = True
    by_slot[TRAINING].extend(training_extra)

    out = []
    for sid in SLOTS:
        title = f"STG {sid}  -  {slot_titles.get(sid, sid)}"
        if sid == TRAINING:
            title += "   (incl. custom & ported stages)"
        out.append((title, by_slot[sid]))
    return out


def load_locks():
    """{slot_id: variant_key} for slots pinned to one stage."""
    try:
        with open(config.STAGE_LOCKS_JSON, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def set_lock(slot, key):
    """Pin a slot to a variant key, or clear the pin with key=None."""
    locks = load_locks()
    if key:
        locks[slot] = key
    else:
        locks.pop(slot, None)
    with open(config.STAGE_LOCKS_JSON, "w", encoding="utf-8") as f:
        json.dump(locks, f, indent=1, sort_keys=True)


def counts():
    """(total variants, excluded) for the status line."""
    secs = sections()
    total = sum(len(vs) for _t, vs in secs)
    v = load_verdicts()
    excluded = sum(1 for _t, vs in secs for x in vs if v.get(x["key"]) == "delete")
    return total, excluded


def thumb_image(variant, width=200):
    """PIL image for a variant's thumbnail (gray placeholder if missing)."""
    path = variant.get("thumb")
    if path:
        try:
            im = Image.open(path).convert("RGB")
            h = max(1, int(im.height * width / im.width))
            return im.resize((width, h), Image.LANCZOS)
        except Exception:
            pass
    return Image.new("RGB", (width, int(width * 9 / 16)), (60, 60, 66))
