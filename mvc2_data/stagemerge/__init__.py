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


def fits(repacked_pol, repacked_tex, stock_pol, stock_tex):
    """True if the repacked port fits the slot (records and bytes both within)."""
    pr, pb = port_caps(repacked_pol, repacked_tex)
    sr, sb = slot_caps(stock_pol, stock_tex)
    return pr <= sr and pb <= sb
