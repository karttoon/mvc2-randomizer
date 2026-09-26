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

# Mesh marker present only in cross-slot "pipeline" ports (repacked to merge into
# any animated slot). Slim training-only customs lack it.
PORT_MESH_MARK = bytes.fromhex("fec0a148")

# Short tags for the games ports came from. Stage names repeat across games
# (e.g. "Code Red"), so the gallery and lock dropdowns prefix a port's name
# with its game tag (note 3).
GAME_ABBR = {
    "Marvel Super Heroes vs. Street Fighter": "MSHvSF",
    "Marvel vs. Capcom": "MvC1",
    "X-Men vs. Street Fighter": "XvSF",
    "Capcom vs. SNK 2": "CvS2",
    "Street Fighter Alpha 2": "SFA2",
    "Street Fighter Alpha 3": "SFA3",
    "Darkstalkers": "DS",
    "Mega Man": "MM",
    "Red Earth": "RedE",
    "Dragon Ball Z": "DBZ",
    "Guilty Gear X": "GGX",
    "Kabuki Klash": "KK",
    "Marvel Super Heroes": "MSH",
    "Mortal Kombat II": "MKII",
    "Resident Evil": "RE",
    "Street Fighter II": "SFII",
    "Street Fighter III: 3rd Strike": "SF3S",
    "Street Fighter III: New Generation": "SF3NG",
    "X-Men: Children of the Atom": "CotA",
}


def game_abbr(game):
    """Short tag for a game name (explicit map, else the words' capitals)."""
    if not game:
        return ""
    if game in GAME_ABBR:
        return GAME_ABBR[game]
    caps = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", game)
                   if w[:1].isupper())
    return caps or game[:6]


def display_name(v):
    """Variant name for the UI, prefixed with the game tag when it's a port
    from a specific game (names repeat across games)."""
    ab = game_abbr(v.get("game"))
    return f"{ab} - {v['name']}" if ab else v["name"]


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


def _bin_path(name):
    if not name:
        return None
    p = os.path.join(config.STAGES, "bins", name)
    return p if os.path.isfile(p) else None


def _bin_exists(name):
    return bool(_bin_path(name))


def _variant(key, name, author, kind, media, tex, pol=None, game=None):
    media = media or {}
    return {
        "key": key, "name": name, "author": author, "kind": kind,
        "game": game,
        "tex_path": _bin_path(tex), "pol_path": _bin_path(pol),
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
        elif slot != "XX" and not (files.get("pol")
                                   and _has_mesh_marker(files["pol"])):
            # a pipeline port pinned to this slot is repacked+merged into it, so
            # it needn't match the slot's stock TEX size; anything else must.
            d_tex = os.path.join(config.STAGES, "bins", f"d_{slot}_tex.BIN")
            if (os.path.isfile(d_tex) and os.path.isfile(files["tex"])
                    and os.path.getsize(files["tex"]) != os.path.getsize(d_tex)):
                invalid = (f"TEX size doesn't fit slot {slot} - if it's a "
                           f"full custom stage it belongs to Training")
        thumb = files.get("thumb")
        v = {"key": f"u_{slot}_{name}", "name": name, "author": "you",
             "kind": "custom", "game": None, "thumb": thumb,
             "tex_path": files.get("tex"), "pol_path": files.get("pol"),
             "views": ({"C": thumb} if thumb else {}),
             "has_bin": invalid is None, "invalid": invalid}
        if slot == "XX":
            training_extra.append(v)
        elif slot in by_slot:
            by_slot[slot].append(v)

    # One section per slot in game order (defaults, edits, slot-specific
    # retextures). Ports/customs get their own game-grouped sections below - a
    # port's verdict is shared across every slot it can roll into, so it appears
    # once here, not once per slot.
    slot_secs = []
    for sid in SLOTS:
        title = f"STG {sid}  -  {slot_titles.get(sid, sid)}"
        slot_secs.append((title, by_slot[sid]))

    # Port sections: custom & ported stages grouped by source game (note 2).
    # These roll into any slot they fit - Training always, animated slots when
    # cross-slot merge is enabled. Ungrouped / user drop-ins come last.
    for v in training_extra:
        v["xx"] = True
    groups = {}
    for v in training_extra:
        groups.setdefault(v.get("game"), []).append(v)
    port_secs = []
    for game in sorted(groups, key=lambda g: (g is None, game_abbr(g), g or "")):
        vs = sorted(groups[game], key=lambda v: v["name"].lower())
        # "Ports:" (no "  -  ") so the tree keeps the whole tag; abbrev stays
        # short in the narrow slot list, full game name shows on the card.
        label = game_abbr(game) or (game if game else "Custom")
        port_secs.append((f"Ports: {label}", vs))
    return slot_secs + port_secs


def _has_mesh_marker(pol_path):
    """True if a POL is a cross-slot pipeline port (mergeable into any slot)."""
    try:
        with open(pol_path, "rb") as f:
            return PORT_MESH_MARK in f.read()
    except OSError:
        return False


def slot_lock_options(distribute_ports=False, slot_fits=None):
    """[(slot_id, short_title, [variant, ...]), ...] for the 17 slots - what each
    Lock Selections dropdown offers. Training always offers every port (raw);
    when cross-slot merge is on, each animated slot offers only the pipeline
    ports that actually FIT it. Fit is the engine's two-axis rule, passed in as
    slot_fits {slot_id: {port_key, ...}}; without it (not yet computed) every
    pipeline port is offered as a fallback."""
    secs = sections()
    slot_secs, port_secs = secs[:len(SLOTS)], secs[len(SLOTS):]
    ports = [v for _t, vs in port_secs for v in vs]
    pipeline = [p for p in ports
                if p.get("pol_path") and _has_mesh_marker(p["pol_path"])]
    out = []
    for sid, (title, vs) in zip(SLOTS, slot_secs):
        short = title.split("  -  ", 1)[-1]
        opts = list(vs)
        if sid == TRAINING:
            opts += ports
        elif distribute_ports:
            if slot_fits is not None:
                keys = slot_fits.get(sid, set())
                opts += [p for p in pipeline if p["key"] in keys]
            else:
                opts += pipeline                 # fit map unknown: offer all
        out.append((sid, short, opts))
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
        stem, part = _split_poltex(f)
        key = stem.lower() or "stage"
        item = pending.setdefault(key, {"stem": stem or "stage", "tex": None,
                                        "pol": None, "other": []})
        if part:
            item[part] = path            # pol/tex file -> pairs by shared stem
        else:
            item["other"].append(path)   # no recognizable pol/tex token
    return recognized, list(pending.values())


def _split_poltex(name):
    """(stem, 'pol'|'tex'|None) for a .BIN drop-in whose name isn't in our
    convention. A pol/tex token counts wherever it sits as long as it's at the
    end or followed by a separator (so STGXXPOL, my_pol_v2, name-tex all pair,
    while a stray 'pol' inside a word like 'metropolis' is ignored). The stem
    is the rest of the name, so a stage's pol and tex share one stem."""
    base = name[:-4] if name[-4:].lower() == ".bin" else name
    best = None
    for m in re.finditer(r"(?i)pol|tex", base):
        after = base[m.end()] if m.end() < len(base) else ""
        if after == "" or after in " ._-":
            best = m                     # keep the last qualifying token
    if not best:
        return base, None
    stem = base[:best.start()] + base[best.end():]
    stem = re.sub(r"[ ._-]+", "_", stem).strip("_")
    return stem, base[best.start():best.end()].lower()


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
