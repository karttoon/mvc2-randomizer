"""Install / remove the Arcade CPU-AI pack ("smarter CPU") in the game_50.arc ROM.

The pack (mvc2_data/ai_pack/) replaces 173 AI script files (AFS entries 504+).
Those entries grow, which shifts every later entry - harmless here: all the
palette/FX data this app writes lives in entries 209-267 (before them), and
stage swaps look entries up in the current table of contents.

ai_pack_apply.py is a verbatim copy of the ArcadeLogic applier; keep it that way
so a newer copy can be dropped in. This module adds the randomizer's rules:
  - never overwrite AI files that belong to another mod;
  - recognise an older version of THIS pack (by what we recorded installing),
    so a pack update replaces it instead of being refused as "foreign";
  - turning it off restores the stock AI files (shipped in ai_pack/stock/).
"""
import hashlib
import json
import os

from . import ai_pack_apply as A

PACK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_pack")   # built in
PACK_FORMAT = 1                 # the pack layout this app understands
# Steam MvC2 loads each character's AI files (PLxx_AI_SCRIPT_00..04) into fixed
# 4 KB slots: a bigger file's tail is overwritten by the next one, the CPU runs
# garbage script bytes and the game hangs (ArcadeLogic, 2026-10-29). Never
# install one, from this pack or any other.
AI_SLOT = 4096
# A pack the user downloaded (Arcade tab > Check for CPU pack updates) - set by
# the app to data/arcade_pack. Used instead of the built-in one when it's newer.
USER_DIR = None


def active_dir():
    """The pack in use: the downloaded one if it's there and newer, else built in."""
    if USER_DIR:
        try:
            p = load_pack(USER_DIR)
            if p.get("format") == PACK_FORMAT and (p.get("built") or "") > (
                    pack_version(PACK_DIR) or ""):
                return USER_DIR
        except (OSError, ValueError):
            pass
    return PACK_DIR


def load_pack(pack_dir=None):
    with open(os.path.join(pack_dir or active_dir(), "ai_pack.json"), encoding="utf-8") as f:
        return json.load(f)


def pack_version(pack_dir=None):
    """The pack's build stamp (e.g. '2026-10-18 09:36'), or None if missing."""
    try:
        return load_pack(pack_dir).get("built")
    except (OSError, ValueError):
        return None


def validate_pack(pack_dir):
    """(ok, message): every AI file and stock copy is present and matches the
    manifest's fingerprints. Run on a downloaded pack before it's ever used."""
    try:
        pack = load_pack(pack_dir)
    except (OSError, ValueError) as e:
        return False, f"no readable ai_pack.json ({e})"
    if pack.get("format") != PACK_FORMAT:
        return False, f"pack format {pack.get('format')} - this app reads format {PACK_FORMAT}"
    ents = pack.get("entries") or []
    if not ents:
        return False, "the pack lists no AI files"
    for e in ents:
        for path, want in ((os.path.join(pack_dir, e["file"]), e["md5"]),
                           (os.path.join(pack_dir, "stock", f"{e['idx']}.bin"), e["stock_md5"])):
            try:
                with open(path, "rb") as f:
                    if _md5(f.read()) != want:
                        return False, f"{os.path.basename(path)} doesn't match the pack"
            except OSError:
                return False, f"{os.path.basename(path)} is missing"
    big = oversized(pack_dir)
    if big:
        return False, (f"{len(big)} AI file(s) are over the game's 4 KB limit "
                       f"({', '.join(big[:3])}) - the game would freeze")
    return True, f"{len(ents)} AI files OK"


def oversized(pack_dir=None):
    """Names of the pack's AI files bigger than the game's 4 KB AI slot."""
    pack_dir = pack_dir or active_dir()
    out = []
    for e in load_pack(pack_dir)["entries"]:
        try:
            n = os.path.getsize(os.path.join(pack_dir, e["file"]))
        except OSError:
            n = e.get("size", 0)
        if max(n, e.get("size", 0)) > AI_SLOT:
            out.append(e["name"])
    return out


def oversized_in_rom(rom, pack_dir=None):
    """Names of the game's AI files (now) bigger than the 4 KB AI slot."""
    ents = _entries(rom)
    return [e["name"] for e in load_pack(pack_dir)["entries"]
            if e["idx"] < len(ents) and len(ents[e["idx"]]) > AI_SLOT]


def known_md5s(pack_dir=None):
    """{idx: [md5, ...]} of every pack version ever synced into this build
    (tools/sync_arcade.py keeps it), so an older version in a game file - e.g.
    installed by ArcadeLogic's own tool or an older app build - is upgraded
    rather than refused as another mod's."""
    out = {}
    for d in {PACK_DIR, pack_dir or active_dir()}:   # built-in + active: every version seen
        try:
            with open(os.path.join(d, "known_md5.json"), encoding="utf-8") as f:
                for k, v in json.load(f).items():
                    out.setdefault(k, [])
                    out[k] += [h for h in v if h not in out[k]]
        except (OSError, ValueError):
            pass
    return out


def _entries(rom):
    return [bytes(rom[A.AFS + o:A.AFS + o + s]) if s else b""
            for o, s in A.parse_toc(rom)]


def _md5(b):
    return hashlib.md5(b).hexdigest()


def status(rom, record=None, pack_dir=None):
    """What the game's AI files are right now:
      'installed' - exactly this pack
      'stock'     - untouched
      'ours-old'  - a pack version this app installed before (or partly ours)
      'foreign'   - at least one AI file belongs to some other mod
    record = {idx: md5} of what this app last installed."""
    pack_dir = pack_dir or active_dir()
    pack = load_pack(pack_dir)
    ents = _entries(rom)
    rec = record or {}
    known = known_md5s(pack_dir)
    kinds = set()
    for e in pack["entries"]:
        h = _md5(ents[e["idx"]])
        if h == e["md5"]:
            kinds.add("pack")
        elif h == e["stock_md5"]:
            kinds.add("stock")
        elif rec.get(str(e["idx"])) == h or h in known.get(str(e["idx"]), ()):
            kinds.add("old")
        else:
            kinds.add("foreign")
    if "foreign" in kinds:
        return "foreign"
    if kinds == {"pack"}:
        return "installed"
    if kinds == {"stock"}:
        return "stock"
    return "ours-old"


def sync(rom, enabled, record=None, pack_dir=None):
    """Bring the ROM's AI files in line with the setting.

    Returns (rom, record, message). rom is a new bytearray when the archive was
    re-laid out, else the input unchanged. record is what to remember as
    installed ({idx: md5}; {} when stock). message is for the run log, or None
    when nothing changed."""
    pack_dir = pack_dir or active_dir()
    pack = load_pack(pack_dir)
    st = status(rom, record, pack_dir)
    if st == "foreign":
        msg = ("Arcade (WIP): the game's CPU AI files were changed by another mod or "
               "a newer Arcade build - left alone")
        big = oversized_in_rom(rom, pack_dir)
        if big:
            msg += (f"\n  WARNING: {len(big)} of them are over the game's 4 KB AI limit "
                    f"({', '.join(big[:3])}) - known to freeze the game. "
                    "Home > Restore > CPU puts back the stock CPU.")
        return rom, record or {}, msg
    if enabled and oversized(pack_dir):
        return rom, record or {}, ("Arcade (WIP): this CPU pack has AI files over the "
                                   "game's 4 KB limit - NOT installed (it would freeze "
                                   "the game)")
    if enabled and st == "installed":
        return rom, {str(e["idx"]): e["md5"] for e in pack["entries"]}, None
    if not enabled and st == "stock":
        return rom, {}, None
    ents = _entries(rom)
    for e in pack["entries"]:
        if enabled:
            path = os.path.join(pack_dir, e["file"])
        else:
            path = os.path.join(pack_dir, "stock", f"{e['idx']}.bin")
        with open(path, "rb") as f:
            ents[e["idx"]] = f.read()
    new = A.build(rom, ents)
    if enabled:
        rec = {str(e["idx"]): e["md5"] for e in pack["entries"]}
        verb = "updated" if st == "ours-old" else "installed"
        return new, rec, f"Arcade (WIP): smarter CPU {verb} (version {pack.get('built')})"
    return new, {}, "Arcade (WIP): smarter CPU removed - original CPU restored"
