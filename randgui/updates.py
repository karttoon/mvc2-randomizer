"""Update checks (Settings > Updates): the app itself, the Arcade CPU pack and the
palette / stage gallery.

Network access only when the user turned "Check for updates when the app starts"
on (off by default, always), or clicked Check now / an Update button. A failed
check goes to the activity log, never a pop-up.
"""
import datetime
import hashlib
import json
import os
import subprocess
import sys

import activity
import config
import randomize

ON_LAUNCH = "update_on_launch"          # master switch - off by default
KEYS = {"app": "update_check_app", "cpu": "update_check_cpu",
        "gallery": "update_check_gallery"}
CPU_AUTO = "update_cpu_auto"            # install a newer CPU pack without asking
EXE_NAME = "MvC2Randomizer.exe"


def settings():
    cfg = randomize.get_config()
    out = {k: bool(cfg.get(v, True)) for k, v in KEYS.items()}
    out["on_launch"] = bool(cfg.get(ON_LAUNCH, False))
    out["cpu_auto"] = bool(cfg.get(CPU_AUTO, False))
    return out


def _api(url):
    return json.loads(randomize._fetch(url, 4 << 20))


# ---------------------------------------------------------------- the app
def check_app():
    rel = _api(config.APP_RELEASE_API)
    tag = (rel.get("tag_name") or "").lstrip("vV")
    exe = next((a for a in rel.get("assets") or [] if a.get("name") == EXE_NAME), None)
    newer = bool(tag and exe) and randomize._ver(tag) > randomize._ver(config.VERSION)
    activity.log(f"Updates: app latest {tag or '?'}, have {config.VERSION}")
    return {"newer": newer, "latest": tag, "current": config.VERSION,
            "notes": rel.get("body") or "", "page": rel.get("html_url"),
            "asset": exe and {"url": exe.get("browser_download_url"),
                              "size": exe.get("size", 0), "digest": exe.get("digest")}}


def can_self_update():
    """Only the packaged exe can replace itself (a source checkout can't)."""
    return bool(getattr(sys, "frozen", False)) and \
        sys.executable.lower().endswith(".exe")


def _download(url, size, status=None):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": f"MvC2Randomizer/{config.VERSION}"})
    buf, got, step = [], 0, 0
    with urllib.request.urlopen(req, timeout=60) as r:
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            buf.append(chunk)
            got += len(chunk)
            if got > size + (1 << 20):
                raise ValueError("download is larger than expected")
            if status and size and got * 10 // size > step:
                step = got * 10 // size
                status(f"Downloading the new app... {min(100, got * 100 // size)}%")
    return b"".join(buf)


def install_app(asset, progress=None, status=None):
    """Download the new exe, check it against GitHub's SHA-256, and swap it in
    place of the running one (same name, so a Steam launch option keeps
    working). The running exe is renamed to .old (Windows allows that) and
    deleted on the next start. Returns 0 when the new exe is in place."""
    say = progress or print
    exe = sys.executable
    randomize._engine()
    from mvc2_data import arcade_companion
    if arcade_companion.game_running():
        say("Close the game first - the app can't update while it's running.")
        return 1
    digest = (asset or {}).get("digest") or ""
    if not digest.startswith("sha256:"):
        say("This release has no published fingerprint - not installed. "
            "Get it from the Releases page instead.")
        return 1
    try:
        say(f"Downloading the new app ({asset['size'] / 1048576:.0f} MB)...")
        blob = _download(asset["url"], asset["size"], status)
    except Exception as e:
        activity.error("app update download", e)
        say("Couldn't download the new app - nothing was changed. "
            + randomize.pack_error(e)[1])
        return 1
    if hashlib.sha256(blob).hexdigest() != digest[7:] or len(blob) != asset["size"] \
            or blob[:2] != b"MZ":
        activity.log("Updates: app download REJECTED (sha256/size mismatch)")
        say("The download didn't match the published fingerprint - nothing was changed.")
        return 1
    new, old = exe + ".new", exe + ".old"
    try:
        with open(new, "wb") as f:
            f.write(blob)
        try:
            os.remove(old)
        except OSError:
            pass
        os.replace(exe, old)
        try:
            os.replace(new, exe)
        except OSError:
            os.replace(old, exe)            # put the running one back
            raise
    except OSError as e:
        activity.error("app update swap", e)
        try:
            os.remove(new)
        except OSError:
            pass
        say(f"Couldn't replace the app file ({e}) - nothing was changed. "
            "Is the folder read-only?")
        return 1
    activity.log(f"Updates: new app installed over {exe}")
    say("New version downloaded and checked. Restarting...")
    return 0


def relaunch():
    """Start the (new) exe as a fresh process - not sharing this one's unpacked
    files - so the caller can close."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_MEI", "_PYI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen([sys.executable], env=env, close_fds=True,
                     cwd=os.path.dirname(sys.executable),
                     creationflags=0x00000008 | 0x00000200)   # detached, new group


def cleanup_old():
    """Remove the previous exe left by an update (it may still be closing)."""
    if not can_self_update():
        return True
    done = True
    for p in (sys.executable + ".old", sys.executable + ".new"):
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                done = False
    return done


# ---------------------------------------------------------------- the CPU pack
def check_cpu():
    try:
        r = randomize.arcade_check_update()
    except Exception as e:
        if randomize.pack_error(e)[0] == "none":      # nothing posted yet
            return {"newer": False, "current": randomize.arcade_pack_info()["built"]}
        raise
    r["newer"] = r["newer"] and not r["needs_app"]
    return r


# ---------------------------------------------------------------- the gallery
def gallery_local():
    """{'sha', 'downloaded'} recorded by the last gallery download, else None."""
    try:
        with open(config.GALLERY_VERSION_JSON, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _have_gallery():
    try:
        return any(os.scandir(config.SKINS))
    except OSError:
        return False


def check_gallery():
    head = _api(config.GALLERY_API + "commits/master")
    sha, date = head["sha"], head["commit"]["committer"]["date"]
    out = {"newer": False, "latest": date[:10], "palettes": None, "stages": None}
    if not _have_gallery():
        out.update(newer=True, first=True)
        return out
    have = gallery_local() or {}
    if have.get("sha"):
        if have["sha"] == sha:
            return out
        out["newer"] = True
        try:                               # what changed since your download
            cmp = _api(f"{config.GALLERY_API}compare/{have['sha']}...{sha}")
            files = cmp.get("files") or []
            out["palettes"] = sum(1 for f in files if f.get("status") == "added"
                                  and f["filename"].startswith("skins/")
                                  and f["filename"].lower().endswith(".png"))
            out["stages"] = sum(1 for f in files if f["filename"].startswith("stages/"))
            out["capped"] = len(files) >= 300         # GitHub lists at most 300
        except Exception as e:
            activity.error("gallery compare", e)
    else:                                  # downloaded before 1.0.11: go by date
        when = have.get("downloaded")
        if not when:
            try:
                when = datetime.datetime.fromtimestamp(
                    os.path.getmtime(config.SKINS), datetime.timezone.utc).isoformat()
            except OSError:
                when = ""
        out["newer"] = date > (when or "")
    activity.log(f"Updates: gallery latest {sha[:8]} ({date}), have "
                 f"{(have.get('sha') or '?')[:8]}")
    return out


CHECKS = {"app": check_app, "cpu": check_cpu, "gallery": check_gallery}


def check(which):
    """{key: result | {'error': message}} for each key in `which`."""
    out = {}
    for k in which:
        try:
            out[k] = CHECKS[k]()
        except Exception as e:
            activity.error(f"update check ({k})", e)
            out[k] = {"error": randomize.pack_error(e)[1]}
    return out
