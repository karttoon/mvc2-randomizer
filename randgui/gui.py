#!/usr/bin/env python3
r"""gui.py - MvC2 Randomizer, graphical front-end (Tkinter, stdlib only).

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
from tkinter import ttk, filedialog, messagebox, scrolledtext, simpledialog

from PIL import Image as PILImage, ImageDraw, ImageTk

import config
import randomize
import steamcfg
import palettes
import locks
import stagegal
import presets

APP_TITLE = "MvC2 Randomizer"


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.busy = False
        self._action_widgets = []
        self._slot_fits = None          # {slot: {port key,...}} lock-fit map (lazy)

        root.title(APP_TITLE)
        # Open large and centered so every tab's content is visible without
        # resizing; tall tabs also scroll (see _scroll_body).
        self._center_window(1180, 900)
        root.minsize(900, 620)
        try:
            root.call("tk", "scaling", 1.2)
        except tk.TclError:
            pass

        self.seed_var = tk.StringVar()

        # Relocate pre-1.0.3 root state files and ensure dirs BEFORE any tab
        # reads verdicts/config from the new data/ folder.
        config.migrate_state()
        config.ensure_custom_dirs()

        self._build_header()
        self._build_tabs()
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
        titlerow = ttk.Frame(top); titlerow.pack(fill="x")
        ttk.Label(titlerow, text="Marvel vs. Capcom 2 - Randomizer",
                  font=("Segoe UI", 15, "bold")).pack(side="left")
        ttk.Label(titlerow, text=f"v{config.VERSION}", foreground="#999",
                  font=("Segoe UI", 9)).pack(side="right", pady=(6, 0))
        ttk.Label(top, text="Randomizes your curated palettes and stages every time you launch the game.",
                  foreground="#555").pack(anchor="w")
        self.status_lbl = ttk.Label(top, text="", font=("Segoe UI", 9))
        self.status_lbl.pack(anchor="w", pady=(6, 0))

    def _build_tabs(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=14, pady=(6, 4))
        self.nb = nb
        self._tab_randomize(nb)
        self._tab_palettes(nb)
        self._tab_stages(nb)
        self._tab_locks(nb)
        self._tab_logs(nb)
        self._tab_setup(nb)
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _tab_logs(self, nb):
        t = ttk.Frame(nb, padding=(10, 8)); nb.add(t, text="5. Logs")
        self.log = scrolledtext.ScrolledText(t, wrap="word", state="disabled",
                                             font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)

    def _center_window(self, w, h):
        """Size the window to (w, h) - clamped to the screen - and center it."""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = min(w, sw - 80)
        h = min(h, sh - 120)
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 3)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _scroll_body(self, parent):
        """Wrap a tab in a vertical scroll region and return the inner frame to
        fill. Keeps tall tabs (Setup, Lock Selections) fully reachable at any
        window size."""
        canvas = tk.Canvas(parent, highlightthickness=0, borderwidth=0)
        vsb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.bind("<Enter>", lambda e: canvas.bind_all(
            "<MouseWheel>", lambda ev: canvas.yview_scroll(
                int(-ev.delta / 120), "units")))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        return inner

    def _build_statusbar(self):
        bar = ttk.Frame(self.root, padding=(14, 2, 14, 8))
        bar.pack(fill="x")
        self.busy_lbl = ttk.Label(bar, text="", foreground="#555")
        self.busy_lbl.pack(side="right", padx=(0, 10))
        self.pbar = ttk.Progressbar(bar, mode="indeterminate", length=160)
        # only shown while a job is running (a stopped indeterminate bar shows a
        # stray idle block, which looks like it's stuck ~1/10 full)

    # ------------------------------------------------------------- setup tab
    SETUP_WRAP = 900

    def _tab_setup(self, nb):
        outer = ttk.Frame(nb); nb.add(outer, text="6. Setup")
        t = ttk.Frame(self._scroll_body(outer), padding=14)
        t.pack(fill="both", expand=True)
        ttk.Label(t, text="Game install", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(t, justify="left", foreground="#555", wraplength=self.SETUP_WRAP,
                  text="Your Steam install is detected automatically. If the "
                       "game is on another drive or isn't found, use "
                       "“Change game folder…” to point at it."
                  ).pack(anchor="w", pady=(2, 6))

        self.game_lbl = ttk.Label(t, text="", foreground="#333",
                                  wraplength=self.SETUP_WRAP, justify="left")
        self.game_lbl.pack(anchor="w", pady=(0, 2))
        ch = ttk.Button(t, text="Change game folder...", command=self.on_set_game)
        ch.pack(anchor="w", pady=(0, 4))
        self._action_widgets.append(ch)

        ttk.Separator(t, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(t, text="Auto-randomize on launch", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(t, foreground="#555", justify="left", wraplength=self.SETUP_WRAP,
                  text="A one-time setup: this copies a short line to your "
                       "clipboard and shows where to paste it in Steam. After "
                       "that, your palettes are randomized automatically every "
                       "time you launch the game."
                  ).pack(anchor="w", pady=(2, 6))
        b = ttk.Button(t, text="Enable Auto-Randomize in Steam",
                       command=self.on_enable_steam)
        b.pack(anchor="w")
        self._action_widgets.append(b)

        ttk.Separator(t, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(t, text="Randomization scope", font=("Segoe UI", 11, "bold")
                  ).pack(anchor="w")
        ttk.Label(t, foreground="#555", justify="left", wraplength=self.SETUP_WRAP,
                  text="By default the randomizer uses both the items you've "
                       "kept and any you haven't reviewed yet. Uncheck this to "
                       "randomize using ONLY your kept selections (palettes and "
                       "stages you never reviewed are left out; a character or "
                       "slot with nothing kept keeps its default)."
                  ).pack(anchor="w", pady=(2, 6))
        self.incl_unrev_var = tk.BooleanVar(
            value=bool(randomize.get_config().get("include_unreviewed", True)))
        ttk.Checkbutton(t, text="Include unreviewed items when randomizing",
                        variable=self.incl_unrev_var,
                        command=self._on_incl_unrev_toggle).pack(anchor="w")

        ttk.Separator(t, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(t, text="Cross-slot stages", font=("Segoe UI", 11, "bold")
                  ).pack(anchor="w")
        ttk.Label(t, foreground="#555", justify="left", wraplength=self.SETUP_WRAP,
                  text="Normally ported/custom stages can only appear in the "
                       "Training slot. Turn this on to merge them into every "
                       "stage slot too, so any port can show up anywhere. Merges "
                       "happen automatically at launch (a few seconds the first "
                       "time, instant after that). Needs stages enabled above."
                  ).pack(anchor="w", pady=(2, 6))
        self.ports_var = tk.BooleanVar(
            value=bool(randomize.get_config().get("distribute_ports", False)))
        ttk.Checkbutton(t, text="Distribute ported stages across all slots",
                        variable=self.ports_var,
                        command=self._on_ports_toggle).pack(anchor="w")

        ttk.Separator(t, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(t, text="Gallery content", font=("Segoe UI", 11, "bold")
                  ).pack(anchor="w")
        ttk.Label(t, foreground="#555", justify="left", wraplength=self.SETUP_WRAP,
                  text="Downloads and updates everything from the community "
                       "gallery: palettes, stages, previews, and curated "
                       "mixes. Safe to run any time - only new or updated "
                       "files are fetched, and your reviews and drop-ins are "
                       "preserved."
                  ).pack(anchor="w", pady=(2, 6))
        grow = ttk.Frame(t); grow.pack(anchor="w")
        gd = ttk.Button(grow, text="Download / Update gallery content",
                        command=self.on_download)
        gd.pack(side="left")
        self._action_widgets.append(gd)
        rf = ttk.Button(grow, text="Rescan local files", command=self.on_rescan)
        rf.pack(side="left", padx=(8, 0))
        self._action_widgets.append(rf)

        ttk.Separator(t, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(t, text="Curated mixes", font=("Segoe UI", 11, "bold")
                  ).pack(anchor="w")
        ttk.Label(t, foreground="#555", justify="left", wraplength=self.SETUP_WRAP,
                  text="Start from someone else's curation instead of reviewing "
                       "thousands of palettes yourself. \"Fresh start\" adopts "
                       "the whole mix; \"fill gaps\" only judges what you "
                       "haven't. Your own drop-ins are never touched, and you "
                       "can export your curation to share."
                  ).pack(anchor="w", pady=(2, 6))
        prow = ttk.Frame(t); prow.pack(anchor="w")
        self.preset_var = tk.StringVar()
        self.preset_combo = ttk.Combobox(prow, textvariable=self.preset_var,
                                         state="readonly", width=34, values=[])
        self.preset_combo.pack(side="left")
        self.preset_combo.bind("<<ComboboxSelected>>",
                               lambda e: self._preset_info())
        pa = ttk.Button(prow, text="Apply (fresh start)",
                        command=lambda: self.on_apply_preset("replace"))
        pa.pack(side="left", padx=(8, 0)); self._action_widgets.append(pa)
        pf = ttk.Button(prow, text="Apply (fill gaps)",
                        command=lambda: self.on_apply_preset("fill"))
        pf.pack(side="left", padx=(6, 0)); self._action_widgets.append(pf)
        pe = ttk.Button(prow, text="Export mine...",
                        command=self.on_export_preset)
        pe.pack(side="left", padx=(6, 0)); self._action_widgets.append(pe)
        self.preset_lbl = ttk.Label(t, text="", foreground="#666",
                                    wraplength=self.SETUP_WRAP)
        self.preset_lbl.pack(anchor="w", pady=(4, 0))
        self._presets = []
        self._refresh_presets()

        ttk.Separator(t, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(t, text="Danger zone", font=("Segoe UI", 11, "bold"),
                  foreground="#b00").pack(anchor="w")
        ttk.Label(t, foreground="#555", justify="left", wraplength=self.SETUP_WRAP,
                  text="Rejected palettes normally just stay hidden and out of "
                       "the pool. This permanently deletes their files from "
                       "disk - there is NO way to recover them short of "
                       "re-downloading the gallery."
                  ).pack(anchor="w", pady=(2, 6))
        dz = ttk.Button(t, text="Permanently delete rejected palette files...",
                        command=self.on_remove_rejected)
        dz.pack(anchor="w")
        self._action_widgets.append(dz)

    # ---------------------------------------------------------- palettes tab
    GRID_COLS = 5
    IMG_WIDTH = 600
    KEEP_COLOR = "#2f8f2f"        # kept: green
    REJECT_COLOR = "#c53a3a"      # rejected: red
    NEUTRAL_COLOR = "#c9a227"     # unreviewed/new: muted gold
    SELECT_COLOR = "#2b6cb0"      # grid: palettes picked for comparison

    def _tab_palettes(self, nb):
        t = ttk.Frame(nb, padding=10); nb.add(t, text="2. Palette Gallery")

        top = ttk.Frame(t); top.pack(fill="x")
        ttk.Label(top, text="Character:").pack(side="left")
        self.char_var = tk.StringVar()
        self.char_combo = ttk.Combobox(top, textvariable=self.char_var, state="readonly",
                                       width=24, values=[])
        self.char_combo.pack(side="left", padx=(4, 12))
        self.char_combo.bind("<<ComboboxSelected>>", lambda e: self._on_char_change())
        self.view_var = tk.StringVar(value="grid")
        ttk.Radiobutton(top, text="Image", value="image", variable=self.view_var,
                        command=self._show_view).pack(side="left")
        ttk.Radiobutton(top, text="Grid", value="grid", variable=self.view_var,
                        command=self._show_view).pack(side="left", padx=(4, 0))
        self.sortcol_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Sort by color", variable=self.sortcol_var,
                        command=self._on_char_change).pack(side="left", padx=(10, 0))
        self.unrev_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Review new", variable=self.unrev_var,
                        command=self._on_unrev_toggle).pack(side="left", padx=(12, 0))
        self.hiderej_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Hide rejected", variable=self.hiderej_var,
                        command=self._on_char_change).pack(side="left", padx=(8, 0))
        self.cmp_btn = ttk.Button(top, text="Compare selected...", command=self.on_compare)
        self.cmp_btn.pack(side="left", padx=(12, 0))
        self._action_widgets.append(self.cmp_btn)
        pv_btn = ttk.Button(top, text="◀ Prev", width=8,
                            command=lambda: self._jump_unreviewed(-1))
        pv_btn.pack(side="left", padx=(6, 0))
        self._action_widgets.append(pv_btn)
        nx_btn = ttk.Button(top, text="Next ▶", width=8,
                            command=lambda: self._jump_unreviewed(1))
        nx_btn.pack(side="left", padx=(2, 0))
        self._action_widgets.append(nx_btn)
        rf_btn = ttk.Button(top, text="↻ Rescan", command=self.on_rescan)
        rf_btn.pack(side="right"); self._action_widgets.append(rf_btn)
        self.palettes_lbl = ttk.Label(t, text="", foreground="#555")
        self.palettes_lbl.pack(anchor="w", pady=(6, 6))

        # Rejected palettes stay on disk (use "Hide rejected" to tidy the
        # view); permanent deletion lives in the Setup tab's danger zone.
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
        # selectmode extended: Ctrl/Shift-click builds a set for "Compare selected"
        self.file_list = tk.Listbox(left, width=32, activestyle="dotbox",
                                    exportselection=False, font=("Consolas", 9),
                                    selectmode="extended")
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
        self.file_list.bind("<Return>", lambda e: self.on_compare())
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
        if text.endswith("Palette Gallery"):
            self.file_list.focus_set()
            self.root.after(80, self._render_image)   # pane is now sized
        elif text.endswith("Lock Selections"):
            self._lock_populate_chars()
            self._lock_load()
            self._slock_populate()
        elif text.endswith("Stage Gallery"):
            self._load_stage_gallery()
            self.stg_canvas.focus_set()

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
        bar = ttk.Frame(self.grid_view)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Label(bar, text="Thumbnail size:").pack(side="left")
        # Discrete slider: one tick per row-count (10 per row ... 1 per row);
        # thumbnails always stretch so every row spans the full width.
        self.grid_cols_var = tk.IntVar(value=3)
        self._colvals = list(range(10, 0, -1))    # left = small, right = big
        self.col_slider = tk.Canvas(bar, width=220, height=24,
                                    highlightthickness=0, cursor="hand2")
        try:
            self.col_slider.configure(bg=ttk.Style().lookup("TFrame", "background")
                                      or "#f0f0f0")
        except tk.TclError:
            pass
        self.col_slider.pack(side="left", padx=(6, 8))
        self.col_slider.bind("<Button-1>", self._col_slider_pick)
        self.col_slider.bind("<B1-Motion>", self._col_slider_pick)
        self.grid_size_lbl = ttk.Label(bar, text="3 per row", foreground="#777")
        self.grid_size_lbl.pack(side="left")
        self._draw_col_slider()
        # Y/N review of the focused thumbnail (Prev/Next up top moves the focus)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=(12, 12))
        self.grid_keep_btn = ttk.Button(bar, text="Keep (Y)", width=10,
                                        command=lambda: self._set_verdict(palettes.KEEP))
        self.grid_keep_btn.pack(side="left")
        self.grid_clear_btn = ttk.Button(bar, text="Clear (C)", width=10,
                                         command=self._clear_verdict)
        self.grid_clear_btn.pack(side="left", padx=(4, 0))
        self.grid_reject_btn = ttk.Button(bar, text="Reject (N)", width=10,
                                          command=lambda: self._set_verdict(palettes.REJECT))
        self.grid_reject_btn.pack(side="left", padx=(4, 0))
        self.grid_status_lbl = ttk.Label(bar, text="", font=("Segoe UI", 9, "bold"))
        self.grid_status_lbl.pack(side="left", padx=(12, 0))
        self._grid_cur = None
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
        # Y/N/C fast review of the focused thumbnail; arrows move the focus
        self.gal_canvas.configure(takefocus=1)
        self.gal_canvas.bind("<Button-1>", lambda e: self.gal_canvas.focus_set())
        for k in ("y", "Y"):
            self.gal_canvas.bind(f"<{k}>", lambda e: self._set_verdict(palettes.KEEP))
        for k in ("n", "N"):
            self.gal_canvas.bind(f"<{k}>", lambda e: self._set_verdict(palettes.REJECT))
        for k in ("c", "C"):
            self.gal_canvas.bind(f"<{k}>", lambda e: self._clear_verdict())
        self.gal_canvas.bind("<Left>", lambda e: (self._jump_grid(-1), "break")[1])
        self.gal_canvas.bind("<Right>", lambda e: (self._jump_grid(1), "break")[1])
        self._thumb_refs = []
        self._grid_w = 0

    def _on_wheel(self, e):
        self.gal_canvas.yview_scroll(int(-e.delta / 120), "units")

    def _draw_col_slider(self):
        c = self.col_slider
        c.delete("all")
        w, y, pad = 220, 12, 12
        n = len(self._colvals)
        self._col_xs = [pad + i * (w - 2 * pad) / (n - 1) for i in range(n)]
        c.create_line(pad, y, w - pad, y, fill="#999")
        for x in self._col_xs:
            c.create_line(x, y - 5, x, y + 5, fill="#888")
        x = self._col_xs[self._colvals.index(self.grid_cols_var.get())]
        c.create_oval(x - 6, y - 6, x + 6, y + 6,
                      fill="#4a76b0", outline="#2b567f", width=2)

    def _col_slider_pick(self, e):
        """Click/drag: snap to the nearest tick (row count)."""
        i = min(range(len(self._col_xs)), key=lambda j: abs(self._col_xs[j] - e.x))
        v = self._colvals[i]
        if v == self.grid_cols_var.get():
            return
        self.grid_cols_var.set(v)
        self.grid_size_lbl.config(text=f"{v} per row")
        self._draw_col_slider()
        job = getattr(self, "_grid_size_job", None)
        if job:
            self.root.after_cancel(job)
        self._grid_size_job = self.root.after(150, self._load_grid)

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
            self.gal_canvas.focus_set()      # so Y/N/C keys act on the focused thumb
        else:
            self.image_view.pack(fill="both", expand=True)
            self.file_list.focus_set()
        self._update_compare_btn()

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
                  if total else "No palettes yet - run Download / Update on the Setup tab."))

    # ---- image-review view ----
    def _on_unrev_toggle(self):
        """'Review new': jump straight to the first unreviewed palette. The
        view is never filtered - neighbors stay visible for comparing."""
        if self.unrev_var.get():
            self._last_new_jump = None
            self._jump_unreviewed(1)

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
        self.pal_files = palettes.list_palettes(char) if char else []
        if char and self.hiderej_var.get():
            self.pal_files = [f for f in self.pal_files
                              if verdicts.get(palettes.key_for(char, f))
                              != palettes.REJECT]
        if char and self.sortcol_var.get():
            self.pal_files = palettes.sort_similarity(char, self.pal_files)
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

    def _nav_char(self, direction):
        """Move to the prev/next character that has palettes, landing on the
        first (forward) or last (back) palette."""
        if not self._chars:
            return
        idx = self._chars.index(self._cur_char) if self._cur_char in self._chars else 0
        n = len(self._chars)
        for step in range(1, n + 1):
            c = self._chars[(idx + direction * step) % n]
            if palettes.list_palettes(c):
                self._load_char(c, land=0 if direction > 0 else -1)
                return

    def _cur_index(self):
        """Index of the palette shown in the preview. With multi-select, the
        'active' item (the one last clicked / keyboard cursor) is previewed."""
        if not self.pal_files:
            return None
        try:
            i = int(self.file_list.index("active"))
        except (tk.TclError, ValueError):
            i = -1
        if 0 <= i < len(self.pal_files):
            return i
        sel = self.file_list.curselection()
        return sel[0] if sel else 0

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
            self.cur_status_lbl.config(text="No palettes for this character.",
                                       foreground="#888")
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
        if fn.startswith("custom/"):
            ok, why = palettes.validate_custom(char, fn)
            if not ok:
                self.cur_status_lbl.config(
                    text=f"INVALID (never randomized) - {why}",
                    foreground=self.REJECT_COLOR)
        self.keep_btn.config(text=("✓ Kept" if v == palettes.KEEP else "Keep (Y)"))
        self.reject_btn.config(text=("✓ Rejected" if v == palettes.REJECT else "Reject (N)"))
        self.clear_btn.config(text="Clear (C)")

    def _clear_verdict(self):
        char = self._cur_char
        if not char or not self.pal_files:
            return "break"
        if self.view_var.get() == "grid":
            targets = self._grid_targets()
            if not targets:
                return "break"
            v = palettes.load_verdicts()
            for fn in targets:
                key = palettes.key_for(char, fn)
                if key in v:
                    del v[key]
                    self._unrev[char] = self._unrev.get(char, 0) + 1
            palettes.save_verdicts(v)
            self._rebuild_char_values()
            self._update_palette_counts()
            self._grid_sel = []           # cleared: drop the selection badges
            for fn in targets:
                self._grid_update_cell(fn, None)
            self._grid_cur = targets[-1]
            self._grid_restyle(targets[-1])
            self._grid_show_status()
            self.gal_canvas.focus_set()
            return "break"
        i = self._cur_index()
        if i is None:
            return "break"
        fn = self.pal_files[i]
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

    # ---- comparison view ----
    def on_compare(self):
        """Open a side-by-side comparison of the selected palettes; the user
        clicks the keepers, and the rest are rejected."""
        files, first_idx = self._compare_selection()
        if len(files) < 2:
            messagebox.showinfo(
                "Compare palettes",
                "Select two or more palettes first.\n\n"
                "List: Ctrl-click to add one, Shift-click for a range.\n"
                "Grid: Ctrl-click thumbnails to toggle them (blue border).")
            return "break"
        char = self._cur_char

        dlg = tk.Toplevel(self.root)
        dlg.title(f"Compare {len(files)} palettes - {char}")
        dlg.transient(self.root)
        dlg.grab_set()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        W, H = int(sw * 0.85), int(sh * 0.85)
        dlg.geometry(f"{W}x{H}+{(sw - W) // 2}+{(sh - H) // 2}")

        ttk.Label(dlg, padding=(10, 8, 10, 0), foreground="#555",
                  text="Click every palette you want to KEEP (click again to "
                       "unpick) - the rest will be rejected.").pack(anchor="w")

        # Bottom buttons (created first so the grid callbacks can reference them)
        btnrow = ttk.Frame(dlg, padding=10)
        btnrow.pack(side="bottom", fill="x")
        picked = set()

        def close():
            canvas.unbind_all("<MouseWheel>")
            dlg.destroy()

        def apply():
            if not picked:
                return
            close()
            winners = [f for f in files if f in picked]
            losers = [f for f in files if f not in picked]
            self._apply_compare(char, winners, losers, first_idx)

        keep_btn = ttk.Button(btnrow, text="Keep... (click palettes above first)",
                              state="disabled", command=apply)
        keep_btn.pack(side="right")
        ttk.Button(btnrow, text="Cancel", command=close).pack(side="right", padx=(0, 8))
        dlg.protocol("WM_DELETE_WINDOW", close)
        dlg.bind("<Escape>", lambda e: close())

        # Scrollable thumbnail grid
        body = ttk.Frame(dlg)
        body.pack(fill="both", expand=True, padx=10, pady=6)
        canvas = tk.Canvas(body, highlightthickness=0, background="#2d2d2d")
        vsb = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, background="#2d2d2d")
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Enter>", lambda e: canvas.bind_all(
            "<MouseWheel>", lambda ev: canvas.yview_scroll(int(-ev.delta / 120), "units")))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        cols = 2 if len(files) <= 4 else (3 if len(files) <= 12 else 4)
        tw = max(180, (W - 80) // cols - 24)
        cells = {}

        def refresh_btn():
            n = len(picked)
            if n:
                keep_btn.config(state="normal",
                                text=f"Keep {n} - reject the other {len(files) - n}")
            else:
                keep_btn.config(state="disabled",
                                text="Keep... (click palettes above first)")

        def choose(fn):
            if fn in picked:
                picked.discard(fn)
            else:
                picked.add(fn)
            cell, cap = cells[fn]
            on = fn in picked
            color = self.KEEP_COLOR if on else "#555555"
            cell.config(highlightbackground=color, highlightcolor=color)
            cap.config(foreground="#7fdc7f" if on else "#cccccc",
                       text=(f"KEEP   {fn}" if on else fn),
                       font=("Consolas", 8, "bold" if on else "normal"))
            refresh_btn()

        imgs = []
        for i, fn in enumerate(files):
            cell = tk.Frame(inner, highlightthickness=4, background="#2d2d2d",
                            highlightbackground="#555555", highlightcolor="#555555")
            cell.grid(row=i // cols, column=i % cols, padx=8, pady=8)
            try:
                img = ImageTk.PhotoImage(palettes.thumbnail(char, fn, width=tw))
            except Exception:
                cell.destroy()
                continue
            imgs.append(img)
            pic = tk.Label(cell, image=img, bd=0)
            pic.pack()
            cap = tk.Label(cell, text=fn, font=("Consolas", 8),
                           background="#2d2d2d", foreground="#cccccc")
            cap.pack(fill="x")
            for w in (cell, pic, cap):
                w.bind("<Button-1>", lambda e, f=fn: choose(f))
            cells[fn] = (cell, cap)
        dlg._imgs = imgs          # keep PhotoImage refs alive
        return "break"

    def _apply_compare(self, char, winners, losers, first_idx):
        """Record the comparison outcome: winners kept, losers rejected."""
        v = palettes.load_verdicts()
        for fn, verdict in ([(f, palettes.KEEP) for f in winners]
                            + [(f, palettes.REJECT) for f in losers]):
            key = palettes.key_for(char, fn)
            if v.get(key) is None and self._unrev.get(char):
                self._unrev[char] -= 1
            v[key] = verdict
        palettes.save_verdicts(v)
        self._rebuild_char_values()
        self.logln(f"Compare: kept {len(winners)} ({', '.join(winners)}), "
                   f"rejected {len(losers)} other(s).")
        self._load_char(char, land=first_idx)
        self.file_list.focus_set()

    def _grid_targets(self):
        """Palettes a grid Y/N/C decision applies to: the selection if any, else
        the review cursor."""
        if self._grid_sel:
            return [f for f in self._grid_sel if f in self.pal_files]
        cur = getattr(self, "_grid_cur", None)
        return [cur] if cur in self.pal_files else []

    def _set_verdict(self, verdict):
        char = self._cur_char
        if not char or not self.pal_files:
            return "break"
        if self.view_var.get() == "grid":
            targets = self._grid_targets()
            if not targets:
                return "break"
            v = palettes.load_verdicts()
            for fn in targets:
                key = palettes.key_for(char, fn)
                if v.get(key) is None and self._unrev.get(char):
                    self._unrev[char] -= 1
                v[key] = verdict
            palettes.save_verdicts(v)
            self._rebuild_char_values()
            self._update_palette_counts()
            self._grid_sel = []           # decided: drop the selection badges
            for fn in targets:
                self._grid_update_cell(fn, verdict)
            self._grid_cur = targets[-1]
            self._grid_advance()          # step to the next unreviewed
            self.gal_canvas.focus_set()
            return "break"
        i = self._cur_index()
        if i is None:
            return "break"
        fn = self.pal_files[i]
        v = palettes.load_verdicts()
        was_unreviewed = v.get(palettes.key_for(char, fn)) is None
        v[palettes.key_for(char, fn)] = verdict
        palettes.save_verdicts(v)
        if was_unreviewed and self._unrev.get(char):
            self._unrev[char] -= 1
            self._rebuild_char_values()
        self._update_palette_counts()
        if i < len(self.pal_files) - 1:
            self._nav(1)                  # auto-advance for fast review
        else:
            self._show_current()
        self.file_list.focus_set()
        return "break"

    # ---- grid (overview + click-to-select for comparison) ----
    def _load_grid(self):
        if not hasattr(self, "gal_inner"):
            return
        for w in self.gal_inner.winfo_children():
            w.destroy()
        self._thumb_refs = []
        self._grid_sel = []           # filenames selected (checkmark badge), in click order
        self._grid_cells = {}         # filename -> (cell, pic label, base pil, PhotoImage, verdict color)
        self._grid_imgs = {}          # filename -> current badged PhotoImage (GC ref)
        char = self._cur_char
        if char:
            cw = max(self.gal_canvas.winfo_width(), 320)
            self._grid_w = cw
            cols = self.grid_cols_var.get()             # from the tick slider
            tw = max(60, (cw - (cols + 1) * 10) // cols)   # fill the full row
            verdicts = palettes.load_verdicts()
            # pal_files carries the active ordering (alphabetical or color-sorted)
            for i, fn in enumerate(self.pal_files):
                v = verdicts.get(palettes.key_for(char, fn))
                color = (self.REJECT_COLOR if v == palettes.REJECT
                         else self.KEEP_COLOR if v == palettes.KEEP else self.NEUTRAL_COLOR)
                cell = tk.Frame(self.gal_inner, highlightthickness=3,
                                highlightbackground=color, highlightcolor=color)
                cell.grid(row=i // cols, column=i % cols, padx=4, pady=4)
                try:
                    pil = palettes.thumbnail(char, fn, width=tw)
                    img = ImageTk.PhotoImage(pil)
                except Exception:
                    cell.destroy(); continue
                self._thumb_refs.append(img)
                pic = tk.Label(cell, image=img, bd=0)
                pic.pack()
                # left-click toggles this thumbnail in the selection (blue border)
                for w in (cell, pic):
                    w.bind("<Button-1>", lambda e, f=fn: self._grid_click(f))
                self._grid_cells[fn] = (cell, pic, pil, img, color)
        self.gal_canvas.yview_moveto(0)
        # nothing is auto-selected on open; keep the review cursor only if its
        # palette is still shown
        if self._grid_cur not in self._grid_cells:
            self._grid_cur = None
        for fn in self._grid_cells:
            self._grid_paint(fn)          # draw verdict badge + border
        self._grid_show_status()
        self._update_compare_btn()

    FOCUS_COLOR = "#ffffff"      # review-cursor ring (no colour tint over the art)

    def _badge(self, draw, cx, cy, r, fill, glyph):
        """Draw a small filled circle badge with a white glyph (check / x / dot)."""
        lw = max(2, r // 4)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill,
                     outline="white", width=lw)
        if glyph == "check":
            draw.line([(cx - r // 2, cy), (cx - r // 6, cy + r // 2),
                       (cx + r // 2, cy - r // 2)], fill="white", width=lw)
        elif glyph == "x":
            draw.line([(cx - r // 2, cy - r // 2), (cx + r // 2, cy + r // 2)],
                      fill="white", width=lw)
            draw.line([(cx - r // 2, cy + r // 2), (cx + r // 2, cy - r // 2)],
                      fill="white", width=lw)
        # "dot" = plain circle (unreviewed / new)

    def _grid_render(self, fn):
        """Thumbnail with just a blue check badge (top-right) when selected - no
        tint, nothing over the art otherwise. Verdict shows via the cell border."""
        base = self._grid_cells[fn][2]
        if fn not in self._grid_sel:
            return ImageTk.PhotoImage(base.convert("RGB"))
        im = base.convert("RGB").copy()
        d = ImageDraw.Draw(im)
        r = max(8, min(im.size) // 10)
        m = r + 5
        self._badge(d, im.width - m, m, r, (43, 108, 176), "check")   # selected only
        return ImageTk.PhotoImage(im)

    def _grid_paint(self, fn):
        """Re-render a cell's badges (verdict + selection) and its border."""
        if fn not in self._grid_cells:
            return
        img = self._grid_render(fn)
        self._grid_imgs[fn] = img                                 # keep a GC ref
        self._grid_cells[fn][1].config(image=img)
        self._grid_restyle(fn)

    def _grid_restyle(self, fn):
        """Cell border: a white ring when it's the review cursor, otherwise the
        verdict colour. (Selection is shown by the checkmark badge, not a border.)"""
        if fn not in self._grid_cells:
            return
        cell, _pic, _pil, _img, vcolor = self._grid_cells[fn]
        if fn == getattr(self, "_grid_cur", None):
            cell.config(highlightbackground=self.FOCUS_COLOR,
                        highlightcolor=self.FOCUS_COLOR, highlightthickness=5)
        else:
            cell.config(highlightbackground=vcolor, highlightcolor=vcolor,
                        highlightthickness=3)

    def _grid_click(self, fn):
        """Left-click: toggle this thumbnail in the selection (for Y/N or Compare)."""
        self.gal_canvas.focus_set()      # so Y/N/C keys work
        self._grid_toggle(fn)

    def _grid_toggle(self, fn):
        """Add/remove a thumbnail from the selection - shown by a blue checkmark
        badge (no tint, no scroll, so the view never jumps). Y/N act on the
        selection, and it also feeds the Compare button."""
        if fn in self._grid_sel:
            self._grid_sel.remove(fn)
        else:
            self._grid_sel.append(fn)
        self._grid_paint(fn)
        self._grid_show_status()
        self._update_compare_btn()

    def _grid_focus(self, fn, scroll=True):
        """Move the review cursor to `fn`, centre it, and SELECT it (a blue check
        badge, no tint) so Y/N can decide it without comparing. Replaces any
        existing selection - Prev/Next/arrows are single-palette review; use
        click to build a multi-selection."""
        prev_cur = getattr(self, "_grid_cur", None)
        prev_sel = list(self._grid_sel)
        self._grid_cur = fn
        self._grid_sel = [fn]
        changed = set(prev_sel) | {fn}
        if prev_cur:
            changed.add(prev_cur)
        for f in changed:
            self._grid_paint(f)          # repaint selection badge + cursor border
        if scroll and fn in self._grid_cells:
            self._grid_scroll_to(fn)
        self._grid_show_status()
        self._update_compare_btn()

    def _grid_show_status(self):
        char = self._cur_char
        n = len(self._grid_sel)
        if n > 1:
            self.grid_status_lbl.config(
                text=f"{n} selected  ·  Y = keep   N = reject", foreground="#333")
            return
        fn = self._grid_sel[0] if n == 1 else getattr(self, "_grid_cur", None)
        if not fn or fn not in self.pal_files:
            self.grid_status_lbl.config(text="")
            return
        i = self.pal_files.index(fn)
        v = palettes.load_verdicts().get(palettes.key_for(char, fn))
        text, color = (("KEPT", self.KEEP_COLOR) if v == palettes.KEEP else
                       ("REJECTED", self.REJECT_COLOR) if v == palettes.REJECT else
                       ("not reviewed", self.NEUTRAL_COLOR))
        self.grid_status_lbl.config(
            text=f"{i + 1}/{len(self.pal_files)}  ·  {text}", foreground=color)

    def _grid_update_cell(self, fn, verdict):
        """Record a cell's new verdict colour after a decision, then repaint its
        badge + border."""
        if fn not in self._grid_cells:
            return
        color = (self.REJECT_COLOR if verdict == palettes.REJECT
                 else self.KEEP_COLOR if verdict == palettes.KEEP
                 else self.NEUTRAL_COLOR)
        cell, pic, pil, img, _old = self._grid_cells[fn]
        self._grid_cells[fn] = (cell, pic, pil, img, color)
        self._grid_paint(fn)

    def _jump_grid(self, direction):
        """Arrow keys in grid: move the focus to the adjacent palette (any state)."""
        if not self.pal_files:
            return
        cur = getattr(self, "_grid_cur", None)
        i = self.pal_files.index(cur) if cur in self.pal_files else 0
        j = max(0, min(len(self.pal_files) - 1, i + direction))
        self._grid_focus(self.pal_files[j])

    def _grid_advance(self):
        """After a Y/N decision, select the next unreviewed palette - within this
        character, then crossing into the next character that has any."""
        char = self._cur_char
        if not self.pal_files:
            return
        verdicts = palettes.load_verdicts()
        cur = getattr(self, "_grid_cur", None)
        start = (self.pal_files.index(cur) + 1) if cur in self.pal_files else 0
        for off in range(len(self.pal_files)):
            f = self.pal_files[(start + off) % len(self.pal_files)]
            if verdicts.get(palettes.key_for(char, f)) is None:
                self._grid_focus(f)
                return
        self._cross_to_unreviewed(1)      # this character is done; go to the next

    def _land_unreviewed(self, fn):
        """Select/centre an unreviewed palette in the active view."""
        self._last_new_jump = fn
        if self.view_var.get() == "grid":
            self._grid_focus(fn)          # centre + select (blue check, no tint)
            self.gal_canvas.focus_set()
        else:
            j = self.pal_files.index(fn)
            self.file_list.selection_clear(0, "end")
            self.file_list.selection_set(j)
            self.file_list.activate(j)
            self.file_list.see(j)
            self._show_current()
            self.file_list.focus_set()

    def _cross_to_unreviewed(self, direction):
        """Load the nearest character (in `direction`) that still has unreviewed
        palettes, then land on its first (Next) / last (Prev) unreviewed."""
        char = self._cur_char
        idx = self._chars.index(char) if char in self._chars else 0
        n = len(self._chars)
        for step in range(1, n + 1):
            c = self._chars[(idx + direction * step) % n]
            if self._unrev.get(c, 0) > 0:
                self._load_char(c)
                self._last_new_jump = None
                self._grid_cur = None
                # let the grid rebuild, then land on the first/last unreviewed
                self.root.after(120, lambda d=direction: self._jump_unreviewed(d))
                return
        messagebox.showinfo("No new palettes",
                            "Everything is reviewed - nothing new left.")

    def _jump_unreviewed(self, direction):
        """Prev/Next: step through unreviewed ("new") palettes across ALL
        characters. Within the current character it moves to the adjacent
        unreviewed palette; at that character's first/last unreviewed it crosses
        into the next/previous character that has any. Grid centres + selects it;
        image view selects it in the list."""
        char = self._cur_char
        if not char:
            return
        verdicts = palettes.load_verdicts()
        unrev = [f for f in self.pal_files
                 if verdicts.get(palettes.key_for(char, f)) is None]
        if unrev:
            ref = (self._grid_cur if self.view_var.get() == "grid"
                   else getattr(self, "_last_new_jump", None))
            target = None
            if ref in unrev:
                j = unrev.index(ref) + direction
                if 0 <= j < len(unrev):
                    target = unrev[j]          # adjacent unreviewed in this char
                # else: stepped past the first/last -> cross characters below
            elif ref in self.pal_files:
                # sitting on a reviewed palette: next unreviewed toward `direction`
                k = self.pal_files.index(ref) + direction
                while 0 <= k < len(self.pal_files):
                    if self.pal_files[k] in unrev:
                        target = self.pal_files[k]
                        break
                    k += direction
            else:
                target = unrev[0] if direction > 0 else unrev[-1]
            if target is not None:
                self._land_unreviewed(target)
                return
        # nothing left this character in that direction -> next character
        self._cross_to_unreviewed(direction)

    def _grid_scroll_to(self, fn):
        entry = self._grid_cells.get(fn)
        if not entry:
            return
        cell = entry[0]
        self.gal_inner.update_idletasks()
        total = max(1, self.gal_inner.winfo_height())
        self.gal_canvas.yview_moveto(max(0.0, (cell.winfo_y() - 40) / total))

    def _update_compare_btn(self):
        n = len(getattr(self, "_grid_sel", ())) if self.view_var.get() == "grid" else 0
        self.cmp_btn.config(text=f"Compare selected ({n})..." if n
                            else "Compare selected...")

    def _compare_selection(self):
        """(files, first_index) chosen for comparison in the active view."""
        if self.view_var.get() == "grid":
            # click order, so the comparison lays them out as they were picked
            files = [f for f in getattr(self, "_grid_sel", [])
                     if f in self.pal_files]
        else:
            files = [self.pal_files[i] for i in self.file_list.curselection()]
        first = self.pal_files.index(files[0]) if files else 0
        return files, first

    # ---- removal ----
    def on_remove_rejected(self):
        verdicts = palettes.load_verdicts()
        keys = sorted(k for k, val in verdicts.items() if val == palettes.REJECT
                      and os.path.isfile(os.path.join(config.SKINS, *k.split("/"))))
        if not keys:
            messagebox.showinfo("Nothing to delete",
                                "No rejected palette files are on disk.")
            return
        if not messagebox.askyesno(
                "Are you SURE?",
                f"You are about to PERMANENTLY DELETE {len(keys)} rejected "
                "palette file(s) from disk.\n\n"
                "There is NO recovery - only re-downloading the gallery can "
                "bring gallery files back, and your own files would be gone "
                "for good.\n\n"
                "Rejected palettes are already excluded from randomization "
                "and hidden from view - you do NOT need to delete them.\n\n"
                "Continue to the file list?",
                icon="warning", default="no"):
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
        outer = ttk.Frame(nb); nb.add(outer, text="4. Lock Selections")
        t = ttk.Frame(self._scroll_body(outer), padding=14)
        t.pack(fill="both", expand=True)
        ttk.Label(t, text="Lock palettes to buttons", font=("Segoe UI", 11, "bold")
                  ).pack(anchor="w")
        ttk.Label(t, foreground="#555", justify="left", wraplength=self.SETUP_WRAP,
                  text="Pin a specific palette to a button slot so the "
                       "randomizer always uses it for that character. Leave a "
                       "slot on \"(random)\" to keep shuffling it."
                  ).pack(anchor="w", pady=(2, 8))

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

        # ---- stage locks: one row per slot, like the palette buttons ----
        ttk.Separator(t, orient="horizontal").pack(fill="x", pady=12)
        ttk.Label(t, text="Lock stages to slots", font=("Segoe UI", 11, "bold")
                  ).pack(anchor="w")
        ttk.Label(t, foreground="#555", justify="left", wraplength=self.SETUP_WRAP,
                  text="Pin any stage slot to one stage so it never rolls; leave "
                       "it on \"(random)\" to keep shuffling. Turn on \"Distribute "
                       "ported stages\" in Setup to lock ports into any slot too. "
                       "Saved instantly."
                  ).pack(anchor="w", pady=(2, 8))

        sgrid = ttk.Frame(t); sgrid.pack(fill="x", pady=(0, 4))
        sgrid.columnconfigure(2, weight=1)
        self.slock_row_vars = {}
        self.slock_row_boxes = {}
        self.slock_row_maps = {}
        for r, (sid, short, _vs) in enumerate(stagegal.slot_lock_options()):
            ttk.Label(sgrid, text=sid, font=("Consolas", 10, "bold"), width=4
                      ).grid(row=r, column=0, sticky="w", pady=3)
            ttk.Label(sgrid, text=short, foreground="#555", anchor="w"
                      ).grid(row=r, column=1, sticky="w", padx=(4, 10), pady=3)
            var = tk.StringVar(value="(random)")
            box = ttk.Combobox(sgrid, textvariable=var, state="readonly", values=[])
            box.grid(row=r, column=2, sticky="ew", padx=(4, 8), pady=3)
            box.bind("<<ComboboxSelected>>",
                     lambda e, s=sid: self._slock_row_changed(s))
            self.slock_row_vars[sid] = var
            self.slock_row_boxes[sid] = box
            self.slock_row_maps[sid] = {}

        srow = ttk.Frame(t); srow.pack(fill="x", pady=(10, 0))
        scl = ttk.Button(srow, text="Clear all stage locks (random)",
                         command=self.on_clear_stage_locks)
        scl.pack(side="left"); self._action_widgets.append(scl)
        self.slock_status = ttk.Label(srow, text="", foreground="#177245")
        self.slock_status.pack(side="left", padx=(12, 0))

    def _slock_populate(self):
        """Fill every slot row's dropdown with its lockable stages + current
        lock. Ports are offered per slot when cross-slot merge is enabled, and
        only to the slots they actually fit."""
        dp = bool(randomize.get_config().get("distribute_ports", False))
        fits = None
        if dp:
            if getattr(self, "_slot_fits", None) is None:
                self._slot_fits = randomize.stage_fit_map()
            fits = self._slot_fits
        verdicts = stagegal.load_verdicts()
        locked = stagegal.load_locks()
        for sid, _short, variants in stagegal.slot_lock_options(
                distribute_ports=dp, slot_fits=fits):
            box = self.slock_row_boxes.get(sid)
            if box is None:
                continue
            pool = [v for v in variants if verdicts.get(v["key"]) != "delete"]
            dispmap, values = {}, ["(random)"]
            for v in pool:
                kind = stagegal.KIND_LABEL.get(v["kind"], v["kind"])
                disp = f"{stagegal.display_name(v)} — {v['author']} [{kind}]"
                while disp in dispmap:       # de-dupe identical labels
                    disp += " ."
                dispmap[disp] = v
                values.append(disp)
            box["values"] = values
            self.slock_row_maps[sid] = dispmap
            cur = locked.get(sid)
            self.slock_row_vars[sid].set(
                next((d for d, v in dispmap.items() if v["key"] == cur),
                     "(random)"))

    def _slock_row_changed(self, sid):
        v = self.slock_row_maps.get(sid, {}).get(self.slock_row_vars[sid].get())
        stagegal.set_lock(sid, v["key"] if v else None)
        self.slock_status.config(
            text=(f"STG {sid} locked." if v else f"STG {sid} set to random."))
        self.logln(f"Stage {sid} " + (
            f"locked to {stagegal.display_name(v)} ({v['author']})." if v
            else "unlocked (random)."))

    def on_clear_stage_locks(self):
        for sid in list(self.slock_row_boxes):
            stagegal.set_lock(sid, None)
            self.slock_row_vars[sid].set("(random)")
        self.slock_status.config(text="All stage slots set to random.")
        self.logln("Cleared all stage locks (random).")

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

    # ------------------------------------------------------- stage gallery tab
    # Master-detail stage gallery: stage list left (aligned counts), hero
    # preview with C/L/R view swap top-right, that stage's variant cards below.
    HERO_W = 470
    HERO_H = 340
    MINI_W = 128

    def _tab_stages(self, nb):
        t = ttk.Frame(nb, padding=10); nb.add(t, text="3. Stage Gallery")
        pw = ttk.PanedWindow(t, orient="horizontal")
        pw.pack(fill="both", expand=True)

        # ---- left pane: stage list + selected-variant actions + filters
        left = ttk.Frame(pw, padding=(0, 0, 6, 0))
        # Tree + scrollbar: the list now runs 17 slots + port groups, well past
        # one screen, so the scrollbar makes the overflow visible.
        tree_wrap = ttk.Frame(left); tree_wrap.pack(fill="x")
        self.stg_tree = ttk.Treeview(tree_wrap, columns=("n",), show="tree",
                                     selectmode="browse", height=17)
        self.stg_tree.column("#0", width=196, stretch=True)
        self.stg_tree.column("n", width=46, anchor="e", stretch=False)
        stg_vsb = ttk.Scrollbar(tree_wrap, orient="vertical",
                                command=self.stg_tree.yview)
        self.stg_tree.configure(yscrollcommand=stg_vsb.set)
        stg_vsb.pack(side="right", fill="y")
        self.stg_tree.pack(side="left", fill="x", expand=True)
        self.stg_tree.bind("<<TreeviewSelect>>", lambda e: self._on_stage_slot())

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(left, text="Selected stage", font=("Segoe UI", 9, "bold")
                  ).pack(anchor="w")
        self.stg_sel_name = ttk.Label(left, text="-", wraplength=225,
                                      font=("Segoe UI", 10, "bold"))
        self.stg_sel_name.pack(anchor="w", pady=(2, 0))
        self.stg_sel_sub = ttk.Label(left, text="", foreground="#666",
                                     wraplength=225)
        self.stg_sel_sub.pack(anchor="w")
        self.stg_verdict_lbl = ttk.Label(left, text="", font=("Segoe UI", 9))
        self.stg_verdict_lbl.pack(anchor="w", pady=(4, 0))
        vb = ttk.Frame(left); vb.pack(anchor="w", pady=(4, 0))
        self.stg_keep_btn = ttk.Button(
            vb, text="Keep (Y)", width=9,
            command=lambda: self._stage_set(stagegal.KEEP, advance=True))
        self.stg_keep_btn.pack(side="left")
        self.stg_clear_btn = ttk.Button(vb, text="Clear (C)", width=9,
                                        command=lambda: self._stage_set(None))
        self.stg_clear_btn.pack(side="left", padx=(4, 0))
        self.stg_rej_btn = ttk.Button(
            vb, text="Reject (N)", width=10,
            command=lambda: self._stage_set(stagegal.REJECT, advance=True))
        self.stg_rej_btn.pack(side="left", padx=(4, 0))

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(left, text="Show", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.stg_hiderej_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(left, text="Hide rejected", variable=self.stg_hiderej_var,
                        command=self._stage_refilter).pack(anchor="w")
        self.stg_shownew_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(left, text="Show new only", variable=self.stg_shownew_var,
                        command=self._stage_refilter).pack(anchor="w")
        ttk.Button(left, text="↻ Rescan local files",
                   command=self.on_rescan).pack(anchor="w", pady=(2, 0))
        self.stg_assign_btn = ttk.Button(left, text="",
                                         command=self.on_assign_customs)
        # packed only when there are pending drop-ins (see _load_stage_gallery)
        self.stage_lbl = ttk.Label(left, text="", foreground="#666",
                                   wraplength=225, justify="left")
        self.stage_lbl.pack(anchor="w", pady=(10, 0))
        pw.add(left, weight=0)

        # ---- right pane: hero + C/L/R minis, cards below
        right = ttk.Frame(pw)
        self._stg_right = right
        right.bind("<Configure>", self._stage_relayout)
        hr = ttk.Frame(right); hr.pack(fill="x", anchor="w")
        # the hero strip stretches to the minis so the row spans the full
        # width; the image centers inside on the dark letterbox
        self.stg_hero = tk.Label(hr, bd=1, relief="solid", background="#2d2d2d",
                                 foreground="#888", cursor="hand2",
                                 font=("Segoe UI", 11))
        self.stg_hero.pack(side="left", fill="both", expand=True)
        self.stg_hero.bind("<Button-1>", lambda e: self._stage_hero_full())
        minis = ttk.Frame(hr); minis.pack(side="left", fill="y", padx=(8, 0))
        self._stg_minis = {}
        for vk, txt in (("C", "Center"), ("L", "Left"), ("R", "Right")):
            mf = tk.Frame(minis, highlightthickness=2,
                          highlightbackground="#aaaaaa")
            mf.pack(pady=(0, 6), anchor="w")
            ml = tk.Label(mf, bd=0, cursor="hand2", background="#2d2d2d")
            ml.pack()
            mc = tk.Label(mf, text=txt, font=("Segoe UI", 8),
                          background="#f0f0f0", foreground="#444")
            mc.pack(fill="x")
            for w in (mf, ml, mc):
                w.bind("<Button-1>", lambda e, k=vk: self._stage_set_view(k))
            self._stg_minis[vk] = (mf, ml, mc)

        body = ttk.Frame(right); body.pack(fill="both", expand=True, pady=(8, 0))
        self.stg_canvas = tk.Canvas(body, highlightthickness=0, background="#2d2d2d",
                                    takefocus=1)
        # Y/N/C fast review (keep / reject / clear), advancing to next visible
        self.stg_canvas.bind("<y>", lambda e: self._stage_set(stagegal.KEEP, advance=True))
        self.stg_canvas.bind("<Y>", lambda e: self._stage_set(stagegal.KEEP, advance=True))
        self.stg_canvas.bind("<n>", lambda e: self._stage_set(stagegal.REJECT, advance=True))
        self.stg_canvas.bind("<N>", lambda e: self._stage_set(stagegal.REJECT, advance=True))
        self.stg_canvas.bind("<c>", lambda e: self._stage_set(None))
        self.stg_canvas.bind("<C>", lambda e: self._stage_set(None))
        vsb = ttk.Scrollbar(body, orient="vertical", command=self.stg_canvas.yview)
        self.stg_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.stg_canvas.pack(side="left", fill="both", expand=True)
        self.stg_inner = tk.Frame(self.stg_canvas, background="#2d2d2d")
        self.stg_canvas.create_window((0, 0), window=self.stg_inner, anchor="nw")
        self.stg_inner.bind("<Configure>", lambda e: self.stg_canvas.configure(
            scrollregion=self.stg_canvas.bbox("all")))
        self.stg_canvas.bind("<Enter>", lambda e: self.stg_canvas.bind_all(
            "<MouseWheel>", lambda ev: self.stg_canvas.yview_scroll(
                int(-ev.delta / 120), "units")))
        self.stg_canvas.bind("<Leave>",
                             lambda e: self.stg_canvas.unbind_all("<MouseWheel>"))
        pw.add(right, weight=4)

        self._stage_thumb_cache = {}   # (key, width) -> PhotoImage
        self._stage_secs = []          # [(title, variants)]
        self._stg_cur_variant = None
        self._stg_cur_view = "C"
        self._stg_cards = {}           # key -> (cell frame, inner ring frame)
        self._hero_w = self.HERO_W     # dynamic; tracks pane width
        self._card_tw = self.STAGE_TW
        self._stage_built = False

    def _stage_relayout(self, e=None):
        job = getattr(self, "_stg_relayout_job", None)
        if job:
            self.root.after_cancel(job)
        self._stg_relayout_job = self.root.after(160, self._stage_relayout_now)

    def _stage_relayout_now(self):
        """Recompute hero + card sizes from the pane width and re-render."""
        w = self._stg_right.winfo_width()
        if w < 240 or not self._stage_built:
            return
        hero_w = max(300, w - self.MINI_W - 26)
        cw = self.stg_canvas.winfo_width()
        if cw < 120:
            cw = w - 20
        cols = max(2, cw // (self.STAGE_TW + 22))
        card_tw = max(120, (cw - 10 - cols * 22) // cols)
        if (abs(hero_w - self._hero_w) < 16
                and abs(card_tw - self._card_tw) < 12
                and cols == getattr(self, "_card_cols", 0)):
            return
        self._hero_w = hero_w
        self._stage_set_view(self._stg_cur_view)
        self._stage_cards_build()      # computes final tw/cols itself

    STAGE_TW = 170

    def _load_stage_gallery(self, force=False):
        """Fill the stage list; keep the current selection when possible."""
        if self._stage_built and not force:
            return
        self._stage_secs = stagegal.sections()
        self._stage_new = stagegal.new_keys(self._stage_secs)
        keep = self.stg_tree.selection()
        self.stg_tree.delete(*self.stg_tree.get_children())
        verdicts = stagegal.load_verdicts()
        for i, (title, vs) in enumerate(self._stage_secs):
            n = sum(1 for v in vs if verdicts.get(v["key"]) != "delete")
            nnew = sum(1 for v in vs if v["key"] in self._stage_new)
            short = title.split("  -  ", 1)[-1].split("   (")[0]
            if nnew:
                short = f"★ {short}"      # star: this slot has new (unreviewed) stages
            self.stg_tree.insert("", "end", iid=str(i), text=short,
                                 values=(f"{n}/{len(vs)}",))
        if self._stage_secs:
            sel = keep[0] if keep and self.stg_tree.exists(keep[0]) else "0"
            self.stg_tree.selection_set(sel)   # fires _on_stage_slot
        else:
            for w in self.stg_inner.winfo_children():
                w.destroy()
            tk.Label(self.stg_inner, text="No stage data yet - run Download / "
                     "Update on the Setup tab.",
                     background="#2d2d2d", foreground="#aaa",
                     font=("Segoe UI", 11)).pack(padx=20, pady=20)
        self._stage_built = True
        self._update_stage_counts()
        pending = stagegal.pending_customs()
        if pending:
            self.stg_assign_btn.config(
                text=f"Assign {len(pending)} custom stage file(s)...")
            self.stg_assign_btn.pack(anchor="w", pady=(8, 0),
                                     before=self.stage_lbl)
        else:
            self.stg_assign_btn.pack_forget()

    def on_assign_customs(self):
        """Ask which stage each unrecognized drop-in belongs to, then rename
        it into the convention so it joins the pools."""
        pending = stagegal.pending_customs()
        if not pending:
            return
        m = stagegal.manifest() or {}
        slot_names = {e.get("id"): e.get("n", e.get("id"))
                      for e in m.get("stages", [])}
        dlg = tk.Toplevel(self.root)
        dlg.title("Assign custom stage files")
        dlg.transient(self.root); dlg.grab_set()
        ttk.Label(dlg, padding=(12, 10, 12, 4), justify="left", text=(
            "These files don't say which stage they belong to. Pick the stage "
            "for each.\nA full POL+TEX stage can always go to Training; a "
            "TEX-only file also offers\nany slot whose size it matches.")
        ).pack(anchor="w")
        rows = []
        body = ttk.Frame(dlg, padding=(12, 4)); body.pack(fill="both", expand=True)
        for item in pending:
            row = ttk.Frame(body); row.pack(fill="x", pady=3)
            parts = [p for p in ("tex", "pol") if item.get(p)]
            desc = f"{item['stem']}  ({'+'.join(parts) if parts else 'unpaired file'})"
            ttk.Label(row, text=desc, width=38).pack(side="left")
            cands = list(stagegal.candidate_slots(item))
            # A full pol+tex pair can always be a Training stage, even when its
            # size matches no stock slot.
            if item.get("tex") and item.get("pol") and "XX" not in cands:
                cands.insert(0, "XX")
            cmap = {}
            for c in cands:
                label = ("Training (full custom stage)" if c == "XX"
                         else f"STG {c} - {slot_names.get(c, c)}")
                cmap[label] = c
            var = tk.StringVar()
            if cmap:
                cb = ttk.Combobox(row, textvariable=var, state="readonly",
                                  width=34, values=["(skip)"] + list(cmap))
                cb.set("(skip)")
                cb.pack(side="left", padx=(8, 0))
                rows.append((item, var, cmap))
            else:
                why = ("needs its matching POL or TEX file" if len(parts) == 1
                       else "unrecognized files")
                ttk.Label(row, foreground="#b00",
                          text=f"can't place - {why}"
                          ).pack(side="left", padx=(8, 0))
        done = {"n": 0}

        def apply():
            for item, var, cmap in rows:
                target = cmap.get(var.get())
                if target:
                    try:
                        newbase = stagegal.assign_custom(item, target)
                        self.logln(f"Assigned {item['stem']} -> {newbase}")
                        done["n"] += 1
                    except OSError as e:
                        messagebox.showerror("Rename failed", str(e))
            dlg.destroy()
            if done["n"]:
                self._load_stage_gallery(force=True)

        btns = ttk.Frame(dlg, padding=12); btns.pack(fill="x")
        ttk.Button(btns, text="Apply", command=apply).pack(side="right")
        ttk.Button(btns, text="Cancel", command=dlg.destroy
                   ).pack(side="right", padx=(0, 8))

    def _stage_slot_variants(self):
        sel = self.stg_tree.selection()
        if not sel or not self._stage_secs:
            return []
        return self._stage_secs[int(sel[0])][1]

    def _on_stage_slot(self):
        variants = self._stage_slot_variants()
        self._stage_cards_build()
        if self._stg_visible:
            self._stage_select_variant(self._stg_visible[0])
        else:
            self._stage_clear_selection()

    def _stage_refilter(self):
        """Filter checkbox changed: rebuild and land on the first visible card."""
        self._stage_cards_build()
        cur = self._stg_cur_variant
        if cur and cur["key"] in self._stg_cards:
            return                       # current still visible; keep it
        if self._stg_visible:
            self._stage_select_variant(self._stg_visible[0])
        else:
            self._stage_clear_selection()

    def _stage_clear_selection(self):
        self._stg_cur_variant = None
        self.stg_hero.config(image="", text="No stages for this slot",
                             width=42, height=14)
        self._hero_img = None
        self.stg_sel_name.config(text="-")
        self.stg_sel_sub.config(text="")
        self.stg_verdict_lbl.config(text="")
        for _vk, (mf, ml, _mc) in self._stg_minis.items():
            ml.config(image="", text="")
            ml._img = None
            mf.config(highlightbackground="#aaaaaa", highlightcolor="#aaaaaa")

    def _stage_cards_build(self):
        for w in self.stg_inner.winfo_children():
            w.destroy()
        self._stg_cards = {}
        variants = self._stage_slot_variants()
        verdicts = stagegal.load_verdicts()
        shown = variants
        if self.stg_hiderej_var.get():
            shown = [v for v in shown if verdicts.get(v["key"]) != "delete"]
        if self.stg_shownew_var.get():
            shown = [v for v in shown if v["key"] not in verdicts]
        base = [v for v in shown if not v.get("xx")]
        extra = [v for v in shown if v.get("xx")]
        self._stg_visible = base + extra   # display order, for Y/N navigation
        # per-card chrome: 10 grid gap + 6 pool border + 6 selection ring.
        # Cards are sized as if at least 3 sat per row, so a slot with 1-2
        # stages shows them at the same familiar size (and fully on screen)
        # instead of ballooning to fill the width.
        cw = self.stg_canvas.winfo_width()
        if cw < 120:                    # canvas not laid out yet
            cw = 640
        cols = max(2, cw // (self.STAGE_TW + 22))
        biggest = max(len(base), len(extra), 1)
        cols = max(1, min(cols, biggest))
        k = max(cols, 3)
        self._card_tw = min(300, max(120, (cw - 10 - k * 22) // k))
        self._card_cols = cols

        def grid_of(vs):
            row = tk.Frame(self.stg_inner, background="#2d2d2d")
            row.pack(fill="x", padx=4, pady=(4, 2))
            for i, v in enumerate(vs):
                self._stage_card(row, i, cols, v, verdicts)

        if base:
            grid_of(base)
        if extra:
            tk.Label(self.stg_inner,
                     text="custom & ported stages (training slot only)",
                     background="#2d2d2d", foreground="#bbbbbb",
                     font=("Segoe UI", 9, "italic")).pack(anchor="w",
                                                          padx=8, pady=(8, 0))
            grid_of(extra)
        if not shown:
            tk.Label(self.stg_inner, text="(none match the filter)",
                     background="#2d2d2d", foreground="#777",
                     font=("Segoe UI", 9)).pack(anchor="w", padx=12, pady=8)
        # restore the selection ring after a rebuild
        cur = self._stg_cur_variant
        if cur and cur["key"] in self._stg_cards:
            self._stg_cards[cur["key"]][1].config(background=self.SELECT_COLOR)
        self.stg_canvas.yview_moveto(0)

    def _stage_thumb(self, v, width=None):
        width = width or self.STAGE_TW
        ck = (v["key"], width)
        img = self._stage_thumb_cache.get(ck)
        if img is None:
            img = ImageTk.PhotoImage(stagegal.thumb_image(v, width=width))
            self._stage_thumb_cache[ck] = img
        return img

    def _stage_border(self, key, verdicts):
        """Verdict colour: gold = new (no verdict), green = keep, red = reject."""
        v = verdicts.get(key)
        if v == "keep":
            return self.KEEP_COLOR
        if v == "delete":
            return self.REJECT_COLOR
        return self.NEUTRAL_COLOR            # new / unreviewed

    def _stage_card(self, parent, i, cols, v, verdicts):
        """Image-only card: verdict = outer border, selection = inner ring.
        Left-click selects; right-click cycles new -> keep -> reject -> new."""
        color = self._stage_border(v["key"], verdicts)
        cell = tk.Frame(parent, highlightthickness=3, background="#2d2d2d",
                        highlightbackground=color, highlightcolor=color)
        cell.grid(row=i // cols, column=i % cols, padx=5, pady=5, sticky="n")
        inner = tk.Frame(cell, background="#2d2d2d")
        inner.pack()
        pic = tk.Label(inner, image=self._stage_thumb(v, self._card_tw), bd=0)
        pic.pack(padx=3, pady=3)
        self._stg_cards[v["key"]] = (cell, inner)
        for w in (cell, inner, pic):
            w.bind("<Button-1>", lambda e, vv=v: (self._stage_select_variant(vv),
                                                  self.stg_canvas.focus_set()))
            w.bind("<Button-3>", lambda e, vv=v: self._stage_cycle(vv))

    # ---- selection / hero ----
    def _stage_select_variant(self, v):
        prev = self._stg_cur_variant
        self._stg_cur_variant = v
        if prev and prev["key"] in self._stg_cards:
            self._stg_cards[prev["key"]][1].config(background="#2d2d2d")
        if v["key"] in self._stg_cards:
            self._stg_cards[v["key"]][1].config(background=self.SELECT_COLOR)
        kind = stagegal.KIND_LABEL.get(v["kind"], v["kind"])
        self.stg_sel_name.config(text=v["name"])
        if v.get("invalid"):
            self.stg_sel_sub.config(foreground=self.REJECT_COLOR,
                                    text=f"INVALID (never rolled) - {v['invalid']}")
        else:
            self.stg_sel_sub.config(foreground="#666",
                                    text=f"{v['author']} · {kind}"
                                    + (f" · {v['game']}" if v.get("game") else ""))
        self._refresh_verdict_lbl()
        view = "C" if "C" in v.get("views", {}) else next(iter(v.get("views", {})), None)
        self._stage_set_view(view or "C")
        self._stage_fill_minis()

    def _stage_fill_minis(self):
        v = self._stg_cur_variant or {}
        views = v.get("views", {})
        for vk, (mf, ml, mc) in self._stg_minis.items():
            path = views.get(vk)
            if path:
                im = PILImage.open(path).convert("RGB")
                im = im.resize((self.MINI_W,
                                max(1, int(im.height * self.MINI_W / im.width))),
                               PILImage.LANCZOS)
                ph = ImageTk.PhotoImage(im)
                ml.config(image=ph, text="")
                ml._img = ph
            else:
                ml.config(image="", text="n/a", width=14,
                          background="#2d2d2d", foreground="#666")
                ml._img = None

    def _stage_set_view(self, vk):
        self._stg_cur_view = vk
        v = self._stg_cur_variant
        for k, (mf, _ml, _mc) in self._stg_minis.items():
            c = self.SELECT_COLOR if k == vk else "#aaaaaa"
            mf.config(highlightbackground=c, highlightcolor=c)
        path = (v or {}).get("views", {}).get(vk)
        if not path:
            if v is not None:
                self.stg_hero.config(image="", text="no preview", width=42,
                                     height=14)
                self._hero_img = None
            return
        im = PILImage.open(path).convert("RGB")
        scale = min(self._hero_w / im.width, self.HERO_H / im.height)
        im = im.resize((max(1, int(im.width * scale)),
                        max(1, int(im.height * scale))), PILImage.LANCZOS)
        self._hero_img = ImageTk.PhotoImage(im)
        self.stg_hero.config(image=self._hero_img, text="")

    def _stage_hero_full(self):
        v = self._stg_cur_variant
        path = (v or {}).get("views", {}).get(self._stg_cur_view)
        if not path:
            return
        dlg = tk.Toplevel(self.root)
        dlg.title(f"{v['name']} - {v['author']}")
        dlg.transient(self.root)
        dlg.configure(background="#1b1b1f")
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.bind("<Button-1>", lambda e: dlg.destroy())
        im = PILImage.open(path).convert("RGB")
        W = min(im.width, self.root.winfo_screenwidth() - 120)
        im = im.resize((W, int(im.height * W / im.width)), PILImage.LANCZOS)
        ph = ImageTk.PhotoImage(im)
        tk.Label(dlg, image=ph, bd=0).pack(padx=6, pady=6)
        dlg._img = ph

    def _stage_update_tree_row(self):
        sel = self.stg_tree.selection()
        if not sel or not self._stage_secs:
            return
        title, vs = self._stage_secs[int(sel[0])]
        verdicts = stagegal.load_verdicts()
        n = sum(1 for x in vs if verdicts.get(x["key"]) != "delete")
        nnew = sum(1 for x in vs if x["key"] not in verdicts)
        short = title.split("  -  ", 1)[-1].split("   (")[0]
        if nnew:
            short = f"★ {short}"
        self.stg_tree.item(sel[0], text=short, values=(f"{n}/{len(vs)}",))

    # ---- verdicts (keep / reject / clear) ----
    def _stage_set(self, verdict, v=None, advance=False):
        """Set a variant's verdict. With advance, move to the next visible
        stage (for Y/N fast review); otherwise keep the current selection."""
        v = v or self._stg_cur_variant
        if not v:
            return
        key = v["key"]
        vis = list(self._stg_visible)
        idx = next((i for i, x in enumerate(vis) if x["key"] == key), None)
        stagegal.set_verdict(key, verdict)
        if verdict is None:
            self._stage_new.add(key)
        else:
            self._stage_new.discard(key)
        self._stage_cards_build()          # a filter may hide/show this card now
        self._stage_update_tree_row()
        self._update_stage_counts()

        newvis = self._stg_visible
        if advance and newvis:
            pos = next((i for i, x in enumerate(newvis) if x["key"] == key), None)
            target = (min(pos + 1, len(newvis) - 1) if pos is not None
                      else min(idx if idx is not None else 0, len(newvis) - 1))
            self._stage_select_variant(newvis[target])
        elif key in self._stg_cards:
            self._stage_select_variant(v)  # still visible: keep it selected
        elif newvis:
            self._stage_select_variant(newvis[0])
        elif advance and not self._stage_next_slot():
            self._stage_clear_selection()
        else:
            self._stage_clear_selection()
        self.stg_canvas.focus_set()

    def _stage_next_slot(self):
        """Move to the next stage slot that has visible stages. True if moved."""
        kids = list(self.stg_tree.get_children())
        sel = self.stg_tree.selection()
        start = kids.index(sel[0]) if sel and sel[0] in kids else -1
        for step in range(1, len(kids) + 1):
            self.stg_tree.selection_set(kids[(start + step) % len(kids)])
            if self._stg_visible:
                return True
        return False

    def _stage_cycle(self, v):
        """Right-click: new -> keep -> reject -> new (no advance)."""
        cur = stagegal.load_verdicts().get(v["key"])
        nxt = {None: stagegal.KEEP, "keep": stagegal.REJECT,
               "delete": None}.get(cur, stagegal.KEEP)
        self._stage_select_variant(v)
        self._stage_set(nxt, v)

    def _refresh_verdict_lbl(self):
        v = self._stg_cur_variant
        if not v:
            self.stg_verdict_lbl.config(text="")
            return
        state = stagegal.load_verdicts().get(v["key"])
        text, color = {"keep": ("Kept (in pool)", self.KEEP_COLOR),
                       "delete": ("Rejected (excluded)", self.REJECT_COLOR)
                       }.get(state, ("New - unreviewed", self.NEUTRAL_COLOR))
        self.stg_verdict_lbl.config(text=text, foreground=color)

    def _update_stage_counts(self):
        total, rejected, new = stagegal.counts()
        self.stage_lbl.config(
            text=(f"{total} stages · {total - rejected} in pool · "
                  f"{new} new · {rejected} rejected\n"
                  "Left-click: select   ·   Right-click: cycle new/keep/reject")
            if total else "")

    # --------------------------------------------------------- randomize tab
    def _tab_randomize(self, nb):
        t = ttk.Frame(nb, padding=14); nb.add(t, text="1. Randomize")
        ttk.Label(t, text="Randomize now", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(t, foreground="#555", justify="left", wraplength=self.SETUP_WRAP,
                  text="Run a shuffle right now (also happens automatically on "
                       "launch once auto-randomize is set up)."
                  ).pack(anchor="w", pady=(2, 8))

        row = ttk.Frame(t); row.pack(anchor="w", pady=(0, 10))
        ttk.Label(row, text="Fixed seed (optional):").pack(side="left")
        ttk.Entry(row, textvariable=self.seed_var, width=14).pack(side="left", padx=(6, 0))

        srow = ttk.Frame(t); srow.pack(anchor="w", pady=(0, 8))
        self.stages_var = tk.BooleanVar(
            value=bool(randomize.get_config().get("randomize_stages")))
        ttk.Checkbutton(srow, text="Also randomize stages (pick them on the "
                                   "Stage Gallery tab)",
                        variable=self.stages_var,
                        command=self._on_stages_toggle).pack(side="left")

        rrow = ttk.Frame(t); rrow.pack(anchor="w")
        r = ttk.Button(rrow, text="Randomize now", command=self.on_randomize)
        r.pack(side="left")
        self._action_widgets.append(r)
        v = ttk.Button(rrow, text="View last run log", command=self.on_view_log)
        v.pack(side="left", padx=(8, 0))
        self._action_widgets.append(v)

        ttk.Separator(t, orient="horizontal").pack(fill="x", pady=14)
        ttk.Label(t, text="Reset palettes", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(t, foreground="#555", justify="left", wraplength=self.SETUP_WRAP,
                  text="Puts palettes back to vanilla for one character or "
                       "everyone. Only palettes are changed — any other game "
                       "mods (stages, etc.) are left alone."
                  ).pack(anchor="w", pady=(2, 8))
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
        ttk.Label(t, foreground="#555", justify="left", wraplength=self.SETUP_WRAP,
                  text="Characters whose palettes were edited outside this app "
                       "(e.g. with PalMod) are protected automatically - the "
                       "randomizer leaves them alone so your work isn't "
                       "overwritten. Unlock one to randomize over it, or reset "
                       "it to vanilla above."
                  ).pack(anchor="w", pady=(2, 8))
        self.prot_lbl = ttk.Label(t, text="None detected.", foreground="#555",
                                  wraplength=self.SETUP_WRAP, justify="left")
        self.prot_lbl.pack(anchor="w", pady=(0, 6))
        unl = ttk.Button(t, text="Unlock for randomizing...", command=self.on_unprotect)
        unl.pack(anchor="w")
        self._action_widgets.append(unl)

    def on_rescan(self):
        """Re-read skins/, stages/, custom/ and presets/ from disk so files
        added or removed since launch (or by a download) show up now."""
        palettes._color_key_cache.clear()
        palettes._tour_weights.clear()
        palettes._tour_feats.clear()
        self._stage_thumb_cache = {}
        self.refresh_status()          # rebuilds char list, counts, reset combo
        self._refresh_presets()
        self._on_char_change()         # reload current character's palettes
        self._stage_built = False
        self._slot_fits = None         # port files changed - recompute fit map
        if self.nb.tab(self.nb.select(), "text").endswith("Stage Gallery"):
            self._load_stage_gallery(force=True)
        self.logln("Rescanned local files.")

    # ------------------------------------------------------------- presets
    def _refresh_presets(self):
        self._presets = presets.list_presets()
        names = [f"{p['name']}  ({p['author']})" for p in self._presets]
        self.preset_combo["values"] = names
        if names and self.preset_var.get() not in names:
            self.preset_var.set(names[0])
        self._preset_info()

    def _preset_info(self):
        p = self._current_preset()
        if p:
            self.preset_lbl.config(
                text=f"{p['keeps']:,} keeps · {p['rejects']:,} rejects · "
                     f"{p['stages']} stage picks · updated {p['updated']}")
        else:
            self.preset_lbl.config(
                text="No mixes downloaded yet - run Download / Update first.")

    def _current_preset(self):
        i = self.preset_combo.current()
        return self._presets[i] if 0 <= i < len(self._presets) else None

    def on_apply_preset(self, mode):
        p = self._current_preset()
        if not p:
            messagebox.showinfo("No mix selected",
                                "Download the gallery first (Setup tab) - "
                                "curated mixes come with it.")
            return
        if mode == "replace":
            msg = (f"Adopt \"{p['name']}\" wholesale?\n\n"
                   "Your existing keep/reject choices for GALLERY palettes and "
                   "stages will be REPLACED by this mix.\n"
                   "(Your own drop-ins in custom/ are never affected.)")
        else:
            msg = (f"Fill gaps from \"{p['name']}\"?\n\n"
                   "Only palettes and stages you haven't judged yet will take "
                   "this mix's verdicts. Everything you've already decided "
                   "stays yours.")
        if not messagebox.askyesno("Apply curated mix", msg):
            return
        result = presets.apply_preset(p["path"], mode)
        self.logln(f"Applied mix \"{result['name']}\" ({mode}): "
                   f"{result['palettes']:,} palette and {result['stages']} "
                   f"stage verdicts.")
        self.refresh_status()
        self._on_char_change()
        self._stage_built = False     # stage gallery reloads on next visit
        messagebox.showinfo("Mix applied",
                            f"\"{result['name']}\" applied: "
                            f"{result['palettes']:,} palette verdicts, "
                            f"{result['stages']} stage picks.")

    def on_export_preset(self):
        name = simpledialog.askstring(
            "Export your mix", "Name for your mix (shown to other users):",
            parent=self.root)
        if not name:
            return
        author = simpledialog.askstring(
            "Export your mix", "Author name:", parent=self.root) or "anonymous"
        out = filedialog.asksaveasfilename(
            title="Save mix as", defaultextension=".json",
            initialfile=f"{author.lower().replace(' ', '_')}.json",
            filetypes=[("Preset JSON", "*.json")])
        if not out:
            return
        stats = presets.export_preset(out, name, author)
        self.logln(f"Exported mix \"{name}\": {stats['palettes']:,} palette "
                   f"and {stats['stages']} stage verdicts -> {out}")
        messagebox.showinfo(
            "Mix exported",
            f"Saved {stats['palettes']:,} palette and {stats['stages']} stage "
            f"verdicts.\n\nTo share it, submit this file to the mvc2-skins "
            f"repo's presets/ folder.")

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

    def _on_incl_unrev_toggle(self):
        randomize.set_config_key("include_unreviewed",
                                 bool(self.incl_unrev_var.get()))
        self.logln("Randomize scope: "
                   + ("kept + unreviewed." if self.incl_unrev_var.get()
                      else "kept selections only."))

    def _on_ports_toggle(self):
        randomize.set_config_key("distribute_ports", bool(self.ports_var.get()))
        self.logln("Cross-slot ported stages "
                   + ("enabled." if self.ports_var.get() else "disabled."))
        self._stage_built = False       # stage lock dropdowns depend on this

    def _on_stages_toggle(self):
        randomize.set_config_key("randomize_stages", bool(self.stages_var.get()))
        self.logln("Stage randomization "
                   + ("enabled." if self.stages_var.get() else "disabled."))

    def on_download(self):
        if not messagebox.askyesno(
                "Download palette & stage gallery",
                "This downloads the full palette and stage gallery from GitHub "
                "- roughly 750 MB.\n\n"
                "Only new files are added; anything you already have (and your "
                "keep/reject choices) is preserved.\n\nContinue?"):
            return
        self.run_job("Downloading / updating gallery", randomize.download_palettes,
                     on_done=self._after_download)

    def _after_download(self):
        self._on_char_change()
        self._load_stage_gallery(force=True)
        self._refresh_presets()
        # Fresh user with an uncurated collection: offer a curated mix.
        if self._presets and len(palettes.load_verdicts()) < 50:
            p = self._presets[0]
            if messagebox.askyesno(
                    "Skip the curation?",
                    f"A curated mix is available: \"{p['name']}\" by "
                    f"{p['author']} ({p['keeps']:,} hand-picked palettes).\n\n"
                    "Apply it now so you don't have to review thousands of "
                    "palettes yourself? (You can change any of it later, or "
                    "apply a different mix from the Setup tab.)"):
                result = presets.apply_preset(p["path"], "replace")
                self.logln(f"Applied mix \"{result['name']}\": "
                           f"{result['palettes']:,} palette verdicts.")
                self.refresh_status()
                self._on_char_change()
                self._stage_built = False

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
        config.migrate_state()                  # honor pre-1.0.3 verdicts
    except Exception:
        pass
    try:
        randomize.randomize(progress=None)      # silent; never block the game
    except BaseException:                        # incl. SystemExit: game must launch
        try:
            import traceback
            with open(os.path.join(config.STATE, "launch_error.txt"), "w") as _f:
                traceback.print_exc(file=_f)
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
    # A windowed (--noconsole) PyInstaller build sets sys.stdout/stderr to None;
    # numpy (used by the stage-merge repack) and any stray print then crash on
    # None.write. Point them at a sink so the frozen app is safe either way.
    for _s in ("stdout", "stderr"):
        if getattr(sys, _s, None) is None:
            try:
                setattr(sys, _s, open(os.devnull, "w"))
            except OSError:
                pass
    if len(sys.argv) > 1:                        # launcher mode (Steam)
        return _launch_mode(sys.argv[1:])
    root = tk.Tk()
    try:
        style = ttk.Style()
        style.theme_use("vista")
        # space the notebook tabs out so "5. Logs" / "6. Setup" read as separate
        style.configure("TNotebook", tabmargins=(4, 4, 4, 0))
        style.configure("TNotebook.Tab", padding=(14, 5))
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
