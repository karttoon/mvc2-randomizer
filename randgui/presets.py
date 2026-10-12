#!/usr/bin/env python3
"""presets.py - curated mixes ("karttoon's mix" etc.): someone's content choices.

A preset is a JSON bundle of what someone curated:
  { "name": ..., "author": ..., "updated": "YYYY-MM-DD", "description": ...,
    "palette_verdicts": {"Char/file.png": "keep"|"delete", ...},
    "stage_verdicts":   {"d_00": "delete", ...},
    # optional (format 2) - older mixes simply don't have these:
    "palette_locks":   {"Char BTN": "file.png", ...},    # the six picks
    "stage_locks":     {"00": "c_XX_...", ...},
    "stage_playlists": {"name": ["stage key", ...]},
    "stage_playlist":  "active playlist name" | null,
    "settings":        {"randomize_stages": bool, "distribute_ports": bool},
    "fx": {"mode", "chaos_seed", "gradient", "neon_color", "global_color",
           "state": {...}, "locks": {...}} }

Presets ship in the mvc2-skins repo (presets/*.json) and land in the app's
presets/ folder via Download/Update. Applying one NEVER touches the user's
own drop-ins: palette keys containing '/custom/' and stage keys starting
with 'u_' are ignored on apply and stripped on export.
"""
import os, json, datetime

import config

_CUSTOM_PAL = "/custom/"
_CUSTOM_STG = "u_"


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)


def list_presets():
    """[{path, name, author, updated, keeps, rejects, stages}] sorted by name."""
    out = []
    d = config.PRESETS
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        if not f.lower().endswith(".json"):
            continue
        path = os.path.join(d, f)
        try:
            p = _load(path)
            pv = p.get("palette_verdicts", {})
            out.append({
                "path": path,
                "name": p.get("name", f[:-5]),
                "author": p.get("author", "?"),
                "updated": p.get("updated", "?"),
                "description": p.get("description", ""),
                "keeps": sum(1 for v in pv.values() if v == "keep"),
                "rejects": sum(1 for v in pv.values() if v == "delete"),
                "stages": len(p.get("stage_verdicts", {})),
                "pal_locks": len(p.get("palette_locks", {})),
                "stage_locks": len(p.get("stage_locks", {})),
                "playlists": len(p.get("stage_playlists", {})),
                "fx": bool(p.get("fx")),
            })
        except Exception:
            continue
    return sorted(out, key=lambda p: p["name"].lower())


def _merge(target_path, incoming, mode, is_custom):
    """Merge incoming verdicts into a verdict file. Returns (applied, kept)."""
    current = _read_json(target_path)
    applied = 0
    if mode == "replace":
        preserved = {k: v for k, v in current.items() if is_custom(k)}
        new = dict(preserved)
        for k, v in incoming.items():
            if is_custom(k):
                continue
            new[k] = v
            applied += 1
        _write_json(target_path, new)
        return applied, len(preserved)
    # fill: only keys the user hasn't judged
    new = dict(current)
    for k, v in incoming.items():
        if is_custom(k) or k in new:
            continue
        new[k] = v
        applied += 1
    _write_json(target_path, new)
    return applied, len(current)


def _is_custom_pal_file(fn):
    return str(fn).lower().startswith("custom/")


def _stage_keys_on_disk():
    import stagegal
    return {v["key"] for _t, vs in stagegal.sections() for v in vs}


def _apply_palette_locks(incoming, mode):
    """Returns (applied, missing). Buttons locked to the user's own custom/
    files are never overwritten."""
    import locks
    cur = locks.load_locks()
    applied = missing = 0
    new = dict(cur)
    if mode == "replace":                 # start from: only the custom picks kept
        new = {k: v for k, v in cur.items() if v and _is_custom_pal_file(v)}
    for k, fn in incoming.items():
        folder, _, btn = k.rpartition(" ")
        if not folder or btn not in locks.BUTTONS or _is_custom_pal_file(fn):
            continue
        if new.get((folder, btn)) and (mode != "replace"
                                       or _is_custom_pal_file(new[(folder, btn)])):
            continue                      # fill: already locked / custom pick
        if not os.path.isfile(os.path.join(config.SKINS, folder, fn)):
            missing += 1                  # not downloaded here - stays random
            continue
        new[(folder, btn)] = fn
        applied += 1
    locks._write(new)
    return applied, missing


def _apply_stage_locks(incoming, mode):
    import stagegal
    have = _stage_keys_on_disk()
    cur = stagegal.load_locks()
    applied = missing = 0
    if mode == "replace":                 # keep only the user's own drop-in locks
        for sid, key in list(cur.items()):
            if key and not str(key).startswith(_CUSTOM_STG):
                stagegal.set_lock(sid, None)
        cur = stagegal.load_locks()
    for sid, key in incoming.items():
        if not key or str(key).startswith(_CUSTOM_STG):
            continue
        if cur.get(sid) and (mode != "replace"
                             or str(cur[sid]).startswith(_CUSTOM_STG)):
            continue
        if key not in have:
            missing += 1
            continue
        stagegal.set_lock(sid, key)
        applied += 1
    return applied, missing


def _apply_playlists(incoming, active, mode):
    """Adds the mix's playlists (same name: replaced on fresh start, left alone
    on fill). The user's other playlists are never removed."""
    import stagegal
    d = stagegal.load_playlists()
    added = 0
    for name, keys in incoming.items():
        if name in d["playlists"] and mode != "replace":
            continue
        d["playlists"][name] = [k for k in keys
                                if not str(k).startswith(_CUSTOM_STG)]
        added += 1
    if added and not d["editing"]:
        d["editing"] = next(iter(incoming))
    stagegal.save_playlists(d)
    return added


def _apply_fx(fx, mode):
    """Fresh start adopts the mix's whole FX setup; fill only adds per-effect
    locks/toggles for effects you haven't set."""
    import randomize
    if mode == "replace":
        for ck, fk in (("fx_mode", "mode"), ("fx_chaos_seed", "chaos_seed"),
                       ("fx_gradient", "gradient"), ("fx_neon_color", "neon_color"),
                       ("fx_global_color", "global_color")):
            if fk in fx:
                randomize.set_config_key(ck, fx[fk])
        for path, fk in ((config.FX_STATE_JSON, "state"),
                         (config.FX_LOCKS_JSON, "locks")):
            _write_json(path, fx.get(fk) or {})
        return len(fx.get("locks") or {})
    n = 0
    for path, fk in ((config.FX_STATE_JSON, "state"), (config.FX_LOCKS_JSON, "locks")):
        cur = _read_json(path)
        for k, v in (fx.get(fk) or {}).items():
            if k not in cur:
                cur[k] = v
                n += fk == "locks"
        _write_json(path, cur)
    return n


def apply_preset(path, mode):
    """Apply a preset ('replace' = fresh start, 'fill' = fill gaps). Verdicts,
    then (format 2 mixes) palette/stage locks, playlists, FX. Returns a
    summary dict; *_missing counts locks whose files aren't downloaded here."""
    p = _load(path)
    pal_applied, _ = _merge(config.VERDICTS_JSON,
                            p.get("palette_verdicts", {}), mode,
                            lambda k: _CUSTOM_PAL in k)
    stg_applied, _ = _merge(config.STAGE_VERDICTS_JSON,
                            p.get("stage_verdicts", {}), mode,
                            lambda k: k.startswith(_CUSTOM_STG))
    out = {"name": p.get("name", os.path.basename(path)),
           "palettes": pal_applied, "stages": stg_applied, "mode": mode,
           "pal_locks": 0, "pal_locks_missing": 0, "stage_locks": 0,
           "stage_locks_missing": 0, "playlists": 0, "fx_locks": 0,
           "fx": False}
    if p.get("palette_locks"):
        out["pal_locks"], out["pal_locks_missing"] = _apply_palette_locks(
            p["palette_locks"], mode)
    if p.get("stage_locks") or mode == "replace" and "stage_locks" in p:
        out["stage_locks"], out["stage_locks_missing"] = _apply_stage_locks(
            p.get("stage_locks") or {}, mode)
    if p.get("stage_playlists"):
        out["playlists"] = _apply_playlists(p["stage_playlists"],
                                            p.get("stage_playlist"), mode)
    import randomize
    if mode == "replace":
        if "stage_playlist" in p:
            act = p.get("stage_playlist")
            randomize.set_config_key("stage_playlist", act if act else None)
        for k, v in (p.get("settings") or {}).items():
            if k in ("randomize_stages", "distribute_ports"):
                randomize.set_config_key(k, bool(v))
    if p.get("fx"):
        out["fx_locks"] = _apply_fx(p["fx"], mode)
        out["fx"] = True
    return out


def export_preset(out_path, name, author, description=""):
    """Write the user's current curation as a shareable curated mix: verdicts
    plus palette/stage locks, stage playlists and the FX setup. The user's own
    drop-ins (custom/ palettes, u_ stages) are left out - others don't have
    those files."""
    import locks, stagegal, randomize
    pal = {k: v for k, v in _read_json(config.VERDICTS_JSON).items()
           if _CUSTOM_PAL not in k}
    stg = {k: v for k, v in _read_json(config.STAGE_VERDICTS_JSON).items()
           if not k.startswith(_CUSTOM_STG)}
    plocks = {f"{f} {b}": fn for (f, b), fn in locks.load_locks().items()
              if fn and not _is_custom_pal_file(fn)}
    slocks = {s: k for s, k in stagegal.load_locks().items()
              if k and not str(k).startswith(_CUSTOM_STG)}
    pls = {n: [k for k in ks if not str(k).startswith(_CUSTOM_STG)]
           for n, ks in stagegal.load_playlists()["playlists"].items()}
    cfg = randomize.get_config()
    fx = {"mode": cfg.get("fx_mode", "Default"),
          "chaos_seed": int(cfg.get("fx_chaos_seed", 0) or 0),
          "gradient": cfg.get("fx_gradient") or "Normal",
          "neon_color": cfg.get("fx_neon_color") or "Auto",
          "global_color": cfg.get("fx_global_color") or None,
          "state": _read_json(config.FX_STATE_JSON),
          "locks": _read_json(config.FX_LOCKS_JSON)}
    active = cfg.get("stage_playlist")
    _write_json(out_path, {
        "name": name,
        "author": author,
        "updated": datetime.date.today().isoformat(),
        "description": description,
        "format": 2,
        "palette_verdicts": pal,
        "stage_verdicts": stg,
        "palette_locks": plocks,
        "stage_locks": slocks,
        "stage_playlists": pls,
        "stage_playlist": active if active in pls else None,
        "settings": {"randomize_stages": bool(cfg.get("randomize_stages")),
                     "distribute_ports": bool(cfg.get("distribute_ports"))},
        "fx": fx,
    })
    return {"palettes": len(pal), "stages": len(stg), "pal_locks": len(plocks),
            "stage_locks": len(slocks), "playlists": len(pls),
            "fx_locks": len(fx["locks"])}
