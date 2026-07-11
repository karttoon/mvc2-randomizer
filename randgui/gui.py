#!/usr/bin/env python3
r"""gui.py - MvC2 Palette Randomizer, graphical front-end (Tkinter, stdlib only).

Two modes, chosen at startup:
  * No arguments (double-clicked)  -> opens this setup window.
  * With arguments (Steam runs it as a launch option, passing %command%)
    -> silent: randomize palettes, then launch the game, then exit.

Mirrors the MvC2 Mix Converter GUI: one App class, numbered Notebook tabs, a
log pane + indeterminate progress bar, and a threaded run_job/queue so the UI
stays responsive. Flat imports (config/randomize/steamcfg), same as that app.
"""
import os, sys, queue, threading, traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

from PIL import ImageTk

import config
import randomize
import steamcfg
import palettes
import locks

APP_TITLE = "MvC2 Palette Randomizer"


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.busy = False
        self._action_widgets = []

        root.title(APP_TITLE)
        root.geometry("1000x780")
        root.minsize(820, 640)
        try:
            root.call("tk", "scaling", 1.2)
        except tk.TclError:
            pass

        self.seed_var = tk.StringVar()

        self._build_header()
        self._build_tabs()
        self._build_log()
        self._build_statusbar()

        self.refresh_status()
        self.root.after(100, self._drain_queue)
        self.root.after(60, self._on_char_change)   # load the first character's palettes
        # Announce protections detected while running as a Steam launcher
        # (that mode is silent, so the news waits for the next GUI open).
        self.root.after(400, self._announce_new_protected)

    # ---------------------------------------------------------------- layout
    def _build_header(self):
        top = ttk.Frame(self.root, padding=(14, 12, 14, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Marvel vs. Capcom 2 - Palette Randomizer",
                  font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(top, text="Randomizes your curated character palettes every time you launch the game.",
                  foreground="#555").pack(anchor="w")
        self.status_lbl = ttk.Label(top, text="", font=("Segoe UI", 9))
        self.status_lbl.pack(anchor="w", pady=(6, 0))

    def _build_tabs(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=14, pady=(6, 4))
        self.nb = nb
        self._tab_randomize(nb)
        self._tab_palettes(nb)
        self._tab_locks(nb)
        self._tab_setup(nb)
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _build_log(self):
        frm = ttk.LabelFrame(self.root, text="Progress", padding=6)
        frm.pack(fill="x", padx=14, pady=(4, 4))
        self.log = scrolledtext.ScrolledText(frm, height=7, wrap="word",
                                             state="disabled", font=("Consolas", 9))
        self.log.pack(fill="x")

    def _build_statusbar(self):
        bar = ttk.Frame(self.root, padding=(14, 2, 14, 8))
        bar.pack(fill="x")
        self.busy_lbl = ttk.Label(bar, text="", foreground="#555")
        self.busy_lbl.pack(side="right", padx=(0, 10))
        self.pbar = ttk.Progressbar(bar, mode="indeterminate", length=160)
        # only shown while a job is running (a stopped indeterminate bar shows a
        # stray idle block, which looks like it's stuck ~1/10 full)

    # ------------------------------------------------------------- setup tab
    def _tab_setup(self, nb):
        t = ttk.Frame(nb, padding=14); nb.add(t, text="4. Setup")
        ttk.Label(t, text="Game install", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(t, justify="left", foreground="#555", text=(
            "Your Steam install is detected automatically. If the game is on\n"
            "another drive or isn't found, use “Change game folder…” to point at it.")
        ).pack(anchor="w", pady=(4, 10))

        self.game_lbl = ttk.Label(t, text="", foreground="#333",
                                  wraplength=660, justify="left")
        self.game_lbl.pack(anchor="w", pady=(0, 2))
        ch = ttk.Button(t, text="Change game folder...", command=self.on_set_game)
        ch.pack(anchor="w", pady=(0, 12))
        self._action_widgets.append(ch)

        ttk.Separator(t, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(t, text="Auto-randomize on launch", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(t, foreground="#555", justify="left", text=(
            "A one-time setup: this copies a short line to your clipboard and\n"
            "shows where to paste it in Steam. After that, your palettes are\n"
            "randomized automatically every time you launch the game.")
        ).pack(anchor="w", pady=(4, 8))
        b = ttk.Button(t, text="Enable Auto-Randomize in Steam",
                       command=self.on_enable_steam)
        b.pack(anchor="w")
        self._action_widgets.append(b)

    # ---------------------------------------------------------- palettes tab
    GRID_COLS = 5
    IMG_WIDTH = 600
    KEEP_COLOR = "#2f8f2f"
    REJECT_COLOR = "#c53a3a"
    NEUTRAL_COLOR = "#777777"

    def _tab_palettes(self, nb):
        t = ttk.Frame(nb, padding=10); nb.add(t, text="2. Palette Gallery")

        top = ttk.Frame(t); top.pack(fill="x")
        ttk.Label(top, text="Character:").pack(side="left")
        self.char_var = tk.StringVar()
        self.char_combo = ttk.Combobox(top, textvariable=self.char_var, state="readonly",
                                       width=24, values=[])
        self.char_combo.pack(side="left", padx=(4, 12))
        self.char_combo.bind("<<ComboboxSelected>>", lambda e: self._on_char_change())
        self.view_var = tk.StringVar(value="image")
        ttk.Radiobutton(top, text="Image", value="image", variable=self.view_var,
                        command=self._show_view).pack(side="left")
        ttk.Radiobutton(top, text="Grid", value="grid", variable=self.view_var,
                        command=self._show_view).pack(side="left", padx=(4, 0))
        self.unrev_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Unreviewed only", variable=self.unrev_var,
                        command=self._on_unrev_toggle).pack(side="left", padx=(12, 0))
        dl = ttk.Button(top, text="Download / Update", command=self.on_download)
        dl.pack(side="right"); self._action_widgets.append(dl)

        self.palettes_lbl = ttk.Label(t, text="", foreground="#555")
        self.palettes_lbl.pack(anchor="w", pady=(6, 6))

        # Footer, kept well away from Download so it's not an easy mis-click.
        footer = ttk.Frame(t); footer.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Separator(footer, orient="horizontal").pack(fill="x", pady=(0, 6))
        rr = ttk.Button(footer, text="Delete rejected files...",
                        command=self.on_remove_rejected)
        rr.pack(side="left"); self._action_widgets.append(rr)
        ttk.Label(footer, foreground="#888",
                  text="Permanently removes palettes you've marked Reject from disk."
                  ).pack(side="left", padx=(10, 0))

        self.pal_body = ttk.Frame(t); self.pal_body.pack(fill="both", expand=True)
        self._build_image_view(self.pal_body)
        self._build_grid_view(self.pal_body)
        self.pal_files = []
        self._cur_char = None
        self._chars = []         # cached folder list
        self._char_map = {}      # dropdown display string -> folder name
        self._unrev = {}         # folder -> count of unreviewed (cached)
        self._show_view()

    def _build_image_view(self, parent):
        self.image_view = ttk.Frame(parent)
        pw = ttk.PanedWindow(self.image_view, orient="horizontal")
        pw.pack(fill="both", expand=True)
        self._pw = pw

        left = ttk.Frame(pw)
        self.file_list = tk.Listbox(left, width=32, activestyle="dotbox",
                                    exportselection=False, font=("Consolas", 9))
        lsb = ttk.Scrollbar(left, orient="vertical", command=self.file_list.yview)
        self.file_list.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")
        self.file_list.pack(side="left", fill="both", expand=True)
        self.file_list.bind("<<ListboxSelect>>", lambda e: self._show_current())
        # Nav/verdict keys on the list itself; "break" stops the listbox's own
        # Left/Right (which otherwise horizontally scrolls the text).
        self.file_list.bind("<Left>", lambda e: (self._nav(-1), "break")[1])
        self.file_list.bind("<Right>", lambda e: (self._nav(1), "break")[1])
        self.file_list.bind("<y>", lambda e: self._set_verdict(palettes.KEEP))
        self.file_list.bind("<Y>", lambda e: self._set_verdict(palettes.KEEP))
        self.file_list.bind("<n>", lambda e: self._set_verdict(palettes.REJECT))
        self.file_list.bind("<N>", lambda e: self._set_verdict(palettes.REJECT))
        self.file_list.bind("<c>", lambda e: self._clear_verdict())
        self.file_list.bind("<C>", lambda e: self._clear_verdict())
        pw.add(left, weight=0)       # holds its natural width; still draggable

        right = ttk.Frame(pw)
        self.img_area = ttk.Frame(right)
        self.img_area.pack(fill="both", expand=True)
        self.img_lbl = ttk.Label(self.img_area, anchor="center")
        self.img_lbl.place(relx=0.5, rely=0.5, anchor="center")   # centered, doesn't drive layout
        self.img_area.bind("<Configure>", self._on_img_resize)
        for w in (self.img_area, self.img_lbl):   # clicking the image keeps keys working
            w.bind("<Button-1>", lambda e: self.file_list.focus_set())
        self.cur_name_lbl = ttk.Label(right, text="", font=("Consolas", 9), foreground="#333")
        self.cur_name_lbl.pack(anchor="center", pady=(2, 0))
        self.cur_status_lbl = ttk.Label(right, text="", font=("Segoe UI", 12, "bold"))
        self.cur_status_lbl.pack(anchor="center", pady=(2, 8))
        btns = ttk.Frame(right); btns.pack(anchor="center", pady=(0, 4))
        ttk.Button(btns, text="◀ Previous", command=lambda: self._nav(-1)).pack(side="left")
        self.keep_btn = ttk.Button(btns, text="Keep (Y)",
                                   command=lambda: self._set_verdict(palettes.KEEP))
        self.keep_btn.pack(side="left", padx=(8, 4))
        self.clear_btn = ttk.Button(btns, text="Clear (C)", command=self._clear_verdict)
        self.clear_btn.pack(side="left", padx=(0, 4))
        self.reject_btn = ttk.Button(btns, text="Reject (N)",
                                     command=lambda: self._set_verdict(palettes.REJECT))
        self.reject_btn.pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="Next ▶", command=lambda: self._nav(1)).pack(side="left")
        pw.add(right, weight=4)

    def _safe_sash(self, x):
        try:
            self._pw.sashpos(0, x)
        except Exception:
            pass

    def _on_tab_changed(self, e=None):
        try:
            text = self.nb.tab(self.nb.select(), "text")
        except Exception:
            return
        if text.endswith("Gallery"):
            self.file_list.focus_set()
            self.root.after(80, self._render_image)   # pane is now sized
        elif text.endswith("Lock Palettes"):
            self._lock_populate_chars()
            self._lock_load()

    def _on_img_resize(self, e=None):
        job = getattr(self, "_img_job", None)
        if job:
            self.root.after_cancel(job)
        self._img_job = self.root.after(80, self._render_image)

    def _render_image(self):
        self._img_job = None
        i = self._cur_index()
        if i is None or not self.pal_files:
            self.img_lbl.config(image=""); return
        w = self.img_area.winfo_width() - 6
        h = self.img_area.winfo_height() - 6
        if w < 80 or h < 60:
            return   # not laid out yet; a <Configure> will re-trigger us
        char = self._cur_char; fn = self.pal_files[i]
        try:
            self._cur_img = ImageTk.PhotoImage(palettes.load_scaled(char, fn, w, h))
            self.img_lbl.config(image=self._cur_img)
        except Exception:
            self.img_lbl.config(image="")

    def _build_grid_view(self, parent):
        self.grid_view = ttk.Frame(parent)
        self.gal_canvas = tk.Canvas(self.grid_view, highlightthickness=0, background="#2d2d2d")
        vsb = ttk.Scrollbar(self.grid_view, orient="vertical", command=self.gal_canvas.yview)
        self.gal_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.gal_canvas.pack(side="left", fill="both", expand=True)
        self.gal_inner = ttk.Frame(self.gal_canvas)
        self.gal_canvas.create_window((0, 0), window=self.gal_inner, anchor="nw")
        self.gal_inner.bind("<Configure>", lambda e: self.gal_canvas.configure(
            scrollregion=self.gal_canvas.bbox("all")))
        self.gal_canvas.bind("<Enter>", lambda e: self.gal_canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.gal_canvas.bind("<Leave>", lambda e: self.gal_canvas.unbind_all("<MouseWheel>"))
        self.gal_canvas.bind("<Configure>", self._on_grid_resize)
        self._thumb_refs = []
        self._grid_w = 0

    def _on_wheel(self, e):
        self.gal_canvas.yview_scroll(int(-e.delta / 120), "units")

    def _on_grid_resize(self, e=None):
        if self.view_var.get() != "grid":
            return
        w = self.gal_canvas.winfo_width()
        if abs(w - self._grid_w) < 30:          # ignore small jitters
            return
        job = getattr(self, "_grid_job", None)
        if job:
            self.root.after_cancel(job)
        self._grid_job = self.root.after(180, self._load_grid)

    def _show_view(self):
        self.image_view.pack_forget(); self.grid_view.pack_forget()
        if self.view_var.get() == "grid":
            self.grid_view.pack(fill="both", expand=True)
            self._load_grid()
        else:
            self.image_view.pack(fill="both", expand=True)
            self.file_list.focus_set()

    def _populate_chars(self):
        """Full refresh of the dropdown, recomputing per-character unreviewed counts."""
        self._chars = palettes.char_folders()
        self._unrev = palettes.unreviewed_by_char()
        self._rebuild_char_values()

    def _rebuild_char_values(self):
        """Rebuild the dropdown display strings from the cached counts (cheap)."""
        self._char_map = {}
        values = []
        for c in self._chars:
            n = self._unrev.get(c, 0)
            disp = f"{c}   ({n})" if n else f"{c}   ✓"
            self._char_map[disp] = c
            values.append(disp)
        self.char_combo["values"] = values
        disp = next((d for d, c in self._char_map.items() if c == self._cur_char), None)
        if disp:
            self.char_var.set(disp)
        elif values:
            self.char_var.set(values[0])
            self._cur_char = self._char_map[values[0]]

    def _current_char(self):
        return self._char_map.get(self.char_var.get()) or self._cur_char

    def _update_palette_counts(self):
        total, rej = palettes.counts()
        unrev = sum(self._unrev.values())
        self.palettes_lbl.config(
            text=(f"{total:,} palettes    ·    {unrev} unreviewed    ·    {rej} rejected"
                  if total else "No palettes yet - click 'Download / Update'."))

    # ---- image-review view ----
    def _on_unrev_toggle(self):
        self._on_char_change()
        if self.unrev_var.get() and not self.pal_files:
            self._advance_to_unreviewed_char()

    def _on_char_change(self):
        self._load_char(self._current_char(), land=0)

    def _load_char(self, char, land=0):
        """Populate the list for a character and land on a position (land >= 0
        from the top, land < 0 counts from the end). Syncs the dropdown."""
        self._cur_char = char
        disp = next((d for d, c in self._char_map.items() if c == char), None)
        if disp:
            self.char_var.set(disp)
        verdicts = palettes.load_verdicts()
        self.pal_files = (palettes.list_filtered(char, self.unrev_var.get(), verdicts)
                          if char else [])
        self.file_list.delete(0, "end")
        for fn in self.pal_files:
            self.file_list.insert("end", fn)
        if self.pal_files:
            j = land if land >= 0 else len(self.pal_files) + land
            j = max(0, min(len(self.pal_files) - 1, j))
            self.file_list.selection_clear(0, "end")
            self.file_list.selection_set(j)
            self.file_list.activate(j)
            self.file_list.see(j)
        self._show_current()
        if self.view_var.get() == "grid":
            self._load_grid()
        self._update_palette_counts()

    def _advance_to_unreviewed_char(self):
        """Jump to the next character with unreviewed palettes. Returns True if moved."""
        if not self._chars:
            return False
        start = (self._chars.index(self._cur_char) + 1) if self._cur_char in self._chars else 0
        for c in self._chars[start:] + self._chars[:start]:
            if self._unrev.get(c, 0) > 0:
                self._load_char(c, land=0)
                return True
        return False

    def _nav_char(self, direction):
        """Move to the prev/next character that has palettes in the current view,
        landing on the first (forward) or last (back) palette."""
        if not self._chars:
            return
        idx = self._chars.index(self._cur_char) if self._cur_char in self._chars else 0
        verdicts = palettes.load_verdicts()
        n = len(self._chars)
        for step in range(1, n + 1):
            c = self._chars[(idx + direction * step) % n]
            if palettes.list_filtered(c, self.unrev_var.get(), verdicts):
                self._load_char(c, land=0 if direction > 0 else -1)
                return

    def _cur_index(self):
        sel = self.file_list.curselection()
        return sel[0] if sel else (0 if self.pal_files else None)

    def _nav(self, delta):
        if not self.pal_files:
            self._nav_char(1 if delta > 0 else -1)
            return
        i = self._cur_index() or 0
        j = i + delta
        if j < 0:
            self._nav_char(-1); return
        if j >= len(self.pal_files):
            self._nav_char(1); return
        self.file_list.selection_clear(0, "end")
        self.file_list.selection_set(j)
        self.file_list.activate(j)
        self.file_list.see(j)
        self._show_current()
        self.file_list.focus_set()

    def _show_current(self):
        i = self._cur_index()
        if i is None or not self.pal_files:
            self.img_lbl.config(image="")
            self.cur_name_lbl.config(text="")
            msg = ("All caught up - no unreviewed palettes left." if self.unrev_var.get()
                   else "No palettes for this character.")
            self.cur_status_lbl.config(text=msg, foreground="#888")
            self.keep_btn.config(text="Keep (Y)"); self.reject_btn.config(text="Reject (N)")
            self.clear_btn.config(text="Clear (C)")
            return
        char = self._cur_char; fn = self.pal_files[i]
        self._render_image()
        self.cur_name_lbl.config(text=f"{i + 1} / {len(self.pal_files)}    {fn}")
        v = palettes.load_verdicts().get(palettes.key_for(char, fn))
        if v == palettes.KEEP:
            self.cur_status_lbl.config(text="KEPT", foreground=self.KEEP_COLOR)
        elif v == palettes.REJECT:
            self.cur_status_lbl.config(text="REJECTED", foreground=self.REJECT_COLOR)
        else:
            self.cur_status_lbl.config(text="not reviewed", foreground=self.NEUTRAL_COLOR)
        self.keep_btn.config(text=("✓ Kept" if v == palettes.KEEP else "Keep (Y)"))
        self.reject_btn.config(text=("✓ Rejected" if v == palettes.REJECT else "Reject (N)"))
        self.clear_btn.config(text="Clear (C)")

    def _clear_verdict(self):
        i = self._cur_index()
        if i is None or not self.pal_files:
            return "break"
        char = self._cur_char; fn = self.pal_files[i]
        key = palettes.key_for(char, fn)
        v = palettes.load_verdicts()
        if key in v:                       # was reviewed -> back to unreviewed
            del v[key]
            palettes.save_verdicts(v)
            self._unrev[char] = self._unrev.get(char, 0) + 1
            self._rebuild_char_values()
            self._update_palette_counts()
        self._show_current()
        self.file_list.focus_set()
        return "break"

    def _set_verdict(self, verdict):
        i = self._cur_index()
        if i is None or not self.pal_files:
            return "break"
        char = self._cur_char; fn = self.pal_files[i]
        v = palettes.load_verdicts()
        was_unreviewed = v.get(palettes.key_for(char, fn)) is None
        v[palettes.key_for(char, fn)] = verdict
        palettes.save_verdicts(v)
        if was_unreviewed and self._unrev.get(char):
            self._unrev[char] -= 1
            self._rebuild_char_values()
        self._update_palette_counts()

        if self.unrev_var.get():
            # the item is now reviewed -> remove it and keep the cursor in place
            self.file_list.delete(i)
            del self.pal_files[i]
            if not self.pal_files:
                if not self._advance_to_unreviewed_char():
                    self._show_current()
            else:
                j = min(i, len(self.pal_files) - 1)
                self.file_list.selection_set(j)
                self.file_list.activate(j)
                self.file_list.see(j)
                self._show_current()
        else:
            if i < len(self.pal_files) - 1:
                self._nav(1)            # auto-advance for fast review
            else:
                self._show_current()
        self.file_list.focus_set()
        return "break"

    # ---- grid (read-only overview) ----
    def _load_grid(self):
        if not hasattr(self, "gal_inner"):
            return
        for w in self.gal_inner.winfo_children():
            w.destroy()
        self._thumb_refs = []
        char = self._cur_char
        if char:
            cols = self.GRID_COLS
            cw = max(self.gal_canvas.winfo_width(), 320)
            self._grid_w = cw
            tw = max(110, (cw - (cols + 1) * 10) // cols)   # fill the width
            verdicts = palettes.load_verdicts()
            for i, fn in enumerate(palettes.list_filtered(char, self.unrev_var.get(), verdicts)):
                v = verdicts.get(palettes.key_for(char, fn))
                color = (self.REJECT_COLOR if v == palettes.REJECT
                         else self.KEEP_COLOR if v == palettes.KEEP else self.NEUTRAL_COLOR)
                cell = tk.Frame(self.gal_inner, highlightthickness=3,
                                highlightbackground=color, highlightcolor=color)
                cell.grid(row=i // cols, column=i % cols, padx=4, pady=4)
                try:
                    img = ImageTk.PhotoImage(palettes.thumbnail(char, fn, width=tw))
                except Exception:
                    cell.destroy(); continue
                self._thumb_refs.append(img)
                tk.Label(cell, image=img, bd=0).pack()
        self.gal_canvas.yview_moveto(0)

    # ---- removal ----
    def on_remove_rejected(self):
        verdicts = palettes.load_verdicts()
        keys = sorted(k for k, val in verdicts.items() if val == palettes.REJECT
                      and os.path.isfile(os.path.join(config.SKINS, *k.split("/"))))
        if not keys:
            messagebox.showinfo("Nothing to delete",
                                "No rejected palette files are on disk.")
            return
        if not self._confirm_delete(keys):
            return
        n = palettes.remove_rejected()
        self.logln(f"Deleted {n} rejected palette file(s) from disk.")
        self._on_char_change()

    def _confirm_delete(self, keys):
        dlg = tk.Toplevel(self.root)
        dlg.title("Delete rejected palettes")
        dlg.transient(self.root); dlg.grab_set()
        ttk.Label(dlg, padding=12, justify="left", font=("Segoe UI", 10, "bold"),
                  text=(f"These {len(keys)} palette file(s) will be PERMANENTLY deleted "
                        "from disk:")).pack(anchor="w")
        frm = ttk.Frame(dlg, padding=(12, 0)); frm.pack(fill="both", expand=True)
        lb = tk.Listbox(frm, width=64, height=min(18, max(4, len(keys))), font=("Consolas", 9))
        sb = ttk.Scrollbar(frm, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y"); lb.pack(side="left", fill="both", expand=True)
        for k in keys:
            lb.insert("end", k)
        result = {"ok": False}
        row = ttk.Frame(dlg, padding=12); row.pack(fill="x")
        ttk.Button(row, text=f"Delete {len(keys)} file(s)",
                   command=lambda: (result.update(ok=True), dlg.destroy())).pack(side="right")
        ttk.Button(row, text="Cancel", command=dlg.destroy).pack(side="right", padx=(0, 8))
        dlg.geometry("580x440")
        self.root.wait_window(dlg)
        return result["ok"]

    # ------------------------------------------------------- lock palettes tab
    def _tab_locks(self, nb):
        t = ttk.Frame(nb, padding=14); nb.add(t, text="3. Lock Palettes")
        ttk.Label(t, text="Lock palettes to buttons", font=("Segoe UI", 11, "bold")
                  ).pack(anchor="w")
        ttk.Label(t, foreground="#555", justify="left", text=(
            "Pin a specific palette to a button slot so the randomizer always uses\n"
            "it for that character. Leave a slot on \"(random)\" to keep shuffling it.")
        ).pack(anchor="w", pady=(4, 10))

        top = ttk.Frame(t); top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Character:").pack(side="left")
        self.lock_char_var = tk.StringVar()
        self.lock_char_combo = ttk.Combobox(top, textvariable=self.lock_char_var,
                                             state="readonly", width=24, values=[])
        self.lock_char_combo.pack(side="left", padx=(4, 0))
        self.lock_char_combo.bind("<<ComboboxSelected>>", lambda e: self._lock_load())

        grid = ttk.Frame(t); grid.pack(fill="x", pady=(4, 0))
        grid.columnconfigure(1, weight=1)
        self.lock_vars = {}
        self.lock_boxes = {}
        self.lock_thumbs = {}
        self._lock_thumb_refs = {}
        for r, btn in enumerate(locks.BUTTONS):
            ttk.Label(grid, text=f"{btn}", font=("Consolas", 10, "bold"), width=4
                      ).grid(row=r, column=0, sticky="w", pady=5)
            ttk.Label(grid, text=locks.BUTTON_LABELS[btn], foreground="#777", width=12
                      ).grid(row=r, column=0, sticky="w", padx=(46, 0), pady=5)
            var = tk.StringVar(value=locks.RANDOM)
            box = ttk.Combobox(grid, textvariable=var, state="readonly", values=[])
            box.grid(row=r, column=1, sticky="ew", padx=(12, 12), pady=5)
            box.bind("<<ComboboxSelected>>", lambda e, b=btn: self._lock_row_changed(b))
            thumb = ttk.Label(grid, anchor="center")
            thumb.grid(row=r, column=2, sticky="e", pady=2)
            self.lock_vars[btn] = var
            self.lock_boxes[btn] = box
            self.lock_thumbs[btn] = thumb

        row = ttk.Frame(t); row.pack(fill="x", pady=(14, 0))
        sv = ttk.Button(row, text="Save locks", command=self.on_save_locks)
        sv.pack(side="left"); self._action_widgets.append(sv)
        cl = ttk.Button(row, text="Clear all (random)", command=self.on_clear_locks)
        cl.pack(side="left", padx=(8, 0)); self._action_widgets.append(cl)
        self.lock_status = ttk.Label(row, text="", foreground="#177245")
        self.lock_status.pack(side="left", padx=(12, 0))

    def _lock_populate_chars(self):
        chars = palettes.char_folders()
        self.lock_char_combo["values"] = chars
        cur = self.lock_char_var.get()
        if cur not in chars:
            self.lock_char_var.set(chars[0] if chars else "")

    def _lock_load(self):
        """Populate the six slots for the selected character from saved locks."""
        char = self.lock_char_var.get()
        files = palettes.list_palettes(char) if char else []
        values = [locks.RANDOM] + files
        saved = locks.char_locks(char) if char else {}
        for btn in locks.BUTTONS:
            self.lock_boxes[btn]["values"] = values
            fn = saved.get(btn)
            self.lock_vars[btn].set(fn if fn in files else locks.RANDOM)
            self._lock_row_changed(btn)
        self.lock_status.config(text="")

    def _lock_row_changed(self, btn):
        char = self.lock_char_var.get()
        fn = self.lock_vars[btn].get()
        lbl = self.lock_thumbs[btn]
        if not char or fn == locks.RANDOM:
            lbl.config(image=""); self._lock_thumb_refs.pop(btn, None)
            return
        try:
            img = ImageTk.PhotoImage(palettes.thumbnail(char, fn, width=150))
            self._lock_thumb_refs[btn] = img
            lbl.config(image=img)
        except Exception:
            lbl.config(image=""); self._lock_thumb_refs.pop(btn, None)
        self.lock_status.config(text="")

    def on_save_locks(self):
        char = self.lock_char_var.get()
        if not char:
            return
        mapping = {btn: (None if self.lock_vars[btn].get() == locks.RANDOM
                         else self.lock_vars[btn].get()) for btn in locks.BUTTONS}
        try:
            locks.set_char_locks(char, mapping)
        except Exception as e:
            messagebox.showerror("Could not save locks", str(e))
            return
        n = sum(1 for v in mapping.values() if v)
        self.lock_status.config(
            text=(f"Saved - {n} slot(s) locked for {char}." if n
                  else f"Saved - all slots random for {char}."))
        self.logln(f"Saved locks for {char}: {n} slot(s) locked.")

    def on_clear_locks(self):
        char = self.lock_char_var.get()
        if not char:
            return
        for btn in locks.BUTTONS:
            self.lock_vars[btn].set(locks.RANDOM)
            self._lock_row_changed(btn)
        try:
            locks.clear_char(char)
        except Exception as e:
            messagebox.showerror("Could not save locks", str(e))
            return
        self.lock_status.config(text=f"Cleared - all slots random for {char}.")
        self.logln(f"Cleared locks for {char}.")

    # --------------------------------------------------------- randomize tab
    def _tab_randomize(self, nb):
        t = ttk.Frame(nb, padding=14); nb.add(t, text="1. Randomize")
        ttk.Label(t, text="Randomize now", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(t, foreground="#555", justify="left", text=(
            "Run a shuffle right now (also happens automatically on launch once\n"
            "auto-randomize is set up).")).pack(anchor="w", pady=(4, 10))

        row = ttk.Frame(t); row.pack(anchor="w", pady=(0, 10))
        ttk.Label(row, text="Fixed seed (optional):").pack(side="left")
        ttk.Entry(row, textvariable=self.seed_var, width=14).pack(side="left", padx=(6, 0))

        rrow = ttk.Frame(t); rrow.pack(anchor="w")
        r = ttk.Button(rrow, text="Randomize now", command=self.on_randomize)
        r.pack(side="left")
        self._action_widgets.append(r)
        v = ttk.Button(rrow, text="View last run log", command=self.on_view_log)
        v.pack(side="left", padx=(8, 0))
        self._action_widgets.append(v)

        ttk.Separator(t, orient="horizontal").pack(fill="x", pady=14)
        ttk.Label(t, text="Reset palettes", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(t, foreground="#555", justify="left", text=(
            "Puts palettes back to vanilla for one character or everyone. Only\n"
            "palettes are changed — any other game mods (stages, etc.) are left alone.")
        ).pack(anchor="w", pady=(4, 8))
        rrow = ttk.Frame(t); rrow.pack(anchor="w")
        ttk.Label(rrow, text="Character:").pack(side="left")
        self.reset_char_var = tk.StringVar(value="All characters")
        self.reset_char_combo = ttk.Combobox(rrow, textvariable=self.reset_char_var,
                                             state="readonly", width=24,
                                             values=["All characters"])
        self.reset_char_combo.pack(side="left", padx=(4, 10))
        rest = ttk.Button(rrow, text="Reset to vanilla", command=self.on_reset)
        rest.pack(side="left")
        self._action_widgets.append(rest)

        ttk.Separator(t, orient="horizontal").pack(fill="x", pady=14)
        ttk.Label(t, text="Protected characters", font=("Segoe UI", 11, "bold")
                  ).pack(anchor="w")
        ttk.Label(t, foreground="#555", justify="left", text=(
            "Characters whose palettes were edited outside this app (e.g. with\n"
            "PalMod) are protected automatically - the randomizer leaves them\n"
            "alone so your work isn't overwritten. Unlock one to randomize over\n"
            "it, or reset it to vanilla above.")
        ).pack(anchor="w", pady=(4, 8))
        self.prot_lbl = ttk.Label(t, text="None detected.", foreground="#555",
                                  wraplength=680, justify="left")
        self.prot_lbl.pack(anchor="w", pady=(0, 6))
        unl = ttk.Button(t, text="Unlock for randomizing...", command=self.on_unprotect)
        unl.pack(anchor="w")
        self._action_widgets.append(unl)

    # ------------------------------------------------------------- status
    def refresh_status(self):
        root = config.game_root()
        arc = config.game_arc(root)
        if root and arc and os.path.exists(arc):
            self.game_lbl.config(text="Game found: " + root, foreground="#177245")
        elif root:
            self.game_lbl.config(text="Folder found but game_50.arc missing: " + root,
                                 foreground="#b00")
        else:
            self.game_lbl.config(text="Game NOT found - use 'Change game folder...'",
                                 foreground="#b00")
        self._populate_chars()
        self._update_palette_counts()
        # Reset dropdown covers the whole roster (a PalMod'd character may have
        # no downloaded palettes yet but still needs reset/unlock).
        try:
            roster = locks._all_folders()
        except Exception:
            roster = self._chars
        self.reset_char_combo["values"] = ["All characters"] + roster
        self._refresh_protected()
        n = config.skins_count()
        if config.has_game() and n:
            self.status_lbl.config(text="Ready. Set up auto-randomize on the Setup tab.",
                                   foreground="#177245")
        else:
            self.status_lbl.config(text="Setup needed - check the Setup and Palettes tabs.",
                                   foreground="#b00")

    def logln(self, msg=""):
        self.log.config(state="normal")
        self.log.insert("end", str(msg) + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    # ------------------------------------------------------------- job runner
    def _set_busy(self, on, label=""):
        self.busy = on
        for w in self._action_widgets:
            w.config(state="disabled" if on else "normal")
        if on:
            self.pbar.pack(side="right"); self.pbar.start(12)
            self.busy_lbl.config(text=label)
        else:
            self.pbar.stop(); self.pbar.pack_forget()
            self.busy_lbl.config(text="")

    def run_job(self, title, fn, on_done=None, **kwargs):
        if self.busy:
            return
        self._job_on_done = on_done
        self.logln("\n" + "=" * 56)
        self.logln(title)
        self.logln("=" * 56)
        self._set_busy(True, title)

        def worker():
            try:
                fn(progress=lambda m: self.q.put(("log", m)),
                   status=lambda m: self.q.put(("status", m)), **kwargs)
                self.q.put(("done", None))
            except Exception as e:
                self.q.put(("log", "\nERROR: " + str(e)))
                self.q.put(("log", traceback.format_exc()))
                self.q.put(("fail", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.logln(payload)
                elif kind == "status":       # live progress next to the spinner
                    msg = payload.split("]", 1)[-1].strip() if "]" in payload else payload
                    self.busy_lbl.config(text=msg or "working...")
                elif kind == "done":
                    self._set_busy(False)
                    self.logln("\nDone.")
                    cb, self._job_on_done = getattr(self, "_job_on_done", None), None
                    if cb:
                        cb()
                    self.refresh_status()
                elif kind == "fail":
                    self._set_busy(False)
                    messagebox.showerror("Something went wrong", payload)
                    self.refresh_status()
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)

    # --------------------------------------------------------------- actions
    def on_set_game(self):
        d = filedialog.askdirectory(
            title="Select your MARVEL vs. CAPCOM Fighting Collection folder")
        if not d:
            return
        arc = config.game_arc(d)
        if not arc or not os.path.exists(arc):
            if not messagebox.askyesno(
                    "Doesn't look right",
                    "That folder doesn't contain the expected game file\n"
                    "(nativeDX11x64\\arc\\pc\\game_50.arc).\n\nUse it anyway?"):
                return
        config.set_game_root(d)
        self.refresh_status()

    def on_enable_steam(self):
        line = steamcfg.launch_option()
        self.root.clipboard_clear()
        self.root.clipboard_append(line)
        self.logln("\n" + steamcfg.instructions())
        messagebox.showinfo("Launch option copied",
                            steamcfg.instructions())

    def on_download(self):
        if not messagebox.askyesno(
                "Download palette gallery",
                "This downloads the full palette gallery from GitHub - roughly 500 MB.\n\n"
                "Only new palettes are added; anything you already have (and your "
                "keep/reject choices) is preserved.\n\nContinue?"):
            return
        self.run_job("Downloading / updating palettes", randomize.download_palettes,
                     on_done=self._on_char_change)

    def on_view_log(self):
        self.logln("\n" + "-" * 56)
        self.logln("Last randomize log:")
        self.logln("-" * 56)
        self.logln(randomize.last_run_text())

    def on_randomize(self):
        if not config.has_game():
            messagebox.showerror("Game not found",
                                 "Set your game folder on the Setup tab first.")
            return
        seed = self.seed_var.get().strip()
        kwargs = {}
        if seed:
            try:
                kwargs["seed"] = int(seed)
            except ValueError:
                messagebox.showerror("Bad seed", "Seed must be a whole number.")
                return
        self.run_job("Randomizing palettes", randomize.randomize,
                     on_done=self._announce_new_protected, **kwargs)

    def _reset_selection(self):
        """None for 'All characters', else the chosen character folder."""
        v = self.reset_char_var.get()
        return None if v == "All characters" else v

    def on_reset(self):
        if not config.has_game():
            messagebox.showerror("Game not found",
                                 "Set your game folder on the Setup tab first.")
            return
        char = self._reset_selection()
        who = char or "every character"
        if not messagebox.askyesno(
                "Reset palettes",
                f"Put {who}'s palettes back to vanilla?\n\n"
                "Only palettes change — other game mods are left alone."):
            return
        self.run_job(f"Resetting {who} to vanilla", randomize.reset_palettes,
                     character=char, on_done=self._refresh_protected)

    def on_unprotect(self):
        if not config.has_game():
            messagebox.showerror("Game not found",
                                 "Set your game folder on the Setup tab first.")
            return
        prot, _ = randomize.protected_chars()
        if not prot:
            messagebox.showinfo("Nothing to unlock", "No characters are protected.")
            return
        char = self._reset_selection()
        if char and char not in prot:
            messagebox.showinfo("Not protected",
                                f"{char} isn't protected - nothing to unlock.")
            return
        who = char or f"all {len(prot)} protected character(s)"
        names = char or ", ".join(prot)
        if not messagebox.askyesno(
                "Unlock for randomizing",
                f"Unlock {who}?\n\n({names})\n\n"
                "The next randomize will OVERWRITE their custom palettes.\n"
                "If you want to keep that work, back it up first (e.g. save\n"
                "your PalMod project)."):
            return
        self.run_job(f"Unlocking {who}", randomize.unprotect,
                     character=char, on_done=self._refresh_protected)

    def _refresh_protected(self):
        prot, _ = randomize.protected_chars()
        self.prot_lbl.config(
            text=("Protected: " + ", ".join(sorted(prot)) if prot else "None detected."),
            foreground="#8a5a00" if prot else "#555")

    def _announce_new_protected(self):
        """After a randomize, pop a dialog for protections found on that run."""
        self._refresh_protected()
        _, fresh = randomize.protected_chars()
        if not fresh:
            return
        randomize.mark_protected_notified()
        messagebox.showinfo(
            "Existing palette edits found",
            "These characters' palettes were edited outside this app (e.g.\n"
            "with PalMod), so they were NOT randomized:\n\n"
            f"  {', '.join(sorted(fresh))}\n\n"
            "Your work is protected. To include a character again, use the\n"
            "Randomize tab: reset it to vanilla, or unlock it to let the\n"
            "randomizer overwrite it.")


# --------------------------------------------------------------- entry point
def _launch_mode(cmd):
    """Steam invoked us with the game command as args: randomize, then launch."""
    import subprocess
    try:
        randomize.randomize(progress=None)      # silent; never block the game
    except Exception:
        pass
    try:
        # Explicit null stdio: we run windowed (no console), and a console
        # child inheriting our invalid handles can hang waiting on stdin.
        return subprocess.call(cmd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    except Exception:
        return 0


def main():
    if len(sys.argv) > 1:                        # launcher mode (Steam)
        return _launch_mode(sys.argv[1:])
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
