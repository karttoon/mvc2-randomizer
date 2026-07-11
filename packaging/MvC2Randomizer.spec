# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the MvC2 Palette Randomizer GUI.

Build:  py -m PyInstaller packaging/MvC2Randomizer.spec --noconfirm
Output: dist/MvC2Randomizer.exe   (onefile, windowed)

The randomizer needs Pillow (PIL), so - unlike the Mix Converter - PIL is kept
in the bundle. Skins are downloaded at runtime (not embedded). The engine
(mvc2_randomizer.py + mvc2_data/) is imported in-process, so it's declared in
hiddenimports and the repo root is on pathex.
"""
import os

ROOT = os.path.dirname(os.path.abspath(SPECPATH))    # repo root (parent of packaging/)
RANDGUI = os.path.join(ROOT, "randgui")

# Flat GUI modules (bare `import config` etc.) plus the CLI engine and its data
# package - list them so analysis doesn't miss the dynamic/in-process imports.
HIDDEN = [
    "config", "randomize", "steamcfg", "palettes", "locks",
    "PIL.ImageTk",
    "mvc2_randomizer",
    "mvc2_data", "mvc2_data.characters", "mvc2_data.steam",
]

a = Analysis(
    [os.path.join(RANDGUI, "gui.py")],
    pathex=[RANDGUI, ROOT],
    binaries=[],
    # Bundle the small stock-palette data used to reset the game to vanilla
    # (replaces keeping a full game_50.arc backup).
    datas=[(os.path.join(ROOT, "mvc2_data", "vanilla_palettes.bin"), "mvc2_data")],
    hiddenimports=HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=["numpy", "pytest", "setuptools"],   # keep PIL - the engine needs it
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MvC2Randomizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # --windowed (no console popup in launcher mode)
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "packaging", "app.ico")
        if os.path.exists(os.path.join(ROOT, "packaging", "app.ico")) else None,
)
