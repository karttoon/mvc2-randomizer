#!/usr/bin/env python3
"""stagegal.py - stage-gallery data for the Stage Gallery tab.

Reads the downloaded stage manifest (stages/stages.json), preview JPGs
(stages/previews/) and payloads (stages/bins/). Verdicts live in
stage_verdicts.json: {variant_key: "delete"} excludes a variant from the
randomizer pool - everything is IN the pool by default. Keys match the
engine's pool keys: d_00 (default), m_00 (author edit), m_CV (custom),
c_04_68776038 / c_XX_a6920569 (community).
"""
import os, json, re

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


# Three states, mirroring palettes: verdict absent = new/unreviewed,
# "keep" = kept, "delete" = rejected.
KEEP = "keep"
REJECT = "delete"


def set_verdict(key, verdict):
    """Set a variant's verdict to KEEP/REJECT, or clear it (verdict=None)."""
    v = load_verdicts()
    if verdict is None:
        v.pop(key, None)
    else:
        v[key] = verdict
    save_verdicts(v)


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

    # Drop-in custom stages. Recognized names join their pool (marked with a
    # reason and never rolled when invalid); unrecognized names show up via
    # pending_customs() so the user can assign them to a stage in the app.
    recognized, _pending = custom_stage_scan()
    for (slot, name), files in sorted(recognized.items()):
        invalid = None
        if "tex" not in files:
            invalid = "missing the tex file"
        elif slot == "XX" and not files.get("pol"):
            invalid = "a full custom stage needs both pol and tex"
        elif slot != "XX":
            d_tex = os.path.join(config.STAGES, "bins", f"d_{slot}_tex.BIN")
            if (os.path.isfile(d_tex) and os.path.isfile(files["tex"])
                    and os.path.getsize(files["tex"]) != os.path.getsize(d_tex)):
                invalid = (f"TEX size doesn't fit slot {slot} - if it's a "
                           f"full custom stage it belongs to Training")
        thumb = files.get("thumb")
        v = {"key": f"u_{slot}_{name}", "name": name, "author": "you",
             "kind": "custom", "game": None, "thumb": thumb,
             "views": ({"C": thumb} if thumb else {}),
             "has_bin": invalid is None, "invalid": invalid}
        if slot == "XX":
            training_extra.append(v)
        elif slot in by_slot:
            by_slot[slot].append(v)

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


# ---- custom stage drop-ins -------------------------------------------------

def custom_stage_scan():
    """(recognized, pending) from custom/stages/.

    recognized: {(slot_or_XX, name): {'tex': path, 'pol': path, 'thumb': path}}
      - our convention  <slot|XX>_<Name>_<tex|pol>.BIN
      - game-native     STG<slot><POL|TEX>.BIN
    pending: [{'stem', 'tex', 'pol', 'other': [paths]}] - files whose names
      don't say which stage they belong to; the app asks the user.
    """
    cdir = os.path.join(config.CUSTOM, "stages")
    recognized, pending = {}, {}
    if not os.path.isdir(cdir):
        return recognized, []
    for f in sorted(os.listdir(cdir)):
        low = f.lower()
        path = os.path.join(cdir, f)
        if low.endswith(".jpg"):
            m = re.match(r"^(XX|[0-9A-F]{2})_(.+)_thumb\.jpg$", f, re.I)
            if m:
                recognized.setdefault((m.group(1).upper(), m.group(2)),
                                      {})["thumb"] = path
            continue
        if not low.endswith(".bin"):
            continue
        m = re.match(r"^(XX|[0-9A-F]{2})_(.+)_(tex|pol)\.BIN$", f, re.I)
        if m:
            recognized.setdefault((m.group(1).upper(), m.group(2)),
                                  {})[m.group(3).lower()] = path
            continue
        m = re.match(r"^STG([0-9A-F]{2})(POL|TEX)\.BIN$", f, re.I)
        if m:
            slot = m.group(1).upper()
            recognized.setdefault((slot, f"stg{slot.lower()}"),
                                  {})[m.group(2).lower()] = path
            continue
        m = re.match(r"^(.+?)[ ._-]*(pol|tex)[ ._-]*\.bin$", f, re.I)
        if m:
            item = pending.setdefault(m.group(1).lower(),
                                      {"stem": m.group(1), "tex": None,
                                       "pol": None, "other": []})
            item[m.group(2).lower()] = path
        else:
            stem = f[:-4]
            item = pending.setdefault(stem.lower(),
                                      {"stem": stem, "tex": None,
                                       "pol": None, "other": []})
            item["other"].append(path)
    return recognized, list(pending.values())


def pending_customs():
    return custom_stage_scan()[1]


def candidate_slots(item):
    """Slots whose default TEX size matches this drop-in; pairs may also be a
    full custom stage for the training pool ('XX' candidate, listed first)."""
    out = []
    if item.get("tex") and item.get("pol"):
        out.append("XX")
    if item.get("tex"):
        try:
            size = os.path.getsize(item["tex"])
        except OSError:
            return out
        for slot in SLOTS:
            d = os.path.join(config.STAGES, "bins", f"d_{slot}_tex.BIN")
            if os.path.isfile(d) and os.path.getsize(d) == size:
                out.append(slot)
    return out


def assign_custom(item, target):
    """Rename a pending drop-in into the convention for `target` ('XX' or a
    slot id). Returns the new base name, or raises on trouble."""
    stem = re.sub(r"[^A-Za-z0-9\-]+", "-", item["stem"]).strip("-") or "stage"
    cdir = os.path.join(config.CUSTOM, "stages")
    base = f"{target}_{stem}"
    n = 2
    while any(os.path.exists(os.path.join(cdir, f"{base}_{p}.BIN"))
              for p in ("tex", "pol")):
        base = f"{target}_{stem}-{n}"
        n += 1
    for part in ("tex", "pol"):
        if item.get(part):
            os.rename(item[part], os.path.join(cdir, f"{base}_{part}.BIN"))
    return base


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


# ---- "new" = no verdict yet (mirrors palettes) -----------------------------

def all_keys(secs=None):
    secs = secs if secs is not None else sections()
    return {v["key"] for _t, vs in secs for v in vs}


def new_keys(secs=None):
    """Variant keys with no verdict yet (never kept or rejected)."""
    secs = secs if secs is not None else sections()
    v = load_verdicts()
    return {x["key"] for _t, vs in secs for x in vs if x["key"] not in v}


def mark_all_seen():
    """Acknowledge all currently-new variants by keeping them (they already
    roll by default; this just clears the 'new' flag)."""
    v = load_verdicts()
    for key in all_keys():
        v.setdefault(key, KEEP)
    save_verdicts(v)


def counts():
    """(total, rejected, new) across all variants, for the status line."""
    secs = sections()
    v = load_verdicts()
    total = new = rejected = 0
    for _t, vs in secs:
        for x in vs:
            total += 1
            verdict = v.get(x["key"])
            if verdict == REJECT:
                rejected += 1
            elif verdict is None:
                new += 1
    return total, rejected, new


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
