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
