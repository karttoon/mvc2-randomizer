#!/usr/bin/env python3
"""Find (and optionally resolve) duplicate palettes in the skins folder.

A duplicate = same character folder + same 8-char content hash in the filename
but a different descriptor, e.g.:
    Iron_Man_ecf56f07_Guh.png
    Iron_Man_ecf56f07_Guh-umbra.png
These accumulate when the gallery is reprocessed and descriptors change; the
palette content (the hash) is the same.

Default run is a read-only report. Use --interactive to resolve each group by
choosing which file to keep - the others are deleted from disk and marked
'delete' in gallery_verdicts.json (so a future Download/Update won't restore
them), and the kept file inherits a 'keep' review from a deleted twin if it
doesn't have its own verdict yet.

Usage:
    python tools/dedupe_skins.py                # report only
    python tools/dedupe_skins.py --interactive  # choose which to keep
    python tools/dedupe_skins.py --auto         # keep newest file per group
                                                # (only offered for groups whose
                                                # files are byte-identical)
"""
import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
SKINS = os.path.join(ROOT, "skins")
VERDICTS = os.path.join(ROOT, "gallery_verdicts.json")

# Descriptor is optional: some older files are just Char_hash.png
NAME_PAT = re.compile(r"^(?P<stem>.+)_(?P<hash>[0-9a-f]{8})(?:_(?P<desc>.+))?\.png$",
                      re.IGNORECASE)


def load_verdicts():
    try:
        with open(VERDICTS, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_verdicts(v):
    with open(VERDICTS, "w", encoding="utf-8") as f:
        json.dump(v, f, indent=1, sort_keys=True)


def find_groups():
    """{(char_folder, hash): [filenames]} for hashes with more than one file."""
    groups = defaultdict(list)
    for char in sorted(os.listdir(SKINS)):
        cdir = os.path.join(SKINS, char)
        if not os.path.isdir(cdir):
            continue
        for fn in sorted(os.listdir(cdir)):
            m = NAME_PAT.match(fn)
            if m:
                groups[(char, m.group("hash").lower())].append(fn)
    return {k: v for k, v in groups.items() if len(v) > 1}


def file_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def describe_group(char, files, verdicts):
    """Print one duplicate group; returns per-file info list."""
    md5s = {fn: file_md5(os.path.join(SKINS, char, fn)) for fn in files}
    identical = len(set(md5s.values())) == 1
    note = "identical bytes" if identical else "DIFFERENT bytes (same palette hash)"
    m = NAME_PAT.match(files[0])
    print(f"\n{char}  [{m.group('hash') if m else '?'}]  "
          f"({len(files)} files, {note})")
    infos = []
    for i, fn in enumerate(files, 1):
        path = os.path.join(SKINS, char, fn)
        v = verdicts.get(f"{char}/{fn}")
        vs = {"keep": "KEEP", "delete": "REJECTED"}.get(v, "unreviewed")
        mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
        print(f"  [{i}] {fn:56s} {vs:10s} {mtime}")
        infos.append((fn, v))
    return infos


def resolve_group(char, files, infos, verdicts):
    """Prompt for which file to keep; delete the rest and fix verdicts.

    Returns True to continue, False to quit."""
    while True:
        ans = input(f"  keep which? [1-{len(files)} / s=skip / q=quit] ").strip().lower()
        if ans == "s" or ans == "":
            return True
        if ans == "q":
            return False
        if ans.isdigit() and 1 <= int(ans) <= len(files):
            break
        print("  ?")
    keep_i = int(ans) - 1
    keep_fn, keep_v = infos[keep_i]
    # Inherit a 'keep' review from a deleted twin if the kept file is unreviewed.
    if keep_v is None and any(v == "keep" for fn, v in infos if fn != keep_fn):
        verdicts[f"{char}/{keep_fn}"] = "keep"
        print(f"  -> {keep_fn}: inherited KEEP review from deleted twin")
    for fn, _v in infos:
        if fn == keep_fn:
            continue
        os.remove(os.path.join(SKINS, char, fn))
        # 'delete' verdict keeps a future gallery download from restoring it.
        verdicts[f"{char}/{fn}"] = "delete"
        print(f"  -> deleted {fn}")
    save_verdicts(verdicts)
    return True


def resolve_auto(char, files, infos, verdicts):
    """Keep the best-named file of a byte-identical group.

    If one filename extends another (old name + '-author' suffix, the current
    gallery convention), keep the extended one; otherwise keep the newest.
    """
    md5s = {fn: file_md5(os.path.join(SKINS, char, fn)) for fn in files}
    if len(set(md5s.values())) != 1:
        print("  -> bytes differ, skipping (resolve with --interactive)")
        return
    stems = {fn: fn[:-4].lower() for fn in files}
    extended = [fn for fn in files
                if any(fn != o and (stems[fn].startswith(stems[o] + "-")
                                    or stems[fn].startswith(stems[o] + "_"))
                       for o in files)]
    if len(extended) == 1:
        keep_fn = extended[0]
    else:
        keep_fn = max(files, key=lambda fn: (
            os.path.getmtime(os.path.join(SKINS, char, fn)), len(fn)))
    keep_v = dict(infos)[keep_fn]
    if keep_v is None and any(v == "keep" for fn, v in infos if fn != keep_fn):
        verdicts[f"{char}/{keep_fn}"] = "keep"
        print(f"  -> {keep_fn}: inherited KEEP review")
    for fn, _v in infos:
        if fn == keep_fn:
            continue
        os.remove(os.path.join(SKINS, char, fn))
        verdicts[f"{char}/{fn}"] = "delete"
        print(f"  -> deleted {fn}")
    save_verdicts(verdicts)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--interactive", action="store_true",
                    help="prompt per group and delete the files you don't keep")
    ap.add_argument("--auto", action="store_true",
                    help="keep the newest file of each byte-identical group")
    args = ap.parse_args()

    if not os.path.isdir(SKINS):
        print(f"skins folder not found: {SKINS}")
        return 1
    groups = find_groups()
    if not groups:
        print("No duplicate palette hashes found - collection is clean.")
        return 0

    verdicts = load_verdicts()
    total_files = sum(len(v) for v in groups.values())
    print(f"{len(groups)} duplicate group(s), {total_files} files involved "
          f"({total_files - len(groups)} deletable).")

    for (char, h), files in sorted(groups.items()):
        infos = describe_group(char, files, verdicts)
        if args.interactive:
            if not resolve_group(char, files, infos, verdicts):
                print("Stopped.")
                return 0
        elif args.auto:
            resolve_auto(char, files, infos, verdicts)

    if not (args.interactive or args.auto):
        print("\nReport only - nothing changed. Run with --interactive to "
              "choose per group, or --auto to keep the newest of each "
              "byte-identical pair.")
    else:
        print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
