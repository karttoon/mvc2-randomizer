#!/usr/bin/env python3
"""presets.py - curated verdict mixes ("karttoon's mix" etc.).

A preset is a JSON bundle of gallery verdicts someone curated:
  { "name": ..., "author": ..., "updated": "YYYY-MM-DD", "description": ...,
    "palette_verdicts": {"Char/file.png": "keep"|"delete", ...},
    "stage_verdicts":   {"d_00": "delete", ...} }

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


def apply_preset(path, mode):
    """Apply a preset ('replace' or 'fill'). Returns a summary dict."""
    p = _load(path)
    pal_applied, _ = _merge(config.VERDICTS_JSON,
                            p.get("palette_verdicts", {}), mode,
                            lambda k: _CUSTOM_PAL in k)
    stg_applied, _ = _merge(config.STAGE_VERDICTS_JSON,
                            p.get("stage_verdicts", {}), mode,
                            lambda k: k.startswith(_CUSTOM_STG))
    return {"name": p.get("name", os.path.basename(path)),
            "palettes": pal_applied, "stages": stg_applied, "mode": mode}


def export_preset(out_path, name, author, description=""):
    """Write the user's current curation as a shareable preset."""
    pal = {k: v for k, v in _read_json(config.VERDICTS_JSON).items()
           if _CUSTOM_PAL not in k}
    stg = {k: v for k, v in _read_json(config.STAGE_VERDICTS_JSON).items()
           if not k.startswith(_CUSTOM_STG)}
    _write_json(out_path, {
        "name": name,
        "author": author,
        "updated": datetime.date.today().isoformat(),
        "description": description,
        "palette_verdicts": pal,
        "stage_verdicts": stg,
    })
    return {"palettes": len(pal), "stages": len(stg)}
