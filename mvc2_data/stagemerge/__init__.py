"""Cross-slot stage merge: put a ported/custom stage into any animated slot.

Two steps (see CPS2_StageRE notes 20-22):
  repack(port_pol, port_tex) -> (pol, tex)   # numpy; deterministic; cache it
  merge(stock_pol, stock_tex, slot, repacked_pol, repacked_tex) -> (pol, tex)  # stdlib; fast

`merge` output is exactly the stock POL/TEX size, so the game treats it as a
stock file and its animation code keeps running behind the hidden stock
geometry. A port only fits a slot when its atlas count and TEX bytes are within
the slot's - fits() checks that up front.
"""
import struct

from .merge_slot import (merge, MergeError, records, POL_RAM,
                         sort_translucent_strips)

REC = 16

# Per-slot model index the port draws at. Default 0 works for 16 slots; STG08
# (the Abyss) runs boss code that drives model 0 and draws it untextured (flat
# green), so its port draws at index 60 - an index the boss code leaves alone,
# validated 0.0px against the reference. See CPS2_StageRE notes/24.
DRAW_INDEX = {"08": 60}


def draw_index_for(slot):
    """The model-table index a port should draw at in this slot."""
    return DRAW_INDEX.get(slot.upper(), 0)


def repack(port_pol, port_tex, max_tex=None, max_w=1024, max_h=1024):
    """Repack a port's textures into a few atlases, folded down to at most
    max_tex records (a slot's texture-record limit) when given. Imports numpy
    lazily so the stdlib merge path stays usable even if numpy is unavailable."""
    from .repack_tex import repack as _repack
    return _repack(port_pol, port_tex, max_tex=max_tex, max_w=max_w, max_h=max_h)


def slot_caps(stock_pol, stock_tex):
    """(texture_record_count, tex_bytes) a slot can host."""
    return len(records(stock_pol)), len(stock_tex)


def port_caps(repacked_pol, repacked_tex):
    """(atlas_count, tex_bytes) a repacked port needs."""
    return len(records(repacked_pol)), len(repacked_tex)


def raw_fits(port_pol, port_tex, stock_pol, stock_tex):
    """True if a port can be merged AS IS (no repack): it fits the slot and its
    texture records sit back to back from the start of texture RAM (merge()
    rewrites the bases that way). Repacking is what breaks tiled textures
    (CPS2_StageRE notes/30), so a port that already fits skips it."""
    try:
        from .merge_slot import TEX_RAM
        recs = records(port_pol)
        if not recs or not fits(port_pol, port_tex, stock_pol, stock_tex):
            return False
        at = TEX_RAM
        for r in recs:
            if r["base"] != at:
                return False
            at += r["w"] * r["h"] * 2
        return at - TEX_RAM <= len(port_tex)
    except Exception:
        return False


def merge_ok(stock_pol, stock_tex, slot, port_pol, port_tex):
    """(True, "") if the port merges into the slot under every rule - incl. the
    DC stub one, which only the merge itself can check (is there room for the
    stub inside the TEX?) - else (False, why). A merge takes ~1 ms."""
    try:
        merge(stock_pol, stock_tex, slot, port_pol, port_tex,
              draw_index=draw_index_for(slot))
        return True, ""
    except MergeError as e:
        return False, str(e)


def fits(repacked_pol, repacked_tex, stock_pol, stock_tex):
    """True if the repacked port fits the slot: at most the slot's records - 1
    (one stays free as the DC stub, CPS2_StageRE notes/30) and its TEX bytes."""
    pr, pb = port_caps(repacked_pol, repacked_tex)
    sr, sb = slot_caps(stock_pol, stock_tex)
    return pr <= sr - 1 and pb <= sb
