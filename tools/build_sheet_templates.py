"""Build mvc2_data/sheet_templates.zip: one palette sheet per character, used by
the importer to turn palettes found in a game file into gallery-format sheets
without needing the gallery download.

Usage: py tools/build_sheet_templates.py [skins_dir]   (default: ./skins)

Each template is a gallery sheet whose pixel layout matches
mvc2_data/sheet_specs.json (size + md5 of the index data). Its colours are
replaced with a neutral grey ramp - only the layout matters.
"""
import io
import json
import os
import sys
import zipfile
import hashlib

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPECS = os.path.join(ROOT, "mvc2_data", "sheet_specs.json")
OUT = os.path.join(ROOT, "mvc2_data", "sheet_templates.zip")


def main():
    skins = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "skins")
    with open(SPECS, encoding="utf-8") as f:
        specs = json.load(f)
    grey = []
    for i in range(256):
        v = (i * 17) % 256
        grey += [v, v, v]
    missing = []
    with zipfile.ZipFile(OUT + ".tmp", "w", zipfile.ZIP_DEFLATED) as z:
        for char, spec in sorted(specs.items()):
            d = os.path.join(skins, char)
            found = None
            for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
                if not fn.lower().endswith(".png"):
                    continue
                with Image.open(os.path.join(d, fn)) as im:
                    if (im.mode == "P" and im.size == (spec["w"], spec["h"])
                            and hashlib.md5(im.tobytes()).hexdigest() == spec["md5"]):
                        found = Image.frombytes("P", im.size, im.tobytes())
                        break
            if found is None:
                missing.append(char)
                continue
            found.putpalette(grey)
            buf = io.BytesIO()
            found.save(buf, "PNG", optimize=True)
            z.writestr(f"{char}.png", buf.getvalue())
    if missing:
        os.remove(OUT + ".tmp")
        sys.exit(f"no matching sheet for: {', '.join(missing)}")
    os.replace(OUT + ".tmp", OUT)
    print(f"wrote {OUT} ({os.path.getsize(OUT) // 1024} KB, {len(specs)} characters)")


if __name__ == "__main__":
    main()
