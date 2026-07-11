#!/usr/bin/env python3
"""randomize.py - thin wrapper around the CLI engine (mvc2_randomizer).

Runs the existing engine in-process so nothing is reimplemented. The engine
prints progress and derives its file paths from module globals, so we point
those at the app's writable data dir and capture stdout into the GUI log.
"""
import os, sys, io, json, contextlib

import config


def _engine():
    """Import the CLI engine and redirect its file paths to our data dir."""
    for p in (config.resource_dir(), config.app_dir()):
        if p not in sys.path:
            sys.path.insert(0, p)
    import mvc2_randomizer as engine
    # The engine reads these module globals at call time; repoint them so a
    # frozen exe writes config/locks/last-run/verdicts next to itself, not into
    # the read-only PyInstaller extraction dir.
    engine.SCRIPT_DIR = config.DATA
    engine.DEFAULT_CONFIG = config.CONFIG_JSON
    engine.DEFAULT_SKINS = config.SKINS
    engine.DEFAULT_LOCKS = config.LOCKS_TXT
    engine.LAST_RUN_LOG = config.LAST_RUN
    engine.PALETTE_STATE = config.PALETTE_STATE
    return engine


@contextlib.contextmanager
def _capture(progress, status=None):
    """Redirect stdout: completed lines go to `progress`, and carriage-return
    progress updates (e.g. a download bar) go to `status` instead of spamming
    the log. flush() is a no-op on purpose - print(flush=True) on every tick
    would otherwise emit a line per update.
    """
    class _W(io.TextIOBase):
        def __init__(self):
            self.buf = ""
        def write(self, s):
            self.buf += s
            while "\n" in self.buf:
                line, self.buf = self.buf.split("\n", 1)
                if "\r" in line:
                    line = line.rsplit("\r", 1)[-1]
                if progress:
                    progress(line)
            if "\r" in self.buf and status:      # in-progress bar (no newline yet)
                status(self.buf.rsplit("\r", 1)[-1].strip())
            return len(s)
        def flush(self):
            pass
    old = sys.stdout
    w = _W()
    sys.stdout = w
    try:
        yield
    finally:
        sys.stdout = old
        if w.buf and progress:
            progress(w.buf.rsplit("\r", 1)[-1])


def _run(argv, progress=None, status=None):
    engine = _engine()
    os.makedirs(config.SKINS, exist_ok=True)
    old_argv = sys.argv
    sys.argv = ["mvc2_randomizer"] + argv
    try:
        with _capture(progress, status):
            return engine.main() or 0
    finally:
        sys.argv = old_argv


# ----------------------------------------------------------------- public API
def randomize(progress=None, status=None, seed=None, character=None, dry_run=False):
    # --quiet: the full per-character list goes to last_run.txt, not the GUI log.
    argv = ["--skins", config.SKINS, "--config", config.CONFIG_JSON, "--quiet"]
    g = config.game_native()
    if g:
        argv += ["--game", g]
    if seed is not None:
        argv += ["--seed", str(seed)]
    if character:
        argv += ["--character", character]
    if dry_run:
        argv += ["--dry-run"]
    return _run(argv, progress)


def download_palettes(progress=None, status=None):
    return _run(["--gallery-download", "--skins", config.SKINS], progress, status)


def reset_palettes(progress=None, status=None, character=None):
    """Reset character palettes to vanilla (one character, or all if None);
    leaves other game data intact."""
    argv = ["--reset-palettes", "--skins", config.SKINS]
    if character:
        argv += ["--character", character]
    g = config.game_native()
    if g:
        argv += ["--game", g]
    return _run(argv, progress)


def unprotect(progress=None, status=None, character=None):
    """Unlock protected characters (external edits) for randomizing again."""
    argv = ["--unprotect", "--skins", config.SKINS]
    if character:
        argv += ["--character", character]
    g = config.game_native()
    if g:
        argv += ["--game", g]
    return _run(argv, progress)


def protected_chars():
    """(protected, unnotified) character-folder lists from palette_state.json."""
    try:
        with open(config.PALETTE_STATE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        return [], []
    prot = state.get("protected", [])
    notified = set(state.get("notified", []))
    return prot, [c for c in prot if c not in notified]


def mark_protected_notified():
    """Record that the GUI has announced the current protections."""
    try:
        with open(config.PALETTE_STATE, "r", encoding="utf-8") as f:
            state = json.load(f)
        state["notified"] = list(state.get("protected", []))
        with open(config.PALETTE_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1, sort_keys=True)
    except Exception:
        pass


def last_run_text():
    """Return the contents of the last-run assignment log, or a friendly note."""
    try:
        if os.path.exists(config.LAST_RUN):
            with open(config.LAST_RUN, encoding="utf-8", errors="replace") as fh:
                return fh.read().strip() or "(last run log is empty)"
    except Exception as e:
        return f"(could not read log: {e})"
    return "No randomize has been run yet — no log to show."
