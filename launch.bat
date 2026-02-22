@echo off
REM ============================================================
REM MvC2 Palette Randomizer - Steam Launcher
REM ============================================================
REM
REM This script randomizes palettes then launches the game.
REM Set it as a Steam launch option (see README for instructions).
REM
REM Edit the paths below to match your setup:
REM ============================================================

REM Path to Python (find yours with: where python)
set PYTHON=C:\Users\kartt\AppData\Local\Programs\Python\Python312\python.exe

REM Path to the randomizer script
set RANDOMIZER=%~dp0mvc2_randomizer.py

REM Path to your skins folder
set SKINS=%~dp0skins

REM ============================================================
REM Do not edit below this line
REM ============================================================

echo [MvC2 Randomizer] Randomizing palettes...
"%PYTHON%" "%RANDOMIZER%" --skins "%SKINS%"

if %ERRORLEVEL% NEQ 0 (
    echo [MvC2 Randomizer] Warning: Randomizer exited with error %ERRORLEVEL%
    echo [MvC2 Randomizer] Game will still launch.
    pause
)

echo [MvC2 Randomizer] Done! Launching game...
