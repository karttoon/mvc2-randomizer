"""PyInstaller runtime hook: guard sys.stdout/stderr before anything else runs.

A --windowed (--noconsole) build has sys.stdout/stderr == None. numpy (pulled in
by the stage-merge repack) touches stderr during initialization, and any stray
print would too - either crashes the app at startup on None.write. Runtime hooks
execute before the bundled packages initialize, so pointing the streams at a sink
here makes the frozen windowed app safe.
"""
import os
import sys

for _name in ("stdout", "stderr"):
    if getattr(sys, _name, None) is None:
        try:
            setattr(sys, _name, open(os.devnull, "w"))
        except OSError:
            pass
