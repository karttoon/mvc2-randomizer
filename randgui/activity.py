"""activity.py - the app's own diary, for troubleshooting testers' setups.

Every setting change, Randomize / restore / backup, game check result, Arcade
companion line and error goes to data/activity_log.txt with a timestamp, so a
user's "Save support info" zip shows what they did and what happened. Never
raises: logging must not break the app.
"""
import os
import sys
import time
import traceback

import config

MAX_BYTES = 2_000_000          # then roll over to activity_log.1.txt


def _path():
    return os.path.join(config.STATE, "activity_log.txt")


def log(msg, mode=None):
    """Append one line (or several, indented) to the activity log."""
    try:
        p = _path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.exists(p) and os.path.getsize(p) > MAX_BYTES:
            old = os.path.join(config.STATE, "activity_log.1.txt")
            if os.path.exists(old):
                os.remove(old)
            os.replace(p, old)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        tag = f" [{mode}]" if mode else ""
        lines = str(msg).rstrip("\n").splitlines() or [""]
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{stamp}{tag}  {lines[0]}\n")
            for ln in lines[1:]:
                f.write(f"{' ' * 21}{ln}\n")
    except Exception:
        pass


def error(where, exc=None):
    """Log an exception with its traceback."""
    tb = traceback.format_exc() if exc is None else "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__))
    log(f"ERROR in {where}:\n{tb}")


def session_start(mode):
    frozen = "exe" if getattr(sys, "frozen", False) else "source"
    log(f"=== MvC2 Randomizer {config.VERSION} ({frozen}) started - {mode} ===")
