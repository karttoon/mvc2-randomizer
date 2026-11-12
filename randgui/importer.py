"""Import custom content from a game file into the app's folders.

Palettes, effect colours and stages that another tool (PalMod, a stage mod, a
friend's game file...) put into game_50.arc are written out as files the app
manages, and locked where they were, so the game looks the same afterwards
but those parts are no longer "untouchable":

  palettes  -> custom/skins/<Character>/<Character>_<tag>_<BTN>.png, locked to
               that button (or locked to the gallery file when it's an exact
               gallery palette)
  effects   -> custom/fx/<Character>.json (raw palette entries, always win)
  stages    -> custom/stages/<slot>_<Name>_{pol,tex}.BIN, locked to that slot
               (or locked to the gallery stage when it's a known one)

scan() only reads. run() writes files and locks. Nothing touches the game
file itself.
"""
import datetime
import hashlib
import io
import json
import os
import re
import zipfile

from PIL import Image

import config
import locks as pal_locks
import palettes
import randomize
import stagegal

SOURCES = ("live", "game", "backup", "file")
INDEX_JSON = "palette_index.json"
IMPORTED_STAGES = "imported.json"


def _md5(b):
    return hashlib.md5(b).hexdigest()


# ----------------------------------------------------------------- palettes
def _templates():
    """{Character folder: Image} - one sheet layout per character."""
    eng = randomize._engine()
    for base in (config.resource_dir(), config.app_dir()):
        p = os.path.join(base, "mvc2_data", "sheet_templates.zip")
        if os.path.isfile(p):
            out = {}
            with zipfile.ZipFile(p) as z:
                for n in z.namelist():
                    im = Image.open(io.BytesIO(z.read(n)))
                    im.load()
                    out[n[:-4]] = im
            return out
    raise FileNotFoundError("sheet_templates.zip is missing from the app")


def _rows(cid):
    from mvc2_data.characters import palette_rows, palette_slot_map
    sm = palette_slot_map(cid)
    return [sm[r] for r in range(palette_rows(cid))]


def _rom_key(rom, cid, btn):
    """Raw bytes of one button's sheet rows, as the game holds them."""
    from mvc2_data.steam import palette_offset
    return b"".join(bytes(rom[o:o + 32]) for o in
                    (palette_offset(cid, btn, s) for s in _rows(cid)))


def _png_key(eng, cid, path):
    """The bytes apply_skin would write for a sheet's rows."""
    from mvc2_data.steam import rgb_to_argb4444
    cols = eng.extract_png_palette(path) or []
    out = bytearray()
    for r in range(len(_rows(cid))):
        row = list(cols[r * 16:r * 16 + 16])
        row += [(0, 0, 0)] * (16 - len(row))
        for i, (rr, g, b) in enumerate(row):
            c = 0 if i == 0 else rgb_to_argb4444(rr, g, b)
            out += c.to_bytes(2, "little")
    return bytes(out)


def _near_gallery(idx, key, nrows):
    """A gallery sheet that matches `key` (row bytes, 32 per row) on every row
    but a few - the ones an FX setting recoloured in the game (effect palettes
    can share entries with the body). Returns (name, [differing row numbers])
    or (None, None). At most a third of the rows may differ, and at least two
    must match, so a genuinely different palette isn't mistaken for one."""
    if nrows < 2:
        return None, None
    rows = [key[r * 32:(r + 1) * 32] for r in range(nrows)]
    allow = max(1, nrows // 3)
    best = (None, None)
    for khex, name in idx.items():
        if name.startswith("custom/"):
            continue
        other = bytes.fromhex(khex)
        if len(other) != len(key):
            continue
        diff = [r for r in range(nrows) if other[r * 32:(r + 1) * 32] != rows[r]]
        if diff and len(diff) <= allow and nrows - len(diff) >= 2 and (
                best[1] is None or len(diff) < len(best[1])):
            best = (name, diff)
    return best


def _gallery_index(eng, folders, progress=None):
    """{folder: {row-bytes hex: filename}} for the gallery (and custom) sheets
    of the given characters. Cached in data/palette_index.json by size+mtime."""
    path = os.path.join(config.STATE, INDEX_JSON)
    try:
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    out, dirty = {}, False
    for n, folder in enumerate(sorted(folders)):
        cid = eng.FOLDER_TO_CHAR_ID.get(folder)
        if cid is None:
            continue
        old = cache.get(folder, {})
        new = {}
        for sub, d in (("", os.path.join(config.SKINS, folder)),
                       ("custom/", os.path.join(config.CUSTOM, "skins", folder))):
            if not os.path.isdir(d):
                continue
            for fn in os.listdir(d):
                if not fn.lower().endswith(".png"):
                    continue
                p = os.path.join(d, fn)
                st = os.stat(p)
                name = sub + fn
                hit = old.get(name)
                if hit and hit[0] == st.st_size and hit[1] == int(st.st_mtime):
                    new[name] = hit
                    continue
                try:
                    new[name] = [st.st_size, int(st.st_mtime), _png_key(eng, cid, p).hex()]
                except Exception:
                    continue
                dirty = True
        if set(new) != set(old):
            dirty = True
        cache[folder] = new
        # gallery files first so an exact gallery copy wins over an older import
        idx = {}
        for name in sorted(new, key=lambda k: (k.startswith("custom/"), k)):
            idx.setdefault(new[name][2], name)
        out[folder] = idx
        if progress and n % 8 == 7:
            progress(f"  indexed palettes for {n + 1} of {len(folders)} characters")
    if dirty:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cache, f)
        except OSError:
            pass
    return out


# ------------------------------------------------------------------- stages
def _stage_index():
    """{(tex md5, pol md5 or None): variant key} for the gallery's stages."""
    out = {}
    for _t, vs in stagegal.sections():
        for v in vs:
            if v.get("kind") == "custom" and not v.get("has_bin"):
                continue
            try:
                tex = _md5(open(v["tex_path"], "rb").read()) if v.get("tex_path") else None
                pol = _md5(open(v["pol_path"], "rb").read()) if v.get("pol_path") else None
            except OSError:
                continue
            if tex:
                out.setdefault((tex, pol), v["key"])
    return out


# --------------------------------------------------------------------- scan
def scan(arc_path, source="file", progress=None):
    """Read a game file and work out what would be imported. Returns a plan:
    {"source", "arc", "palettes": [...], "fx": {folder: {entry: hex}},
     "stages": [...], "other": n, "cpu": status, "error"?}"""
    say = progress or (lambda m: None)
    eng = randomize._engine()
    from mvc2_data import gamecheck
    from mvc2_data import stages as S
    from mvc2_data.steam import STEAM_PALETTE_OFFSETS, TOTAL_PALETTE_COUNT
    say("Reading the game file...")
    rom = eng.read_arc(arc_path)
    van = eng.load_vanilla_palettes()
    rep = gamecheck.check(rom, vanilla=van)
    plan = {"source": source, "arc": arc_path, "palettes": [], "fx": {},
            "stages": [], "other": len(rep.get("other") or []),
            "cpu": rep.get("cpu"), "layout_ok": rep.get("layout_ok", True)}

    # Which characters to look at. From the live game, only the ones the app
    # didn't write itself (protected); from a backup or someone's file, all.
    pstate = eng.load_palette_state()
    only = set(pstate["protected"]) if source == "live" else None
    folders = []
    for cid in STEAM_PALETTE_OFFSETS:
        folder = eng.CHAR_ID_TO_FOLDER.get(cid, eng.safe_name(eng.CHARACTERS[cid]))
        if only is not None and folder not in only:
            continue
        start, n = STEAM_PALETTE_OFFSETS[cid], TOTAL_PALETTE_COUNT[cid] * 32
        if bytes(rom[start:start + n]) != bytes(van[cid]):
            folders.append((cid, folder))

    if folders:
        say(f"Looking at {len(folders)} character(s) with changed colours...")
        gidx = _gallery_index(eng, [f for _c, f in folders], say)
        tmpl = _templates()
    for cid, folder in folders:
        start = STEAM_PALETTE_OFFSETS[cid]
        n = TOTAL_PALETTE_COUNT[cid]
        block = van[cid]
        # Simulate the app writing this character: stock block, then each
        # changed button's sheet. Whatever still differs from that (and from
        # stock) is a hand-edited effect colour.
        work = bytearray(rom)
        work[start:start + len(block)] = block
        any_btn = False
        for bi, btn in enumerate(pal_locks.BUTTONS):
            key = _rom_key(rom, cid, bi)
            if key == _rom_key(work, cid, bi):
                continue                         # stock button
            fidx = gidx.get(folder, {})
            item = {"folder": folder, "cid": cid, "button": btn, "bi": bi,
                    "key": key.hex(), "gallery": fidx.get(key.hex())}
            near = None
            if not item["gallery"]:
                g, diff = _near_gallery(fidx, key, len(_rows(cid)))
                if g:
                    item["gallery"], item["fx_rows"] = g, diff
                    near = os.path.join(config.SKINS, folder, g)
            item["ok"] = folder in tmpl
            if item["ok"]:
                from mvc2_data.steam import read_palette
                cols = []
                for s in _rows(cid):
                    cols += read_palette(rom, cid, bi, s)
                item["colors"] = cols
                # A near match is the gallery sheet plus FX-recoloured rows:
                # simulate the sheet, so those rows come out as effect colours.
                sim = (eng.extract_png_palette(near) or cols) if near else cols
                _apply_cols(eng, work, cid, bi, sim, tmpl[folder])
                any_btn = True
                item["exact"] = _rom_key(work, cid, bi) == key
            plan["palettes"].append(item)
        if any_btn:
            eng.apply_body_extras(work, cid)
        fx = {}
        for e in range(n):
            o = start + e * 32
            cur = bytes(rom[o:o + 32])
            if cur != block[e * 32:e * 32 + 32] and cur != bytes(work[o:o + 32]):
                fx[e] = cur.hex()
        if fx:
            plan["fx"][folder] = fx

    # stages
    say("Looking at the stages...")
    stock = gamecheck.load_stock_entries()
    sstate = eng.load_stage_state()
    ents = gamecheck._entries(rom)
    sidx = _stage_index()
    for slot in S.STAGE_SLOTS:
        p, t = S.slot_entries(slot)
        if t >= len(ents):
            continue
        pol, tex = ents[p], ents[t]
        mp, mt = _md5(pol), _md5(tex)
        if mp == stock["md5"][p] and mt == stock["md5"][t]:
            continue
        if source == "live":
            if slot not in sstate["protected"]:
                continue                         # the app's own pick
        key = sidx.get((mt, mp)) or (sidx.get((mt, None)) if mp == stock["md5"][p] else None)
        plan["stages"].append({"slot": slot, "gallery": key,
                               "pol_md5": mp, "tex_md5": mt,
                               "size": len(pol) + len(tex)})
    plan["_rom"] = rom
    return plan


def _apply_cols(eng, rom, cid, bi, cols, template):
    """apply_skin with colours instead of a file (via the template sheet)."""
    path = _sheet_png(cols, template)
    tmp = os.path.join(config.STATE, "_import_tmp.png")
    with open(tmp, "wb") as f:
        f.write(path)
    try:
        eng.apply_skin(rom, cid, bi, tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _sheet_png(cols, template):
    im = template.copy()
    pal = [c for rgb in cols for c in rgb]
    pal += [0] * (768 - len(pal))
    im.putpalette(pal)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def summary(plan):
    """Counts for the confirmation dialog."""
    pal = plan["palettes"]
    new = [p for p in pal if p.get("ok") and not p.get("gallery")]
    gal = [p for p in pal if p.get("gallery")]
    return {
        "palettes_new": len(new), "palettes_gallery": len(gal),
        "palette_chars": len({p["folder"] for p in pal}),
        "palettes_skipped": len([p for p in pal if not p.get("ok")]),
        "fx_chars": len(plan["fx"]),
        "fx_entries": sum(len(v) for v in plan["fx"].values()),
        "stages_new": len([s for s in plan["stages"] if not s["gallery"]]),
        "stages_gallery": len([s for s in plan["stages"] if s["gallery"]]),
        "other": plan["other"],
    }


# ---------------------------------------------------------------------- run
def _safe(s):
    return re.sub(r"[^A-Za-z0-9\-]+", "-", s).strip("-") or "x"


def run(plan, lock_gallery=True, tag=None, progress=None):
    """Write the files and locks for a scan() plan. Returns a report dict."""
    say = progress or print
    eng = randomize._engine()
    from mvc2_data import stages as S
    from mvc2_data import gamecheck
    tag = _safe(tag or ("import-" + datetime.date.today().strftime("%Y%m%d")))
    rep = {"palettes": [], "locked_gallery": [], "fx": [], "stages": [],
           "unprotected": [], "skipped": []}
    tmpl = _templates() if plan["palettes"] else {}

    # palettes
    locks = pal_locks.load_locks()
    # What you import is yours: mark it Keep too, so it stays in the random
    # pool (and the lock lists) even with "only kept items" on.
    pverd = palettes.load_verdicts()
    for item in plan["palettes"]:
        folder, btn = item["folder"], item["button"]
        if item.get("gallery"):
            if lock_gallery:
                locks[(folder, btn)] = item["gallery"]
                pverd[palettes.key_for(folder, item["gallery"])] = palettes.KEEP
                rep["locked_gallery"].append(f"{folder} {btn}: {item['gallery']}")
            continue
        if not item.get("ok"):
            rep["skipped"].append(f"{folder} {btn}: no sheet layout for this character")
            continue
        d = os.path.join(config.CUSTOM, "skins", folder)
        os.makedirs(d, exist_ok=True)
        fn = f"{folder}_{tag}_{btn}.png"
        n = 2
        while os.path.exists(os.path.join(d, fn)):
            fn = f"{folder}_{tag}_{btn}-{n}.png"
            n += 1
        with open(os.path.join(d, fn), "wb") as f:
            f.write(_sheet_png(item["colors"], tmpl[folder]))
        locks[(folder, btn)] = "custom/" + fn
        pverd[palettes.key_for(folder, "custom/" + fn)] = palettes.KEEP
        rep["palettes"].append(f"custom/skins/{folder}/{fn}"
                               + ("" if item.get("exact", True) else " (closest match)"))
    if plan["palettes"]:
        pal_locks._write(locks)
        palettes.save_verdicts(pverd)

    # effect colours
    for folder, ents in plan["fx"].items():
        d = os.path.join(config.CUSTOM, "fx")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, folder + ".json")
        try:
            with open(p, encoding="utf-8") as f:
                cur = json.load(f)
        except (OSError, ValueError):
            cur = {}
        cur.setdefault("entries", {}).update({str(k): v for k, v in ents.items()})
        cur["source"] = os.path.basename(plan["arc"])
        cur["imported"] = datetime.datetime.now().isoformat(timespec="seconds")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cur, f, indent=1, sort_keys=True)
        rep["fx"].append(f"custom/fx/{folder}.json ({len(ents)} effect colour sets)")

    # stages
    rom = plan.get("_rom")
    ents = gamecheck._entries(rom) if (rom is not None and plan["stages"]) else []
    cdir = os.path.join(config.CUSTOM, "stages")
    imported = {}
    for st in plan["stages"]:
        slot = st["slot"]
        if st["gallery"]:
            if lock_gallery:
                stagegal.set_lock(slot, st["gallery"])
                stagegal.set_verdict(st["gallery"], "keep")
                rep["locked_gallery"].append(f"STG {slot}: {st['gallery']}")
            continue
        os.makedirs(cdir, exist_ok=True)
        p, t = S.slot_entries(slot)
        # Training-slot content is a full stage: it goes in the training pool.
        target = "XX" if slot == S.TRAINING_SLOT else slot
        name = f"{tag}-{slot}"
        base = f"{target}_{name}"
        n = 2
        while any(os.path.exists(os.path.join(cdir, f"{base}_{x}.BIN")) for x in ("pol", "tex")):
            base = f"{target}_{name}-{n}"
            n += 1
        for part, data in (("pol", ents[p]), ("tex", ents[t])):
            with open(os.path.join(cdir, f"{base}_{part}.BIN"), "wb") as f:
                f.write(data)
        imported[base] = {"slot": slot, "pol": st["pol_md5"], "tex": st["tex_md5"],
                          "source": os.path.basename(plan["arc"])}
        stagegal.set_lock(slot, "u_" + base)
        stagegal.set_verdict("u_" + base, "keep")
        rep["stages"].append(f"STG {slot}: custom/stages/{base}_pol/tex.BIN")
    if imported:
        p = os.path.join(cdir, IMPORTED_STAGES)
        try:
            with open(p, encoding="utf-8") as f:
                cur = json.load(f)
        except (OSError, ValueError):
            cur = {}
        cur.update(imported)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cur, f, indent=1, sort_keys=True)

    # Where the user's game still holds exactly what was imported (importing
    # from the live game, or from the backup before anything changed), the app
    # now owns those parts: record them as its own writes so they aren't
    # flagged as "edited elsewhere" and skipped.
    live = None
    try:
        arc = config.game_arc()
        if plan["source"] == "live":
            live = rom
        elif arc and os.path.exists(arc) and plan["source"] in ("game", "backup", "file"):
            live = eng.read_arc(arc)
    except Exception:
        live = None
    if live is not None:
        from mvc2_data.steam import STEAM_PALETTE_OFFSETS, TOTAL_PALETTE_COUNT
        state = eng.load_palette_state()
        chars = {i["folder"] for i in plan["palettes"]} | set(plan["fx"])
        for folder in sorted(chars):
            cid = eng.FOLDER_TO_CHAR_ID.get(folder)
            if cid is None:
                continue
            a, n = STEAM_PALETTE_OFFSETS[cid], TOTAL_PALETTE_COUNT[cid] * 32
            if bytes(live[a:a + n]) != bytes(rom[a:a + n]):
                continue
            state["written"][folder] = eng.block_hash(live, cid)
            if folder in state["protected"]:
                state["protected"].remove(folder)
                rep["unprotected"].append(folder)
            if folder in state["notified"]:
                state["notified"].remove(folder)
        eng.save_palette_state(state)
        sst = eng.load_stage_state()
        lents = gamecheck._entries(live) if plan["stages"] else []
        for st in plan["stages"]:
            p, t = S.slot_entries(st["slot"])
            if t < len(lents) and _md5(lents[p]) == st["pol_md5"] and _md5(lents[t]) == st["tex_md5"]:
                sst["written"][st["slot"]] = {"pol": st["pol_md5"], "tex": st["tex_md5"]}
                if st["slot"] in sst["protected"]:
                    sst["protected"].remove(st["slot"])
        eng.save_stage_state(sst)
    import activity
    activity.log(f"Import from {plan['source']} ({os.path.basename(plan['arc'])}): "
                 f"{len(rep['palettes'])} palettes, {len(rep['locked_gallery'])} gallery locks, "
                 f"{len(rep['fx'])} fx files, {len(rep['stages'])} stages")
    return rep


# --------------------------------------------------------------- reconcile
def _keep_rows_as_fx(eng, cid, folder, bi, key, rows):
    """Write the given sheet rows (as the game holds them) into
    custom/fx/<folder>.json, so they still override the gallery sheet."""
    from mvc2_data.steam import STEAM_PALETTE_OFFSETS, palette_offset
    slots = _rows(cid)
    ents = {str((palette_offset(cid, bi, slots[r]) - STEAM_PALETTE_OFFSETS[cid]) // 32):
            key[r * 32:(r + 1) * 32].hex() for r in rows}
    d = os.path.join(config.CUSTOM, "fx")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, folder + ".json")
    try:
        with open(p, encoding="utf-8") as f:
            cur = json.load(f)
    except (OSError, ValueError):
        cur = {}
    cur.setdefault("entries", {}).update(ents)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=1, sort_keys=True)
RECONCILED = "reconciled"          # custom/reconciled/... keeps the matched copies


def reconcile(progress=None):
    """Match the user's own palettes and stages (imported or dropped in) to
    the gallery, byte for byte. A match takes the gallery item's name and
    previews: locks and Keep/Reject move to the gallery item, and the custom
    copy moves to custom/reconciled/ (kept, just out of the way). Exact
    matches only - anything that differs stays yours."""
    say = progress or (lambda m: None)
    eng = randomize._engine()
    rep = {"palettes": [], "stages": []}
    moved_root = os.path.join(config.CUSTOM, RECONCILED)

    def move(src, sub):
        d = os.path.join(moved_root, sub)
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, os.path.basename(src))
        n = 2
        while os.path.exists(dst):
            stem, ext = os.path.splitext(os.path.basename(src))
            dst = os.path.join(d, f"{stem}-{n}{ext}")
            n += 1
        os.replace(src, dst)

    # palettes
    croot = os.path.join(config.CUSTOM, "skins")
    folders = []
    if os.path.isdir(croot) and config.skins_count():
        for f in sorted(os.listdir(croot)):
            d = os.path.join(croot, f)
            if os.path.isdir(d) and any(n.lower().endswith(".png") for n in os.listdir(d)):
                folders.append(f)
    if folders:
        say(f"Comparing your palettes for {len(folders)} character(s) with the gallery...")
        gidx = _gallery_index(eng, folders, say)
        locks = pal_locks.load_locks()
        pverd = palettes.load_verdicts()
        for folder in folders:
            cid = eng.FOLDER_TO_CHAR_ID.get(folder)
            if cid is None:
                continue
            d = os.path.join(croot, folder)
            for fn in sorted(os.listdir(d)):
                p = os.path.join(d, fn)
                if not fn.lower().endswith(".png") or not os.path.isfile(p):
                    continue
                try:
                    key = _png_key(eng, cid, p).hex()
                except Exception:
                    continue
                g = gidx.get(folder, {}).get(key)
                if g and g.startswith("custom/"):
                    g = None                     # that's this file itself
                old = "custom/" + fn
                fx_rows = None
                if not g:
                    btns = [b for (f, b), v in locks.items() if f == folder and v == old]
                    if len(btns) == 1:          # rows map to one button's entries
                        g, fx_rows = _near_gallery(gidx.get(folder, {}),
                                                   bytes.fromhex(key), len(_rows(cid)))
                if not g or g.startswith("custom/"):
                    continue
                if fx_rows:
                    _keep_rows_as_fx(eng, cid, folder, pal_locks.BUTTONS.index(btns[0]),
                                     bytes.fromhex(key), fx_rows)
                for k, v in list(locks.items()):
                    if k[0] == folder and v == old:
                        locks[k] = g
                vd = pverd.pop(palettes.key_for(folder, old), None)
                gk = palettes.key_for(folder, g)
                if vd and pverd.get(gk) != palettes.REJECT:
                    pverd[gk] = vd
                move(p, os.path.join("skins", folder))
                rep["palettes"].append(f"{folder}: {fn} -> {g}")
        if rep["palettes"]:
            pal_locks._write(locks)
            palettes.save_verdicts(pverd)

    # stages
    if stagegal.manifest() is not None:
        from mvc2_data import gamecheck
        from mvc2_data import stages as S
        say("Comparing your stages with the gallery...")
        sidx = {k: v for k, v in _stage_index().items() if not v.startswith("u_")}
        stock = gamecheck.load_stock_entries()["md5"]
        recognized, _pending = stagegal.custom_stage_scan()
        slocks = stagegal.load_locks()
        sverd = stagegal.load_verdicts()
        imp_path = os.path.join(config.CUSTOM, "stages", IMPORTED_STAGES)
        try:
            with open(imp_path, encoding="utf-8") as f:
                imported = json.load(f)
        except (OSError, ValueError):
            imported = {}
        for (slot, name), files in sorted(recognized.items()):
            if not files.get("tex"):
                continue
            try:
                mt = _md5(open(files["tex"], "rb").read())
                mp = _md5(open(files["pol"], "rb").read()) if files.get("pol") else None
            except OSError:
                continue
            real = S.TRAINING_SLOT if slot == "XX" else slot
            p, _t = S.slot_entries(real)
            g = sidx.get((mt, mp))
            if not g and (mp is None or mp == stock[p]):
                g = sidx.get((mt, None))
            if not g:
                continue
            ukey = f"u_{slot}_{name}"
            for s, v in list(slocks.items()):
                if v == ukey:
                    stagegal.set_lock(s, g)
            vd = sverd.get(ukey)
            if vd and stagegal.load_verdicts().get(g) != stagegal.REJECT:
                stagegal.set_verdict(g, vd)
            stagegal.set_verdict(ukey, None)
            for part in ("pol", "tex", "thumb"):
                if files.get(part) and os.path.isfile(files[part]):
                    move(files[part], "stages")
            imported.pop(f"{slot}_{name}", None)
            rep["stages"].append(f"{slot}_{name} -> {g}")
        if rep["stages"] and os.path.isfile(imp_path):
            with open(imp_path, "w", encoding="utf-8") as f:
                json.dump(imported, f, indent=1, sort_keys=True)

    import activity
    activity.log(f"reconcile: {len(rep['palettes'])} palettes, {len(rep['stages'])} "
                 "stages matched to the gallery")
    return rep
