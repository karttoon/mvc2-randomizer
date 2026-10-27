#!/usr/bin/env python3
"""Build the stock-game reference data shipped with the app (run once; the
stock game doesn't change):

  mvc2_data/stock_entries.json   md5 + size of every AFS entry in the stock
                                 game file - lets the game check spot ANY file
                                 another tool changed
  mvc2_data/stock_stages.bin     the stock stage files (entries 801-834),
                                 zlib-compressed - lets "restore stages to
                                 stock" work without the gallery download

    py tools/build_stock_reference.py [path to an untouched stock game_50.arc]

Reads the stock arc only; never writes a game file.
"""
import hashlib, json, os, struct, sys, zlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from mvc2_data.steam import read_arc           # noqa: E402
from mvc2_data import stages as S              # noqa: E402

STOCK = r"D:\Storage\MvC2Modding\MVC2_Steam_STOCK_Baseline\game_50.arc"


def main():
    rom = read_arc(sys.argv[1] if len(sys.argv) > 1 else STOCK)
    toc = S.parse_toc(rom)
    ents = [bytes(rom[S.AFS_BASE + o:S.AFS_BASE + o + s]) for o, s in toc]
    out = os.path.join(REPO, "mvc2_data")
    with open(os.path.join(out, "stock_entries.json"), "w") as f:
        json.dump({"count": len(ents), "size": [len(e) for e in ents],
                   "md5": [hashlib.md5(e).hexdigest() for e in ents]}, f)
    first, last = S.STG_FIRST_INDEX, S.STG_FIRST_INDEX + 2 * len(S.STAGE_SLOTS)
    index, blob = {}, bytearray()
    for i in range(first, last):
        index[i] = [len(blob), len(ents[i])]
        blob += ents[i]
    head = json.dumps(index).encode()
    with open(os.path.join(out, "stock_stages.bin"), "wb") as f:
        f.write(struct.pack("<I", len(head)) + head + zlib.compress(bytes(blob), 9))
    print(f"{len(ents)} entries; stock stages {last - first} files, "
          f"{os.path.getsize(os.path.join(out, 'stock_stages.bin')) // 1024} KB")


if __name__ == "__main__":
    main()
