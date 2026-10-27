#!/usr/bin/env python3
"""Pull the latest Arcade AI pack from ArcadeLogic into this repo (and rebuild).

    py tools/sync_arcade.py                  # sync + verify only
    py tools/sync_arcade.py --build          # ... then rebuild the exe (copied to repo root)
    py tools/sync_arcade.py --build --deploy C:\\MvC2Test   # ... and copy it there too

What it does:
  1. Reads ArcadeLogic/ai/out/ai_pack/ (ai_pack.json + entries/) and checks
     every file against the manifest's md5/size. Nothing is copied on a mismatch.
  2. Replaces mvc2_data/ai_pack/entries/ + ai_pack.json (stale files removed).
  3. Keeps mvc2_data/ai_pack/stock/ complete: any AI file the pack now covers
     that we have no stock copy of is extracted from the STOCK baseline arc
     (read-only) and verified against stock_md5.
  4. Adds every pack version's md5s to known_md5.json, so the app recognises
     ANY earlier version of the pack in a game file as ours and upgrades it
     (instead of calling it "another mod").
  5. Copies ai_pack_apply.py verbatim if ArcadeLogic changed it.
  6. Warns when files we ported by hand changed upstream (companion code,
     teams.toml) - those need a manual look, they're not copied.
  7. Writes mvc2_data/ai_pack/source.json (what was synced, when).
Never touches any game_50.arc.
"""
import argparse, hashlib, json, os, shutil, subprocess, sys, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(REPO, "mvc2_data", "ai_pack")
ARCADE = r"D:\Storage\MvC2Modding\ArcadeLogic"
STOCK_ARC = r"D:\Storage\MvC2Modding\MVC2_Steam_STOCK_Baseline\game_50.arc"
# Hand-ported upstream files: a change means "look at it", not "copy it".
WATCH = {"companion": r"companion\mvc2_arcade.py", "teams": r"companion\teams.toml"}


def md5(b):
    return hashlib.md5(b).hexdigest()


def md5f(p):
    with open(p, "rb") as f:
        return md5(f.read())


def load_json(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", default=ARCADE, help="ArcadeLogic folder")
    ap.add_argument("--stock-arc", default=STOCK_ARC, help="untouched stock game_50.arc")
    ap.add_argument("--build", action="store_true", help="rebuild the exe afterwards")
    ap.add_argument("--deploy", metavar="DIR", help="also copy the built exe here")
    a = ap.parse_args()

    src = os.path.join(a.src, "ai", "out", "ai_pack")
    new = load_json(os.path.join(src, "ai_pack.json"), None)
    if not new:
        sys.exit(f"No pack found at {src}")
    if new.get("format") != 1:
        sys.exit(f"Pack format {new.get('format')} - this tool knows format 1. Update it first.")
    old = load_json(os.path.join(DEST, "ai_pack.json"), {"entries": []})

    # 1. verify the incoming pack completely before touching anything
    blobs = {}
    for e in new["entries"]:
        p = os.path.join(src, e["file"])
        with open(p, "rb") as f:
            b = f.read()
        if md5(b) != e["md5"] or len(b) != e["size"]:
            sys.exit(f"ABORT: {e['file']} doesn't match the manifest (half-written export?)")
        blobs[e["file"]] = b

    # 3. stock copies for every covered AI file
    stock_dir = os.path.join(DEST, "stock")
    os.makedirs(stock_dir, exist_ok=True)
    need = [e for e in new["entries"]
            if not os.path.exists(os.path.join(stock_dir, f"{e['idx']}.bin"))
            or md5f(os.path.join(stock_dir, f"{e['idx']}.bin")) != e["stock_md5"]]
    if need:
        sys.path.insert(0, REPO)
        from mvc2_data import ai_pack_apply as A
        from mvc2_data.steam import read_arc
        rom = read_arc(a.stock_arc)                   # decompressed ROM, read-only
        toc = A.parse_toc(rom)
        for e in need:
            o, s = toc[e["idx"]]
            b = bytes(rom[A.AFS + o:A.AFS + o + s])
            if md5(b) != e["stock_md5"]:
                sys.exit(f"ABORT: stock arc entry {e['idx']} isn't stock (md5 {md5(b)})")
            with open(os.path.join(stock_dir, f"{e['idx']}.bin"), "wb") as f:
                f.write(b)
        print(f"stock: extracted {len(need)} AI file(s) from the stock baseline")

    # 4. remember every version's md5s (old pack first, then the new one)
    kp = os.path.join(DEST, "known_md5.json")
    known = load_json(kp, {})
    for pack in (old, new):
        for e in pack["entries"]:
            lst = known.setdefault(str(e["idx"]), [])
            if e["md5"] not in lst:
                lst.append(e["md5"])
    with open(kp, "w", encoding="utf-8") as f:
        json.dump(known, f, indent=0)

    # 2. swap in the new entries
    ent_dir = os.path.join(DEST, "entries")
    tmp = ent_dir + ".new"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    for name, b in blobs.items():
        with open(os.path.join(tmp, os.path.basename(name)), "wb") as f:
            f.write(b)
    shutil.rmtree(ent_dir, ignore_errors=True)
    os.replace(tmp, ent_dir)
    with open(os.path.join(DEST, "ai_pack.json"), "w", encoding="utf-8") as f:
        json.dump(new, f, indent=1)
    om = {e["idx"]: e["md5"] for e in old["entries"]}
    changed = sum(1 for e in new["entries"] if om.get(e["idx"]) != e["md5"])
    print(f"pack:  {old.get('built', 'none')}  ->  {new['built']}   "
          f"({changed} of {len(new['entries'])} AI files changed)")

    # 5. the applier is vendored verbatim
    ap_src = os.path.join(src, "ai_pack_apply.py")
    ap_dst = os.path.join(REPO, "mvc2_data", "ai_pack_apply.py")
    if os.path.exists(ap_src) and md5f(ap_src) != md5f(ap_dst):
        shutil.copy(ap_src, ap_dst)
        print("applier: ai_pack_apply.py UPDATED from ArcadeLogic - re-run the tests")

    # 6. hand-ported files
    sp = os.path.join(DEST, "source.json")
    prev = load_json(sp, {})
    watched = {}
    for k, rel in WATCH.items():
        p = os.path.join(a.src, rel)
        watched[k] = md5f(p) if os.path.exists(p) else None
        before = prev.get("watched", {}).get(k)
        if before and watched[k] != before:
            print(f"PORT NEEDED: {rel} changed upstream since the last sync - review it "
                  "(companion -> mvc2_data/arcade_companion.py, teams -> "
                  "mvc2_data/arcade_teams.json)")

    # 7. record the sync
    with open(sp, "w", encoding="utf-8") as f:
        json.dump({"pack_built": new["built"], "synced": time.strftime("%Y-%m-%d %H:%M"),
                   "source": src, "watched": watched}, f, indent=1)

    if a.build:
        print("building exe ...")
        r = subprocess.run([sys.executable, "-m", "PyInstaller",
                            os.path.join(REPO, "packaging", "MvC2Randomizer.spec"),
                            "--noconfirm"], cwd=REPO, capture_output=True, text=True)
        if r.returncode:
            print(r.stdout[-3000:], r.stderr[-3000:])
            sys.exit("build FAILED")
        exe = os.path.join(REPO, "dist", "MvC2Randomizer.exe")
        print("built: dist\\MvC2Randomizer.exe")
        for d in [REPO] + ([a.deploy] if a.deploy else []):
            try:
                shutil.copy(exe, os.path.join(d, "MvC2Randomizer.exe"))
                print(f"copied to {d}")
            except PermissionError:
                print(f"NOT copied to {d} - that exe is open; close it, then copy "
                      "dist\\MvC2Randomizer.exe there")
    print("done.")


if __name__ == "__main__":
    main()
