"""Repack a built stage's textures into a few atlases so it can be spliced into
a stock MvC2 slot.

Our builder emits one texture per tile (11-59 of them); a stock slot has only
4-16 texture records. This re-lays the art into a handful of atlases and
rewrites the mesh texture ids, UVs and TSP size bits. NO RESAMPLING - every
texel is copied 1:1 (verify_repack confirms texel-exact), so the picture is
unchanged; only the packing differs.

Vendored from CPS2_StageRE/scripts/repack_tex.py and adapted to run in-process
on bytes (no file IO), returning the repacked (POL, TEX). numpy is required
here - this is the deterministic, cache-once step, never run on the fly if a
cached result exists.
"""
import struct

import numpy as np

from . import tex as TEX

POL_RAM = 0x0CEA0000
TEX_RAM = 0x0CC00000
REC = 16
MESH_MARK = bytes.fromhex("fec0a148")      # 0x48A1C0FE, first word of a mesh header
HDR = 0x34                                 # mesh header size; strip bytes follow, total at +0x30
TSP = -0x14                                # TSP word, relative to the marker: bits 5-3 = U size, 2-0 = V size
# build_stage gives every mesh POINT sampling (TSP filter bits 00), so the
# hardware never blends neighbouring texels and nothing bleeds across a seam -
# no guard border needed.
PADX = 0
PADY = 0


def log2sz(n):
    """The 3-bit texture size code the TSP word wants: 8 << code == n."""
    k = 0
    while (8 << k) < n:
        k += 1
    return k


def pow2(n, lo=8):
    p = lo
    while p < n:
        p *= 2
    return p


def parse(pol, tex):
    b = bytearray(pol)
    mt, mc, trs, tre = struct.unpack_from("<4I", b, 0)
    recs, i = [], trs - POL_RAM
    while i + REC <= tre - POL_RAM:
        w, h, fmt, fl, _, base, _ = struct.unpack_from("<HHBBHII", b, i)
        if w == 0 and h == 0:
            break
        recs.append(dict(w=w, h=h, fmt=fmt, fl=fl, off=base - TEX_RAM))
        i += REC
    ptrs = list(struct.unpack_from(f"<{mc}I", b, mt - POL_RAM))
    meshes = []                            # (file offset of the mesh, texId, [(vertex offset, u, v), ...])
    for mi, p in enumerate(ptrs):
        start = p - POL_RAM
        end = (ptrs[mi + 1] - POL_RAM) if mi + 1 < len(ptrs) else len(b)
        j = start
        while True:
            j = b.find(MESH_MARK, j, end)
            if j < 0:
                break
            tid = struct.unpack_from("<I", b, j + 4)[0]
            total = struct.unpack_from("<I", b, j + 0x30)[0]
            q, stop, vs = j + HDR, j + HDR + total, []
            while q + 8 <= stop:
                flags, cnt = struct.unpack_from("<II", b, q)
                if cnt == 0 or cnt > 64:
                    break
                for k in range(cnt):
                    o = q + 8 + k * 32
                    u, v = struct.unpack_from("<2f", b, o + 24)
                    vs.append((o, u, v))
                q += 8 + cnt * 32
            meshes.append(dict(at=j, tid=tid, verts=vs))
            j = stop
    return b, bytes(tex), mt, mc, trs, tre, ptrs, recs, meshes


def used_rect(rec, ms):
    """Pixel rect of a texture that its meshes actually address. build_stage
    pads art to a power-of-two width on the right, so width is worth cropping;
    height keeps the existing duplicated border and stays a power of two."""
    us = [u for m in ms for _, u, _ in m["verts"]]
    x0 = max(0, int(np.floor(min(us) * rec["w"])))
    x1 = min(rec["w"], max(int(np.ceil(max(us) * rec["w"])), x0 + 1))
    return x0, 0, x1, rec["h"]


def plan_bins(area, max_w, max_h):
    """Atlas heights for `area` padded texels at width max_w, largest first;
    split rather than round a tail up (wastes almost nothing, costs a record)."""
    rows, out = max(8, -(-area // max_w)), []
    while rows > 0:
        h = min(max_h, pow2(rows))
        if h > rows and h // 2 >= 8:
            h //= 2
        out.append(h)
        rows -= h
    return out


def merge_bins(bins, target, max_h):
    """Stack bins vertically until there are at most `target` of them.

    A slot caps the texture-RECORD count (STG03/STG0C allow only 4, most allow
    7-16), so a port packing into more bins than that must fold some together or
    it can't merge into the slot. Stacking is safe: a cell keeps its x and shifts
    down by the height above it, joined height rounded to a power of two. Bins
    that can't join without exceeding max_h are left alone; the caller stops when
    a pass changes nothing."""
    bins = sorted(bins, key=lambda b: b[0])
    while len(bins) > target:
        best = None
        for i in range(len(bins)):
            for j in range(i + 1, len(bins)):
                h = pow2(bins[i][0] + bins[j][0])
                if h <= max_h and (best is None or h < best[0]):
                    best = (h, i, j)
        if best is None:
            break                                        # nothing joins without busting max_h
        h, i, j = best
        (hi, ci), (hj, cj) = bins[i], bins[j]
        merged = list(ci) + [(t, x, y + hi) for t, x, y in cj]
        bins = [b for k, b in enumerate(bins) if k not in (i, j)] + [(h, merged)]
        bins.sort(key=lambda b: b[0])
    return bins


def guillotine(items, max_w, max_h):
    """Pack pieces into a few power-of-two-tall atlases with a guillotine packer
    (short pieces fill the column beside tall ones).

    items: [(w, h, key)] including padding.  -> [(atlas height, [(key, x, y), ...]), ...]"""
    todo = sorted(items, key=lambda t: (-t[1], -t[0]))
    for w, h, _ in todo:
        if w > max_w or h > max_h:
            raise ValueError(f"piece {w}x{h} does not fit a {max_w}x{max_h} atlas")
    queue, bins = plan_bins(sum(w * h for w, h, _ in todo), max_w, max_h), []
    while todo:
        area = sum(w * h for w, h, _ in todo)
        bh = min(max_h, queue.pop(0) if queue else max(8, pow2(-(-area // max_w))))
        while bh < todo[0][1]:                          # the tallest piece left must fit this atlas
            bh *= 2
        free, cells, rest = [(0, 0, max_w, bh)], [], []
        for w, h, k in todo:
            best = None
            for fi, (fx, fy, fw, fh) in enumerate(free):
                if w <= fw and h <= fh:
                    sc = (fh - h, fw - w)               # best short side fit
                    if best is None or sc < best[0]:
                        best = (sc, fi)
            if best is None:
                rest.append((w, h, k))
                continue
            fx, fy, fw, fh = free.pop(best[1])
            cells.append((k, fx, fy, w, h))
            if fw - w > 0:
                free.append((fx + w, fy, fw - w, h))    # strip beside the piece...
            if fh - h > 0:
                free.append((fx, fy + h, fw, fh - h))   # ...and below it, full width
        bins.append((pow2(max(y + h for _, _, y, _, h in cells)), [(k, x, y) for k, x, y, _, _ in cells]))
        todo = rest
    return bins


def _safe_v(f):
    """NaomiLib marks a real vertex by bit 0 of x and of v; keep v's bit set."""
    raw = struct.unpack("<I", struct.pack("<f", f))[0] | 1
    return struct.unpack("<f", struct.pack("<I", raw))[0]


def repack(pol, tex, max_tex=None, max_w=1024, max_h=1024):
    """Repack a port's (POL, TEX) into a few atlases. Returns (pol_out, tex_out).

    max_tex caps the atlas (texture-record) count: a port that packs into more
    bins than the target slot allows is folded down with merge_bins (fewer
    records means more padding, so the TEX grows - the caller checks it still
    fits the slot's byte budget). None = natural packing, no fold-down."""
    b, tex, mt, mc, trs, tre, ptrs, recs, meshes = parse(pol, tex)
    by_tid = {}
    for m in meshes:
        by_tid.setdefault(m["tid"], []).append(m)

    # decode each drawn texture and crop it to the rect its meshes address
    pieces = {}
    for tid, rec in enumerate(recs):
        ms = by_tid.get(tid)
        if not ms:
            continue                                    # a record nothing draws: drop it
        img = TEX.decode(tex[rec["off"]:rec["off"] + rec["w"] * rec["h"] * 2],
                         rec["w"], rec["h"], rec["fmt"])
        x0, y0, x1, y1 = used_rect(rec, ms)
        pieces[tid] = dict(img=np.asarray(img)[y0:y1, x0:x1], fmt=rec["fmt"], x0=x0)

    # pack, one atlas set per pixel format
    groups = {}
    for fmt in sorted({p["fmt"] for p in pieces.values()}):
        items = [(pieces[t]["img"].shape[1] + 2 * PADX, pieces[t]["img"].shape[0] + 2 * PADY, t)
                 for t, p in pieces.items() if p["fmt"] == fmt]
        groups[fmt] = guillotine(items, max_w, max_h)
    # fold atlases down to the slot's record limit (stack the pair that joins
    # smallest, repeatedly), stopping when a pass can't reduce further
    while max_tex and sum(len(v) for v in groups.values()) > max_tex:
        f = max(groups, key=lambda k: len(groups[k]))
        n = len(groups[f])
        groups[f] = merge_bins(groups[f], n - 1, max_h)
        if len(groups[f]) == n:
            break

    atlases, placed = [], {}                            # placed: tid -> (atlas index, x, y)
    for fmt, bins in groups.items():
        for bh, cells in bins:
            bw = pow2(max(x + pieces[t]["img"].shape[1] + 2 * PADX for t, x, _ in cells))
            at = np.zeros((bh, bw, 4), np.uint8)
            for t, x, y in cells:
                im = pieces[t]["img"]
                ih, iw = im.shape[:2]
                at[y:y + ih, x + PADX:x + PADX + iw] = im
                placed[t] = (len(atlases), x + PADX, y)
            atlases.append((fmt, at))

    # rewrite the meshes onto the atlases
    for tid, ms in by_tid.items():
        if tid not in placed:
            continue
        ai, px, py = placed[tid]
        _, at = atlases[ai]
        AW, AH = at.shape[1], at.shape[0]
        rec = recs[tid]
        x0 = pieces[tid]["x0"]
        ih = pieces[tid]["img"].shape[0]
        for m in ms:
            struct.pack_into("<I", b, m["at"] + 4, ai)
            tsp = struct.unpack_from("<I", b, m["at"] + TSP)[0]
            struct.pack_into("<I", b, m["at"] + TSP, (tsp & ~0x3F) | (log2sz(AW) << 3) | log2sz(AH))
            for o, u, v in m["verts"]:
                nu = (px + u * rec["w"] - x0) / AW
                nv = (AH - py - ih + v * rec["h"]) / AH
                struct.pack_into("<2f", b, o + 24, nu, _safe_v(nv))

    # rewrite the record table into the stock table's own slots
    newrecs = b""
    off = 0
    blob = bytearray()
    for fmt, at in atlases:
        newrecs += struct.pack("<HHBBHII", at.shape[1], at.shape[0], fmt, recs[0]["fl"], 0, TEX_RAM + off, 0)
        enc = TEX.encode(at, fmt)
        blob += enc
        off += len(enc)
    newrecs += b"\0" * REC
    if len(newrecs) > (tre - trs):
        raise ValueError("repacked record table longer than the original - unsupported")
    b[trs - POL_RAM:trs - POL_RAM + len(newrecs)] = newrecs
    struct.pack_into("<I", b, 0x0C, trs + len(newrecs))            # texRecEnd
    return bytes(b), bytes(blob)
