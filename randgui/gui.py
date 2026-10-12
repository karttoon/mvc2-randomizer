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
from tkinter import ttk, filedialog, messagebox, scrolledtext, simpledialog, colorchooser

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
        # Dropdowns and spin boxes never change value on the mouse wheel - you're
        # almost always scrolling the page past them (it changed stage locks by
        # accident). The wheel scrolls the page instead; an OPEN dropdown list
        # still scrolls normally (that's a separate listbox).
        for cls in ("TCombobox", "TSpinbox"):
            for ev in ("<MouseWheel>", "<Shift-MouseWheel>",
                       "<Button-4>", "<Button-5>"):
                root.unbind_class(cls, ev)

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
        self._tab_fx(nb)
        self._tab_locks(nb)
        self._tab_logs(nb)
        self._tab_setup(nb)
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _tab_logs(self, nb):
        t = ttk.Frame(nb, padding=(10, 8)); nb.add(t, text="6. Logs")
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
        outer = ttk.Frame(nb); nb.add(outer, text="7. Setup")
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
        ttk.Label(t, text="Curated mixes (content choices)",
                  font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(t, foreground="#555", justify="left", wraplength=self.SETUP_WRAP,
                  text="A curated mix is a small file of someone's CHOICES - "
                       "which palettes and stages they kept or rejected, their "
                       "palette picks and stage locks, stage playlists and FX "
                       "setup - applied to your own gallery. \"Fresh start\" "
                       "adopts all of it; \"fill gaps\" only adds what you "
                       "haven't decided. Your own drop-ins are never touched. "
                       "(To hand someone the finished game file instead, use "
                       "the Randomize tab.)"
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
        pe = ttk.Button(prow, text="Export my curated mix...",
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
        # Mix slots: the six palettes this character uses (= palette locks).
        # Keys 1-6 put the palette you're viewing into LP..A2.
        self.mix_bar = ttk.Frame(t); self.mix_bar.pack(fill="x", pady=(0, 6))
        self._mix_imgs = []
        self._mix_cells = {}
        self._mix_assigned = {}      # filename -> button, for the current char

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
        for _i in range(6):          # 1-6 = put this palette in mix slot LP..A2
            self.file_list.bind(f"<Key-{_i + 1}>",
                                lambda e, i=_i: self._mix_assign(i))
        self.file_list.bind("<Button-3>", self._mix_list_menu)
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
        for _i in range(6):          # 1-6 = put the focused palette in mix slot
            self.gal_canvas.bind(f"<Key-{_i + 1}>",
                                 lambda e, i=_i: self._mix_assign(i))
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
        self._mix_assigned = self._mix_files(char)    # before the grid badges draw
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
        self._mix_build()

    # ---- mix slots (six palettes per character = the palette locks) -------
    def _mix_files(self, char):
        """{filename: button} of the character's current mix picks."""
        if not char:
            return {}
        return {fn: b for b, fn in locks.char_locks(char).items() if fn}

    def _mix_current_file(self):
        """The palette you're looking at: the single selected / cursor thumbnail
        in Grid view, the previewed one in Image view."""
        if not self.pal_files:
            return None
        if self.view_var.get() == "grid":
            if len(self._grid_sel) == 1:
                return self._grid_sel[0]
            return getattr(self, "_grid_cur", None)
        i = self._cur_index()
        return self.pal_files[i] if i is not None else None

    def _mix_build(self):
        """(Re)draw the six mix slots for the current character."""
        for w in self.mix_bar.winfo_children():
            w.destroy()
        self._mix_imgs, self._mix_cells = [], {}
        self._mix_widget_btn = {}        # str(widget) -> button, for drag targets
        char = self._cur_char
        if not char:
            return
        picks = locks.char_locks(char)
        ttk.Label(self.mix_bar, text="Mix:", font=("Segoe UI", 9, "bold")
                  ).pack(side="left", padx=(0, 6))
        for i, b in enumerate(locks.BUTTONS):
            fn = picks.get(b)
            cell = tk.Frame(self.mix_bar, highlightthickness=2,
                            highlightbackground="#bbbbbb", cursor="hand2")
            cell.pack(side="left", padx=2)
            tk.Label(cell, text=f"{i + 1}  {b}", font=("Segoe UI", 8, "bold"),
                     foreground="#555").pack()
            img = None
            if fn:
                try:
                    pil = palettes.load_scaled(char, fn, 72, 44)
                    img = ImageTk.PhotoImage(pil)
                    self._mix_imgs.append(img)
                except Exception:
                    img = None
            pic = (tk.Label(cell, image=img, bd=0) if img else
                   tk.Label(cell, text=("missing" if fn else "random"),
                            width=10, height=3, foreground="#999",
                            background="#eeeeee", font=("Segoe UI", 8)))
            pic.pack(padx=2, pady=(0, 1))
            # small move-left / move-right nudges under a filled slot
            arr = tk.Frame(cell)
            arr.pack(fill="x")
            for txt, d in (("◀", -1), ("▶", 1)):
                j = i + d
                a = tk.Label(arr, text=txt, font=("Segoe UI", 7),
                             foreground=("#666" if fn and 0 <= j < 6 else "#dddddd"),
                             cursor=("hand2" if fn and 0 <= j < 6 else ""))
                a.pack(side="left" if d < 0 else "right", padx=3)
                if fn and 0 <= j < 6:
                    a.bind("<Button-1>", lambda e, s=b, t=locks.BUTTONS[j]:
                           (self._mix_swap(s, t), "break")[1])
            for w in (cell, pic) + tuple(cell.winfo_children()[:1]):
                self._mix_widget_btn[str(w)] = b
                w.bind("<ButtonPress-1>", lambda e, bb=b: self._mix_press(e, bb))
                w.bind("<B1-Motion>", self._mix_motion)
                w.bind("<ButtonRelease-1>", self._mix_release)
                w.bind("<Button-3>", lambda e, bb=b: self._mix_set(bb, None))
            self._mix_widget_btn[str(arr)] = b
            self._mix_cells[b] = (cell, fn)
        side = ttk.Frame(self.mix_bar); side.pack(side="left", padx=(10, 0))
        ttk.Button(side, text="Clear mix", width=10,
                   command=self._mix_clear_all).pack(anchor="w")
        self.mix_hint = ttk.Label(side, text="Click a slot = show its palette  ·  "
                                  "drag a slot onto another (or ◀ ▶) = reorder\n"
                                  "Fill: select palette(s), click an empty slot, "
                                  "Shift+click to replace, or press 1-6\n"
                                  "Right-click a palette for a menu  ·  right-click "
                                  "a slot = clear",
                                  foreground="#888", font=("Segoe UI", 8))
        self.mix_hint.pack(anchor="w", pady=(2, 0))
        lk = locks.load_locks()
        full = sum(1 for f in self._chars if locks.count_locked(f, lk) == 6)
        self.mix_count_lbl = ttk.Label(self.mix_bar, foreground="#555",
                                       text=f"{full}/{len(self._chars)} characters "
                                            "have all six picked")
        self.mix_count_lbl.pack(side="right")
        self._mix_highlight()

    def _mix_highlight(self):
        """Ring the slot that holds the palette you're viewing."""
        cells = getattr(self, "_mix_cells", {})
        if not cells:
            return
        cur = self._mix_current_file()
        for b, (cell, fn) in cells.items():
            try:
                on = bool(fn) and fn == cur
                cell.config(highlightbackground="#d6a014" if on else "#bbbbbb",
                            highlightthickness=3 if on else 2)
            except tk.TclError:
                pass

    def _mix_set(self, btn, fn):
        """Put `fn` in `btn` (None clears). A palette already in another slot
        swaps places with what was here, so a palette is never used twice."""
        char = self._cur_char
        if not char:
            return
        if fn is not None and not self._mix_allowed(char, fn):
            return
        picks = locks.char_locks(char)
        old = picks.get(btn)
        if fn is not None:
            other = next((b for b, f in picks.items() if f == fn and b != btn), None)
            if other:
                picks[other] = old
        picks[btn] = fn
        locks.set_char_locks(char, picks)
        before = set(self._mix_assigned)
        self._mix_assigned = self._mix_files(char)
        for f in before | set(self._mix_assigned):
            if f in getattr(self, "_grid_cells", {}):
                self._grid_paint(f)
        self._mix_build()

    def _mix_allowed(self, char, fn):
        """Only KEPT palettes go in the mix (custom drop-ins must also be
        valid) - the mix is your curated set. Explains why when refused."""
        v = palettes.load_verdicts().get(palettes.key_for(char, fn))
        why = None
        if v != palettes.KEEP:
            why = ("rejected" if v == palettes.REJECT else "not reviewed yet") + \
                  " - press Y to keep it first"
        elif fn.startswith("custom/"):
            ok, reason = palettes.validate_custom(char, fn)
            if not ok:
                why = f"invalid custom sheet ({reason})"
        if why:
            if getattr(self, "mix_hint", None):
                self.mix_hint.config(text=f"Only kept palettes can go in the mix: "
                                     f"{why}.", foreground=self.REJECT_COLOR)
            self.root.bell()
            return False
        return True

    def _mix_assign(self, i):
        """Key 1-6: put the viewed palette in that slot (again = take it out)."""
        fn = self._mix_pick_file()
        if not fn or not self._cur_char:
            return "break"
        btn = locks.BUTTONS[i]
        cur = locks.char_locks(self._cur_char).get(btn)
        self._mix_set(btn, None if cur == fn else fn)
        return "break"

    def _mix_pick_file(self):
        """The ONE palette a slot click / 1-6 key uses: the most recently
        selected thumbnail in Grid view (last click wins), else the viewed one."""
        if self.view_var.get() == "grid":
            sel = [f for f in getattr(self, "_grid_sel", []) if f in self.pal_files]
            if sel:
                return sel[-1]
        return self._mix_current_file()

    # -- slot press / drag-to-reorder / click --------------------------------
    def _mix_slot_at(self, x_root, y_root):
        """The mix slot (button name) under a screen point, or None."""
        w = self.root.winfo_containing(x_root, y_root)
        while w is not None:
            b = self._mix_widget_btn.get(str(w))
            if b:
                return b
            w = w.master
        return None

    def _mix_press(self, event, btn):
        self._mix_drag = {"src": btn, "x": event.x_root, "y": event.y_root,
                          "moved": False, "shift": bool(event.state & 0x0001),
                          "over": None}
        return "break"

    def _mix_motion(self, event):
        d = getattr(self, "_mix_drag", None)
        if not d:
            return
        if not d["moved"] and abs(event.x_root - d["x"]) + abs(event.y_root - d["y"]) < 8:
            return
        src_fn = self._mix_cells.get(d["src"], (None, None))[1]
        if not src_fn:                    # nothing to move from an empty slot
            return
        if not d["moved"]:
            d["moved"] = True
            self.root.config(cursor="fleur")
        over = self._mix_slot_at(event.x_root, event.y_root)
        if over != d["over"]:
            d["over"] = over
            for b, (cell, _fn) in self._mix_cells.items():
                try:
                    if b == over and b != d["src"]:
                        cell.config(highlightbackground="#2b6cb0",
                                    highlightthickness=3)
                    elif b != d["src"]:
                        cell.config(highlightbackground="#bbbbbb",
                                    highlightthickness=2)
                except tk.TclError:
                    pass

    def _mix_release(self, event):
        d, self._mix_drag = getattr(self, "_mix_drag", None), None
        self.root.config(cursor="")
        if not d:
            return
        if d["moved"]:                    # drag: drop onto another slot = swap
            tgt = self._mix_slot_at(event.x_root, event.y_root)
            if tgt and tgt != d["src"]:
                self._mix_swap(d["src"], tgt)
            else:
                self._mix_highlight()
            return
        self._mix_slot_click(d["src"], replace=d["shift"])

    def _mix_swap(self, a, b):
        """Swap two slots (a palette can move into an empty slot too)."""
        char = self._cur_char
        if not char or a == b:
            return
        picks = locks.char_locks(char)
        picks[a], picks[b] = picks.get(b), picks.get(a)
        locks.set_char_locks(char, picks)
        before = set(self._mix_assigned)
        self._mix_assigned = self._mix_files(char)
        for f in before | set(self._mix_assigned):
            if f in getattr(self, "_grid_cells", {}):
                self._grid_paint(f)       # badges show the new buttons
        self._mix_build()

    def _mix_slot_click(self, btn, replace=False):
        """Click a slot. Filled: show its palette in the gallery. Empty (or
        Shift+click a filled one): put the selected palette there - with
        several selected, only the LAST one you selected goes in."""
        here = locks.char_locks(self._cur_char).get(btn) if self._cur_char else None
        if here and not replace:
            self._mix_goto(btn)
            return
        fn = self._mix_pick_file()
        if not fn or fn == here:
            self._mix_goto(btn)
            return
        self._mix_set(btn, fn)

    def _mix_menu(self, event, fn):
        """Right-click menu on a palette: put it in a mix slot / take it out."""
        if not self._cur_char or not fn:
            return "break"
        picks = locks.char_locks(self._cur_char)
        m = tk.Menu(self.root, tearoff=0)
        for i, b in enumerate(locks.BUTTONS):
            mark = "  (here now)" if picks.get(b) == fn else \
                   ("  - replaces current" if picks.get(b) else "")
            m.add_command(label=f"Put in {b}  ({i + 1}){mark}",
                          command=lambda bb=b: self._mix_set(bb, fn))
        cur = self._mix_assigned.get(fn)
        if cur:
            m.add_separator()
            m.add_command(label=f"Remove from mix ({cur})",
                          command=lambda bb=cur: self._mix_set(bb, None))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()
        return "break"

    def _mix_list_menu(self, event):
        """Right-click in the Image view's file list: pick that row, then menu."""
        i = self.file_list.nearest(event.y)
        if 0 <= i < len(self.pal_files):
            self.file_list.selection_clear(0, "end")
            self.file_list.selection_set(i)
            self.file_list.activate(i)
            self._show_current()
            return self._mix_menu(event, self.pal_files[i])
        return "break"

    def _mix_goto(self, btn):
        """Show the slot's palette: scroll the grid to it (centred, with a brief
        flash) or select it in the Image view. If a filter is hiding it, the
        filter is turned off so it can be shown."""
        fn = locks.char_locks(self._cur_char).get(btn) if self._cur_char else None
        if not fn:
            return
        if fn not in self.pal_files and self.hiderej_var.get():
            self.hiderej_var.set(False)              # it's hidden as rejected
            self._load_char(self._cur_char)
        if fn not in self.pal_files:
            if getattr(self, "mix_hint", None):
                self.mix_hint.config(text=f"{btn}'s palette file is missing: {fn}",
                                     foreground=self.REJECT_COLOR)
            return
        if self.view_var.get() == "grid":
            self._grid_focus(fn, scroll=False)
            self._grid_scroll_center(fn)
            self._grid_flash(fn)
            self.gal_canvas.focus_set()
        else:
            j = self.pal_files.index(fn)
            self.file_list.selection_clear(0, "end")
            self.file_list.selection_set(j)
            self.file_list.activate(j)
            self.file_list.see(j)
            self._show_current()
            self.file_list.focus_set()

    def _grid_scroll_center(self, fn):
        """Scroll the grid so this thumbnail sits in the middle of the view."""
        entry = self._grid_cells.get(fn)
        if not entry:
            return
        cell = entry[0]
        self.gal_inner.update_idletasks()
        total = max(1, self.gal_inner.winfo_height())
        view = max(1, self.gal_canvas.winfo_height())
        y = cell.winfo_y() + cell.winfo_height() / 2 - view / 2
        self.gal_canvas.yview_moveto(max(0.0, min(1.0, y / total)))

    def _grid_flash(self, fn, n=6):
        """Blink a thumbnail's border gold a few times so the eye finds it."""
        entry = self._grid_cells.get(fn)
        if not entry:
            return
        cell = entry[0]

        def step(k):
            try:
                if k >= n:
                    self._grid_restyle(fn)
                    return
                c = "#d6a014" if k % 2 == 0 else self.FOCUS_COLOR
                cell.config(highlightbackground=c, highlightcolor=c,
                            highlightthickness=6)
                self.root.after(140, lambda: step(k + 1))
            except tk.TclError:
                pass
        step(0)

    def _mix_clear_all(self):
        char = self._cur_char
        if not char or not any(locks.char_locks(char).values()):
            return
        if not messagebox.askyesno("Clear mix", f"Clear all six picks for {char}? "
                                   "Their buttons go back to random."):
            return
        locks.clear_char(char)
        before = set(self._mix_assigned)
        self._mix_assigned = {}
        for f in before:
            if f in getattr(self, "_grid_cells", {}):
                self._grid_paint(f)
        self._mix_build()

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
        self._mix_highlight()

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
                    w.bind("<Button-3>", lambda e, f=fn: self._mix_menu(e, f))
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
        mix_btn = self._mix_assigned.get(fn)
        if fn not in self._grid_sel and not mix_btn:
            return ImageTk.PhotoImage(base.convert("RGB"))
        im = base.convert("RGB").copy()
        d = ImageDraw.Draw(im)
        r = max(8, min(im.size) // 10)
        m = r + 5
        if fn in self._grid_sel:
            self._badge(d, im.width - m, m, r, (43, 108, 176), "check")
        if mix_btn:                              # in the mix: gold button tag
            self._mix_tag(d, 4, im.height - 4, mix_btn, max(12, r + 4))
        return ImageTk.PhotoImage(im)

    def _mix_tag(self, draw, x, y_bottom, text, size):
        """Gold 'LP'-style tag anchored at bottom-left (x, y_bottom)."""
        try:
            from PIL import ImageFont
            font = ImageFont.truetype("segoeuib.ttf", size)
        except Exception:
            font = None
        bb = draw.textbbox((0, 0), text, font=font)
        w, h = bb[2] - bb[0], bb[3] - bb[1]
        pad = max(3, size // 4)
        box = [x, y_bottom - h - 2 * pad, x + w + 2 * pad, y_bottom]
        draw.rounded_rectangle(box, radius=pad, fill=(214, 160, 20),
                               outline="white", width=2)
        draw.text((x + pad - bb[0], y_bottom - h - pad - bb[1]), text,
                  fill="white", font=font)

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
        self._mix_highlight()
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
        outer = ttk.Frame(nb); nb.add(outer, text="5. Lock Selections")
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
        cl = ttk.Button(row, text="Clear this character (random)",
                        command=self.on_clear_locks)
        cl.pack(side="left", padx=(8, 0)); self._action_widgets.append(cl)
        ca = ttk.Button(row, text="Clear ALL characters...",
                        command=self.on_clear_all_char_locks)
        ca.pack(side="left", padx=(8, 0)); self._action_widgets.append(ca)
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

        # Playlist: which stages unlocked slots draw from on every Randomize,
        # plus a one-shot fill of all 17 locks from a playlist.
        prow = ttk.Frame(t); prow.pack(fill="x", pady=(0, 8))
        ttk.Label(prow, text="Randomize stages from:").pack(side="left")
        self.splay_var = tk.StringVar()
        self.splay_combo = ttk.Combobox(prow, textvariable=self.splay_var,
                                        state="readonly", width=28)
        self.splay_combo.pack(side="left", padx=(6, 0))
        self.splay_combo.bind("<<ComboboxSelected>>",
                              lambda e: self._splay_changed())
        fb = ttk.Button(prow, text="Fill locks from playlist...",
                        command=self.on_fill_locks_from_playlist)
        fb.pack(side="left", padx=(10, 0)); self._action_widgets.append(fb)
        self.splay_info = ttk.Label(prow, text="", foreground="#666")
        self.splay_info.pack(side="left", padx=(10, 0))

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
        snk = ttk.Button(srow, text="Clear locks on stages you haven't kept...",
                         command=self.on_clear_unkept_stage_locks)
        snk.pack(side="left", padx=(8, 0)); self._action_widgets.append(snk)
        self.slock_status = ttk.Label(srow, text="", foreground="#177245")
        self.slock_status.pack(side="left", padx=(12, 0))

    def _slock_populate(self):
        """Fill every slot row's dropdown with its lockable stages + current
        lock. Ports are offered per slot when cross-slot merge is enabled, and
        only to the slots they actually fit."""
        if getattr(self, "splay_combo", None) is not None:
            self._splay_refresh()
        cfg = randomize.get_config()
        dp = bool(cfg.get("distribute_ports", False))
        # Same eligibility as Randomize: kept only, unless Setup allows
        # unreviewed items too (then everything but rejected).
        inc_unrev = bool(cfg.get("include_unreviewed", True))
        fits = None
        if dp:
            # Recompute the fit map whenever the set of port files changes (a
            # download, a drop-in, files copied in while the app is open) -
            # a stale map silently hid new ports from every slot but Training.
            sig = self._stage_port_sig()
            if getattr(self, "_slot_fits", None) is None or \
                    sig != getattr(self, "_slot_fits_sig", None):
                self._slot_fits, self._slot_fit_why = randomize.stage_fit_detail()
                self._slot_fits_sig = sig
            fits = self._slot_fits
        why_all = getattr(self, "_slot_fit_why", {}) if dp else {}
        verdicts = stagegal.load_verdicts()
        locked = stagegal.load_locks()
        allowed = lambda v: (verdicts.get(v["key"]) != "delete" if inc_unrev
                             else verdicts.get(v["key"]) == "keep")
        all_ports = [v for t, vs in stagegal.sections() if t.startswith("Ports")
                     for v in vs]
        self.slock_dead = {}
        for sid, _short, variants in stagegal.slot_lock_options(
                distribute_ports=dp, slot_fits=fits):
            box = self.slock_row_boxes.get(sid)
            if box is None:
                continue
            cur_lock = locked.get(sid)
            pool = [v for v in variants
                    if allowed(v) or v["key"] == cur_lock]   # keep an existing lock
            # Nothing just vanishes: everything that could be listed here but
            # can't be picked is shown greyed with the reason -
            #   (sign) not kept per your review setting (Stage Gallery verdict)
            #   (skull) won't fit this slot / Training-only port
            def verdict_reason(v):
                vd = verdicts.get(v["key"])
                return ("rejected in the Stage Gallery" if vd == "delete" else
                        "not reviewed - keep it in the Stage Gallery to use it")
            # Rejected stages never show (except as an existing lock, above).
            rejected = lambda v: verdicts.get(v["key"]) == "delete"
            dead = [("⊘", v, verdict_reason(v)) for v in variants
                    if not allowed(v) and not rejected(v) and v["key"] != cur_lock]
            if dp and sid != stagegal.TRAINING:
                here = {v["key"] for v in variants}
                why = why_all.get(sid, {})
                dead += [("☠", v, why.get(v["key"], "doesn't fit this slot"))
                         for v in all_ports
                         if v["key"] not in here and not rejected(v)]
            dispmap, values, deadmap = {}, ["(random)"], {}
            for v in pool:
                kind = stagegal.KIND_LABEL.get(v["kind"], v["kind"])
                disp = f"{stagegal.display_name(v)} — {v['author']} [{kind}]"
                if not allowed(v):              # the current lock isn't kept
                    disp = "⚠ " + disp + " — locked, but " + (
                        "rejected" if verdicts.get(v["key"]) == "delete"
                        else "not reviewed")
                while disp in dispmap:       # de-dupe identical labels
                    disp += " ."
                dispmap[disp] = v
                values.append(disp)
            # most useful first: not-reviewed (one click away in the gallery),
            # then won't-fit, then the ones you rejected
            rank = lambda x: (0 if x[0] == "⊘" and
                              verdicts.get(x[1]["key"]) != "delete"
                              else 1 if x[0] == "☠" else 2)
            for icon, v, reason in sorted(
                    dead, key=lambda x: (rank(x), stagegal.display_name(x[1]))):
                kind = stagegal.KIND_LABEL.get(v["kind"], v["kind"])
                disp = (f"{icon} {stagegal.display_name(v)} — {v['author']} "
                        f"[{kind}] — can't use: {reason}")
                while disp in deadmap or disp in dispmap:
                    disp += " ."
                deadmap[disp] = (v, reason)
                values.append(disp)
            box["values"] = values
            box.configure(postcommand=lambda s=sid: self.root.after_idle(
                lambda: self._slock_gray(s)))
            self.slock_row_maps[sid] = dispmap
            self.slock_dead[sid] = deadmap
            cur = locked.get(sid)
            self.slock_row_vars[sid].set(
                next((d for d, v in dispmap.items() if v["key"] == cur),
                     "(random)"))

    @staticmethod
    def _stage_port_sig():
        """Cheap fingerprint of the port files on disk (key, size, mtime)."""
        sig = []
        for title, vs in stagegal.sections():
            if not title.startswith("Ports"):
                continue
            for v in vs:
                p = v.get("pol_path")
                try:
                    st = os.stat(p) if p else None
                except OSError:
                    st = None
                sig.append((v["key"], st.st_size if st else 0,
                            int(st.st_mtime) if st else 0))
        return tuple(sorted(sig))

    def _slock_gray(self, sid):
        """Grey out the skull rows in a stage dropdown's open list. (A ttk
        Combobox can't style items, but its popdown is a plain Tk listbox.)"""
        box = self.slock_row_boxes.get(sid)
        dead = getattr(self, "slock_dead", {}).get(sid) or {}
        if box is None or not dead:
            return
        try:
            pop = box.tk.call("ttk::combobox::PopdownWindow", str(box))
            lb = f"{pop}.f.l"
            for i, label in enumerate(box.cget("values")):
                if label in dead:
                    box.tk.call(lb, "itemconfigure", i, "-foreground", "#9a9a9a",
                                "-selectforeground", "#e6e6e6")
        except tk.TclError:
            pass

    def _slock_row_changed(self, sid):
        label = self.slock_row_vars[sid].get()
        dead = getattr(self, "slock_dead", {}).get(sid, {}).get(label)
        if dead:                       # a skull row: refuse, keep the old choice
            v, reason = dead
            cur = stagegal.load_locks().get(sid)
            self.slock_row_vars[sid].set(next(
                (d for d, x in self.slock_row_maps.get(sid, {}).items()
                 if x["key"] == cur), "(random)"))
            self.slock_status.config(
                text=f"STG {sid}: {stagegal.display_name(v)} can't go in this "
                     f"slot - {reason}.", foreground="#b03030")
            self.root.bell()
            return
        self.slock_status.config(foreground="#177245")
        v = self.slock_row_maps.get(sid, {}).get(label)
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

    # ---- stage playlists (Lock Selections side) ----
    SPLAY_ALL = "All kept stages"

    def _splay_refresh(self):
        """Sync the 'Randomize stages from' box with the playlists on disk."""
        pls = stagegal.load_playlists()["playlists"]
        self.splay_combo["values"] = [self.SPLAY_ALL] + sorted(pls, key=str.lower)
        cur = randomize.get_config().get("stage_playlist")
        if cur and cur not in pls:
            cur = None                     # deleted playlist -> all kept
            randomize.set_config_key("stage_playlist", None)
        self.splay_var.set(cur or self.SPLAY_ALL)
        n = len(pls.get(cur, [])) if cur else 0
        self.splay_info.config(
            text=(f"{n} stage(s) - every Randomize draws unlocked slots from it"
                  if cur else "make playlists in the Stage Gallery"
                  if not pls else ""))

    def _splay_changed(self):
        name = self.splay_var.get()
        randomize.set_config_key("stage_playlist",
                                 None if name == self.SPLAY_ALL else name)
        self._splay_refresh()
        self.logln("Stages now randomize from: " + name + ".")

    def _splay_plan(self, name):
        """{slot: (variant, how)} filling every slot from a playlist, the way
        Randomize does: tightest slots first, fresh stages before repeats, the
        slot's stock stage where nothing in the playlist fits. how is
        'playlist' or 'stock'."""
        import random as _r
        keys = set(stagegal.playlist_keys(name))
        verdicts = stagegal.load_verdicts()
        dp = bool(randomize.get_config().get("distribute_ports", False))
        self._slock_populate()                 # makes sure the fit map is fresh
        opts = {sid: vs for sid, _s, vs in stagegal.slot_lock_options(
            distribute_ports=dp, slot_fits=self._slot_fits if dp else None)}
        cands = {sid: [v for v in vs if v["key"] in keys
                       and verdicts.get(v["key"]) != "delete"]
                 for sid, vs in opts.items()}
        order = sorted((s for s in opts if s != stagegal.TRAINING),
                       key=lambda s: (len(cands[s]), _r.random()))
        if stagegal.TRAINING in opts:
            order.append(stagegal.TRAINING)
        used, plan = set(), {}
        for sid in order:
            fresh = [v for v in cands[sid] if v["key"] not in used]
            pool = fresh or cands[sid]
            if pool:
                v = _r.choice(pool)
                used.add(v["key"])
                plan[sid] = (v, "playlist")
            else:
                d = next((v for v in opts[sid] if v["key"] == f"d_{sid}"), None)
                plan[sid] = (d, "stock")
        return plan

    def on_fill_locks_from_playlist(self):
        name = self.splay_var.get()
        pls = stagegal.load_playlists()["playlists"]
        if name == self.SPLAY_ALL or name not in pls:
            if not pls:
                messagebox.showinfo("No playlists yet",
                                    "Make a playlist in the Stage Gallery first "
                                    "(Playlist > New, then P to add stages).")
                return
            name = stagegal.load_playlists()["editing"] or sorted(pls)[0]
        if not pls.get(name):
            messagebox.showinfo("Empty playlist", f"'{name}' has no stages yet.")
            return
        plan = self._splay_plan(name)
        from_pl = {s: v for s, (v, how) in plan.items() if how == "playlist"}
        stock = sorted(s for s, (v, how) in plan.items() if how == "stock")
        distinct = len({v["key"] for v in from_pl.values()})
        lines = "\n".join(
            f"  STG {s}: " + (stagegal.display_name(v) if v else "(stock)")
            + ("" if how == "playlist" else "  - stock, nothing in the playlist fits")
            for s, (v, how) in sorted(plan.items()))
        if not messagebox.askyesno(
                f"Fill stage locks from '{name}'",
                f"{len(from_pl)} slot(s) from the playlist ({distinct} different "
                f"stage(s)), {len(stock)} on stock.\n\n{lines}\n\n"
                "This replaces your current stage locks. Continue?"):
            return
        for sid, (v, _how) in plan.items():
            stagegal.set_lock(sid, v["key"] if v else None)
        self._slock_populate()
        self.slock_status.config(
            text=f"Locked {len(plan)} slots from '{name}' ({distinct} different "
                 f"stages, {len(stock)} stock).", foreground="#177245")
        self.logln(f"Stage locks filled from playlist '{name}':\n" + lines)

    def on_clear_unkept_stage_locks(self):
        """Clear the stage locks that point at a stage your review setting
        doesn't allow (unreviewed, when only kept stages are used; or rejected).
        Such locks predate a verdict change - Randomize still honours them."""
        inc_unrev = bool(randomize.get_config().get("include_unreviewed", True))
        verdicts = stagegal.load_verdicts()
        names = {v["key"]: stagegal.display_name(v)
                 for _t, vs in stagegal.sections() for v in vs}
        bad = []
        for sid, key in sorted(stagegal.load_locks().items()):
            if not key:
                continue
            vd = verdicts.get(key)
            if vd == "delete" or (vd != "keep" and not inc_unrev):
                bad.append((sid, key, "rejected" if vd == "delete" else "not reviewed"))
        if not bad:
            self.slock_status.config(text="Every stage lock points at a kept stage.",
                                     foreground="#177245")
            return
        lines = "\n".join(f"  STG {s}: {names.get(k, k)} ({why})" for s, k, why in bad)
        if not messagebox.askyesno(
                "Clear locks on stages you haven't kept",
                f"These slots are locked to stages you haven't kept:\n\n{lines}\n\n"
                "Clear these locks (those slots go back to random)?\n\n"
                "Prefer to keep them? Click No, then mark them Keep in the "
                "Stage Gallery instead."):
            return
        for sid, _k, _w in bad:
            stagegal.set_lock(sid, None)
        self._slock_populate()
        self.slock_status.config(text=f"Cleared {len(bad)} lock(s) on stages you "
                                 "hadn't kept.", foreground="#177245")
        self.logln("Cleared stage locks on non-kept stages: "
                   + ", ".join(f"{s} ({names.get(k, k)})" for s, k, _w in bad))

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

    def on_clear_all_char_locks(self):
        """Clear every character's palette locks (all buttons back to random).
        This also empties every character's mix in the Palette Gallery."""
        lk = locks.load_locks()
        n = sum(1 for v in lk.values() if v)
        if not n:
            self.lock_status.config(text="No palette locks to clear.")
            return
        chars = len({f for (f, _b), v in lk.items() if v})
        if not messagebox.askyesno(
                "Clear ALL palette locks",
                f"Clear {n} palette lock(s) across {chars} character(s)?\n\n"
                "Every button goes back to random, and every character's mix in "
                "the Palette Gallery is emptied. This can't be undone.",
                icon="warning"):
            return
        try:
            locks.clear_all()
        except Exception as e:
            messagebox.showerror("Could not save locks", str(e))
            return
        self._lock_load()
        if getattr(self, "_cur_char", None):
            self._mix_assigned = {}
            self._load_char(self._cur_char)
        self.lock_status.config(text=f"Cleared {n} locks - all characters random.")
        self.logln(f"Cleared all palette locks ({n} across {chars} characters).")

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

        # ---- playlists: named sets of stages that randomize together ----
        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(left, text="Playlist", font=("Segoe UI", 9, "bold")
                  ).pack(anchor="w")
        plr = ttk.Frame(left); plr.pack(anchor="w", fill="x", pady=(2, 0))
        self.stg_pl_var = tk.StringVar()
        self.stg_pl_combo = ttk.Combobox(plr, textvariable=self.stg_pl_var,
                                         state="readonly", width=20)
        self.stg_pl_combo.pack(side="left")
        self.stg_pl_combo.bind("<<ComboboxSelected>>",
                               lambda e: self._stg_pl_pick())
        plb = ttk.Frame(left); plb.pack(anchor="w", pady=(4, 0))
        for txt, cmd in (("New...", self._stg_pl_new), ("Rename...", self._stg_pl_rename),
                         ("Delete", self._stg_pl_delete)):
            ttk.Button(plb, text=txt, width=8, command=cmd).pack(side="left",
                                                                 padx=(0, 4))
        self.stg_pl_btn = ttk.Button(left, text="♫ Add to playlist (P)",
                                     command=self._stg_pl_toggle)
        self.stg_pl_btn.pack(anchor="w", pady=(4, 0))
        self.stg_pl_lbl = ttk.Label(left, text="", foreground="#666",
                                    wraplength=225, justify="left")
        self.stg_pl_lbl.pack(anchor="w")
        self._stg_pl_keys = set()

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(left, text="Show", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.stg_hiderej_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(left, text="Hide rejected", variable=self.stg_hiderej_var,
                        command=self._stage_refilter).pack(anchor="w")
        self.stg_shownew_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(left, text="Show new only", variable=self.stg_shownew_var,
                        command=self._stage_refilter).pack(anchor="w")
        self.stg_plonly_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(left, text="Playlist only", variable=self.stg_plonly_var,
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
        self.stg_canvas.bind("<p>", lambda e: self._stg_pl_toggle())
        self.stg_canvas.bind("<P>", lambda e: self._stg_pl_toggle())
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
        self._stg_pl_refresh()
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
        self._stg_pl_keys = set(self._stg_pl_current_keys())
        if getattr(self, "stg_plonly_var", None) is not None and self.stg_plonly_var.get():
            shown = [v for v in shown if v["key"] in self._stg_pl_keys]
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
        clickable = [cell, inner, pic]
        if v["key"] in getattr(self, "_stg_pl_keys", ()):   # in the playlist
            badge = tk.Label(inner, text=" ♫ ", background="#d6a014",
                             foreground="white", font=("Segoe UI", 10, "bold"))
            badge.place(x=6, y=6)
            clickable.append(badge)
        self._stg_cards[v["key"]] = (cell, inner)
        for w in clickable:
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
        self._stg_pl_update_btn()

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

    # ---- playlists (Stage Gallery side) ----
    def _stg_pl_current_keys(self):
        name = stagegal.load_playlists()["editing"]
        return stagegal.playlist_keys(name) if name else []

    def _stg_pl_refresh(self):
        """Sync the playlist box, the add/remove button and the count."""
        d = stagegal.load_playlists()
        names = sorted(d["playlists"], key=str.lower)
        self.stg_pl_combo["values"] = names
        self.stg_pl_var.set(d["editing"] or "")
        self._stg_pl_keys = set(d["playlists"].get(d["editing"]) or [])
        self._stg_pl_update_btn()

    def _stg_pl_update_btn(self):
        name = self.stg_pl_var.get()
        v = getattr(self, "_stg_cur_variant", None)
        if not name:
            self.stg_pl_btn.config(text="♫ Add to playlist (P)", state="disabled")
            self.stg_pl_lbl.config(text="Click New... to start a playlist (e.g. "
                                   "'Retextures', '2D stages').")
            return
        inn = bool(v) and v["key"] in self._stg_pl_keys
        self.stg_pl_btn.config(state="normal" if v else "disabled",
                               text=("✓ In playlist - remove (P)" if inn
                                     else "♫ Add to playlist (P)"))
        self.stg_pl_lbl.config(text=f"'{name}': {len(self._stg_pl_keys)} stage(s)")

    def _stg_pl_pick(self):
        stagegal.playlist_set_editing(self.stg_pl_var.get())
        self._stg_pl_refresh()
        self._stage_cards_build()

    def _stg_pl_new(self):
        name = simpledialog.askstring("New playlist", "Playlist name:",
                                      parent=self.root)
        name = (name or "").strip()
        if not name:
            return
        if name in stagegal.load_playlists()["playlists"]:
            messagebox.showinfo("Already exists", f"There's already a playlist "
                                f"called '{name}'.")
            return
        stagegal.playlist_create(name)
        self._stg_pl_refresh()
        self._stage_cards_build()
        self.logln(f"Created stage playlist '{name}'. Select stages and press P "
                   "to add them.")

    def _stg_pl_rename(self):
        old = self.stg_pl_var.get()
        if not old:
            return
        new = simpledialog.askstring("Rename playlist", "New name:",
                                     initialvalue=old, parent=self.root)
        new = (new or "").strip()
        if not new or new == old:
            return
        if not stagegal.playlist_rename(old, new):
            messagebox.showinfo("Can't rename", f"'{new}' is already taken.")
            return
        if randomize.get_config().get("stage_playlist") == old:
            randomize.set_config_key("stage_playlist", new)   # keep it active
        self._stg_pl_refresh()

    def _stg_pl_delete(self):
        name = self.stg_pl_var.get()
        if not name:
            return
        n = len(stagegal.playlist_keys(name))
        if not messagebox.askyesno("Delete playlist",
                                   f"Delete '{name}' ({n} stage(s))? The stages "
                                   "themselves aren't touched."):
            return
        stagegal.playlist_delete(name)
        if randomize.get_config().get("stage_playlist") == name:
            randomize.set_config_key("stage_playlist", None)  # back to all kept
        self._stg_pl_refresh()
        self._stage_cards_build()

    def _stg_pl_toggle(self):
        """P / button: add the selected stage to the playlist or take it out.
        Adding marks an unreviewed stage Kept (it's one you like); a rejected
        stage can't be added."""
        name = self.stg_pl_var.get()
        v = getattr(self, "_stg_cur_variant", None)
        if not v:
            return "break"
        if not name:
            self._stg_pl_new()
            name = self.stg_pl_var.get()
            if not name:
                return "break"
        verdict = stagegal.load_verdicts().get(v["key"])
        if v["key"] not in self._stg_pl_keys:
            if verdict == stagegal.REJECT:
                self.stg_pl_lbl.config(text="Rejected stages can't go in a "
                                       "playlist - press Y to keep it first.")
                self.root.bell()
                return "break"
            if verdict is None:            # liking it enough to add = keep
                stagegal.set_verdict(v["key"], stagegal.KEEP)
                self._stage_new.discard(v["key"])
                self._refresh_verdict_lbl()
                self._stage_update_tree_row()
                self._update_stage_counts()
        now = stagegal.playlist_toggle(name, v["key"])
        self._stg_pl_refresh()
        self._stage_cards_build()
        if v["key"] in self._stg_cards:
            self._stage_select_variant(v)
        self.stg_canvas.focus_set()
        self.logln(f"{'Added' if now else 'Removed'} {v['name']} "
                   f"{'to' if now else 'from'} playlist '{name}'.")
        return "break"

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

    # ------------------------------------------------------------------- FX tab
    def _tab_fx(self, nb):
        t = ttk.Frame(nb, padding=10); nb.add(t, text="4. FX")
        self._fx_tab = t

        # Top controls: global mode + chaos seed + running summary.
        top = ttk.Frame(t); top.pack(fill="x", pady=(0, 6))
        ttk.Label(top, text="Effect recolor", font=("Segoe UI", 11, "bold")
                  ).pack(side="left")
        ttk.Label(top, text="Mode:").pack(side="left", padx=(16, 4))
        cfg = randomize.get_config()
        try:
            fxm = randomize.fx_module()
            modes = list(fxm.FX_MODES)
            cur_mode = fxm.canon_mode(cfg.get("fx_mode", "Default"))   # Match -> Shades
            self._fx_grad_levels = list(fxm.GRADIENT_LEVELS)
            self._fx_neon_names = list(fxm.NEON_COLORS)
        except Exception:
            modes = ["Default", "Stock", "Shades", "Opposite", "Rainbow",
                     "Rainbow Rev", "Grayscale", "Invert", "Chaos", "Thermal",
                     "Night Vision", "Neon"]
            cur_mode = cfg.get("fx_mode", "Default")
            self._fx_grad_levels = ["Off", "Normal", "Strong"]
            self._fx_neon_names = ["Auto", "Pink", "Blue", "Lime", "Yellow",
                                   "Orange", "Purple", "Red"]
        if cur_mode not in modes:
            cur_mode = "Default"
        # fx_mode_var holds the INTERNAL mode name (config/engine value); the
        # dropdown shows a clearer label ("Default" -> "Off").
        self.fx_mode_var = tk.StringVar(value=cur_mode)
        self._fx_all_modes = modes
        # modes a palette can be LOCKED to (exclude Default/"no recolor").
        self._fx_modes = [m for m in modes if m != "Default"]
        self.fx_mode_disp = tk.StringVar(value=self._fx_disp(cur_mode))
        self.fx_mode_var.trace_add(
            "write", lambda *a: self.fx_mode_disp.set(
                self._fx_disp(self.fx_mode_var.get())))
        self.fx_mode_cb = cb = ttk.Combobox(
            top, textvariable=self.fx_mode_disp,
            values=[self._fx_disp(m) for m in modes], state="readonly", width=16)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: (
            self.fx_mode_var.set(self._fx_val(self.fx_mode_disp.get())),
            self._fx_mode_change()))
        # Focus-based arrows (like any dropdown): while this box has focus, Up/Down
        # cycle the global mode; Left/Right cycle the previewed color button.
        cb.bind("<Up>", lambda e: self._fx_cycle_mode(-1))
        cb.bind("<Down>", lambda e: self._fx_cycle_mode(1))
        cb.bind("<Left>", lambda e: self._fx_lr("left"))
        cb.bind("<Right>", lambda e: self._fx_lr("right"))
        # Gradient boost strength (keeps shading readable after a recolor).
        ttk.Label(top, text="Gradient:").pack(side="left", padx=(12, 4))
        g0 = cfg.get("fx_gradient") or "Normal"
        self.fx_grad_var = tk.StringVar(
            value=g0 if g0 in self._fx_grad_levels else "Normal")
        gb = self.fx_grad_cb = ttk.Combobox(
            top, textvariable=self.fx_grad_var, values=self._fx_grad_levels,
            state="readonly", width=7)
        gb.pack(side="left")
        gb.bind("<<ComboboxSelected>>", lambda e: self._fx_grad_change())
        gb.bind("<Up>", lambda e: self._fx_cycle_var(self.fx_grad_var,
                                                     self._fx_grad_levels, -1,
                                                     self._fx_grad_change))
        gb.bind("<Down>", lambda e: self._fx_cycle_var(self.fx_grad_var,
                                                       self._fx_grad_levels, 1,
                                                       self._fx_grad_change))
        gb.bind("<Left>", lambda e: self._fx_lr("left"))
        gb.bind("<Right>", lambda e: self._fx_lr("right"))
        # Neon tube colour (control shown with Neon - see _fx_build_basebar).
        n0 = cfg.get("fx_neon_color") or "Auto"
        self.fx_neon_var = tk.StringVar(
            value=n0 if n0 in self._fx_neon_names else "Auto")
        # Global Chaos seed (the control itself is placed in the base-color area
        # when the global mode is Chaos — see _fx_render_char).
        self.fx_seed_var = tk.IntVar(value=int(cfg.get("fx_chaos_seed", 0) or 0))
        ttk.Button(top, text="Reset FX", command=self._fx_reset_all
                   ).pack(side="left", padx=(12, 0))
        # Show the actual palette currently loaded in the game (reads the live arc),
        # so Shades can be validated against the skins that really landed.
        self.fx_loaded_var = tk.BooleanVar(value=False)
        self.fx_loaded_btn = ttk.Button(top, text="Load from game",
                                        command=self._fx_toggle_loaded)
        self.fx_loaded_btn.pack(side="left", padx=(12, 0))
        # Global color: when set, Shades anchors to it for EVERY character (a
        # ramp of this color) instead of each skin's own base color.
        self.fx_gcolor = cfg.get("fx_global_color") or None
        self.fx_gcolor_bar = ttk.Frame(top)
        self.fx_gcolor_bar.pack(side="left", padx=(12, 0))
        self._fx_build_gcolor()
        self.fx_summary = ttk.Label(top, text="", foreground="#555")
        self.fx_summary.pack(side="right")

        ttk.Label(t, text="On = this effect is recolored on Randomize.  Off = left "
                  "to the authored skin.  Excluded effects are hidden.  Preview uses "
                  "stock colors as the base; in game Shades follows each applied "
                  "skin.", foreground="#666", wraplength=1120, justify="left"
                  ).pack(fill="x", pady=(0, 6))

        pw = ttk.PanedWindow(t, orient="horizontal"); pw.pack(fill="both", expand=True)

        # Left: character list (with on/total count) + bulk actions.
        left = ttk.Frame(pw, padding=(0, 0, 6, 0))
        tw = ttk.Frame(left); tw.pack(fill="both", expand=True)
        self.fx_tree = ttk.Treeview(tw, columns=("n",), show="tree",
                                    selectmode="browse", height=22)
        self.fx_tree.column("#0", width=176, stretch=True)
        self.fx_tree.column("n", width=52, anchor="e", stretch=False)
        fv = ttk.Scrollbar(tw, orient="vertical", command=self.fx_tree.yview)
        self.fx_tree.configure(yscrollcommand=fv.set)
        fv.pack(side="right", fill="y")
        self.fx_tree.pack(side="left", fill="both", expand=True)
        self.fx_tree.bind("<<TreeviewSelect>>", lambda e: self._fx_render_char())
        # When the char list has focus, its own Up/Down move the selection
        # natively; Left/Right still cycle the previewed color button.
        self.fx_tree.bind("<Left>", lambda e: self._fx_lr("left"))
        self.fx_tree.bind("<Right>", lambda e: self._fx_lr("right"))
        bb = ttk.Frame(left); bb.pack(fill="x", pady=(6, 0))
        ttk.Button(bb, text="All on", width=7,
                   command=lambda: self._fx_bulk(True)).pack(side="left")
        ttk.Button(bb, text="All off", width=7,
                   command=lambda: self._fx_bulk(False)).pack(side="left", padx=(4, 0))
        ttk.Button(bb, text="Defaults", width=9,
                   command=lambda: self._fx_bulk(None)).pack(side="left", padx=(4, 0))
        bb2 = ttk.Frame(left); bb2.pack(fill="x", pady=(4, 0))
        ttk.Button(bb2, text="Reset character", width=16,
                   command=self._fx_reset_char).pack(side="left")
        pw.add(left, weight=0)

        # Right: base editor + a big preview panel + a compact, selectable list.
        right = ttk.Frame(pw)
        self.fx_head = ttk.Label(right, text="Effect randomization",
                                 font=("Segoe UI", 11, "bold"))
        self.fx_head.pack(anchor="w")
        self.fx_basebar = ttk.Frame(right); self.fx_basebar.pack(fill="x", pady=(2, 0))
        self.fx_note = ttk.Label(right, text="", foreground="#888")
        self.fx_note.pack(anchor="w", pady=(0, 4))
        self.fx_protbar = ttk.Frame(right)       # 'protected' banner (if any)
        self.fx_protbar.pack(fill="x")
        self._fx_protected = {}

        # Preview panel on top (tall, grows with the window) + the list below.
        # The preview's height tracks the right pane (see _fx_fit_preview) so the
        # sprites get real room instead of being squeezed.
        prev = ttk.LabelFrame(right, text="Preview", padding=6)
        prev.pack(fill="x", pady=(4, 0))
        self.fx_prev_title = ttk.Label(prev, text="Select an effect below.",
                                       font=("Segoe UI", 9, "bold"), wraplength=1000,
                                       justify="left")
        self.fx_prev_title.pack(anchor="w")
        self.fx_prev_tool = ttk.Frame(prev)      # per-palette lock controls
        self.fx_prev_tool.pack(fill="x", pady=(3, 0))
        self.fx_prev_imgs = tk.Frame(prev, background="#ffffff", height=260)
        self.fx_prev_imgs.pack(fill="both", expand=True, pady=(4, 0))
        self.fx_prev_imgs.pack_propagate(False)
        self.fx_prev_imgs.bind("<Configure>", self._fx_on_prev_resize)
        right.bind("<Configure>", lambda e: self._fx_fit_preview(e.height))

        body = ttk.Frame(right); body.pack(fill="both", expand=True, pady=(8, 0))
        self.fx_canvas = tk.Canvas(body, highlightthickness=0, background="#ffffff")
        fvsb = ttk.Scrollbar(body, orient="vertical", command=self.fx_canvas.yview)
        self.fx_canvas.configure(yscrollcommand=fvsb.set)
        fvsb.pack(side="right", fill="y")
        self.fx_canvas.pack(side="left", fill="both", expand=True)
        self.fx_inner = ttk.Frame(self.fx_canvas)
        self.fx_win = self.fx_canvas.create_window((0, 0), window=self.fx_inner,
                                                   anchor="nw")
        self.fx_inner.bind("<Configure>", lambda e: self.fx_canvas.configure(
            scrollregion=self.fx_canvas.bbox("all")))
        self.fx_canvas.bind("<Configure>", lambda e: self.fx_canvas.itemconfigure(
            self.fx_win, width=e.width))
        def _fx_enter(_e):
            self.fx_canvas.bind_all("<MouseWheel>", lambda ev:
                                    self.fx_canvas.yview_scroll(
                                        int(-ev.delta / 120), "units"))
        self.fx_canvas.bind("<Enter>", _fx_enter)
        self.fx_canvas.bind("<Leave>",
                            lambda e: self.fx_canvas.unbind_all("<MouseWheel>"))
        self.fx_canvas.configure(takefocus=1)
        # Focus-based navigation (no mouse-hover routing): clicking a control keeps
        # it selected and its own Up/Down act on it, exactly like the global Mode
        # dropdown. The effect list (canvas) gets focus when a row is clicked.
        self.fx_canvas.bind("<Up>", lambda e: self._fx_nav(-1))
        self.fx_canvas.bind("<Down>", lambda e: self._fx_nav(1))
        self.fx_canvas.bind("<Left>", lambda e: self._fx_lr("left"))
        self.fx_canvas.bind("<Right>", lambda e: self._fx_lr("right"))
        # Left/Right cycle the previewed color button from anywhere on the tab.
        self.root.bind_all("<Left>", lambda e: self._fx_lr("left"), add="+")
        self.root.bind_all("<Right>", lambda e: self._fx_lr("right"), add="+")
        pw.add(right, weight=4)

        # State: overrides map, target index, live widget vars, image refs.
        self.fx_state_map = randomize.fx_state()
        self._fx_targets = randomize.fx_targets()
        self._fx_spr = randomize.fx_sprites()     # {spr_key: (w, h, index_bytes)}
        self._fx_index = {}            # id -> (char, default_on, verdict)
        for cname, d in self._fx_targets.items():
            for s in d.get("sheets", []):
                self._fx_index[s["id"]] = (cname, s.get("default_on", False),
                                           s.get("verdict"))
        self._fx_vars = {}             # id -> BooleanVar (current char only)
        self._fx_imgs = []             # list-row PhotoImage refs (current render)
        self._fx_prev_refs = []        # preview-panel PhotoImage refs
        self._fx_roww = {}             # id -> (rowframe, label, badge, var)
        self._fx_sel_id = None         # currently previewed sheet id
        self._fx_cur = {}              # id -> sheet dict for the shown character
        self._fx_order = []            # sheet ids in displayed order (kb nav)
        self._fx_locks_map = randomize.fx_locks()   # {sheet_id: lock dict}
        self.fx_lockmode_var = tk.StringVar()
        self._fx_cur_name = None       # character currently shown
        self._fx_prev_btn = 0          # color button previewed (0=LP..5=A2)
        self._fx_btnnames = ["LP", "LK", "HP", "HK", "A1", "A2"]
        self._fx_cur_pv_body = []
        self._fx_populate_tree()

    # --- FX helpers ---------------------------------------------------------
    def _fx_eff_on(self, sid, default):
        """Effective on-state: explicit override, else the baked default."""
        return bool(self.fx_state_map.get(sid, default))

    def _fx_char_counts(self, name):
        """(on, total) non-excluded sheets; locked palettes count as on."""
        d = self._fx_targets.get(name, {})
        on = tot = 0
        for s in d.get("sheets", []):
            if s.get("verdict") == "exclude":
                continue
            tot += 1
            if s["id"] in self._fx_locks_map or self._fx_eff_on(
                    s["id"], s.get("default_on", False)):
                on += 1
        return on, tot

    def _fx_populate_tree(self):
        self.fx_tree.delete(*self.fx_tree.get_children())
        if not self._fx_targets:
            self.fx_tree.insert("", "end", text="(no FX data)", values=("",))
            self.fx_note.config(
                text="mvc2_data/fx_targets.json not found — FX catalogue unavailable.")
            return
        for name in sorted(self._fx_targets, key=str.lower):
            on, tot = self._fx_char_counts(name)
            self.fx_tree.insert("", "end", iid=name, text=name,
                                values=(f"{on}/{tot}",))
        self._fx_refresh_tree_locks()
        first = self.fx_tree.get_children()
        if first:
            self.fx_tree.selection_set(first[0])
            self.fx_tree.focus(first[0])
        self._fx_update_summary()

    def _fx_checker(self, w, h, sq=8):
        """A light checkerboard RGB image, so sparse/transparent effects read."""
        bg = PILImage.new("RGB", (max(1, w), max(1, h)), (255, 255, 255))
        dr = ImageDraw.Draw(bg)
        for y in range(0, h, sq):
            for x in range(0, w, sq):
                if ((x // sq) + (y // sq)) % 2:
                    dr.rectangle([x, y, x + sq - 1, y + sq - 1], fill=(224, 224, 224))
        return bg

    def _fx_sprite_img(self, key, colors, maxw=240, maxh=128):
        """Recolor an indexed effect sprite with `colors` and return a thumbnail
        PhotoImage over a checker background, or None if the sprite is missing."""
        spr = self._fx_spr.get(key) if key else None
        if not spr:
            return None
        w, h, idx = spr
        if len(idx) != w * h:
            return None
        pal, alpha = [], []
        for i in range(256):
            if i < len(colors):
                c = colors[i]
                pal += [c[0], c[1], c[2]]
                alpha.append(0 if (len(c) > 3 and c[3] == 0) else 255)
            else:
                pal += [0, 0, 0]
                alpha.append(255)
        im = PILImage.frombytes("P", (w, h), idx)
        im.putpalette(pal)
        im.info["transparency"] = bytes(alpha)
        im = im.convert("RGBA")
        # Scale to fill the available box (up OR down) with nearest-neighbour so
        # pixel art stays crisp. PIL's thumbnail() only shrinks, which left small
        # sprites tiny in a big panel.
        if w and h:
            scale = max(0.05, min(maxw / w, maxh / h, 12))
            im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                           PILImage.NEAREST)
        bg = self._fx_checker(im.width, im.height).convert("RGBA")
        bg.alpha_composite(im)
        ph = ImageTk.PhotoImage(bg.convert("RGB"))
        self._fx_imgs.append(ph)
        return ph

    def _fx_render_char(self):
        keep = self._fx_sel_id          # preserve selection across mode/base changes
        for w in self.fx_inner.winfo_children():
            w.destroy()
        for w in self.fx_basebar.winfo_children():
            w.destroy()
        self._fx_imgs = []
        self._fx_vars = {}
        self._fx_roww = {}
        self._fx_cur = {}
        self._fx_sel_id = None
        sel = self.fx_tree.selection()
        if not sel or sel[0] not in self._fx_targets:
            return
        name = sel[0]
        mode = self.fx_mode_var.get()
        seed = int(self.fx_seed_var.get() or 0)
        pv = randomize.fx_preview(name, mode, seed)
        self.fx_head.config(text=("\U0001F6E1 " if name in self._fx_protected else "")
                            + name)
        self._fx_build_protbar(name)
        if not pv:
            self.fx_note.config(text="No separate effect sheets.")
            self._fx_clear_preview("No separate effect sheets.")
            return

        # Remember the char's palette + base index for the per-palette lock tool.
        self._fx_cur_name = name
        self._fx_cur_pv_body = pv.get("body", [])
        self._fx_cur_base_idx = pv["base_idx"]   # lock row's default swatch
        self._fx_build_basebar()                 # Chaos seed / Neon color

        if mode == "Default":
            note = "mode: Off — FX doesn't recolor unlocked effects: they show what " \
                   "the skin gives them (base-game colors for most; skin-tinted ones " \
                   "follow the skin). Last run's FX is undone. Locks still apply."
        elif mode == "Stock":
            note = "mode: Stock (base game) — effects that are on are forced to the " \
                   "unedited base-game colors, even skin-tinted ones. Locks still apply."
        elif mode == "Shades":
            anchor = (f"anchored to Global color {self.fx_gcolor}" if self.fx_gcolor
                      else "keys off each skin's base color")
            note = f"mode: {mode} (unlocked)   ·   {anchor}   ·   " \
                   "locks override per effect"
        elif mode in ("Thermal", "Night Vision"):
            look = "fire" if mode == "Thermal" else "green phosphor"
            note = f"mode: {mode} (unlocked)   ·   fixed {look} look (ignores " \
                   "Global color)   ·   locks override per effect"
        elif mode == "Neon":
            note = f"mode: Neon (unlocked)   ·   tube color: {self.fx_neon_var.get()}" \
                   "   ·   locks override per effect"
        else:
            note = f"mode: {mode} (unlocked)   ·   locks override per effect"
        self.fx_note.config(text=note)

        # On effects first, then off; stable within each group.
        self._fx_cur = {s["id"]: s for s in pv["sheets"]}
        ordered = sorted(
            pv["sheets"],
            key=lambda s: (0 if self._fx_eff_on(s["id"], s["default_on"]) else 1))
        self._fx_order = [s["id"] for s in ordered]
        for sh in ordered:
            locked = sh.get("locked")
            on = locked or self._fx_eff_on(sh["id"], sh["default_on"])
            var = tk.BooleanVar(value=self._fx_eff_on(sh["id"], sh["default_on"]))
            self._fx_vars[sh["id"]] = var
            row = tk.Frame(self.fx_inner, background="#ffffff")
            row.pack(fill="x")
            cbtn = tk.Checkbutton(row, variable=var, background="#ffffff",
                                  activebackground="#ffffff",
                                  command=lambda i=sh["id"]: self._fx_toggle(i))
            cbtn.pack(side="left")
            tags = ["per-button" if sh["tag"] == "per-button" else "shared"]
            if sh["fromPalette"]:
                tags.append("skin-driven")
            if locked:
                tags.insert(0, "\U0001F512 " + sh["eff_mode"])
            badge = tk.Label(row, text="   ".join(tags), background="#ffffff",
                             foreground=("#5b5570" if locked else "#999"),
                             font=("Segoe UI", 8))
            badge.pack(side="right", padx=(6, 2))
            lab = tk.Label(row, text=("\U0001F512 " if locked else "") + sh["label"],
                           background="#ffffff", anchor="w", justify="left",
                           wraplength=520,
                           foreground=("#222" if on else "#aaa"), cursor="hand2")
            lab.pack(side="left", fill="x", expand=True)
            for wdg in (row, lab, badge):
                wdg.bind("<Button-1>", lambda e, i=sh["id"]: self._fx_row_click(i))
            self._fx_roww[sh["id"]] = (row, lab, badge, var)
            tk.Frame(self.fx_inner, height=1, background="#eeeeee").pack(fill="x")

        if ordered:
            target = keep if keep in self._fx_order else ordered[0]["id"]
            self._fx_select_row(target)              # keep selection if still shown
        self._fx_update_summary()

    def _fx_clear_preview(self, msg="Select an effect below."):
        for w in self.fx_prev_imgs.winfo_children():
            w.destroy()
        self._fx_prev_refs = []
        self.fx_prev_title.config(text=msg)

    def _fx_select_row(self, sid):
        prev = self._fx_sel_id
        self._fx_sel_id = sid
        for i in (prev, sid):
            w = self._fx_roww.get(i)
            if not w:
                continue
            bg = "#dbe9fb" if i == sid else "#ffffff"
            for wdg in w[:3]:
                try:
                    wdg.configure(background=bg)
                except tk.TclError:
                    pass
        self._fx_update_preview(sid)

    def _fx_update_preview(self, sid):
        for w in self.fx_prev_imgs.winfo_children():
            w.destroy()
        self._fx_prev_refs = []
        sh = self._fx_cur.get(sid)
        self._fx_build_lock_tool(sh)
        if not sh:
            self.fx_prev_title.config(text="Select an effect below.")
            return
        per_btn = sh["tag"] == "per-button" and sh["slot"] < 8
        btn = self._fx_prev_btn if per_btn else 0
        # Per-button preview: resolve this sheet for the chosen color button.
        pvb = randomize.fx_preview_button(
            self._fx_cur_name, sid, btn, self.fx_mode_var.get(),
            int(self.fx_seed_var.get() or 0)) or {
                "orig": sh["orig"], "trans": sh["trans"],
                "eff_mode": sh.get("eff_mode", "Default")}
        eff = pvb["eff_mode"]
        recolor = eff not in (None, "Default", "Stock")
        # "Show loaded" -> the right image is the ACTUAL palette in the live arc.
        loaded_cols = None
        if self.fx_loaded_var.get():
            loaded_cols = randomize.fx_loaded_colors(self._fx_cur_name, sid, btn)
        if loaded_cols is not None:
            right_cols, right_cap, show_right = loaded_cols, "loaded (in game)", True
            tag = "loaded from game"
        else:
            right_cols, right_cap, show_right = pvb["trans"], eff, recolor
            tag = f"{eff}{' (locked)' if sh.get('locked') else ''}" if recolor \
                else (f"Stock — original colors{' (locked)' if sh.get('locked') else ''}"
                      if eff == "Stock" and (sh.get("locked") or
                                             self._fx_eff_on(sid, sh["default_on"]))
                      else "effect off — not recolored" if not (sh.get("locked") or
                      self._fx_eff_on(sid, sh["default_on"]))
                      else "FX off — not recolored")
            if self.fx_loaded_var.get():
                tag += "  ·  (game not loaded — showing preview)"
        if self._fx_cur_name in self._fx_protected:
            tag += "  ·  \U0001F6E1 protected: in game it keeps the outside edit"
        self.fx_prev_title.config(text=f"{sh['label']}    ·    {tag}")
        key = sh.get("spr")

        # Color-button selector (per-button slots only) so you can inspect each.
        # Left/Right arrow keys cycle it too (see the canvas bindings).
        if per_btn:
            selrow = tk.Frame(self.fx_prev_imgs, background="#ffffff")
            selrow.pack(anchor="w")
            tk.Label(selrow, text="Color:", background="#ffffff",
                     foreground="#777", font=("Segoe UI", 8)).pack(side="left")
            lt = tk.Label(selrow, text="◀", background="#ffffff",
                          foreground="#888", cursor="hand2", font=("Segoe UI", 8))
            lt.pack(side="left", padx=(2, 0))
            lt.bind("<Button-1>", lambda e: self._fx_btn_nav(-1))
            for bi, bn in enumerate(self._fx_btnnames):
                sel = bi == btn
                b = tk.Label(selrow, text=bn, font=("Segoe UI", 8,
                             "bold" if sel else "normal"), cursor="hand2",
                             padx=5, background=("#dbe9fb" if sel else "#ffffff"),
                             foreground=("#1a3b66" if sel else "#666"))
                b.pack(side="left", padx=1)
                b.bind("<Button-1>", lambda e, i=bi: self._fx_set_prev_btn(i))
            rt = tk.Label(selrow, text="▶", background="#ffffff",
                          foreground="#888", cursor="hand2", font=("Segoe UI", 8))
            rt.pack(side="left", padx=(0, 4))
            rt.bind("<Button-1>", lambda e: self._fx_btn_nav(1))
            tk.Label(selrow, text="(← → keys)", background="#ffffff",
                     foreground="#aaa", font=("Segoe UI", 8)).pack(side="left")

        area = tk.Frame(self.fx_prev_imgs, background="#ffffff")
        area.pack(fill="both", expand=True)
        fw = max(120, self.fx_prev_imgs.winfo_width())
        fh = max(70, self.fx_prev_imgs.winfo_height() - (22 if per_btn else 0))
        mh = max(60, fh - 26)
        mw = max(60, (fw - 90) // 2) if show_right else max(60, fw - 40)
        stock = self._fx_big_sprite(key, pvb["orig"], mw, mh)
        rec = self._fx_big_sprite(key, right_cols, mw, mh) if show_right else None

        def cell(parent, img, caption):
            c = tk.Frame(parent, background="#ffffff")
            (tk.Label(c, image=img, bd=0, background="#ffffff") if img
             else tk.Label(c, text="(no preview)", background="#ffffff",
                           foreground="#aaa")).pack()
            tk.Label(c, text=caption, background="#ffffff", foreground="#777",
                     font=("Segoe UI", 8)).pack()
            return c

        holder = tk.Frame(area, background="#ffffff")
        holder.place(relx=0.5, rely=0.5, anchor="center")
        if per_btn:
            cap = ("stock (same for all colors)" if pvb.get("uniform")
                   else f"stock ({self._fx_btnnames[btn]})")
        else:
            cap = "stock"
        cell(holder, stock, cap).pack(side="left", padx=10)
        if rec is not None:
            tk.Label(holder, text="→", background="#ffffff",
                     foreground="#999", font=("Segoe UI", 14)).pack(side="left")
            cell(holder, rec, right_cap).pack(side="left", padx=10)

    def _fx_in_tab(self):
        """True while the FX tab is the one on screen."""
        try:
            return self.nb.select() == str(self._fx_tab)
        except Exception:
            return False

    def _fx_lr(self, direction):
        """Left/Right (from any FX control that routes here, or app-wide) cycle the
        previewed color button. Text fields keep their arrows for the cursor."""
        if not self._fx_in_tab():
            return
        try:
            w = self.root.focus_get()
        except Exception:
            w = None
        if isinstance(w, (ttk.Entry, tk.Entry, ttk.Spinbox, tk.Spinbox)):
            return
        self._fx_btn_nav(-1 if direction == "left" else 1)
        return "break"

    def _fx_btn_nav(self, delta):
        """Left/Right cycle the previewed color button (per-button effects)."""
        sh = self._fx_cur.get(self._fx_sel_id)
        if not sh or not (sh["tag"] == "per-button" and sh["slot"] < 8):
            return "break"
        self._fx_set_prev_btn((self._fx_prev_btn + delta) % 6)
        return "break"

    def _fx_toggle_loaded(self):
        on = not self.fx_loaded_var.get()
        self.fx_loaded_var.set(on)
        if on:
            randomize.live_rom(force=True)          # pull fresh palettes from the arc
        self.fx_loaded_btn.config(
            text="Loaded from game ✓" if on else "Load from game")
        self._fx_build_basebar()                    # lock swatches use loaded colors
        if self._fx_sel_id:
            self._fx_update_preview(self._fx_sel_id)

    def _fx_set_prev_btn(self, bi):
        self._fx_prev_btn = bi
        self._fx_build_basebar()                    # lock swatches follow the button
        if self._fx_sel_id:
            self._fx_update_preview(self._fx_sel_id)

    def _fx_build_lock_tool(self, sh):
        """Per-palette lock controls in the preview toolbar: a mode selector
        (Follow global / a specific mode = locked) plus base/seed when relevant."""
        for w in self.fx_prev_tool.winfo_children():
            w.destroy()
        self.fx_lockbox = None
        if not sh:
            return
        sid = sh["id"]
        lk = self._fx_locks_map.get(sid)

        # Row 1: mode selector + Unlock. Click the box to select it, then Up/Down
        # cycle its mode and Left/Right cycle the color button (like global Mode).
        r1 = ttk.Frame(self.fx_prev_tool); r1.pack(fill="x")
        ttk.Label(r1, text="This effect:").pack(side="left")
        self.fx_lockmode_var.set(self._fx_disp(lk.get("mode")) if lk else "Follow global")
        box = ttk.Combobox(r1, textvariable=self.fx_lockmode_var,
                           state="readonly", width=13,
                           values=["Follow global"] +
                                  [self._fx_disp(m) for m in self._fx_modes])
        box.pack(side="left", padx=(4, 0))
        self.fx_lockbox = box
        box.bind("<<ComboboxSelected>>", lambda e, i=sid: self._fx_set_lockmode(i))
        box.bind("<Up>", lambda e: self._fx_cycle_lockmode(-1))
        box.bind("<Down>", lambda e: self._fx_cycle_lockmode(1))
        box.bind("<Left>", lambda e: self._fx_lr("left"))
        box.bind("<Right>", lambda e: self._fx_lr("right"))
        if lk:
            ttk.Button(r1, text="Unlock", command=lambda i=sid: self._fx_unlock(i)
                       ).pack(side="left", padx=(6, 0))
        # Row 2 (only for a Shades/Chaos/Neon lock): its base color, seed or tube.
        if lk and lk.get("mode") == "Neon":
            r2 = ttk.Frame(self.fx_prev_tool); r2.pack(fill="x", pady=(2, 0))
            self._fx_neon_picker(r2, "Neon color (all Neon effects):")
        elif lk and lk.get("mode") == "Shades":
            r2 = ttk.Frame(self.fx_prev_tool); r2.pack(fill="x", pady=(2, 0))
            cidx = lk.get("base")
            if cidx is None:
                cidx = self._fx_cur_base_idx
            self._fx_build_base_swatches(
                r2, self._fx_cur_body, cidx,
                lambda idx, i=sid: self._fx_set_lock_base(i, idx),
                lambda i=sid: self._fx_set_lock_base(i, None),
                on_hover=self._fx_show_index_popup)
        elif lk and lk.get("mode") == "Chaos":
            r2 = ttk.Frame(self.fx_prev_tool); r2.pack(fill="x", pady=(2, 0))
            ttk.Label(r2, text="Lock seed:").pack(side="left")
            sv = tk.IntVar(value=int(lk.get("seed", 0) or 0))
            ttk.Spinbox(r2, from_=0, to=999, width=5, textvariable=sv,
                        command=lambda i=sid, v=sv:
                        self._fx_set_lock_seed(i, int(v.get() or 0))
                        ).pack(side="left", padx=(4, 0))
            ttk.Button(r2, text="\U0001F3B2", width=3,
                       command=lambda i=sid: self._fx_lock_reroll(i)
                       ).pack(side="left", padx=(3, 0))

    def _fx_big_sprite(self, key, colors, maxw=330, maxh=186):
        """Large preview render (its PhotoImage ref is kept in _fx_prev_refs)."""
        before = self._fx_imgs
        self._fx_imgs = self._fx_prev_refs      # route the ref into the preview list
        try:
            return self._fx_sprite_img(key, colors, maxw=maxw, maxh=maxh)
        finally:
            self._fx_imgs = before

    def _fx_fit_preview(self, right_h):
        """Keep the preview a bit under half the right pane (min 240px) so it grows
        with the window without leaving a big empty band; the list takes the rest."""
        try:
            h = max(240, min(int(right_h * 0.46), 760))
            if abs(self.fx_prev_imgs.winfo_height() - h) > 4:
                self.fx_prev_imgs.configure(height=h)
        except tk.TclError:
            pass

    def _fx_on_prev_resize(self, event):
        """Re-render the preview to the new panel size (debounced)."""
        if getattr(self, "_fx_prev_after", None):
            try:
                self.root.after_cancel(self._fx_prev_after)
            except Exception:
                pass
        self._fx_prev_after = self.root.after(
            90, lambda: self._fx_sel_id and self._fx_update_preview(self._fx_sel_id))

    def _fx_default(self, sid):
        return self._fx_index.get(sid, (None, False, None))[1]

    def _fx_toggle(self, sid):
        var = self._fx_vars.get(sid)
        if var is None:
            return
        on = bool(var.get())
        if on == self._fx_default(sid):
            self.fx_state_map.pop(sid, None)     # back to default: drop override
        else:
            self.fx_state_map[sid] = on
        randomize.set_fx_state(self.fx_state_map)
        # In-place update (no resort, so the row doesn't jump under the cursor).
        w = self._fx_roww.get(sid)
        if w:
            w[1].configure(foreground="#222" if on else "#aaa")
        sel = self.fx_tree.selection()
        if sel:
            o, tot = self._fx_char_counts(sel[0])
            self.fx_tree.set(sel[0], "n", f"{o}/{tot}")
        if sid == self._fx_sel_id:
            self._fx_update_preview(sid)         # recolor appears/disappears
        self._fx_update_summary()

    def _fx_bulk(self, value):
        """value True=all on, False=all off, None=reset to defaults for the char."""
        sel = self.fx_tree.selection()
        if not sel or sel[0] not in self._fx_targets:
            return
        for s in self._fx_targets[sel[0]].get("sheets", []):
            if s.get("verdict") == "exclude":
                continue
            sid, dflt = s["id"], s.get("default_on", False)
            if value is None or value == dflt:
                self.fx_state_map.pop(sid, None)
            else:
                self.fx_state_map[sid] = bool(value)
        randomize.set_fx_state(self.fx_state_map)
        o, tot = self._fx_char_counts(sel[0])
        self.fx_tree.set(sel[0], "n", f"{o}/{tot}")
        self._fx_render_char()

    # Display labels for internal mode names (config/engine keep the old ones).
    _FX_MODE_LABELS = {"Default": "Off", "Stock": "Stock (base game)"}

    def _fx_disp(self, mode):
        return self._FX_MODE_LABELS.get(mode, mode)

    def _fx_val(self, label):
        for k, v in self._FX_MODE_LABELS.items():
            if v == label:
                return k
        return label

    def _fx_cycle_mode(self, delta):
        """Up/Down on the mode box switches mode immediately (no Enter)."""
        modes = list(self._fx_all_modes)
        if not modes:
            return "break"
        try:
            i = modes.index(self.fx_mode_var.get())
        except ValueError:
            i = 0
        i = max(0, min(len(modes) - 1, i + delta))
        if modes[i] != self.fx_mode_var.get():
            self.fx_mode_var.set(modes[i])
            self._fx_mode_change()
        return "break"

    def _fx_cycle_lockmode(self, delta):
        """Up/Down on the focused 'This effect' box switches its lock mode and
        keeps the (rebuilt) box selected, so arrows keep working on it."""
        if not self._fx_sel_id:
            return "break"
        vals = ["Follow global"] + [self._fx_disp(m) for m in self._fx_modes]
        cur = self.fx_lockmode_var.get()
        i = vals.index(cur) if cur in vals else 0
        i = max(0, min(len(vals) - 1, i + delta))
        if vals[i] != cur:
            self.fx_lockmode_var.set(vals[i])
            self._fx_set_lockmode(self._fx_sel_id)      # rebuilds the lock tool
            box = getattr(self, "fx_lockbox", None)
            if box is not None and box.winfo_exists():
                box.focus_set()                         # stays selected
        return "break"

    def _fx_show_index_popup(self, idx, widget):
        """Pop a small black/yellow body sprite next to `widget` showing where
        palette index `idx` sits on the character (hover a base swatch)."""
        img = self._fx_index_hilite_img(idx, maxw=480, maxh=296)   # 4x the old size
        if not img:
            return
        self._fx_idx_popup_ref = img                    # keep a ref alive
        pop = getattr(self, "_fx_idx_popup", None)
        if pop is None or not pop.winfo_exists():
            pop = tk.Toplevel(self.root)
            pop.overrideredirect(True)
            try:
                pop.attributes("-topmost", True)
            except tk.TclError:
                pass
            self._fx_idx_popup = pop
            self._fx_idx_popup_lbl = tk.Label(pop, background="#1a1a20",
                                              bd=1, relief="solid")
            self._fx_idx_popup_lbl.pack()
        self._fx_idx_popup_lbl.config(image=img)
        try:
            x = widget.winfo_rootx() + widget.winfo_width() + 8
            y = widget.winfo_rooty() - 6
            pop.geometry(f"+{x}+{y}")
            pop.deiconify()
        except tk.TclError:
            pass

    def _fx_hide_index_popup(self):
        pop = getattr(self, "_fx_idx_popup", None)
        if pop is not None and pop.winfo_exists():
            pop.withdraw()

    def _fx_index_hilite_img(self, idx, maxw=120, maxh=74):
        """Body sprite with palette index `idx` in yellow, the rest dimmed."""
        spr = randomize.fx_body_sprite(self._fx_cur_name)
        if not spr:
            return None
        w, h, data = spr
        if len(data) != w * h:
            return None
        pal = []
        for i in range(256):
            if i == idx:
                pal += [255, 232, 0]               # the chosen index -> yellow
            elif i == 0:
                pal += [26, 26, 32]                # transparent/background -> dark
            else:
                pal += [66, 66, 74]                # everything else -> dim grey
        im = PILImage.frombytes("P", (w, h), data)
        im.putpalette(pal)
        im = im.convert("RGB")
        scale = max(0.05, min(maxw / w, maxh / h, 12))
        im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                       PILImage.NEAREST)
        return ImageTk.PhotoImage(im)

    def _fx_relevant_body(self):
        """Body palette for the previewed button (loaded-aware) — the colors the
        base index actually maps to; falls back to the stock char body."""
        b = None
        if self._fx_cur_name is not None:
            b = randomize.fx_body_colors(self._fx_cur_name, self._fx_prev_btn,
                                         self.fx_loaded_var.get())
        return b or self._fx_cur_pv_body

    def _fx_build_basebar(self):
        """(Re)build the top base-color area for the previewed button + mode."""
        for w in self.fx_basebar.winfo_children():
            w.destroy()
        mode = self.fx_mode_var.get()
        body = self._fx_relevant_body()
        self._fx_cur_body = body                  # lock base swatches reuse this
        # No palette-index row here: Shades uses the character's base color (or
        # the top-bar Global color); only a locked effect picks its own.
        if mode == "Chaos":
            ttk.Label(self.fx_basebar, text="Chaos seed:").pack(side="left")
            ttk.Spinbox(self.fx_basebar, from_=0, to=999, width=5,
                        textvariable=self.fx_seed_var,
                        command=self._fx_seed_change).pack(side="left", padx=(4, 0))
            ttk.Button(self.fx_basebar, text="\U0001F3B2 Reroll",
                       command=self._fx_reroll).pack(side="left", padx=(4, 0))
        elif mode == "Neon":
            self._fx_neon_picker(self.fx_basebar, "Neon color:")

    def _fx_neon_picker(self, parent, label):
        """Neon tube-colour dropdown (one global setting; Auto snaps each part of
        an effect to its nearest classic neon colour)."""
        ttk.Label(parent, text=label).pack(side="left")
        nb = ttk.Combobox(parent, textvariable=self.fx_neon_var, state="readonly",
                          values=self._fx_neon_names, width=8)
        nb.pack(side="left", padx=(4, 0))
        nb.bind("<<ComboboxSelected>>", lambda e: self._fx_neon_change())
        nb.bind("<Up>", lambda e: self._fx_cycle_var(
            self.fx_neon_var, self._fx_neon_names, -1, self._fx_neon_change, nb))
        nb.bind("<Down>", lambda e: self._fx_cycle_var(
            self.fx_neon_var, self._fx_neon_names, 1, self._fx_neon_change, nb))
        nb.bind("<Left>", lambda e: self._fx_lr("left"))
        nb.bind("<Right>", lambda e: self._fx_lr("right"))
        self._fx_neon_box = nb

    def _fx_cycle_var(self, var, values, delta, on_change, refocus=None):
        """Up/Down on a focused settings dropdown: step its value and apply."""
        cur = var.get()
        i = values.index(cur) if cur in values else 0
        i = max(0, min(len(values) - 1, i + delta))
        if values[i] != cur:
            var.set(values[i])
            on_change()
            if refocus is not None:          # the box may have been rebuilt
                box = getattr(self, "_fx_neon_box", None)
                if box is not None and box.winfo_exists():
                    box.focus_set()
        return "break"

    def _fx_grad_change(self):
        randomize.set_config_key("fx_gradient", self.fx_grad_var.get())
        self._fx_render_char()

    def _fx_neon_change(self):
        randomize.set_config_key("fx_neon_color", self.fx_neon_var.get())
        self._fx_render_char()

    def _fx_build_base_swatches(self, parent, body, cur_idx, on_click, on_auto,
                                label="Lock base color:", on_hover=None):
        """Clickable body-palette swatches for a locked Shades effect's base
        index, plus "Char" (back to the character's own base color).
        on_hover(idx, widget) pops the black/yellow index highlight on hover."""
        ttk.Label(parent, text=label).pack(side="left")
        for i, c in enumerate(body):
            if len(c) > 3 and c[3] == 0:
                continue                           # transparent index: not a base
            hexc = "#%02x%02x%02x" % (c[0], c[1], c[2])
            bd = 3 if i == cur_idx else 1
            hl = "#111111" if i == cur_idx else "#cccccc"
            sw = tk.Frame(parent, background=hexc, width=22, height=18,
                          highlightthickness=bd, highlightbackground=hl,
                          cursor="hand2")
            sw.pack_propagate(False)
            sw.pack(side="left", padx=1)
            sw.bind("<Button-1>", lambda e, idx=i: on_click(idx))
            if on_hover:
                sw.bind("<Enter>", lambda e, idx=i, w=sw: on_hover(idx, w))
                sw.bind("<Leave>", lambda e: self._fx_hide_index_popup())
        ttk.Button(parent, text="Char", width=5, command=on_auto
                   ).pack(side="left", padx=(8, 0))

    # --- per-palette lock handlers -----------------------------------------
    def _fx_set_lockmode(self, sid):
        choice = self._fx_val(self.fx_lockmode_var.get())
        if choice == "Follow global":
            self._fx_locks_map.pop(sid, None)
        else:
            lk = dict(self._fx_locks_map.get(sid) or {})
            lk["mode"] = choice
            if choice != "Shades":
                lk.pop("base", None)
            if choice != "Chaos":
                lk.pop("seed", None)
            self._fx_locks_map[sid] = lk
        randomize.set_fx_locks(self._fx_locks_map)
        self._fx_after_lock_change(sid)

    def _fx_set_lock_base(self, sid, idx):
        lk = dict(self._fx_locks_map.get(sid) or {})
        if idx is None:
            lk.pop("base", None)                   # back to the character base
        else:
            lk["base"] = idx
        self._fx_locks_map[sid] = lk
        randomize.set_fx_locks(self._fx_locks_map)
        self._fx_after_lock_change(sid)

    def _fx_set_lock_seed(self, sid, seed):
        lk = dict(self._fx_locks_map.get(sid) or {})
        lk["seed"] = int(seed)
        self._fx_locks_map[sid] = lk
        randomize.set_fx_locks(self._fx_locks_map)
        self._fx_after_lock_change(sid)

    def _fx_lock_reroll(self, sid):
        lk = self._fx_locks_map.get(sid) or {}
        self._fx_set_lock_seed(sid, int(lk.get("seed", 0) or 0) + 1)

    def _fx_unlock(self, sid):
        self._fx_locks_map.pop(sid, None)
        randomize.set_fx_locks(self._fx_locks_map)
        self._fx_after_lock_change(sid)

    def _fx_after_lock_change(self, sid):
        # Re-render the list (lock badges + on-first sort) keeping this selection.
        self._fx_sel_id = sid
        self._fx_render_char()
        self._fx_refresh_tree_locks()
        self._fx_update_summary()

    def _fx_char_has_lock(self, name):
        d = self._fx_targets.get(name, {})
        return any(s["id"] in self._fx_locks_map for s in d.get("sheets", []))

    def _fx_refresh_tree_locks(self):
        """Character-list markers: shield = palettes edited outside the app
        (protected, FX won't touch it); padlock = has a per-effect lock."""
        self._fx_protected = randomize.fx_protected()
        for name in self._fx_targets:
            if self.fx_tree.exists(name):
                pre = ("\U0001F6E1 " if name in self._fx_protected else "") + \
                      ("\U0001F512 " if self._fx_char_has_lock(name) else "")
                self.fx_tree.item(name, text=pre + name)

    def _fx_build_protbar(self, name):
        """Banner shown for a protected character (edited outside the app)."""
        for w in self.fx_protbar.winfo_children():
            w.destroy()
        if name not in getattr(self, "_fx_protected", {}):
            return
        box = tk.Frame(self.fx_protbar, background="#fff4dc",
                       highlightthickness=1, highlightbackground="#e2b04a")
        box.pack(fill="x", pady=(2, 4))
        tk.Label(box, text="\U0001F6E1 Protected — this character's palettes were "
                 "edited outside the randomizer (e.g. PalMod). FX won't touch it in "
                 "any mode, Stock included, so your custom work stays. Settings "
                 "below apply only after you unlock it.  'Load from game' shows the "
                 "edited colors.", background="#fff4dc", foreground="#6b4a00",
                 wraplength=760, justify="left").pack(side="left", padx=6, pady=4)
        ttk.Button(box, text="Unlock…",
                   command=lambda f=self._fx_protected[name]: self._fx_unprotect(f)
                   ).pack(side="right", padx=6, pady=4)

    def _fx_unprotect(self, folder):
        if not config.has_game():
            messagebox.showerror("Game not found",
                                 "Set your game folder on the Setup tab first.")
            return
        if not messagebox.askyesno(
                "Unlock for randomizing",
                f"Unlock {folder}?\n\n"
                "The next randomize will OVERWRITE their custom palettes - body\n"
                "skins and effects. If you want to keep that work, back it up\n"
                "first (e.g. save your PalMod project)."):
            return
        self.run_job(f"Unlocking {folder}", randomize.unprotect, character=folder,
                     on_done=self._refresh_protected)

    def _fx_row_click(self, sid):
        self.fx_canvas.focus_set()          # so Up/Down then navigate the list
        self._fx_select_row(sid)

    def _fx_nav(self, delta):
        """Up/Down move the selected effect within the list."""
        order = self._fx_order
        if not order:
            return "break"
        if self._fx_sel_id in order:
            i = max(0, min(len(order) - 1, order.index(self._fx_sel_id) + delta))
        else:
            i = 0 if delta > 0 else len(order) - 1
        self._fx_select_row(order[i])
        self._fx_scroll_to(i)
        return "break"

    def _fx_scroll_to(self, i):
        n = len(self._fx_order) or 1
        self.fx_canvas.yview_moveto(max(0.0, (i - 1) / n))

    # --- global color --------------------------------------------------------
    def _fx_build_gcolor(self):
        """Top-bar Global color control: a picker button, plus a swatch + Clear
        once a color is chosen."""
        for w in self.fx_gcolor_bar.winfo_children():
            w.destroy()
        c = self.fx_gcolor
        ttk.Button(self.fx_gcolor_bar,
                   text="Global color…" if not c else "Global color:",
                   command=self._fx_pick_gcolor).pack(side="left")
        if c:
            sw = tk.Frame(self.fx_gcolor_bar, background=c, width=26, height=18,
                          highlightthickness=1, highlightbackground="#555",
                          cursor="hand2")
            sw.pack_propagate(False)
            sw.pack(side="left", padx=(4, 0))
            sw.bind("<Button-1>", lambda e: self._fx_pick_gcolor())
            ttk.Button(self.fx_gcolor_bar, text="Clear", width=6,
                       command=self._fx_clear_gcolor).pack(side="left", padx=(4, 0))

    def _fx_pick_gcolor(self):
        _rgb, hexc = colorchooser.askcolor(
            color=self.fx_gcolor or "#3a7bd5", parent=self.root,
            title="Global FX color (Shades)")
        if not hexc:
            return                                  # cancelled
        self._fx_set_gcolor(hexc.lower())

    def _fx_clear_gcolor(self):
        self._fx_set_gcolor(None)

    def _fx_set_gcolor(self, hexc):
        self.fx_gcolor = hexc
        randomize.set_config_key("fx_global_color", hexc)
        self._fx_build_gcolor()
        self._fx_render_char()                      # previews re-anchor
        self._fx_update_summary()

    def _fx_reset_char(self):
        """Clear every FX override for the selected character: effect on/off
        and all per-effect locks - back to defaults."""
        sel = self.fx_tree.selection()
        if not sel or sel[0] not in self._fx_targets:
            return
        name = sel[0]
        for s in self._fx_targets[name].get("sheets", []):
            self.fx_state_map.pop(s["id"], None)
            self._fx_locks_map.pop(s["id"], None)
        randomize.set_fx_state(self.fx_state_map)
        randomize.set_fx_locks(self._fx_locks_map)
        o, tot = self._fx_char_counts(name)
        self.fx_tree.set(name, "n", f"{o}/{tot}")
        self._fx_render_char()
        self._fx_refresh_tree_locks()
        self._fx_update_summary()

    def _fx_reset_all(self):
        if not messagebox.askyesno(
                "Reset FX",
                "Reset all FX settings to default?\n\n"
                "Mode → Default, Chaos seed → 0, and every per-character "
                "on/off and base-color change is cleared.\n\n"
                "Run Randomize afterward to revert the game's effects to stock."):
            return
        randomize.reset_fx()
        self.fx_mode_var.set("Default")
        self.fx_seed_var.set(0)
        self.fx_grad_var.set("Normal")
        self.fx_neon_var.set("Auto")
        self.fx_gcolor = None
        self._fx_build_gcolor()
        self.fx_state_map = {}
        self._fx_locks_map = {}
        self._fx_populate_tree()
        self._fx_render_char()

    def _fx_mode_change(self):
        randomize.set_config_key("fx_mode", self.fx_mode_var.get())
        self._fx_render_char()
        self._fx_update_summary()

    def _fx_seed_change(self):
        randomize.set_config_key("fx_chaos_seed", int(self.fx_seed_var.get() or 0))
        if self.fx_mode_var.get() == "Chaos":
            self._fx_render_char()

    def _fx_reroll(self):
        self.fx_seed_var.set(int(self.fx_seed_var.get() or 0) + 1)
        self._fx_seed_change()

    def _fx_update_summary(self):
        on = tot = 0
        for name in self._fx_targets:
            o, t = self._fx_char_counts(name)
            on += o; tot += t
        mode = self.fx_mode_var.get()
        state = "FX off" if mode == "Default" else f"mode: {self._fx_disp(mode)}"
        if self.fx_gcolor and mode == "Shades":
            state += f" ({self.fx_gcolor})"
        elif mode == "Neon":
            state += f" ({self.fx_neon_var.get()})"
        if mode not in ("Default", "Stock"):
            state += f"  ·  gradient {self.fx_grad_var.get()}"
        self.fx_summary.config(text=f"{on} of {tot} effects on   ·   {state}")

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
        ttk.Label(t, text="Export game file (game_50.arc)",
                  font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(t, foreground="#555", justify="left", wraplength=self.SETUP_WRAP,
                  text="Saves your FINISHED game file - palettes, stages and FX "
                       "exactly as the last Randomize wrote them - for someone "
                       "who just wants to drop it in (no app needed). Lock your "
                       "palette picks, stages and FX first so it's your exact "
                       "setup. (To share your choices, locks and playlists "
                       "instead, use Setup > Curated mixes.)"
                  ).pack(anchor="w", pady=(2, 6))
        self.mix_sum_lbl = ttk.Label(t, text="", foreground="#333", justify="left",
                                     wraplength=self.SETUP_WRAP)
        self.mix_sum_lbl.pack(anchor="w", pady=(0, 6))
        xrow = ttk.Frame(t); xrow.pack(anchor="w")
        xb = ttk.Button(xrow, text="Export game_50.arc...", command=self.on_export_mix)
        xb.pack(side="left")
        self._action_widgets.append(xb)
        t.bind("<Map>", lambda e: self._mix_refresh_summary())

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
            extra = []
            if p.get("pal_locks"):
                extra.append(f"{p['pal_locks']} palette picks")
            if p.get("stage_locks"):
                extra.append(f"{p['stage_locks']} stage locks")
            if p.get("playlists"):
                extra.append(f"{p['playlists']} playlist(s)")
            if p.get("fx"):
                extra.append("FX setup")
            self.preset_lbl.config(
                text=f"{p['keeps']:,} keeps · {p['rejects']:,} rejects · "
                     f"{p['stages']} stage picks"
                     + "".join(" · " + x for x in extra)
                     + f" · updated {p['updated']}")
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
                   "Your keep/reject choices for gallery palettes and stages "
                   "will be REPLACED by this mix - and if the mix includes them, "
                   "so will your palette picks, stage locks and FX setup (its "
                   "playlists are added to yours).\n"
                   "(Your own drop-ins in custom/ are never affected.)")
        else:
            msg = (f"Fill gaps from \"{p['name']}\"?\n\n"
                   "Only palettes and stages you haven't judged yet take this "
                   "mix's verdicts; its picks/locks only fill buttons and slots "
                   "you haven't locked; only new playlists are added. "
                   "Everything you've already decided stays yours.")
        if not messagebox.askyesno("Apply curated mix", msg):
            return
        result = presets.apply_preset(p["path"], mode)
        parts = [f"{result['palettes']:,} palette verdicts",
                 f"{result['stages']} stage verdicts"]
        if result.get("pal_locks") or result.get("pal_locks_missing"):
            parts.append(f"{result['pal_locks']} palette picks")
        if result.get("stage_locks") or result.get("stage_locks_missing"):
            parts.append(f"{result['stage_locks']} stage locks")
        if result.get("playlists"):
            parts.append(f"{result['playlists']} playlist(s)")
        if result.get("fx"):
            parts.append(f"FX setup ({result['fx_locks']} effect locks)")
        miss = result.get("pal_locks_missing", 0) + result.get(
            "stage_locks_missing", 0)
        summary = ", ".join(parts) + (
            f"\n\n{miss} lock(s) point at files you haven't downloaded - "
            "those stay random (run Download / Update, then apply again)."
            if miss else "")
        self.logln(f"Applied curated mix \"{result['name']}\" ({mode}): "
                   + summary.replace("\n\n", " "))
        self.refresh_status()
        self._on_char_change()
        self._stage_built = False     # stage gallery reloads on next visit
        self._slot_fits = None            # settings may have changed
        if getattr(self, "_cur_char", None):
            self._load_char(self._cur_char)   # mix bar shows the new picks
        messagebox.showinfo("Curated mix applied",
                            f"\"{result['name']}\" applied: {summary}")

    def on_export_preset(self):
        name = simpledialog.askstring(
            "Export curated mix", "Name for your curated mix (shown to other "
            "users):",
            parent=self.root)
        if not name:
            return
        author = simpledialog.askstring(
            "Export curated mix", "Author name:", parent=self.root) or "anonymous"
        out = filedialog.asksaveasfilename(
            title="Save curated mix as", defaultextension=".json",
            initialfile=f"{author.lower().replace(' ', '_')}.json",
            filetypes=[("Preset JSON", "*.json")])
        if not out:
            return
        stats = presets.export_preset(out, name, author)
        what = (f"{stats['palettes']:,} palette and {stats['stages']} stage "
                f"verdicts, {stats['pal_locks']} palette picks, "
                f"{stats['stage_locks']} stage locks, {stats['playlists']} "
                f"playlist(s) and the FX setup ({stats['fx_locks']} effect locks)")
        self.logln(f"Exported curated mix \"{name}\": {what} -> {out}")
        messagebox.showinfo(
            "Curated mix exported",
            f"Saved {what}.\n\nOthers apply it from Setup > Curated mixes (put "
            f"the file in their presets/ folder, or submit it to the mvc2-skins "
            f"repo's presets/ folder so it ships with the gallery).")

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
        self._select_tab("Logs")         # watch progress; "Done." marks the end

    def _select_tab(self, suffix):
        """Switch the notebook to the tab whose title ends with `suffix`."""
        for tab in self.nb.tabs():
            if self.nb.tab(tab, "text").endswith(suffix):
                self.nb.select(tab)
                return

    def _after_download(self):
        self._on_char_change()
        self._slot_fits = None             # new ports - recompute the fit map
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

    # ------------------------------------------------------------ share mix
    def _mix_summary_text(self, s):
        fx = self._fx_disp(s["fx_mode"]) if hasattr(self, "_fx_disp") else s["fx_mode"]
        lines = [
            f"Palettes: {s['chars_full']}/{s['chars_total']} characters have all six "
            f"picked" + (f" ({s['chars_partial']} partly - the rest of their "
                         "buttons are random)" if s["chars_partial"] else "") + ".",
            (f"Stages: {s['stages_locked']}/{s['stages_total']} slots locked."
             if s["stages_on"] else "Stages: off (stock stages)."),
            f"FX: {fx}" + (f", {s['fx_locks']} per-effect lock(s)" if s["fx_locks"]
                           else "") + ".",
        ]
        if s["stale"]:
            lines.append("Settings changed since the last Randomize - not in the "
                         "game file yet.")
        return "\n".join(lines)

    def _mix_refresh_summary(self):
        try:
            self.mix_sum_lbl.config(text=self._mix_summary_text(randomize.mix_summary()))
        except Exception as e:
            self.mix_sum_lbl.config(text=f"(couldn't read mix state: {e})")

    def on_export_mix(self):
        if not config.has_game():
            messagebox.showerror("Game not found",
                                 "Set your game folder on the Setup tab first.")
            return
        s = randomize.mix_summary()
        if s["stale"]:
            ans = messagebox.askyesnocancel(
                "Apply your latest settings first?",
                "Some settings changed after the game file was last written "
                f"({', '.join(s['stale'])}), so the export wouldn't include "
                "them yet - e.g. FX recolors are baked in by Randomize.\n\n"
                "Yes = Randomize now, then export (unlocked slots re-roll).\n"
                "No = export the game file exactly as it is.")
            if ans is None:
                return
            if ans:
                self.run_job("Randomizing before export", randomize.randomize,
                             on_done=lambda: (self._announce_new_protected(),
                                              self._export_mix_pick()))
                return
        self._export_mix_pick()

    def _verify_report(self, r):
        """(all_ok, text) for a verify_mix() result."""
        P, F, S = r["palettes"], r["fx"], r["stages"]
        ok = not (P["bad"] or F["bad"] or S["bad"])
        lines = [
            f"{'OK ' if not P['bad'] else 'XX '} Palettes: {P['ok']}/{P['checked']} "
            "locked buttons match your picks",
            f"{'OK ' if not F['bad'] else 'XX '} FX: {F['ok']}/{F['checked']} effects "
            f"match ({F['entries']} palettes, built from each button's palette in "
            "the game file)",
            f"{'OK ' if not S['bad'] else 'XX '} Stages: {S['ok']}/{S['checked']} "
            "locked slots placed",
        ]
        if P.get("unpicked"):
            u = P["unpicked"]
            lines.append(f"\nNo pick (random each Randomize): {', '.join(u[:8])}"
                         + (f" +{len(u) - 8} more" if len(u) > 8 else ""))
        problems = P["bad"] + F["bad"] + S["bad"]
        if problems:
            lines.append("\nProblems:")
            lines += [f"  - {b}" for b in problems[:12]]
            if len(problems) > 12:
                lines.append(f"  ... and {len(problems) - 12} more (see Logs tab)")
        return ok, "\n".join(lines)

    def _export_mix_pick(self):
        s = randomize.mix_summary()
        self._mix_refresh_summary()
        # Prove the game file really holds the mix before handing it out.
        self.root.config(cursor="watch"); self.root.update_idletasks()
        try:
            r = randomize.verify_mix()
        except Exception as e:
            r = None
            messagebox.showerror("Couldn't verify", str(e))
        finally:
            self.root.config(cursor="")
        if r is None:
            return
        ok, text = self._verify_report(r)
        self.logln("\nMix check:\n" + text)
        for part in ("palettes", "fx", "stages"):
            for b in r[part]["bad"]:
                self.logln(f"  [{part}] {b}")
        if ok:
            if not messagebox.askokcancel("Game file verified",
                                          text + "\n\nChoose where to save it."):
                return
        elif not messagebox.askyesno(
                "Game file doesn't fully match your locks",
                text + "\n\nExport anyway? (Usually: click Randomize now first.)",
                icon="warning"):
            return
        dest = filedialog.asksaveasfilename(
            title="Export game_50.arc", initialfile="game_50.arc",
            defaultextension=".arc", filetypes=[("Game archive", "*.arc")])
        if not dest:
            return
        if s["arc"] and os.path.abspath(dest) == os.path.abspath(s["arc"]):
            messagebox.showerror("Pick another folder",
                                 "That's the game's own file - choose a different "
                                 "folder to export to.")
            return
        self.run_job("Exporting game_50.arc", randomize.export_arc, dest=dest,
                     on_done=lambda d=dest: self._export_mix_done(d))

    def _export_mix_done(self, dest):
        messagebox.showinfo(
            "Game file exported",
            f"Saved:\n{dest}\n\n"
            "To use it, the other person should:\n"
            "  1. Back up their own file:\n"
            "     <game>\\nativeDX11x64\\arc\\pc\\game_50.arc\n"
            "  2. Replace it with this one.\n\n"
            "It needs the same game version (a Steam update can replace it). "
            "If they also run this randomizer on launch, it will re-roll over "
            "the mix.")

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
        if getattr(self, "fx_tree", None) is not None:   # FX tab shield markers
            try:
                self._fx_refresh_tree_locks()
                self._fx_render_char()
            except tk.TclError:
                pass

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
