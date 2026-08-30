#!/usr/bin/env python3
"""config.py - paths and Steam detection for the MvC2 Randomizer app.

Mirrors the MixConverterGUI approach: registry auto-detect of the Fighting
Collection install, user-writable data living next to the .exe. Flat module
(imported as `import config`), same as the MixConverter GUI.
"""
import os, sys, subprocess


def app_dir():
    """Folder holding user-writable data (skins/, config, backup).

    Frozen as a PyInstaller .exe: the folder the .exe lives in (not the temp
    _MEIPASS extraction dir). From source: the repo root (parent of randgui/).
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_dir():
    """Folder holding read-only bundled resources. Frozen -> _MEIPASS, else app_dir()."""
    return getattr(sys, "_MEIPASS", app_dir())


# Keep helper subprocesses (reg query, the game launch) from flashing a console
# window when we run as a --windowed GUI exe.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

ROOT = app_dir()
DATA = ROOT                                        # writable data sits beside the exe
SKINS = os.path.join(DATA, "skins")
CONFIG_JSON = os.path.join(DATA, "randomizer_config.json")
LOCKS_TXT = os.path.join(DATA, "skin_locks.txt")
LAST_RUN = os.path.join(DATA, "last_run.txt")
VERDICTS_JSON = os.path.join(DATA, "gallery_verdicts.json")
PALETTE_STATE = os.path.join(DATA, "palette_state.json")
STAGES = os.path.join(DATA, "stages")
STAGE_VERDICTS_JSON = os.path.join(DATA, "stage_verdicts.json")
STAGE_STATE = os.path.join(DATA, "stage_state.json")
STAGE_LOCKS_JSON = os.path.join(DATA, "stage_locks.json")

# MvC2 Fighting Collection (Steam) App ID — same one MixConverter uses.
APPID = "2634890"
_DEFAULT_STEAM = (r"C:\Program Files (x86)\Steam\steamapps\common"
                  r"\MARVEL vs. CAPCOM Fighting Collection")
ARC_SUBPATH = os.path.join("nativeDX11x64", "arc", "pc", "game_50.arc")

# Manual override for when the game is on another drive / auto-detect fails.
GAME_OVERRIDE = os.path.join(DATA, "game_path.txt")


def _read_override():
    try:
        if os.path.exists(GAME_OVERRIDE):
            return open(GAME_OVERRIDE, encoding="utf-8").read().strip() or None
    except Exception:
        pass
    return None


def set_game_root(path):
    """Persist a manual game-install override (folder containing nativeDX11x64\\...)."""
    with open(GAME_OVERRIDE, "w", encoding="utf-8") as fh:
        fh.write((path or "").strip())


def _reg_install_location():
    try:
        out = subprocess.run(
            ["reg", "query",
             rf"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App {APPID}",
             "/v", "InstallLocation"],
            capture_output=True, text=True, creationflags=NO_WINDOW)
        for line in out.stdout.splitlines():
            if "InstallLocation" in line:
                parts = line.split("REG_SZ")
                if len(parts) == 2:
                    return parts[1].strip()
    except Exception:
        pass
    return None


def game_root():
    """Return the Fighting Collection install folder, or None.

    Order: manual override -> registry auto-detect -> common default path.
    """
    for cand in (_read_override(), _reg_install_location(), _DEFAULT_STEAM):
        if cand and os.path.isdir(cand):
            return cand
    return None


def game_arc(root=None):
    root = root or game_root()
    return os.path.join(root, ARC_SUBPATH) if root else None


def game_native(root=None):
    """The nativeDX11x64 folder the CLI engine expects as its --game argument
    (it looks for arc/pc/game_50.arc under this)."""
    root = root or game_root()
    return os.path.join(root, "nativeDX11x64") if root else None


def has_game():
    arc = game_arc()
    return bool(arc) and os.path.exists(arc)


def skins_count():
    """Rough count of downloaded skin PNGs, for the status line."""
    n = 0
    if os.path.isdir(SKINS):
        for _root, _dirs, files in os.walk(SKINS):
            n += sum(1 for f in files if f.lower().endswith(".png"))
    return n
