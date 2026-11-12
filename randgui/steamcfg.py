#!/usr/bin/env python3
"""steamcfg.py - Steam launch-option help for auto-randomize-on-play.

Phase 1: build the exact launch-option line and copy it to the clipboard, with
step-by-step instructions. Phase 2 (later): write it into Steam's localconfig.vdf
automatically (needs Steam closed).
"""
import os, sys

import config


def exe_path():
    """Path to point Steam at. Frozen -> the .exe; from source -> this script
    (so the displayed line is representative during development)."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.abspath(sys.argv[0] or __file__)


def launch_option():
    """The single line the user pastes into Steam's Launch Options."""
    return f'"{exe_path()}" %command%'


def instructions():
    return (
        "Enable auto-randomize on every launch:\n"
        "\n"
        "  1. In Steam, right-click MARVEL vs. CAPCOM Fighting Collection\n"
        "     -> Properties\n"
        "  2. Under 'General', find the 'Launch Options' box\n"
        "  3. Paste this line (already copied to your clipboard):\n"
        "\n"
        f"        {launch_option()}\n"
        "\n"
        "  4. Close Properties and launch the game from Steam as normal.\n"
        "     Palettes randomize automatically each time you play.\n"
    )


def launch_option_set():
    """True if Steam's launch options for the collection point at this app,
    False if they don't, None if it can't tell (Steam config not found).
    Read-only: looks at userdata/*/config/localconfig.vdf."""
    import glob
    import re
    roots = []
    gr = config.game_root()
    if gr:   # <steam library>/steamapps/common/<game>
        roots.append(os.path.dirname(os.path.dirname(os.path.dirname(gr))))
    pf = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    roots.append(os.path.join(pf, "Steam"))
    seen = False
    exe = os.path.basename(exe_path()).lower()
    for root in roots:
        for vdf in glob.glob(os.path.join(root, "userdata", "*", "config",
                                          "localconfig.vdf")):
            try:
                with open(vdf, encoding="utf-8", errors="replace") as f:
                    txt = f.read()
            except OSError:
                continue
            seen = True
            for m in re.finditer(r'"%s"\s*\{' % config.APPID, txt):
                chunk = txt[m.end():m.end() + 4000]
                lo = re.search(r'"LaunchOptions"\s+"((?:[^"\\]|\\.)*)"', chunk)
                if lo and exe in lo.group(1).lower() and "%command%" in lo.group(1):
                    return True
    return False if seen else None
