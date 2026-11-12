"""Onboarding pieces for the main window (mixed into gui.App):

- the Home "Getting started" checklist
- importing custom content from a game file (importer.py)
- empty-gallery panels with an in-place Download button
- "Add my own..." palette / stage files
"""
import os
import shutil
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import activity
import config
import importer
import palettes
import randomize
import stagegal
import steamcfg


def open_folder(path):
    try:
        os.makedirs(path, exist_ok=True)
        os.startfile(path)                     # noqa - Windows only app
    except (OSError, AttributeError) as e:
        messagebox.showerror("Couldn't open the folder", f"{path}\n\n{e}")


def has_gallery():
    return bool(config.skins_count()) or stagegal.manifest() is not None


class OnboardMixin:
    # ------------------------------------------------------------ checklist
    CHECK_OK, CHECK_TODO, CHECK_OPT = "✓", "○", "·"

    def _build_checklist(self, t):
        """Getting-started list at the top of Home. Each row ticks itself off;
        when the required ones are done it folds into a single line."""
        self.chk_box = ttk.Frame(t)
        self.chk_box.pack(fill="x")
        self._chk_open = None            # None = automatic (open until done)
        self._chk_last = None

    def _chk_state(self):
        cfg = randomize.get_config()
        game = config.has_game()
        on = {k: bool(cfg.get(k, True)) for k, _, _ in self.HOME_ROWS}
        need_gallery = on["randomize_palettes"] or on["randomize_stages"]
        gal = has_gallery()
        prot, _ = randomize.protected_chars()
        try:
            sprot = list(randomize._engine().load_stage_state()["protected"])
        except Exception:
            sprot = []
        rep = getattr(self, "_last_check", None) or {}
        modded = bool(rep.get("palettes") or rep.get("fx") or rep.get("stages"))
        imported = bool(cfg.get("import_done"))
        try:
            eng = randomize._engine()
            fresh = not (eng.load_palette_state()["written"]
                         or eng.load_stage_state()["written"])
        except Exception:
            fresh = False
        try:
            ms = randomize.mix_summary()
        except Exception:
            ms = {}
        applied = os.path.exists(config.LAST_RUN)
        stale = ms.get("stale") or []
        steam = getattr(self, "_steam_set", None)
        rows = []
        rows.append(("game", game, "Game found" if game else "Find your game",
                     None if game else ("Choose game folder...", self.on_set_game)))
        if need_gallery or not gal:
            rows.append(("gallery", gal,
                         "Gallery downloaded" if gal else
                         "Download the gallery - community palettes and stages "
                         "(about 750 MB)" + ("" if need_gallery else " - optional"),
                         None if gal else ("Download", self.on_download),
                         not need_gallery))
        if prot or sprot:
            what = []
            if prot:
                what.append(f"{len(prot)} character(s)")
            if sprot:
                what.append(f"{len(sprot)} stage slot(s)")
            rows.append(("import", False,
                         f"{' and '.join(what)} were changed by another tool "
                         "(PalMod, a stage mod...) and are left alone. Import them "
                         "to keep them and use them like gallery content.",
                         ("Import them...", lambda: self.on_import("live"))))
        elif modded and fresh and not imported:
            src = "backup" if randomize.backup_info() else "game"
            rows.append(("import", False,
                         "Your game already has custom colours or stages. Import "
                         "them so they're kept (and become yours to lock, mix and "
                         "share).",
                         ("Import them...", lambda: self.on_import(src)), True))
        rnote = getattr(self, "_reconcile_note", None)
        if rnote:
            rows.append(("reconciled", True, rnote, None))
        note = getattr(self, "_import_note", None)
        if note:
            rows.append(("imported", True, note,
                         ("Show on Locks tab", lambda: self._select_tab("Locks"))))
        rows.append(("choose", any(on.values()), "Choose what to change (below)",
                     None))
        cur = self._curated_offer(cfg, on, gal)
        if cur:
            rows.append(("curated", False,
                         f"Optional: start from \"{cur['name']}\" by {cur['author']} - "
                         f"{cur['keeps']:,} hand-picked palettes. Random palettes are "
                         "drawn from the ones marked Keep; this marks their picks as "
                         "Keep so you don't have to review thousands yourself. Your own "
                         "locks stay, and your game isn't changed until you Apply.",
                         [("Use their picks", lambda: self._curated_use(cur)),
                          ("No thanks", lambda: self._curated_skip())], True))
        if not applied:
            rows.append(("apply", False, "Apply to your game - nothing has been "
                         "written yet", ("Apply to game", self.on_randomize)))
        elif stale:
            rows.append(("apply", False, "Some changes aren't in your game yet "
                         f"({', '.join(self._stale_words(stale))})",
                         ("Apply to game", self.on_randomize)))
        else:
            rows.append(("apply", True, "Your game has your latest settings", None))
        rows.append(("steam", bool(steam),
                     "Apply automatically every time you start the game from Steam"
                     + ("" if steam else " - optional"),
                     None if steam else ("Set up...", self.on_enable_steam), True))
        return rows

    @staticmethod
    def _stale_words(stale):
        names = {"palette_locks.txt": "palette locks", "stage_locks.json": "stage locks",
                 "fx_state.json": "FX", "fx_locks.json": "FX locks",
                 "randomizer_config.json": "settings"}
        out = []
        for s in stale:
            w = names.get(s, s)
            if w not in out:
                out.append(w)
        return out

    def _checklist_refresh(self):
        if getattr(self, "chk_box", None) is None:
            return
        if getattr(self, "_steam_set", "x") == "x":
            self._steam_set = None
            threading.Thread(target=self._steam_probe, daemon=True).start()
        rows = self._chk_state()
        sig = repr([(r[0], r[1], r[2]) for r in rows]) + repr(self._chk_open)
        if sig == self._chk_last:
            return
        self._chk_last = sig
        for w in self.chk_box.winfo_children():
            w.destroy()
        required_done = all(r[1] for r in rows if not (len(r) > 4 and r[4]))
        is_open = self._chk_open if self._chk_open is not None else not required_done
        bg, fg = self.BANNER["ok" if required_done else "warn"]
        head = tk.Frame(self.chk_box, bg=bg)
        head.pack(fill="x")
        txt = ("✓  All set - your game is ready." if required_done else
               "Getting started - " + str(sum(1 for r in rows if not r[1]
                                               and not (len(r) > 4 and r[4])))
               + " step(s) left")
        tk.Label(head, text=txt, bg=bg, fg=fg, font=("Segoe UI", 10, "bold"),
                 padx=10, pady=6, anchor="w").pack(side="left")
        if is_open:
            tk.Button(head, text="✕", relief="flat", bg=bg, fg=fg, cursor="hand2",
                      font=("Segoe UI", 11, "bold"), bd=0, padx=8,
                      activebackground=bg,
                      command=lambda: self._chk_toggle(False)).pack(side="right", padx=4)
        else:
            tk.Button(head, text="Show steps", relief="flat", bg=bg, fg=fg,
                      cursor="hand2", activebackground=bg,
                      command=lambda: self._chk_toggle(True)).pack(side="right", padx=6)
        if not is_open:
            return
        body = tk.Frame(self.chk_box, bg=bg)
        body.pack(fill="x")
        for i, r in enumerate(rows):
            key, done, text, action = r[0], r[1], r[2], r[3]
            optional = len(r) > 4 and r[4]
            mark = self.CHECK_OK if done else (self.CHECK_OPT if optional else self.CHECK_TODO)
            col = "#1e6b34" if done else ("#666" if optional else fg)
            tk.Label(body, text=mark, bg=bg, fg=col, font=("Segoe UI", 11, "bold"),
                     width=2).grid(row=i, column=0, sticky="nw", padx=(10, 2), pady=2)
            tk.Label(body, text=text, bg=bg, fg=col, anchor="w", justify="left",
                     wraplength=self.SETUP_WRAP - 220,
                     font=("Segoe UI", 10)).grid(row=i, column=1, sticky="w", pady=2)
            acts = action if isinstance(action, list) else ([action] if action else [])
            if acts:
                cell = tk.Frame(body, bg=bg)
                cell.grid(row=i, column=2, sticky="e", padx=10, pady=2)
                for label, fn in acts:
                    b = ttk.Button(cell, text=label, command=fn)
                    b.pack(side="left", padx=(6, 0))
                    if key in ("apply", "import", "gallery", "curated"):
                        self._action_widgets.append(b)
                        if self.busy:
                            b.config(state="disabled")
        body.columnconfigure(1, weight=1)

    def _chk_toggle(self, open_):
        self._chk_open = open_
        self._checklist_refresh()

    # ------------------------------------------------- curated palette offer
    def _curated_offer(self, cfg, on, gal):
        """The first curated mix, offered once to someone who hasn't reviewed
        palettes yet (their imported / locked ones don't count)."""
        if cfg.get("curated_offer_done") or not gal or not on.get("randomize_palettes"):
            return None
        pres = getattr(self, "_presets", None) or []
        if not pres:
            return None
        import locks as pal_locks
        locked = {f"{f}/{v}" for (f, _b), v in pal_locks.load_locks().items() if v}
        reviewed = [k for k in palettes.load_verdicts()
                    if "/custom/" not in k and k not in locked]
        return pres[0] if len(reviewed) < 50 else None

    def _curated_use(self, p):
        import presets
        try:
            # fill, not replace: never clears the user's own locks / imports
            result = presets.apply_preset(p["path"], "fill")
        except Exception as e:
            activity.error("curated mix", e)
            messagebox.showerror("Couldn't use that mix", str(e))
            return
        randomize.set_config_key("curated_offer_done", True)
        self.logln(f"Using \"{result['name']}\": {result['palettes']:,} palettes "
                   "marked Keep.")
        self.refresh_status()
        self._on_char_change()
        self._stage_built = False
        self._checklist_refresh()

    def _curated_skip(self):
        randomize.set_config_key("curated_offer_done", True)
        self._checklist_refresh()

    def _steam_probe(self):
        try:
            v = steamcfg.launch_option_set()
        except Exception:
            v = None
        self.q.put(("call", lambda: (setattr(self, "_steam_set", v),
                                     self._checklist_refresh())))

    # --------------------------------------------------------------- import
    def on_import(self, source="live", quiet=False):
        """source: 'live' (this game - only parts another tool changed),
        'backup' (the original game file saved on first run), or 'file'.
        quiet: no confirm / report windows (the setup checkbox already said
        yes) - the result shows on the Home checklist instead."""
        if self.busy:
            return
        if source == "file":
            path = filedialog.askopenfilename(
                title="Import custom content from a game file",
                filetypes=[("Game file", "*.arc"), ("All files", "*.*")])
            if not path:
                return
        elif source == "game":
            path = config.game_arc()
            if not path or not os.path.exists(path):
                messagebox.showerror("Game not found", "Set your game folder in "
                                     "Settings first.")
                return
        elif source == "backup":
            path = config.BACKUP_ARC
            if not os.path.exists(path):
                messagebox.showinfo("No backup", "There's no backup of your original "
                                    "game file yet.")
                return
        else:
            path = config.game_arc()
            if not path or not os.path.exists(path):
                messagebox.showerror("Game not found", "Set your game folder in "
                                     "Settings first.")
                return
        self._set_busy(True, "Looking for custom content...")
        activity.log(f"import: scanning {source} ({path})")

        def work():
            try:
                plan = importer.scan(path, source,
                                     progress=lambda m: self.q.put(("status", m)))
                self.q.put(("call", lambda: self._import_confirm(plan, quiet)))
            except Exception as e:
                activity.error("import scan", e)
                err = str(e)
                self.q.put(("call", lambda: (self._set_busy(False), messagebox.showerror(
                    "Couldn't read that game file", err))))
        threading.Thread(target=work, daemon=True).start()

    def _import_confirm(self, plan, quiet=False):
        self._set_busy(False)
        s = importer.summary(plan)
        lines = []
        if s["palettes_new"]:
            lines.append(f"{s['palettes_new']} custom palette(s) - saved as your own "
                         "sheets and locked to the button they're on")
        if s["palettes_gallery"]:
            lines.append(f"{s['palettes_gallery']} palette(s) that are already in the "
                         "gallery")
        if s["fx_entries"]:
            lines.append(f"Edited effect colours on {s['fx_chars']} character(s) - "
                         "kept as they are")
        if s["stages_new"]:
            lines.append(f"{s['stages_new']} custom stage(s) - saved as your own and "
                         "locked to their slot")
        if s["stages_gallery"]:
            lines.append(f"{s['stages_gallery']} stage(s) that are already in the gallery")
        if s["palettes_skipped"]:
            lines.append(f"{s['palettes_skipped']} palette(s) can't be imported (unknown "
                         "sheet layout)")
        if not lines:
            if plan["source"] != "file":
                randomize.set_config_key("import_done", True)
            if quiet:
                self.logln("Import: no custom palettes, effects or stages found.")
                self._checklist_refresh()
                return
            messagebox.showinfo("Nothing to import",
                                "No custom palettes, effects or stages were found"
                                + (" that another tool changed." if plan["source"] == "live"
                                   else "."))
            self._checklist_refresh()
            return
        if quiet:
            self._import_run(plan, True, quiet=True)
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("Import custom content")
        dlg.transient(self.root)
        box = ttk.Frame(dlg, padding=16)
        box.pack(fill="both", expand=True)
        src = {"live": "your game", "game": "your game",
               "backup": "your original game file",
               "file": os.path.basename(plan["arc"])}[plan["source"]]
        ttk.Label(box, text=f"Found in {src}:", font=("Segoe UI", 11, "bold")
                  ).pack(anchor="w")
        ttk.Label(box, text="".join("\n   •  " + ln for ln in lines), justify="left",
                  wraplength=600).pack(anchor="w")
        lockv = tk.BooleanVar(value=True)
        if s["palettes_gallery"] or s["stages_gallery"]:
            ttk.Checkbutton(box, variable=lockv,
                            text="Also lock the gallery palettes and stages it uses "
                                 "(so they stay put too)").pack(anchor="w", pady=(10, 0))
        if s["other"]:
            ttk.Label(box, foreground="#8a5a00", wraplength=600, justify="left",
                      text=f"\n{s['other']} other changed file(s) (not colours or stages) "
                           "can't be imported. They stay in your game as they are."
                      ).pack(anchor="w")
        ttk.Label(box, foreground="#555", wraplength=600, justify="left",
                  text="\nFiles go into the custom folder next to the app; your game "
                       "file isn't changed. With palettes / stages switched on, the "
                       "next Apply puts exactly these back, and from then on you can "
                       "swap, mix and share them like anything in the gallery."
                  ).pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(12, 0))

        def go():
            dlg.destroy()
            self._import_run(plan, bool(lockv.get()))
        ttk.Button(row, text="Import", command=go).pack(side="right")
        ttk.Button(row, text="Cancel", command=dlg.destroy).pack(side="right", padx=6)
        dlg.grab_set()

    def _import_run(self, plan, lock_gallery, quiet=False):
        out = {}

        def work(progress=None, status=None):
            out["rep"] = importer.run(plan, lock_gallery=lock_gallery, progress=progress)
            return 0

        def done():
            if plan["source"] != "file":
                randomize.set_config_key("import_done", True)
            self._import_report(out.get("rep") or {}, quiet)
            self._import_refresh_all()
        self.run_job("Importing your custom content", work, on_done=done)

    def _import_report(self, rep, quiet=False):
        n_p, n_s = len(rep.get("palettes", [])), len(rep.get("stages", []))
        n_g, n_f = len(rep.get("locked_gallery", [])), len(rep.get("fx", []))
        for k in ("palettes", "locked_gallery", "fx", "stages", "skipped"):
            for ln in rep.get(k, []):
                self.logln(f"  [{k}] {ln}")
        parts = []
        if n_p:
            parts.append(f"{n_p} palette sheet(s) saved to custom\\skins and locked")
        if n_s:
            parts.append(f"{n_s} stage(s) saved to custom\\stages and locked")
        if n_f:
            parts.append(f"effect colours kept for {n_f} character(s) (custom\\fx)")
        if n_g:
            parts.append(f"{n_g} gallery palette/stage lock(s) set")
        if rep.get("unprotected"):
            parts.append(f"{len(rep['unprotected'])} character(s) no longer left alone - "
                         "the app manages them now")
        # Home checklist line until the app closes
        self._chk_open = True                  # show the note, even if all done
        self._import_note = ("Imported your custom content: "
                             + ("; ".join(parts) if parts else "nothing new")
                             + ". Change them any time on the Locks tab.")
        self._checklist_refresh()
        if quiet:
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("Imported")
        dlg.transient(self.root)
        box = ttk.Frame(dlg, padding=16)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="Done:", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(box, text="".join("\n   •  " + p for p in parts) or "\nNothing changed.",
                  justify="left", wraplength=600).pack(anchor="w")
        ttk.Label(box, foreground="#555", wraplength=600, justify="left",
                  text="\nThe full list is on the Logs tab. Your game looks the same "
                       "after the next Apply.").pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(12, 0))
        ttk.Button(row, text="OK", command=dlg.destroy).pack(side="right")
        ttk.Button(row, text="Open custom folder",
                   command=lambda: open_folder(config.CUSTOM)).pack(side="right", padx=6)

    def _import_refresh_all(self):
        """Galleries, locks and Home after files/locks changed."""
        try:
            self.on_rescan()
        except Exception as e:
            activity.error("rescan after import", e)
        for fn in ("_home_refresh", "_refresh_protected"):
            try:
                getattr(self, fn)()
            except Exception:
                pass

    # --------------------------------------------------- empty gallery panel
    def _gallery_empty_panel(self, parent, what, sub="", note=""):
        """A 'get the gallery' panel shown at the top of a gallery tab while
        nothing is downloaded. Returns the frame (packed/unpacked by refresh)."""
        bg, fg = self.BANNER["warn"]
        f = tk.Frame(parent, bg=bg, padx=12, pady=10)
        tk.Label(f, text=f"No {what} yet - the gallery isn't downloaded.", bg=bg, fg=fg,
                 font=("Segoe UI", 11, "bold"), anchor="w").pack(anchor="w")
        tk.Label(f, bg=bg, fg=fg, justify="left", anchor="w",
                 wraplength=self.SETUP_WRAP,
                 text="The community palettes and stages (about 750 MB) aren't "
                      "included in the app - get them once here. " + note
                 ).pack(anchor="w", pady=(2, 8))
        row = tk.Frame(f, bg=bg)
        row.pack(anchor="w")
        b = ttk.Button(row, text="Download the gallery (~750 MB)", command=self.on_download)
        b.pack(side="left")
        self._action_widgets.append(b)
        self._empty_panels = getattr(self, "_empty_panels", []) + [f]
        return f

    def _gallery_empty_refresh(self):
        show = not has_gallery()
        for f in getattr(self, "_empty_panels", []):
            try:
                if show and not f.winfo_ismapped():
                    kids = f.master.pack_slaves()
                    f.pack(fill="x", pady=(0, 8), before=kids[0] if kids else None)
                elif not show:
                    f.pack_forget()
            except tk.TclError:
                pass

    # ------------------------------------------------------ add my own files
    def on_add_palettes(self):
        """Pick palette sheets; each goes to the character whose layout it
        matches (the one selected first), checked before copying."""
        paths = filedialog.askopenfilenames(
            title="Add your own palette sheets (PNG)",
            filetypes=[("Palette sheet", "*.png")])
        if not paths:
            return
        specs = palettes._sheet_specs()
        cur = getattr(self, "char_var", None)
        cur = cur.get() if cur is not None else None
        added, bad = [], []
        from PIL import Image
        import hashlib
        for p in paths:
            try:
                with Image.open(p) as im:
                    if im.mode != "P":
                        bad.append(f"{os.path.basename(p)}: not an indexed (palette) PNG")
                        continue
                    md5 = hashlib.md5(im.tobytes()).hexdigest()
                    size = im.size
            except Exception as e:
                bad.append(f"{os.path.basename(p)}: unreadable ({e})")
                continue
            match = [c for c, s in specs.items() if s["md5"] == md5]
            same_size = [c for c, s in specs.items() if (s["w"], s["h"]) == size]
            if not match:
                why = (f"same size as {same_size[0]}'s sheet but the sprite layout "
                       "differs - colours would scramble" if same_size else
                       "not a gallery-format sheet for any character")
                bad.append(f"{os.path.basename(p)}: {why}")
                continue
            char = cur if cur in match else match[0]
            d = os.path.join(config.CUSTOM, "skins", char)
            os.makedirs(d, exist_ok=True)
            dst = os.path.join(d, os.path.basename(p))
            n = 2
            while os.path.exists(dst):
                stem, ext = os.path.splitext(os.path.basename(p))
                dst = os.path.join(d, f"{stem}-{n}{ext}")
                n += 1
            shutil.copyfile(p, dst)
            added.append(f"{char}: {os.path.basename(dst)}")
        activity.log(f"add palettes: {len(added)} added, {len(bad)} refused")
        msg = ""
        if added:
            msg += "Added (as custom/...):\n" + "\n".join("   " + a for a in added[:15])
            if len(added) > 15:
                msg += f"\n   ... and {len(added) - 15} more"
        if bad:
            msg += ("\n\n" if msg else "") + "Not added:\n" + "\n".join(
                "   " + b for b in bad[:10])
        (messagebox.showinfo if added else messagebox.showwarning)(
            "Add your own palettes", msg)
        if added:
            self._import_refresh_all()
            if has_gallery():
                self.root.after(300, lambda: self.on_reconcile(auto=True))

    def on_add_stages(self):
        """Copy stage files (POL/TEX .BIN) into custom/stages; anything whose
        name doesn't say where it goes is assigned in the Stage Gallery."""
        paths = filedialog.askopenfilenames(
            title="Add your own stage files (POL / TEX .BIN)",
            filetypes=[("Stage files", "*.bin *.BIN"), ("All files", "*.*")])
        if not paths:
            return
        d = os.path.join(config.CUSTOM, "stages")
        os.makedirs(d, exist_ok=True)
        for p in paths:
            dst = os.path.join(d, os.path.basename(p))
            if os.path.abspath(p) != os.path.abspath(dst):
                shutil.copyfile(p, dst)
        activity.log(f"add stages: {len(paths)} file(s) copied")
        pending = stagegal.pending_customs()
        self._import_refresh_all()
        if pending and hasattr(self, "on_assign_customs"):
            self.on_assign_customs()
        else:
            messagebox.showinfo("Add your own stages",
                                f"Added {len(paths)} file(s). They're in the Stage "
                                "Gallery under their slot (or Custom).")

    # ------------------------------------------------------------ reconcile
    @staticmethod
    def _has_custom_content():
        for sub in ("skins", "stages"):
            for _r, _d, files in os.walk(os.path.join(config.CUSTOM, sub)):
                if any(f.lower().endswith((".png", ".bin")) for f in files):
                    return True
        return False

    def on_reconcile(self, auto=False):
        """Match the user's own palettes / stages to identical gallery items
        (importer.reconcile) and report the result on the Home checklist."""
        if self.busy:
            return
        if not has_gallery():
            messagebox.showinfo("Gallery needed", "Download the gallery first - "
                                "your files are compared with it.")
            return
        out = {}

        def work(progress=None, status=None):
            out["rep"] = importer.reconcile(progress=status)
            return 0

        def done():
            rep = out.get("rep") or {}
            n_p, n_s = len(rep.get("palettes", [])), len(rep.get("stages", []))
            for k in ("palettes", "stages"):
                for ln in rep.get(k, []):
                    self.logln(f"  [matched] {ln}")
            if n_p or n_s:
                bits = []
                if n_p:
                    bits.append(f"{n_p} palette(s)")
                if n_s:
                    bits.append(f"{n_s} stage(s)")
                self._reconcile_note = (
                    f"Matched {' and '.join(bits)} of yours to the gallery - they now "
                    "show with the gallery's names and previews, still locked where "
                    "they were. (Your copies are in custom\\reconciled.)")
            elif not auto:
                self._reconcile_note = ("None of your own palettes or stages match "
                                        "a gallery item exactly - they stay as yours.")
            if n_p or n_s or not auto:
                self._import_refresh_all()
                self._chk_open = True          # show the note, even if all done
                self._chk_last = None
                self._checklist_refresh()
        self.run_job("Matching your files to the gallery", work, on_done=done)

    def _import_menu(self):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="From my game - parts another tool changed",
                      command=lambda: self.on_import("live"))
        if randomize.backup_info():
            m.add_command(label="From my original game file (the first-run backup)",
                          command=lambda: self.on_import("backup"))
        m.add_command(label="From another game file (e.g. a friend's game_50.arc)...",
                      command=lambda: self.on_import("file"))
        m.add_separator()
        m.add_command(label="Match my own files to the gallery (names and previews)",
                      command=self.on_reconcile)
        try:
            m.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally:
            m.grab_release()
