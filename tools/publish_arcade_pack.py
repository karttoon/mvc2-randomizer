#!/usr/bin/env python3
"""Build (and optionally publish) the downloadable Arcade CPU pack.

    py tools/publish_arcade_pack.py            # build + verify dist/arcade_pack/ only
    py tools/publish_arcade_pack.py --upload   # ... then upload to the GitHub release

Packs the CPU pack currently in mvc2_data/ai_pack (run tools/sync_arcade.py
first) into dist/arcade_pack/:
  arcade_pack.zip  ai_pack.json, known_md5.json, entries/*.bin, stock/*.bin
  manifest.json    {format, built, min_app, zip_sha256, zip_size, files}

--upload puts both on the release tagged `arcade-pack` (created if missing,
never marked as the latest release, so the app's own release stays on top),
replacing the previous files - the app's download URLs never change. The app
only ever fetches these when a user clicks "Check for CPU pack updates".
"""
import argparse, hashlib, io, json, os, subprocess, sys, zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "mvc2_data", "ai_pack")
OUT = os.path.join(REPO, "dist", "arcade_pack")
TAG = "arcade-pack"
MIN_APP = "1.0.8"            # first app version that can download packs


def build():
    sys.path.insert(0, REPO)
    from mvc2_data import arcade_ai
    ok, msg = arcade_ai.validate_pack(SRC)
    if not ok:
        sys.exit(f"ABORT: mvc2_data/ai_pack failed its checks: {msg}")
    pack = arcade_ai.load_pack(SRC)
    names = ["ai_pack.json", "known_md5.json"]
    for sub in ("entries", "stock"):
        names += [f"{sub}/{f}" for f in sorted(os.listdir(os.path.join(SRC, sub)))]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for n in names:
            z.write(os.path.join(SRC, *n.split("/")), n)
    blob = buf.getvalue()
    man = {"format": arcade_ai.PACK_FORMAT, "built": pack["built"], "min_app": MIN_APP,
           "zip_sha256": hashlib.sha256(blob).hexdigest(), "zip_size": len(blob),
           "files": len(pack["entries"])}
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "arcade_pack.zip"), "wb") as f:
        f.write(blob)
    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, indent=1)
    print(f"built: pack {man['built']}, {man['files']} AI files, zip {len(blob) // 1024} KB "
          f"({msg}) -> dist/arcade_pack/")
    return man


def upload():
    gh = ["gh", "-R", "karttoon/mvc2-randomizer"]
    have = subprocess.run(gh + ["release", "view", TAG], capture_output=True).returncode == 0
    if not have:
        subprocess.run(gh + ["release", "create", TAG, "--title", "Arcade CPU pack",
                             "--notes", "The latest Arcade CPU pack. The app downloads it "
                             "from here when you click Check for CPU pack updates on the "
                             "Arcade tab - no need to download it by hand.",
                             "--latest=false"], check=True)
    subprocess.run(gh + ["release", "upload", TAG, "--clobber",
                         os.path.join(OUT, "manifest.json"),
                         os.path.join(OUT, "arcade_pack.zip")], check=True)
    print(f"uploaded to release '{TAG}'")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--upload", action="store_true", help="publish to the GitHub release")
    a = ap.parse_args()
    build()
    if a.upload:
        upload()
