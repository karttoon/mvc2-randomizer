"""Splice a repacked port into a stock MvC2 slot so it works outside Training on
the Steam Collection.

Only Training (0x0B) tolerates a hand-written POL; every other slot runs
per-stage animation code that walks the stock POL and falls over on a slim
custom file. The fix (see CPS2_StageRE notes 20-22): keep the stock file's
SHAPE - same model count, same byte spans, same POL/TEX sizes - but the port's
art becomes model 0 and the stock geometry is hidden by render state (moved to
the translucent list, pointed at a zeroed transparent stub), never deleted, so
the stock code still finds everything it expects.

Vendored from CPS2_StageRE/scripts/merge_slot.py, adapted to run in-process on
bytes (no file IO) and to use only the validated default path - the CLI's
experimental/known-bad flags (--blank-strips, --blank-models, --park-bounds,
--keep-*) are dropped. stdlib only, so it is safe to run at launch.
"""
import struct

POL_RAM = 0x0CEA0000
TEX_RAM = 0x0CC00000
REC = 16
MHDR = 0x18            # model header: u32 1, u32 3, float cx, cy, cz, radius
XHDR = 0x50            # mesh header
TEXID = 0x20           # texId offset inside a mesh header
TOTAL = 0x4C           # strip-bytes offset inside a mesh header
PCW = 0x00             # param control word: bits 26-24 list type (0 opaque, 2 translucent)
PORT_MARK = bytes.fromhex("fec0a148")   # constant radius our builder writes in every port mesh header
SHADE = 6              # TSP bits 7-6: shading (0 decal, 1 modulate)
ISP = 0x04             # ISP/TSP word: bits 31-29 depth compare (0 Never, 4 Greater)
TSP = 0x08             # TSP word: bits 5-3 tex U size, 2-0 V size


class MergeError(Exception):
    """A pairing the merge can't produce (port too big for the slot, bad data)."""


def header(b):
    return struct.unpack_from("<4I", b, 0)


def records(b):
    mt, mc, trs, tre = header(b)
    out, i = [], trs - POL_RAM
    while i + REC <= tre - POL_RAM:
        w, h, fmt, fl, _, base, _ = struct.unpack_from("<HHBBHII", b, i)
        if w == 0 and h == 0:
            break
        out.append(dict(w=w, h=h, fmt=fmt, fl=fl, base=base))
        i += REC
    return out


def model_spans(b):
    mt, mc, trs, tre = header(b)
    ptrs = list(struct.unpack_from(f"<{mc}I", b, mt - POL_RAM))
    return [(ptrs[k] - POL_RAM, (ptrs[k + 1] - POL_RAM) if k + 1 < len(ptrs) else len(b))
            for k in range(mc)]


def mesh_texids(b, start, end):
    """Offsets of the texId field of every mesh in a model, walking strip sizes
    structurally. Returns [] on trailing non-mesh data (the last model's span
    runs to EOF and can carry ~42 KB of extra data - STG03/STG0C)."""
    out, j = [], start + MHDR
    while j + XHDR <= end:
        pcw = struct.unpack_from("<I", b, j)[0]
        tot = struct.unpack_from("<I", b, j + TOTAL)[0]
        if pcw >> 28 not in (8, 9) or tot == 0 or j + XHDR + tot > end:
            return out if out else []
        out.append(j + TEXID)
        j += XHDR + tot
    return out


def sort_translucent_strips(pol):
    """Reorder every translucent mesh's strips back-to-front (ascending z avg) and
    reassign the strip flags (first 0x72, continuations 0xf2). Same file size,
    only strip bytes within a translucent mesh move.

    PowerVR sorts the translucent list in hardware, so a stage authored on a
    Dreamcast renders correctly whatever order its strips are in; the Steam
    Collection paints in submission order, so out-of-order translucent strips
    show their layers wrong. This is a no-op on DC and the fix on Steam - apply
    it to every port unconditionally (opaque meshes are depth-tested, untouched).
    Vendored from CPS2_StageRE/scripts/sort_strips_depth.py."""
    FIRST, CONT = 0x72, 0xf2
    b = bytearray(pol)
    try:
        mt, mc, trs, tre = header(b)
        base = mt - POL_RAM
        ptrs = [struct.unpack_from("<I", b, base + i * 4)[0] - POL_RAM
                for i in range(mc)]
    except struct.error:
        return bytes(b)
    for lo in ptrs:
        if not 0 <= lo < len(b):
            continue
        hi = min([p for p in ptrs if p > lo] + [len(b)])
        o = lo + MHDR
        while o + XHDR <= hi:
            tot = struct.unpack_from("<I", b, o + TOTAL)[0]
            if tot == 0 or o + XHDR + tot > hi:
                break
            pcw = struct.unpack_from("<I", b, o)[0]
            q, stop, strips, bad = o + XHDR, o + XHDR + tot, [], False
            while q + 8 <= stop:
                _f, c = struct.unpack_from("<II", b, q)
                if c == 0 or c > 4096 or q + 8 + c * 32 > stop:
                    bad = True            # garbage/over-running strip: don't touch this mesh
                    break
                blob = bytes(b[q:q + 8 + c * 32])
                zs = [struct.unpack_from("<f", b, q + 8 + k * 32 + 8)[0]
                      for k in range(c)]
                strips.append((sum(zs) / len(zs), blob))
                q += 8 + c * 32
            if not bad and ((pcw >> 24) & 7) == 2 and len(strips) > 1:
                order = sorted(strips, key=lambda s: s[0])   # furthest first
                if [s[1] for s in order] != [s[1] for s in strips]:
                    w = o + XHDR
                    for k, (_z, blob) in enumerate(order):
                        nb = bytearray(blob)
                        struct.pack_into("<I", nb, 0, FIRST if k == 0 else CONT)
                        b[w:w + len(nb)] = nb
                        w += len(nb)
            o += XHDR + tot
    return bytes(b)


def merge(stock_pol, stock_tex, slot, port_pol, port_tex, draw_index=0):
    """Merge a repacked port into a stock slot. Returns (out_pol, out_tex),
    both exactly the stock sizes. Raises MergeError on an impossible pairing.

    draw_index is the model-table index the port draws at. Default 0 (the static
    backdrop) is right for 16 slots. STG08 (the Abyss) runs boss code that drives
    model 0 and draws it untextured (flat green) - that slot needs draw_index=60,
    an index the boss code leaves alone, which renders identical to the reference.
    The port's BYTES always live in the largest model's span; draw_index only
    changes which table entry points at them."""
    sb = bytes(stock_pol)
    stex = bytes(stock_tex)
    ob = bytes(port_pol)
    otex = bytes(port_tex)

    smt, smc, strs, stre = header(sb)
    srecs = records(sb)
    orecs = records(ob)
    spans = model_spans(sb)
    ospans = model_spans(ob)
    if len(orecs) > len(srecs):
        raise MergeError(f"port needs {len(orecs)} texture records, slot {slot} has "
                         f"{len(srecs)} - repack smaller")
    if len(otex) > len(stex):
        raise MergeError(f"port TEX is {len(otex):,} B, slot {slot} holds {len(stex):,} - "
                         f"repack smaller")

    # one stock model becomes the port's; the draw index by default, else the
    # largest (with the two table entries swapped so the port still draws at DI)
    o0s, o0e = ospans[0]
    blob = bytearray(ob[o0s:o0e])
    sizes = [e - s for s, e in spans]
    if not 0 <= draw_index < smc:
        raise MergeError(f"draw_index {draw_index} out of range for slot {slot} "
                         f"({smc} models)")
    victim = draw_index
    if len(blob) > sizes[draw_index]:
        victim = max(range(smc), key=lambda i: sizes[i])
    if len(blob) > sizes[victim]:
        raise MergeError(f"port model is {len(blob):,} B, slot {slot}'s largest model frees "
                         f"only {sizes[victim]:,} - POL would outgrow the slot buffer")

    # switch the port's own meshes to DECAL shading (colour from texture alone),
    # so a slot whose code writes a base colour into "its" model can't tint us
    j = 0
    while True:
        j = blob.find(PORT_MARK, j)
        if j < 0:
            break
        to = j - 0x14
        struct.pack_into("<I", blob, to, struct.unpack_from("<I", blob, to)[0] & ~(3 << SHADE))
        j += 4

    for w in range(0, len(blob) - 3, 4):                 # the port model must carry no absolute pointers
        v = struct.unpack_from("<I", blob, w)[0]
        if 0x0CE00000 <= v < 0x0CF00000 or 0x0CC00000 <= v < 0x0CE00000:
            raise MergeError(f"port model holds an absolute pointer ({v:#x}) at +{w:#x}")

    # assemble IN PLACE: start from the stock POL, never move a byte
    out = bytearray(sb)
    vs, ve = spans[victim]
    out[vs:ve] = bytes(blob) + b"\0" * (ve - vs - len(blob))

    ptrs = list(struct.unpack_from(f"<{smc}I", sb, smt - POL_RAM))
    if victim != draw_index:
        # port bytes live in the victim's span; swap the two table entries so the
        # port draws at draw_index and the stock model there moves to the victim
        ptrs[draw_index], ptrs[victim] = ptrs[victim], ptrs[draw_index]
        struct.pack_into(f"<{smc}I", out, smt - POL_RAM, *ptrs)

    for k in range(smc):
        s, e = spans[k]
        if k == victim:
            continue                                     # that span is the port now
        body = bytearray(sb[s:e])
        for off in (mesh_texids(sb, s, e) or []):
            tid = struct.unpack_from("<I", sb, off)[0]
            if tid >= len(orecs):
                tid = len(srecs) - 1
                struct.pack_into("<I", body, off - s, tid)
            if tid < len(orecs):
                tid = len(srecs) - 1
                struct.pack_into("<I", body, off - s, tid)
            # move the stock mesh to the translucent list, honouring the stub's
            # zero alpha (so it submits but paints nothing)
            po = off - TEXID + PCW
            struct.pack_into("<I", body, po - s,
                             (struct.unpack_from("<I", body, po - s)[0] & ~0x07000000) | 0x02000000)
            to2 = off - TEXID + TSP
            struct.pack_into("<I", body, to2 - s,
                             (struct.unpack_from("<I", body, to2 - s)[0] & 0x0007FFFF) | 0x94800000)
            to = off - TEXID + TSP
            struct.pack_into("<I", body, to - s, struct.unpack_from("<I", body, to - s)[0] & ~0x3F)
            io = off - TEXID + ISP
            struct.pack_into("<I", body, io - s, struct.unpack_from("<I", body, io - s)[0] & 0x1FFFFFFF)
        out[s:e] = body

    # records: port's fill the first slots, the rest become stubs marching past
    # the TEX end (or padded inside it)
    trs = strs - POL_RAM
    off = 0
    for i, r in enumerate(orecs):
        struct.pack_into("<HHBBHII", out, trs + i * REC, r["w"], r["h"], r["fmt"], r["fl"],
                         0, TEX_RAM + off, 0)
        off += r["w"] * r["h"] * 2
    big = 8 * 8 * 2
    pad = (len(otex) + 31) & ~31
    inside = pad + big <= len(stex)
    stub = pad if inside else len(stex)
    for i in range(len(orecs), len(srecs)):
        struct.pack_into("<HHBBHII", out, trs + i * REC, 128, 128, 0, srecs[i]["fl"],
                         0, TEX_RAM + stub, 0)
        if not inside:
            stub += 128 * 128 * 2
    struct.pack_into("<HHBBHII", out, trs + len(srecs) * REC, 0, 0, 0, 0, 0, 0, 0)

    out_tex = otex + b"\0" * (len(stex) - len(otex))
    # Steam paints translucent strips in submission order - sort them back-to-
    # front so a port authored on auto-sorting PowerVR renders right here too.
    return sort_translucent_strips(bytes(out)), out_tex
