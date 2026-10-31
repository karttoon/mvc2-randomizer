"""Arcade companion: curated CPU teams, shuffled stages, full-strength CPU and
endless mode for MvC2 in the Steam Fighting Collection - live memory only.

Ported from ArcadeLogic/companion/mvc2_arcade.py (addresses verified
2026-10-14 against the Steam build). Nothing on disk is touched: every change
lives in the running game's memory and is gone when the game closes. The
companion must therefore run WHILE the game runs; it waits for the game,
attaches, and re-attaches if the game restarts.

Debug combo/idle logging from the original is not included in this build.
"""
import ctypes
import ctypes.wintypes as W
import json
import random
import re
import struct
import subprocess
import threading
import time

from .characters import CHARACTERS

EXE = "MarvelVsCapcomFightingCollection.exe"

# ---- Steam build layout (RVAs from the exe image base). Verified 2026-10-14. ----
RVA_PICKER = 0x73EE90          # x86 CPU-team picker (signature check only, never patched)
RVA_ROUTE = 0xA98FE0           # stage route table, 4 routes x 8 fights
RVA_ASSIST = 0xAACA28          # default assist per char, 59 bytes
RVA_RANK_THR = 0xA6E780        # u16 x32 rank thresholds
RVA_PTR_P = 0x2EDF560          # -> player/char-select work area
RVA_PTR_G = 0x2EDF580          # -> game globals
PICKER_SIG = bytes.fromhex("4056415541564883ec30488b15")
THR_STOCK = struct.pack("<32H", *range(79, 2560, 80))
THR_MAX_RANK = bytes(62) + THR_STOCK[62:]     # thr[0..30]=0 -> rank 31 within 5 s
# CPU damage scale (ArcadeLogic, verified 2026-10-26): damage x tbl[idx]/32 for a CPU
# attacker / a CPU victim; idx is a difficulty byte the game never writes (0), so a
# CPU always deals AND takes 24/32 = 75%. Entry 0 -> 32 = 100%, like a human.
RVA_DMG_ATK, RVA_DMG_VIC = 0xA6F58E, 0xA6F5AE
DMG_STOCK = bytes([24, 24, 26, 26, 28, 28, 28, 28])
DMG_FULL = bytes([32]) + DMG_STOCK[1:]
ROUTE_STOCK = bytes.fromhex("0001020304050608010304060502070806050007010302080306020407010508")
SLOT0, SLOT_STRIDE = 0x3DB8, 0x738
F_ACTIVE, F_CHAR, F_ASSIST = 0x0001, 0x4478 - SLOT0, 0x440D - SLOT0
# colour (button) per team member, right after the char byte (ArcadeLogic,
# verified on Steam 2026-10-31): 0-5 = LP LK HP HK A1 A2; the CPU always has 0.
# Takes effect when written with the char/assist during select or on VS.
F_COLOR = F_CHAR + 1
BUTTONS = ("LP", "LK", "HP", "HK", "A1", "A2")
FILL_BITS = 0x33A0A            # P + this + side: 0x77 once all 3 CPU slots are picked
G_MODE, G_ROUND, G_HUMAN1, G_HUMAN2 = 0x4C, 0x81, 0x84, 0x85

CHARS = ["Ryu", "Zangief", "Guile", "Morrigan", "Anakaris", "Strider", "Cyclops", "Wolverine",
         "Psylocke", "Iceman", "Rogue", "Captain America", "Spider-Man", "Hulk", "Venom", "Dr. Doom",
         "Tron", "Jill", "Hayato", "Ruby Heart", "SonSon", "Amingo", "Marrow", "Cable", "Abyss 1",
         "Abyss 2", "Abyss 3", "Chun-Li", "Mega Man", "Roll", "Akuma", "BB Hood", "Felicia",
         "Charlie", "Sakura", "Dan", "Cammy", "Dhalsim", "M. Bison", "Ken", "Gambit", "Juggernaut",
         "Storm", "Sabretooth", "Magneto", "Shuma-Gorath", "War Machine", "Silver Samurai",
         "Omega Red", "Spiral", "Colossus", "Iron Man", "Sentinel", "Blackheart", "Thanos", "Jin",
         "Captain Commando", "Bonerine", "Servbot"]
ALIASES = {"anak": 0x04, "silsam": 0x2F, "sentinal": 0x34, "sent": 0x34, "mags": 0x2C, "doom": 0x0F,
           "drdoom": 0x0F, "cap": 0x0B, "capam": 0x0B, "spidey": 0x0C, "spiderman": 0x0C,
           "tronbonne": 0x10, "strider": 0x05, "striderhiryu": 0x05, "bbhood": 0x1F, "bulleta": 0x1F,
           "felicia": 0x20, "felica": 0x20, "bison": 0x26, "dictator": 0x26, "mbison": 0x26,
           "shuma": 0x2D, "shumagorath": 0x2D, "ironman": 0x33, "commando": 0x38, "capcom": 0x38,
           "cc": 0x38, "bonerine": 0x39, "bonewolverine": 0x39, "servbot": 0x3A, "kobun": 0x3A,
           "megaman": 0x1C, "rockman": 0x1C, "chunli": 0x1B, "sonson": 0x14, "warmachine": 0x2E,
           "silversamurai": 0x2F, "samurai": 0x2F, "omegared": 0x30, "rubyheart": 0x13, "ruby": 0x13,
           "wolvie": 0x07, "wolverine": 0x07, "sabertooth": 0x2B, "juggs": 0x29, "jugg": 0x29}


def _key(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


NAME2ID = {_key(n): i for i, n in enumerate(CHARS) if not n.startswith("Abyss")}
NAME2ID.update(ALIASES)
# The app's own names ("Doctor Doom", "Wolverine (Bone Claw)", ...) - what the
# team editor stores.
APP_NAMES = {i: n for i, n in CHARACTERS.items() if i not in (0x18, 0x19, 0x1A)}
NAME2ID.update({_key(n): i for i, n in APP_NAMES.items()})
ASSISTS = ("A", "B", "Y")       # alpha / beta / gamma; none given = alpha (1st assist)
STAGES = {0x00: "Airship", 0x01: "Desert", 0x02: "Factory", 0x03: "Carnival", 0x04: "Swamp",
          0x05: "Blue Cave", 0x06: "Clock Tower", 0x07: "Ice River", 0x09: "Airship (night)",
          0x0A: "Desert (night)", 0x0C: "Carnival (night)", 0x0D: "Pink Swamp", 0x0E: "Lava Cave",
          0x0F: "Clock Tower (winter)", 0x10: "River Raft"}
STAGE_NAME2ID = {_key(v): k for k, v in STAGES.items()}
BOSS_MODES = ("abyss", "team", "endless")


# ---------------------------------------------------------------- config
def parse_team(s, where):
    """A team as [[name, assist], x3] (what the team editor saves; assist
    "A"/"B"/"Y") or as text "Point-A, Mid-B, Anchor-Y". No assist = alpha."""
    if isinstance(s, (list, tuple)):
        team = []
        for m in s:
            name, a = (list(m) + [""])[:2]
            cid = NAME2ID.get(_key(name or ""))
            if cid is None:
                raise ValueError('%s: %s' % (where, 'pick 3 characters' if not name
                                             else 'unknown character "%s"' % name))
            a = (a or "").upper()[:1]
            team.append((cid, ASSISTS.index(a) if a in ASSISTS else 0))
        if len(team) != 3:
            raise ValueError("%s: a team needs exactly 3 characters" % where)
        if len({c for c, _ in team}) != 3:
            raise ValueError("%s: the same character is in it twice" % where)
        return team
    team = []
    for part in [p.strip() for p in s.split(",") if p.strip()]:
        m = re.match(r"^(.*?)(?:[\s-]+(1|2|3|a|b|y|alpha|beta|gamma))?$", part, re.I)
        name, a = m.group(1), (m.group(2) or "").lower()
        cid = NAME2ID.get(_key(name))
        if cid is None:
            raise ValueError('%s: unknown character "%s"' % (where, name))
        idx = {"1": 0, "a": 0, "alpha": 0, "2": 1, "b": 1, "beta": 1,
               "3": 2, "y": 2, "gamma": 2}.get(a, 0)        # none given = alpha
        team.append((cid, idx))
    if len(team) != 3:
        raise ValueError('%s: a team needs exactly 3 characters: "%s"' % (where, s))
    if len({c for c, _ in team}) != 3:
        raise ValueError('%s: duplicate character in "%s"' % (where, s))
    return team


def load_team_lists(path):
    """{list name: ["Point-A, Mid-B, Anchor-Y", ...]} from a team-lists JSON."""
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f).get("lists", {})


def save_team_lists(path, lists):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"lists": lists}, f, indent=1)


def build_pool(lists, active):
    """Parse the teams of the chosen lists. Returns (teams, errors); a bad team
    is skipped and reported instead of stopping everything."""
    pool, errors = [], []
    for name in active:
        for i, t in enumerate(lists.get(name, [])):
            try:
                team = parse_team(t, "%s #%d" % (name, i + 1))
            except ValueError as e:
                errors.append(str(e))
                continue
            if team not in pool:
                pool.append(team)
    return pool, errors


def random_team(last=None):
    """Any 3 different playable characters, each with a random assist (the
    game's own picker is seeded the same way every run, so it repeats).
    Never the same three as the fight before."""
    while True:
        team = [(c, random.randrange(3)) for c in random.sample(sorted(APP_NAMES), 3)]
        if not last or {c for c, _ in team} != {c for c, _ in last}:
            return team


def team_str(team, colors=None):
    return " / ".join("%s-%s%s" % (CHARS[c], "ABY"[a] if a is not None and a < 3 else "?",
                                   " (%s)" % BUTTONS[colors[i]] if colors else "")
                      for i, (c, a) in enumerate(team))


# ---------------------------------------------------------------- process access
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
k32.OpenProcess.restype = W.HANDLE
k32.CreateMutexW.restype = W.HANDLE


def pid_of(name):
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq " + name],
                         capture_output=True, text=True, creationflags=0x08000000).stdout
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if parts and parts[0].lower() == name.lower():
            return int(parts[1])
    return None


def game_running():
    return pid_of(EXE) is not None


class Game:
    def __init__(self, pid):
        self.h = k32.OpenProcess(0x0438, False, pid)
        if not self.h:
            raise OSError("OpenProcess failed (%d)" % ctypes.get_last_error())
        mods = (ctypes.c_void_p * 1)()
        need = W.DWORD()
        psapi.EnumProcessModulesEx(self.h, mods, ctypes.sizeof(mods), ctypes.byref(need), 3)
        self.base = mods[0]

    def alive(self):
        code = W.DWORD()
        return k32.GetExitCodeProcess(self.h, ctypes.byref(code)) and code.value == 259

    def read(self, a, n):
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(a), buf, n, ctypes.byref(got)) \
                or got.value != n:
            raise OSError("read failed at %#x" % a)
        return buf.raw

    def write(self, a, data):
        if not k32.WriteProcessMemory(self.h, ctypes.c_void_p(a), data, len(data), None):
            raise OSError("write failed at %#x" % a)

    def q(self, a):
        return struct.unpack("<Q", self.read(a, 8))[0]

    def close(self):
        if self.h:
            k32.CloseHandle(self.h)
            self.h = None


class BuildMismatch(Exception):
    """The game exe isn't the build these addresses were verified for."""


# ---------------------------------------------------------------- companion
class Companion(threading.Thread):
    """Background companion. settings = {
        'teams': bool, 'pool': [team, ...], 'order': 'random'|'sequential',
        'boss': 'abyss'|'team'|'endless', 'stages': bool, 'stage_pool': [...],
        'max_rank': bool }
    log(msg) receives every status line; status(text) a short summary."""

    MUTEX = "Local\\MvC2RandomizerArcadeCompanion"

    # settings that can change while it runs (from the next fight on)
    LIVE_KEYS = ("teams", "random_teams", "pool", "active", "order", "random_colors")

    def __init__(self, settings, log=print, status=None):
        super().__init__(daemon=True, name="arcade-companion")
        self.s = settings
        self.log = log
        self.status = status or (lambda t: None)
        self._halt = threading.Event()
        self._mutex = None
        self._new = None

    def update(self, settings):
        """New CPU-team settings from the app - used from the next fight on, even
        mid-run (e.g. a team list switched on). Other settings need a restart."""
        self._new = {k: settings.get(k) for k in self.LIVE_KEYS}

    # -- lifecycle --
    def stop(self):
        self._halt.set()

    @property
    def stopping(self):
        return self._halt.is_set()

    def _sleep(self, t):
        return self._halt.wait(t)

    def _claim(self):
        """Only one companion may drive the game (the app and the Steam launcher
        could both be running)."""
        h = k32.CreateMutexW(None, False, self.MUTEX)
        if h and ctypes.get_last_error() == 183:          # ERROR_ALREADY_EXISTS
            k32.CloseHandle(h)
            return False
        self._mutex = h
        return True

    def run(self):
        if not self._claim():
            self.log("Arcade companion: another copy is already running (the Steam "
                     "launcher?) - this one won't attach.")
            self.status("Already running elsewhere")
            return
        try:
            self.log("Arcade companion: started - waiting for the game...")
            self.status("Waiting for the game")
            while not self.stopping:
                pid = pid_of(EXE)
                if pid:
                    if self._sleep(3):                     # let the exe unpack itself
                        break
                    game = None
                    try:
                        game = Game(pid)
                        self.log("Arcade companion: attached to the game (pid %d)" % pid)
                        self._run(game)
                        if not self.stopping:
                            self.log("Arcade companion: game closed - waiting for it to start again")
                            self.status("Waiting for the game")
                    except BuildMismatch as e:
                        self.log("Arcade companion: %s - not touching anything." % e)
                        self.status("Game build changed (Steam update?) - stopped")
                        return
                    except OSError as e:
                        self.log("Arcade companion: lost the game (%s) - retrying" % e)
                    finally:
                        if game:
                            game.close()
                if self._sleep(2):
                    break
        finally:
            if self._mutex:
                k32.CloseHandle(self._mutex)
                self._mutex = None
            self.log("Arcade companion: stopped")
            self.status("Stopped")

    # -- stage order: a deck, so every stage plays once before any repeats --
    def _draw_stages(self, n):
        """Next n stages from the deck. The deck is every stage shuffled; it is
        refilled only when used up, and a refill never starts with the stage
        just played. Carries on across fights, Endless loops and runs."""
        pool = list(self.s["stage_pool"] or sorted(STAGES))
        out = []
        for _ in range(n):
            if not self._deck:
                self._deck = random.sample(pool, len(pool))
                if len(pool) > 1 and self._deck[0] == self._last_stage:
                    k = random.randrange(1, len(pool))
                    self._deck[0], self._deck[k] = self._deck[k], self._deck[0]
            self._last_stage = self._deck.pop(0)
            out.append(self._last_stage)
        return out

    def _new_routes(self, game, why):
        """Write the next 7 stages into ALL four arcade routes (whichever route
        the game picks, fight N gets stage N of this order)."""
        seven = self._draw_stages(7)
        last = 0x08 if self.s["boss"] == "abyss" else seven[-1]   # endless never reaches 8
        self._routes = bytes((seven + [last]) * 4)
        game.write(game.base + RVA_ROUTE, self._routes)
        self.log("Arcade companion: stages for %s: %s" % (
            why, ", ".join(STAGES.get(x, "Abyss") for x in seven)))

    def _stage_name(self, rnd):
        r = getattr(self, "_routes", None)
        return STAGES.get(r[rnd], "Abyss") if r and 0 <= rnd < 8 else None

    def _run(self, game):
        s, b = self.s, game.base
        self.status("Waiting for MvC2 (pick it on the collection menu)")
        seen = None
        while game.alive() and not self.stopping:
            try:
                got = game.read(b + RVA_PICKER, len(PICKER_SIG))
            except OSError:
                got = b""
            if got == PICKER_SIG:
                break
            live = False
            try:
                live = game.read(b + RVA_ROUTE, 32) == ROUTE_STOCK and game.q(b + RVA_PTR_G) != 0
            except OSError:
                pass
            if live and got:
                if seen == got:          # unpacked, stable, still different -> real mismatch
                    raise BuildMismatch("game build doesn't match (expected %s, found %s)"
                                        % (PICKER_SIG.hex(), got.hex()))
                seen = got
            if self._sleep(2):
                return
        else:
            return
        teams_on = s.get("teams") and (s.get("pool") or s.get("random_teams"))
        default_assist = game.read(b + RVA_ASSIST, 59)
        routes = bool(s.get("stages"))
        self._routes, self._deck, self._last_stage = None, [], None
        if routes:
            if game.read(b + RVA_ROUTE, 32) != ROUTE_STOCK:
                self.log("Arcade companion: stage routes were already modified - overwriting")
            self._new_routes(game, "fights 1-7")
        if s.get("max_rank"):
            if game.read(b + RVA_RANK_THR, 64) not in (THR_STOCK, THR_MAX_RANK):
                raise BuildMismatch("CPU rank table doesn't match")
            game.write(b + RVA_RANK_THR, THR_MAX_RANK)
            self.log("Arcade companion: CPU reaches full strength within 5 s of every fight")
        full_dmg = bool(s.get("full_dmg"))
        if full_dmg:
            tables = (RVA_DMG_ATK, RVA_DMG_VIC)
            if all(game.read(b + r, 8) in (DMG_STOCK, DMG_FULL) for r in tables):
                for r in tables:
                    game.write(b + r, DMG_FULL)
                self.log("Arcade companion: CPU deals and takes full damage (stock: 75%)")
            else:
                full_dmg = False          # not where expected - leave the game alone
                self.log("Arcade companion: damage tables not where expected - full damage NOT applied")
        if teams_on and s.get("random_teams"):
            self.log("Arcade companion: CPU teams fully random (fight 8: %s)" % s["boss"])
        elif teams_on:
            self.log("Arcade companion: %d CPU teams (%s, order %s, fight 8: %s)" % (
                len(s["pool"]), ", ".join(s.get("active", [])) or "-", s["order"], s["boss"]))
        self.status("Attached - ready for Arcade mode")

        next_check, used, forced, last_round, by_round = 0, [], None, None, {}
        loops, last_wrap = 0, 0.0
        while game.alive() and not self.stopping:
            new, self._new = self._new, None
            if new:
                s.update(new)
                teams_on = s.get("teams") and (s.get("pool") or s.get("random_teams"))
                if not teams_on:
                    why = "the game's own picks"
                elif s.get("random_teams"):
                    why = "fully random"
                else:
                    why = "%d teams from %s, order %s" % (
                        len(s["pool"]), ", ".join(s.get("active") or []) or "-", s["order"])
                self.log("Arcade companion: CPU teams changed - from the next fight: " + why)
            if (routes or s.get("max_rank") or full_dmg) and time.time() > next_check:
                next_check = time.time() + 1
                if routes and game.read(b + RVA_ROUTE, 32) == ROUTE_STOCK:
                    game.write(b + RVA_ROUTE, self._routes)
                    self.log("Arcade companion: stage routes were reset by the game - re-applied")
                if s.get("max_rank") and game.read(b + RVA_RANK_THR, 64) == THR_STOCK:
                    game.write(b + RVA_RANK_THR, THR_MAX_RANK)
                    self.log("Arcade companion: rank table was reset by the game - re-applied")
                if full_dmg and any(game.read(b + r, 8) == DMG_STOCK
                                    for r in (RVA_DMG_ATK, RVA_DMG_VIC)):
                    for r in (RVA_DMG_ATK, RVA_DMG_VIC):
                        game.write(b + r, DMG_FULL)
                    self.log("Arcade companion: damage table was reset by the game - re-applied")
            P, G = game.q(b + RVA_PTR_P), game.q(b + RVA_PTR_G)
            if not P or not G:
                self._sleep(0.2)
                continue
            g = game.read(G, 0x90)
            mode, rnd, hf = g[G_MODE], g[G_ROUND], g[G_HUMAN1] | g[G_HUMAN2]
            in_arcade = mode == 0 and hf in (1, 2)
            if not in_arcade:
                forced, used, by_round = None, [], {}      # left arcade -> fresh next run
                self._sleep(0.05)
                continue
            if s["boss"] == "endless" and rnd >= 7:
                game.write(G + G_ROUND, bytes([0]))
                last_round = 0
                if time.time() - last_wrap < 30:            # same wrap, re-written by the game
                    self._sleep(0.01)
                    continue
                last_wrap = time.time()
                loops += 1
                by_round = {}
                self.log("Arcade companion: ENDLESS loop %d - next is fight %d (win streak %d)" % (
                    loops + 1, loops * 7 + 1, loops * 7))
                if routes:                                 # fresh stages for the new loop
                    self._new_routes(game, "fights %d-%d" % (loops * 7 + 1, loops * 7 + 7))
                self.status("Endless: loop %d, win streak %d" % (loops + 1, loops * 7))
                self._sleep(0.01)
                continue
            if last_round is not None and rnd < last_round:
                used, by_round, loops = [], {}, 0          # new arcade run
                if routes:                                 # deck carries on: no early repeats
                    self._new_routes(game, "the new run")
            last_round = rnd
            colors_on = bool(s.get("random_colors"))
            if not (teams_on or colors_on) or (rnd == 7 and s["boss"] == "abyss"):
                self._sleep(0.01)
                continue
            cpu = 1 if hf == 1 else 0
            slots = [P + SLOT0 + (pt * 2 + cpu) * SLOT_STRIDE for pt in range(3)]
            cur = [(game.read(x + F_CHAR, 1)[0], game.read(x + F_ASSIST, 1)[0]) for x in slots]
            fill = game.read(P + FILL_BITS + cpu, 1)[0]
            fresh = (fill == 0x77 and len({c for c, _ in cur}) == 3
                     and all(c < 59 and a == default_assist[c] for c, a in cur)
                     and cur != forced)
            if fresh:
                if rnd in by_round:                         # game re-ran its picker
                    team, cols = by_round[rnd]
                elif not teams_on:                          # the game's own picks
                    team, cols = cur, None
                elif s.get("random_teams"):
                    team = random_team(used[-1] if used else None)
                elif s["order"] == "sequential":
                    # keeps counting through Endless loops (fight 8 = team #8 ...)
                    team = s["pool"][(loops * 7 + rnd) % len(s["pool"])]
                else:
                    # every team once before any repeat; never the same team twice in a row
                    avail = [t for t in s["pool"] if t not in used]
                    if not avail:
                        used = used[-1:]
                        avail = [t for t in s["pool"] if t not in used] or s["pool"]
                    team = random.choice(avail)
                if rnd not in by_round:
                    if teams_on:
                        used.append(team)
                    cols = None
                    if colors_on:
                        # a random button each; never the colour the human gave the
                        # same character (two of one character can't share one)
                        hum = [P + SLOT0 + (pt * 2 + 1 - cpu) * SLOT_STRIDE for pt in range(3)]
                        taken = {}
                        for x in hum:
                            hc, hcol = game.read(x + F_CHAR, 2)
                            taken.setdefault(hc, set()).add(hcol)
                        cols = [random.choice([k for k in range(6) if k not in taken.get(c, ())]
                                              or [0]) for c, _ in team]
                    by_round[rnd] = (team, cols)
                want = [(c, default_assist[c] if a is None else a) for c, a in team]
                for i, (x, (c, a)) in enumerate(zip(slots, want)):
                    if teams_on:
                        game.write(x + F_CHAR, bytes([c]))
                        game.write(x + F_ACTIVE, bytes([c]))
                        game.write(x + F_ASSIST, bytes([a]))
                    if cols:
                        game.write(x + F_COLOR, bytes([cols[i]]))
                forced = want
                stage = self._stage_name(rnd)
                self.log("Arcade companion: fight %d: %s   (%s)%s" % (
                    loops * 7 + rnd + 1, team_str(want, cols),
                    "game had " + " / ".join(CHARS[c] for c, _ in cur) if teams_on
                    else "the game's own pick",
                    "   stage: %s" % stage if stage else ""))
                self.status("Fight %d: %s" % (loops * 7 + rnd + 1, team_str(want)))
            self._sleep(0.005)
