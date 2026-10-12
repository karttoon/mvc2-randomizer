#!/usr/bin/env python3
"""randomize.py - thin wrapper around the CLI engine (mvc2_randomizer).

Runs the existing engine in-process so nothing is reimplemented. The engine
prints progress and derives its file paths from module globals, so we point
those at the app's writable data dir and capture stdout into the GUI log.
"""
import os, sys, io, json, contextlib

import config


def _engine():
    """Import the CLI engine and redirect its file paths to our data dir."""
    for p in (config.resource_dir(), config.app_dir()):
        if p not in sys.path:
            sys.path.insert(0, p)
    import mvc2_randomizer as engine
    # The engine reads these module globals at call time; repoint them so a
    # frozen exe writes config/locks/last-run/verdicts next to itself, not into
    # the read-only PyInstaller extraction dir.
    # STATE_DIR is where the engine reads/writes loose state (it derives
    # gallery_verdicts.json from it); SCRIPT_DIR is the content root.
    engine.SCRIPT_DIR = config.DATA
    engine.STATE_DIR = config.STATE
    engine.DEFAULT_CONFIG = config.CONFIG_JSON
    engine.DEFAULT_SKINS = config.SKINS
    engine.DEFAULT_LOCKS = config.LOCKS_TXT
    engine.LAST_RUN_LOG = config.LAST_RUN
    engine.PALETTE_STATE = config.PALETTE_STATE
    engine.DEFAULT_STAGES = config.STAGES
    engine.STAGE_VERDICTS = config.STAGE_VERDICTS_JSON
    engine.STAGE_STATE = config.STAGE_STATE
    engine.STAGE_LOCKS = config.STAGE_LOCKS_JSON
    engine.STAGE_PLAYLISTS = config.STAGE_PLAYLISTS_JSON
    engine.CUSTOM_DIR = config.CUSTOM
    engine.PRESETS_DIR = config.PRESETS
    engine.FX_STATE = config.FX_STATE_JSON
    engine.FX_LOCKS = config.FX_LOCKS_JSON
    engine.FX_APPLIED = config.FX_APPLIED_JSON
    return engine


@contextlib.contextmanager
def _capture(progress, status=None):
    """Redirect stdout: completed lines go to `progress`, and carriage-return
    progress updates (e.g. a download bar) go to `status` instead of spamming
    the log. flush() is a no-op on purpose - print(flush=True) on every tick
    would otherwise emit a line per update.
    """
    class _W(io.TextIOBase):
        def __init__(self):
            self.buf = ""
        def write(self, s):
            self.buf += s
            while "\n" in self.buf:
                line, self.buf = self.buf.split("\n", 1)
                if "\r" in line:
                    line = line.rsplit("\r", 1)[-1]
                if progress:
                    progress(line)
            if "\r" in self.buf and status:      # in-progress bar (no newline yet)
                status(self.buf.rsplit("\r", 1)[-1].strip())
            return len(s)
        def flush(self):
            pass
    old = sys.stdout
    w = _W()
    sys.stdout = w
    try:
        yield
    finally:
        sys.stdout = old
        if w.buf and progress:
            progress(w.buf.rsplit("\r", 1)[-1])


def _run(argv, progress=None, status=None):
    engine = _engine()
    os.makedirs(config.SKINS, exist_ok=True)
    old_argv = sys.argv
    sys.argv = ["mvc2_randomizer"] + argv
    try:
        with _capture(progress, status):
            return engine.main() or 0
    finally:
        sys.argv = old_argv


# ----------------------------------------------------------------- public API
def randomize(progress=None, status=None, seed=None, character=None, dry_run=False):
    # --quiet: the full per-character list goes to last_run.txt, not the GUI log.
    argv = ["--skins", config.SKINS, "--config", config.CONFIG_JSON, "--quiet"]
    g = config.game_native()
    if g:
        argv += ["--game", g]
    if seed is not None:
        argv += ["--seed", str(seed)]
    if character:
        argv += ["--character", character]
    if dry_run:
        argv += ["--dry-run"]
    return _run(argv, progress)


def download_palettes(progress=None, status=None):
    return _run(["--gallery-download", "--skins", config.SKINS], progress, status)


def reset_palettes(progress=None, status=None, character=None):
    """Reset character palettes to vanilla (one character, or all if None);
    leaves other game data intact."""
    argv = ["--reset-palettes", "--skins", config.SKINS]
    if character:
        argv += ["--character", character]
    g = config.game_native()
    if g:
        argv += ["--game", g]
    return _run(argv, progress)


def unprotect(progress=None, status=None, character=None):
    """Unlock protected characters (external edits) for randomizing again."""
    argv = ["--unprotect", "--skins", config.SKINS]
    if character:
        argv += ["--character", character]
    g = config.game_native()
    if g:
        argv += ["--game", g]
    return _run(argv, progress)


def protected_chars():
    """(protected, unnotified) character-folder lists from palette_state.json."""
    try:
        with open(config.PALETTE_STATE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        return [], []
    prot = state.get("protected", [])
    notified = set(state.get("notified", []))
    return prot, [c for c in prot if c not in notified]


def _mix_setting_files():
    """Files whose changes only reach the game on the next Randomize."""
    return [config.LOCKS_TXT, config.STAGE_LOCKS_JSON, config.FX_STATE_JSON,
            config.FX_LOCKS_JSON, config.CONFIG_JSON]


def mix_summary():
    """What a shared mix would contain right now, for the export dialog:
    palette locks (characters with all six buttons picked), stage locks, FX
    mode/locks, and whether any setting changed after the game file was last
    written (then the arc doesn't include it yet - re-randomize first)."""
    import locks as pal_locks
    import stagegal
    lk = pal_locks.load_locks()
    folders = pal_locks._all_folders()
    full = sum(1 for f in folders if pal_locks.count_locked(f, lk) == 6)
    partial = sum(1 for f in folders if 0 < pal_locks.count_locked(f, lk) < 6)
    slocks = {k: v for k, v in stagegal.load_locks().items() if v}
    cfg = get_config()
    arc = config.game_arc()
    arc_mtime = os.path.getmtime(arc) if arc and os.path.exists(arc) else None
    stale = []
    if arc_mtime is not None:
        for p in _mix_setting_files():
            try:
                if os.path.getmtime(p) > arc_mtime + 1:
                    stale.append(os.path.basename(p))
            except OSError:
                pass
    return {
        "chars_full": full, "chars_partial": partial, "chars_total": len(folders),
        "stages_locked": len(slocks), "stages_total": len(stagegal.SLOTS),
        "stages_on": bool(cfg.get("randomize_stages")),
        "fx_mode": cfg.get("fx_mode", "Default"), "fx_locks": len(fx_locks()),
        "arc": arc, "arc_mtime": arc_mtime, "stale": stale,
    }


def verify_mix():
    """Check the game file on disk actually contains the mix, by re-running the
    engine's own logic on in-memory copies of it (read-only - nothing written):

      palettes: every locked button's body palette == what apply_skin writes
                for that locked PNG.
      fx:       every FX palette the current settings recolor == what apply_fx
                produces from THIS file's skins (so Shades/per-button effects
                are checked against the locked palette on each button).
      stages:   every locked slot was placed as locked on the last Randomize
                (from its log), not dropped or re-rolled.

    Returns {"palettes": {...}, "fx": {...}, "stages": {...}, "stale": [...]}
    where each part has ok/checked counts and a 'bad' list of readable lines.
    """
    import locks as pal_locks
    import stagegal
    eng = _engine()
    fxm = fx_module()
    from mvc2_data.steam import STEAM_PALETTE_OFFSETS, palette_offset
    rom = live_rom(force=True)
    if rom is None:
        raise FileNotFoundError("Couldn't read game_50.arc - set the game folder first.")
    out = {"stale": mix_summary()["stale"]}
    prot = set(protected_chars()[0])

    # ---- palettes ---------------------------------------------------------
    lk = pal_locks.load_locks()
    work = bytearray(rom)
    checked, bad, unpicked = 0, [], []
    for folder in pal_locks._all_folders():
        cid = eng.FOLDER_TO_CHAR_ID.get(folder)
        if cid is None:
            continue
        for bi, btn in enumerate(pal_locks.BUTTONS):
            val = lk.get((folder, btn))
            if not val:
                unpicked.append(f"{folder} {btn}")
                continue
            checked += 1
            if folder in prot:
                bad.append(f"{folder} {btn}: character is protected (skipped by "
                           "Randomize) - unlock it on the Randomize tab")
                continue
            if val.lower().startswith("custom/"):
                d = os.path.join(config.CUSTOM, "skins", folder)
                actual = eng.find_skin_file(d, val[7:]) if os.path.isdir(d) else None
            else:
                d = os.path.join(config.SKINS, folder)
                actual = eng.find_skin_file(d, val) if os.path.isdir(d) else None
            if not actual:
                bad.append(f"{folder} {btn}: locked file not found ({val}) - "
                           "Randomize gives this button a random palette")
                continue
            if not eng.apply_skin(work, cid, bi, os.path.join(d, actual)):
                bad.append(f"{folder} {btn}: couldn't read {val}")
                continue
            off = palette_offset(cid, bi, 0)
            if bytes(work[off:off + 32]) != bytes(rom[off:off + 32]):
                bad.append(f"{folder} {btn}: game has a different palette than "
                           f"the locked {val}")
    out["palettes"] = {"checked": checked, "ok": checked - len(bad), "bad": bad,
                       "unpicked": unpicked}

    # ---- fx ---------------------------------------------------------------
    cfg = get_config()
    mode = cfg.get("fx_mode", "Default")
    flocks = fx_locks()
    tg = fxm.load_fx_targets()
    enabled = None
    st = fx_state()
    if st:
        enabled = {s["id"] for d in tg.values() for s in d["sheets"]
                   if st.get(s["id"], s["default_on"])}
    skip = {eng.FOLDER_TO_CHAR_ID[f] for f in prot if f in eng.FOLDER_TO_CHAR_ID}
    work = bytearray(rom)
    applied = {}
    log = []
    fxm.apply_fx(work, mode, targets=tg, enabled=enabled,
                 chaos_seed=int(cfg.get("fx_chaos_seed", 0) or 0),
                 skip_uids=skip, locks=flocks,
                 applied_out=applied, log=log,
                 base_color=cfg.get("fx_global_color") or None,
                 gradient=cfg.get("fx_gradient") or "Normal",
                 neon=cfg.get("fx_neon_color") or "Auto")
    # which sheet owns each entry, for readable messages
    owner = {}
    for name, d in tg.items():
        for s in d["sheets"]:
            ents = s.get("entries") or [s["slot"]]
            for e in ents:
                owner.setdefault((d["uid"], e), (name, s["label"][:40], s["id"]))
    entries = [(uid, e) for uid, es in applied.items() for e in set(es)]
    bad_sheets = {}
    for uid, e in entries:
        off = STEAM_PALETTE_OFFSETS[uid] + e * 32
        if bytes(work[off:off + 32]) != bytes(rom[off:off + 32]):
            nm, lab, sid = owner.get((uid, e), (str(uid), f"entry {e}", None))
            bad_sheets.setdefault((nm, lab), 0)
            bad_sheets[(nm, lab)] += 1
    sheets = {owner.get((u, e), (u, e, None))[2] for u, e in entries}
    out["fx"] = {"checked": len(sheets), "ok": len(sheets) - len(bad_sheets),
                 "entries": len(entries), "mode": mode, "locks": len(flocks),
                 "bad": [f"{n}: {l} ({k} palette(s) differ)"
                         for (n, l), k in sorted(bad_sheets.items())]}

    # ---- stages -----------------------------------------------------------
    slocks = {k: v for k, v in stagegal.load_locks().items() if v}
    sbad, lines = [], {}
    try:
        txt = open(config.LAST_RUN, encoding="utf-8", errors="replace").read()
        if "Stages:" in txt:
            for ln in txt.split("Stages:", 1)[1].splitlines():
                ln = ln.strip()
                if ln[:2] in {s for s in stagegal.SLOTS} and " " in ln:
                    lines.setdefault(ln[:2], []).append(ln)
    except OSError:
        txt = ""
    names = {v["key"]: stagegal.display_name(v)
             for _t, vs in stagegal.sections() for v in vs}
    for slot, key in sorted(slocks.items()):
        got = lines.get(slot, [])
        if not cfg.get("randomize_stages"):
            sbad.append(f"STG {slot}: stage randomizing is off - stages stay stock")
        elif not got:
            sbad.append(f"STG {slot}: not in the last Randomize log - Randomize again")
        elif not any(l.endswith("[locked]") for l in got):
            sbad.append(f"STG {slot}: locked {names.get(key, key)} wasn't placed - "
                        + got[-1].split(":", 1)[-1].strip())
    out["stages"] = {"checked": len(slocks), "ok": len(slocks) - len(sbad),
                     "bad": sbad}
    return out


def export_arc(dest, progress=None, status=None):
    """Copy the game's current game_50.arc (palettes, injected stages and FX
    recolors as of the last Randomize) to `dest` for sharing."""
    import shutil
    src = config.game_arc()
    if not src or not os.path.exists(src):
        raise FileNotFoundError("game_50.arc not found - set the game folder first.")
    if os.path.abspath(src) == os.path.abspath(dest):
        raise ValueError("Pick a different folder than the game's own arc folder.")
    if status:
        status("Exporting game_50.arc")
    shutil.copy2(src, dest)
    if progress:
        progress(f"Exported {os.path.getsize(dest) / 1048576:.1f} MB -> {dest}")
    return dest


def fx_protected():
    """{FX-tab character name: protection folder} for characters whose palettes
    were edited outside the randomizer. Protection covers the whole palette
    block (body AND effects), so FX skips them in every mode, Stock included."""
    prot = set(protected_chars()[0])
    if not prot:
        return {}
    try:
        eng = _engine()
        out = {}
        for name, d in fx_targets().items():
            uid = d.get("uid")
            folder = eng.CHAR_ID_TO_FOLDER.get(uid) or eng.safe_name(eng.CHARACTERS[uid])
            if folder in prot:
                out[name] = folder
        return out
    except Exception:
        return {}


def mark_protected_notified():
    """Record that the GUI has announced the current protections."""
    try:
        with open(config.PALETTE_STATE, "r", encoding="utf-8") as f:
            state = json.load(f)
        state["notified"] = list(state.get("protected", []))
        with open(config.PALETTE_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1, sort_keys=True)
    except Exception:
        pass


def stage_fit_detail():
    """(fit, why) for cross-slot ports, computed exactly as the engine does at
    randomize (natural repack, folding down to the slot's texture-record limit
    when needed, then the two-axis fit check).

      fit: {slot_id: {port_key, ...}}            ports that can go in the slot
      why: {slot_id: {port_key: "reason", ...}}  every other port, and why not

    Uses the engine's on-disk repack cache, so it is fast when warm and only
    pays the numpy repack on a cold cache. ({}, {}) if the merge tooling is
    missing."""
    try:
        engine = _engine()
        from mvc2_data import stagemerge
        import stagegal
    except Exception:
        return {}, {}
    cache = os.path.join(config.STAGES, "repack_cache")
    bins = config.STAGES + "/bins"
    mb = lambda n: f"{n / 1048576:.2f} MB"
    ports, never = [], {}      # never: key -> reason it can't go in ANY slot
    for title, vs in stagegal.sections():
        if not title.startswith("Ports"):
            continue
        for v in vs:
            pol, tex = v.get("pol_path"), v.get("tex_path")
            if not (pol and tex):
                never[v["key"]] = "no POL file - Training only"
                continue
            try:
                if not engine.is_pipeline_port(pol):
                    never[v["key"]] = ("not built as a mergeable port - "
                                       "Training only")
                    continue
                rp = engine.repacked_port(pol, tex, cache)
            except Exception:
                rp = None
            if rp:
                ports.append((v["key"], pol, tex, rp))
            else:
                never[v["key"]] = "couldn't be repacked - Training only"
    fit, why = {}, {}
    for sid in stagegal.SLOTS:
        if sid == stagegal.TRAINING:
            continue
        try:
            spol = open(os.path.join(bins, f"d_{sid}_pol.BIN"), "rb").read()
            stex = open(os.path.join(bins, f"d_{sid}_tex.BIN"), "rb").read()
        except OSError:
            continue
        sr, sb = stagemerge.slot_caps(spol, stex)
        keys, no = set(), dict(never)
        for key, pol, tex, rp in ports:
            rpol, rtex = rp
            if len(stagemerge.records(rpol)) > sr:
                folded = engine.repacked_port(pol, tex, cache, max_tex=sr)
                if not folded:
                    no[key] = (f"needs {len(stagemerge.records(rpol))} texture "
                               f"pages, slot holds {sr}")
                    continue
                rpol, rtex = folded
            pr, pb = stagemerge.port_caps(rpol, rtex)
            if pr > sr:
                no[key] = f"needs {pr} texture pages, slot holds {sr}"
            elif pb > sb:
                no[key] = f"textures {mb(pb)} > slot's {mb(sb)}"
            else:
                keys.add(key)
        fit[sid], why[sid] = keys, no
    return fit, why


def get_config():
    """The user's randomizer_config.json as a dict ({} if absent/broken)."""
    try:
        # utf-8-sig tolerates a BOM (Notepad/PowerShell) that plain json would reject
        with open(config.CONFIG_JSON, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


def set_config_key(key, value):
    """Persist one config setting (comment keys are preserved)."""
    c = get_config()
    c[key] = value
    with open(config.CONFIG_JSON, "w", encoding="utf-8") as f:
        json.dump(c, f, indent=2)


# ----------------------------------------------------------------- FX helpers
def fx_module():
    """The engine's FX module (ensures mvc2_data is importable)."""
    _engine()
    from mvc2_data import fx as fxmod
    return fxmod


def fx_targets():
    """The per-character FX target catalogue ({} if missing)."""
    try:
        return fx_module().load_fx_targets()
    except Exception:
        return {}


_FX_SPRITES = None


def fx_sprites():
    """Bundled effect sprites {spr_key: (w, h, index_bytes)} (cached)."""
    global _FX_SPRITES
    if _FX_SPRITES is None:
        try:
            _FX_SPRITES = fx_module().load_fx_sprites()
        except Exception:
            _FX_SPRITES = {}
    return _FX_SPRITES


_FX_BODYSPR = None


def fx_body_sprite(char_name):
    """Character body sprite (w, h, index_bytes) for the index-highlight view."""
    global _FX_BODYSPR
    if _FX_BODYSPR is None:
        try:
            _FX_BODYSPR = fx_module().load_fx_bodysprites()
        except Exception:
            _FX_BODYSPR = {}
    try:
        uid = fx_targets().get(char_name, {}).get("uid")
        return _FX_BODYSPR.get(uid)
    except Exception:
        return None


def fx_state():
    """Per-sheet on/off overrides written by the FX tab ({id: bool})."""
    try:
        with open(config.FX_STATE_JSON, encoding="utf-8-sig") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def set_fx_state(d):
    """Persist the per-sheet overrides (only entries that differ from default)."""
    try:
        with open(config.FX_STATE_JSON, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1, sort_keys=True)
    except OSError:
        pass


def fx_locks():
    """Per-palette pinned settings ({sheet_id: {mode, base?, seed?}})."""
    try:
        with open(config.FX_LOCKS_JSON, encoding="utf-8-sig") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return {}
    except Exception:
        return {}
    # retired mode names (Match -> Shades) so the GUI shows a valid choice
    try:
        canon = fx_module().canon_mode
    except Exception:
        return d
    for lk in d.values():
        if isinstance(lk, dict) and lk.get("mode"):
            lk["mode"] = canon(lk["mode"])
    return d


def fx_render_opts():
    """(gradient, neon) FX render settings from config, for previews."""
    c = get_config()
    return (c.get("fx_gradient") or "Normal", c.get("fx_neon_color") or "Auto")


def set_fx_locks(d):
    """Persist per-palette locks."""
    try:
        with open(config.FX_LOCKS_JSON, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1, sort_keys=True)
    except OSError:
        pass


def reset_fx():
    """Revert all FX settings to default: mode off, seed 0, no per-sheet or
    locks. Leaves fx_applied.json so the next Randomize
    reverts the game's currently-applied effects to stock."""
    set_config_key("fx_mode", "Default")
    set_config_key("fx_chaos_seed", 0)
    set_config_key("fx_global_color", None)
    set_config_key("fx_gradient", "Normal")
    set_config_key("fx_neon_color", "Auto")
    for p in (config.FX_STATE_JSON, config.FX_LOCKS_JSON):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def fx_preview(char_name, mode, chaos_seed=0):
    """Resolve a character's FX sheets for the FX tab.

    Each sheet's preview uses its EFFECTIVE settings: a per-palette lock
    (mode/base/seed) wins, else the global mode + the character's base index +
    the global seed. Colours preview from the STOCK palette (the live skin isn't
    known until a randomize runs). Returns {uid, base_idx, body,
    sheets:[{..., eff_mode, locked, trans}]}.
    """
    try:
        fxm = fx_module()
        from mvc2_data.steam import load_vanilla_palettes
    except Exception:
        return None
    tg = fxm.load_fx_targets()
    d = tg.get(char_name)
    if not d:
        return None
    uid = d["uid"]
    van = load_vanilla_palettes()
    blk = van.get(uid)
    if blk is None:
        return None
    nent = len(blk) // 32
    char_base_idx = d.get("base_idx", 0)
    body16 = fxm._read_entry(blk, 0)                 # [(r,g,b,a)] x16 (stock body)
    body = [(c[0], c[1], c[2]) for c in body16]
    cseed = fxm._char_seed(char_name)
    locks = fx_locks()
    gc = fxm.color_hsl(get_config().get("fx_global_color"))   # global color anchor
    grad, neon = fx_render_opts()
    mode = fxm.canon_mode(mode)

    def base_hsl(idx):
        if idx is None:                             # no explicit lock base
            if gc:
                return gc
            idx = char_base_idx
        return fxm._base_hsl(body, idx if 0 <= idx < len(body) else char_base_idx)

    sheets = []
    for s in d["sheets"]:
        if s.get("verdict") == "exclude":
            continue
        entry = s["slot"] if 0 <= s["slot"] < nent else 0
        cols = fxm._read_entry(blk, entry)
        lk = locks.get(s["id"])
        if lk:                                      # per-effect lock
            eff_mode = lk.get("mode", "Default")
            if eff_mode in (None, "Default"):
                trans = list(cols)
            else:
                bh = base_hsl(lk.get("base"))
                trans = fxm.recolor(cols, eff_mode, bh[0], bh[1], cseed,
                                    int(lk.get("seed", 0) or 0), grad, neon)
        else:                                       # follow global
            eff_mode = mode
            if mode in (None, "Default"):
                trans = list(cols)
            else:
                bh = base_hsl(None)                 # global color, else char base
                trans = fxm.recolor(cols, mode, bh[0], bh[1], cseed, chaos_seed,
                                    grad, neon)
        sheets.append({
            "id": s["id"], "label": s["label"], "tag": s["tag"],
            "fromPalette": s.get("fromPalette", False), "verdict": s.get("verdict"),
            "default_on": s.get("default_on", False), "slot": s["slot"],
            "spr": s.get("spr"), "orig": cols, "trans": trans,
            "locked": lk is not None, "eff_mode": eff_mode,
        })
    return {"uid": uid, "base_idx": char_base_idx, "body": body16,
            "sheets": sheets}


_LIVE_ROM = {"path": None, "mtime": None, "rom": None}


def live_rom(force=False):
    """The decompressed live game ROM, cached and auto-refreshed on file change
    (so it updates after a randomize). None if the game/arc can't be read."""
    try:
        arc = config.game_arc()
        if not arc or not os.path.exists(arc):
            return None
        mt = os.path.getmtime(arc)
        if (force or _LIVE_ROM["rom"] is None or _LIVE_ROM["path"] != arc
                or _LIVE_ROM["mtime"] != mt):
            engine = _engine()
            _LIVE_ROM.update(path=arc, mtime=mt, rom=engine.read_arc(arc))
        return _LIVE_ROM["rom"]
    except Exception:
        return None


def fx_body_colors(char_name, button, loaded=False):
    """The character's body (Main Color) palette for one color button -> 16
    (r,g,b,a). From the live arc when loaded=True (the real skin on that button),
    else the stock costume. None if unavailable."""
    try:
        fxm = fx_module()
        from mvc2_data.steam import STEAM_PALETTE_OFFSETS, load_vanilla_palettes
    except Exception:
        return None
    d = fxm.load_fx_targets().get(char_name)
    if not d or d["uid"] not in STEAM_PALETTE_OFFSETS:
        return None
    uid = d["uid"]
    entry = button * 8                              # slot 0 of that button
    if loaded:
        rom = live_rom()
        if rom is not None:
            abs_entry = STEAM_PALETTE_OFFSETS[uid] // 32 + entry
            if (abs_entry + 1) * 32 <= len(rom):
                return fxm._read_entry(rom, abs_entry)
    blk = load_vanilla_palettes().get(uid)
    if blk is None:
        return None
    return fxm._read_entry(blk, entry)


def fx_loaded_colors(char_name, sheet_id, button):
    """The CURRENT palette (as loaded in the game) for one sheet+button, read
    straight from the live arc -> [(r,g,b,a)] x16, or None if unavailable."""
    rom = live_rom()
    if rom is None:
        return None
    try:
        fxm = fx_module()
        from mvc2_data.steam import STEAM_PALETTE_OFFSETS
    except Exception:
        return None
    d = fxm.load_fx_targets().get(char_name)
    if not d or d["uid"] not in STEAM_PALETTE_OFFSETS:
        return None
    s = next((x for x in d["sheets"] if x["id"] == sheet_id), None)
    if not s:
        return None
    slot = s["slot"]
    per_btn = s["tag"] == "per-button" and slot < 8
    entry = (slot + button * 8) if per_btn else slot
    base = STEAM_PALETTE_OFFSETS[d["uid"]]
    off = base + entry * 32
    if off + 32 > len(rom):
        return None
    out = []
    for i in range(16):
        v = rom[off + i * 2] | (rom[off + i * 2 + 1] << 8)
        out.append((((v >> 8) & 0xF) * 17, ((v >> 4) & 0xF) * 17,
                    (v & 0xF) * 17, (v >> 12) & 0xF))
    return out


def fx_preview_button(char_name, sheet_id, button, mode, chaos_seed=0):
    """Preview one sheet for one color button (0-5). Uses that button's STOCK
    costume as the base and its own stock effect source, resolving lock/global
    exactly as the engine does. Returns {orig, trans, eff_mode, eff_seed, locked}.
    """
    try:
        fxm = fx_module()
        from mvc2_data.steam import load_vanilla_palettes
    except Exception:
        return None
    tg = fxm.load_fx_targets()
    d = tg.get(char_name)
    if not d:
        return None
    s = next((x for x in d["sheets"] if x["id"] == sheet_id), None)
    if not s:
        return None
    uid = d["uid"]
    blk = load_vanilla_palettes().get(uid)
    if blk is None:
        return None
    nent = len(blk) // 32
    slot = s["slot"]
    per_btn = s["tag"] == "per-button" and slot < 8
    b = button if per_btn else 0
    entry = (slot + b * 8) if per_btn else slot
    if not (0 <= entry < nent):
        entry = slot if 0 <= slot < nent else 0
    cols = fxm._read_entry(blk, entry)
    # Is the stock palette identical across all six colour buttons? (1x effect)
    uniform = False
    if per_btn:
        first = bytes(blk[slot * 32:slot * 32 + 32])
        uniform = all(bytes(blk[(slot + bb * 8) * 32:(slot + bb * 8) * 32 + 32])
                      == first for bb in range(6))

    char_base_idx = d.get("base_idx", 0)
    eff_on = fx_state().get(sheet_id, s.get("default_on", False))
    enabled = {sheet_id} if eff_on else set()
    res = fxm._resolve_settings(fx_locks().get(sheet_id), mode, chaos_seed,
                                enabled, s, char_base_idx)
    if res is None:
        return {"orig": cols, "trans": list(cols), "eff_mode": "Default",
                "eff_seed": 0, "locked": sheet_id in fx_locks(), "uniform": uniform}
    smode, sbidx, sseed = res
    lk = fx_locks().get(sheet_id)
    gc = fxm.color_hsl(get_config().get("fx_global_color"))
    if gc and not (lk and lk.get("mode") and lk.get("base") is not None):
        bh = gc                                     # global color anchor
    else:
        body = [(c[0], c[1], c[2])
                for c in fxm._read_entry(blk, (b * 8) if per_btn else 0)]
        bh = fxm._base_hsl(body, sbidx if 0 <= sbidx < len(body) else char_base_idx)
    grad, neon = fx_render_opts()
    trans = fxm.recolor(cols, smode, bh[0], bh[1], fxm._char_seed(char_name), sseed,
                        grad, neon)
    return {"orig": cols, "trans": trans, "eff_mode": smode, "eff_seed": sseed,
            "locked": sheet_id in fx_locks(), "uniform": uniform}


def last_run_text():
    """Return the contents of the last-run assignment log, or a friendly note."""
    try:
        if os.path.exists(config.LAST_RUN):
            with open(config.LAST_RUN, encoding="utf-8", errors="replace") as fh:
                return fh.read().strip() or "(last run log is empty)"
    except Exception as e:
        return f"(could not read log: {e})"
    return "No randomize has been run yet — no log to show."
