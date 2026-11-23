#!/usr/bin/env python3
"""
PAAF — interface graphique de l'import séquentiel AAF / vidéos dans Pro Tools (PTSL).
Doit se trouver dans le même dossier que ptsl_import_sequentiel.py, aaf_inspect.py,
paaf_theme.py et paaf_strings.py.
"""

import contextlib
import json
import plistlib
import queue
import re
import sys
import threading
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:  # glisser-déposer depuis le Finder
    from tkinterdnd2 import TkinterDnD, DND_FILES
except Exception:
    TkinterDnD = None
    DND_FILES = None

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ptsl_import_sequentiel as core  # noqa: E402
import aaf_inspect  # noqa: E402
import paaf_theme as th  # noqa: E402
from paaf_strings import STRINGS  # noqa: E402

pt = core.pt
APP_NAME = "PAAF"
FILETYPES = [("AAF / OMF / Vidéo", "*.aaf *.omf *.omfi *.mov *.mp4 *.m4v *.mxf *.avi"),
             ("*", "*")]
AAF_AUDIO_MODES = ["link", "copy", "convert", "consolidate"]
PREFS = Path.home() / "Library" / "Application Support" / "PAAF" / "prefs.json"
RELEASES_API = "https://api.github.com/repos/julessourzac-gif/PAAF-RELEASES/releases/latest"


def app_version():
    """VERSION du dépôt (lancement depuis les sources) ou Info.plist de l'app."""
    with contextlib.suppress(Exception):
        return (HERE.parent / "VERSION").read_text().strip()
    with contextlib.suppress(Exception):
        with open(HERE.parent / "Info.plist", "rb") as fh:
            return plistlib.load(fh)["CFBundleShortVersionString"]
    return "?"


APP_VERSION = app_version()


def version_tuple(s):
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:3])


class QueueWriter:
    """Redirige les print() du module vers le journal de l'interface."""

    def __init__(self, q):
        self.q = q

    def write(self, s):
        for line in s.splitlines():
            if line.strip():
                tag = "err" if line.lstrip().startswith(("✗", "!")) else None
                self.q.put(("log", line.rstrip(), tag))

    def flush(self):
        pass


def fmt_duration(seconds, fps=25):
    if seconds is None:
        return "—"
    return core.TC(fps).to_tc(round(seconds * fps))


def fmt_clock(seconds):
    """Durée courte pour le tableau : 08:42, ou 1:02:10 au-delà d'une heure."""
    s = int(round(seconds))
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1400x900")
        self.minsize(1100, 760)

        self.items = []          # [{"path": Path, "tc": str, "manual": int | None}]
        self.groups = []         # groupes d'indices calés au même TC
        self.meta = {}           # Path -> analyse (vidéo) / aperçu (AAF)
        self.durations = {}
        self.fps_base = 25
        self.real_fps = 25.0
        self.drop_frame = False
        self.session = None
        self.session_fmt = None
        self.session_tracks = []
        self.chip_state = ("wait", None, "")
        self.conn_error = ""
        self.prep = None         # core.AudioPrep (préconversion)
        self.q = queue.Queue()
        self.cancel_flag = threading.Event()
        self.busy = False
        self.states = {}         # indice -> done | failed | current | pending (pendant / après un import)
        self.result = None       # bilan du dernier import
        self.current = None
        self.log_lines = []      # [(texte, tag)] : rejoué après un changement de thème / langue
        self.show_log = False
        self.plan_error = False
        self.update_info = None
        self.settings = None
        self.insp_win = None
        self._recompute_job = None
        self._pulse_job = None
        self._editor = None
        self._view_key = None
        self.stop_btn = self.resume_btn = None
        self.bg_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="paaf-meta")

        self.prefs = self._load_prefs()
        self.lang = self.prefs.get("lang") if self.prefs.get("lang") in STRINGS else "fr"
        self.theme_name = "light" if self.prefs.get("theme") == "light" else "dark"
        self.f = th.make_fonts(self)
        self._init_vars()
        self._load_images()
        self._setup_drop_global()
        self._build()
        self._bind_keys()
        self.after(100, self._poll_queue)
        self.after(300, self.test_connection)
        if self.check_updates_var.get():
            self.after(1500, self._check_updates)
        self.lift()
        self.attributes("-topmost", True)
        self.after(600, lambda: self.attributes("-topmost", False))
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ textes / préférences
    def t(self, key, **kw):
        s = STRINGS[self.lang].get(key)
        if s is None:
            s = STRINGS["fr"].get(key, key)
        return s.format(**kw) if kw else s

    def tn(self, key, n, **kw):
        return self.t(key + ("_one" if n == 1 else "_other"), n=n, **kw)

    def _load_prefs(self):
        try:
            return json.loads(PREFS.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_prefs(self):
        data = {"theme": self.theme_name, "lang": self.lang, "check_updates": self.check_updates_var.get()}
        with contextlib.suppress(Exception):
            PREFS.parent.mkdir(parents=True, exist_ok=True)
            PREFS.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _init_vars(self):
        sr = self.schedule_recompute
        self.mode_var = tk.StringVar(value="fixe")
        self.start_var = tk.StringVar(value="01:00:00:00")
        self.step_var = tk.StringVar(value="00:10:00:00")
        self.gap_var = tk.StringVar(value="00:00:10:00")
        for v in (self.start_var, self.step_var, self.gap_var):
            v.trace_add("write", lambda *a: sr())
        self.link_var = tk.BooleanVar(value=True)
        self.link_var.trace_add("write", lambda *a: sr())
        self.recursive_var = tk.BooleanVar(value=False)
        self.aaf_audio_var = tk.StringVar(value=AAF_AUDIO_MODES[0])
        self.match_var = tk.BooleanVar(value=False)
        self.match_var.trace_add("write", lambda *a: self._on_match())
        self.clip_gain_var = tk.BooleanVar(value=True)
        self.vol_auto_var = tk.BooleanVar(value=True)
        self.copy_video_var = tk.BooleanVar(value=False)
        self.video_audio_var = tk.BooleanVar(value=True)
        self.video_audio_var.trace_add("write", lambda *a: self._on_prep_option())
        self.audio_track_var = tk.StringVar(value="AUDIO VIDEO")
        self.audio_track_var.trace_add("write", lambda *a: self._on_track_name())
        self.new_video_track_var = tk.BooleanVar(value=False)
        self.ffmpeg_ok = core.has_ffmpeg()
        self.ffmpeg_var = tk.BooleanVar(value=self.ffmpeg_ok)
        self.stereo_var = tk.BooleanVar(value=True)
        self.preconvert_var = tk.BooleanVar(value=self.ffmpeg_ok)
        for v in (self.ffmpeg_var, self.stereo_var, self.preconvert_var):
            v.trace_add("write", lambda *a: self._on_prep_option())
        self.check_updates_var = tk.BooleanVar(value=self.prefs.get("check_updates", True))
        self.check_updates_var.trace_add("write", lambda *a: self._save_prefs())
        self.lang_var = tk.StringVar(value=self.lang)
        self.lang_var.trace_add("write", lambda *a: self._on_lang())
        self.insp_tab = tk.StringVar(value="file")
        self.plan_msg = tk.StringVar()

    def _load_images(self):
        self.logos = {}
        for theme, name in (("dark", "paaf-logo-clair-2x.png"), ("light", "paaf-logo-sombre-2x.png")):
            with contextlib.suppress(Exception):
                self.logos[theme] = tk.PhotoImage(file=str(HERE / "assets" / name)).subsample(2)
        with contextlib.suppress(Exception):
            self._icon = tk.PhotoImage(file=str(HERE / "assets" / "logo_256.png"))
            self.iconphoto(True, self._icon)

    # ------------------------------------------------------------------ construction
    def _build(self):
        self.p = th.DARK if self.theme_name == "dark" else th.LIGHT
        th.apply_ttk(self, self.p, self.f)
        self.configure(bg=self.p["bg"])
        self._view_key = None
        self._build_ui()
        self._register_drop()
        self._apply_chip()
        self._refresh()
        self._replay_log()

    def _rebuild(self, reopen_settings=False):
        self._close_editor(commit=True)
        insp = self.insp_win is not None
        self._close_settings()
        self._close_inspector()
        self.ui.destroy()
        self._build()
        if reopen_settings:
            self._open_settings()
        if insp:
            self.open_inspector(self.insp_tab.get())

    def _build_ui(self):
        p, f, t = self.p, self.f, self.t
        bg = p["bg"]
        self.ui = tk.Frame(self, bg=bg)
        self.ui.pack(fill="both", expand=True)

        # ---- en-tête
        head = tk.Frame(self.ui, bg=bg, height=84)
        head.pack(fill="x")
        head.pack_propagate(False)
        tk.Frame(self.ui, bg=p["line"], height=1).pack(fill="x")
        hin = tk.Frame(head, bg=bg)
        hin.pack(fill="both", expand=True, padx=32)
        logo = self.logos.get(self.theme_name)
        if logo:
            tk.Label(hin, image=logo, bg=bg, bd=0).pack(side="left")
        else:
            tk.Label(hin, text=APP_NAME, font=f.h2, bg=bg, fg=p["txt"]).pack(side="left")
        self.update_btn = th.PillButton(hin, " ", self._open_update, p, f, kind="link", font=f.small_b)
        if self.update_info:
            self._show_update()
        self.gear = th.IconButton(hin, "gear", self.toggle_settings, p)
        self.gear.pack(side="right")
        th.IconButton(hin, "sun" if self.theme_name == "dark" else "moon",
                      lambda: self.after_idle(self.toggle_theme), p).pack(side="right", padx=(0, 6))
        self.chip = th.SessionChip(hin, p, f, t("refresh"), self.test_connection,
                                   on_click=lambda: self.open_inspector("session"))
        self.chip.pack(side="right", padx=(0, 22))
        th.Tooltip(self.chip, p, f, lambda: self.conn_error if self.session is None else "")

        # ---- corps
        body = tk.Frame(self.ui, bg=bg)
        body.pack(fill="both", expand=True, padx=40, pady=(0, 20))
        self.body = body

        # barre d'actions
        self.actions = tk.Frame(body, bg=bg)
        th.PillButton(self.actions, t("add_files"), self.add_files, p, f).pack(side="left")
        th.PillButton(self.actions, t("add_folder"), self.add_folder, p, f).pack(side="left", padx=(10, 0))
        self.csv_btn = th.PillButton(self.actions, t("csv_menu"), self._post_csv_menu, p, f, menu=True)
        self.csv_btn.pack(side="left", padx=(10, 0))
        self.import_btn = th.PillButton(self.actions, t("import_btn"), self.start_import, p, f, kind="primary",
                                        font=f.body_b, padx=24, height=42)
        self.import_btn.pack(side="right")
        self.export_link = th.PillButton(self.actions, t("export_plan"), self.export_csv, p, f, kind="link")
        self.export_link.pack(side="right", padx=(0, 14))

        # titre d'état
        self.title_row = tk.Frame(body, bg=bg)
        self.title_dot = th.Dot(self.title_row, 14, p["txt3"], p)
        self.title_dot.pack(side="left", padx=(0, 18))
        self.title_btns = tk.Frame(self.title_row, bg=bg)
        self.title_btns.pack(side="right")
        self.title_line = th.RichLine(self.title_row, p, height=52)
        self.title_line.pack(side="left", fill="x", expand=True)

        self.progress = th.ProgressLine(body, p)

        # barre de placement
        self.place_bar = tk.Frame(body, bg=bg)
        th.Segmented(self.place_bar, self.mode_var, [("fixe", t("mode_fixed")), ("bout", t("mode_butt")),
                                                     ("manuel", t("mode_manual"))], p, f,
                     command=self._on_mode).pack(side="left")
        self.f_start = th.FieldBox(self.place_bar, t("first_point"), self.start_var, p, f)
        self.f_start.pack(side="left", padx=(12, 0))
        self.f_step = th.FieldBox(self.place_bar, t("spacing"), self.step_var, p, f)
        self.f_step.pack(side="left", padx=(12, 0))
        self.f_gap = th.FieldBox(self.place_bar, t("gap"), self.gap_var, p, f)
        self.f_gap.pack(side="left", padx=(12, 0))
        th.PillButton(self.place_bar, t("zoom_all"), lambda: self.timeline.zoom_all(), p, f,
                      font=f.small).pack(side="right")
        th.PillButton(self.place_bar, "+", lambda: self._zoom(2), p, f, width=40,
                      font=f.body).pack(side="right", padx=(0, 10))
        th.PillButton(self.place_bar, "−", lambda: self._zoom(.5), p, f, width=40,
                      font=f.body).pack(side="right", padx=(0, 10))
        tk.Label(self.place_bar, textvariable=self.plan_msg, font=f.small, fg=p["rec"], bg=bg,
                 anchor="w").pack(side="left", padx=(16, 0), fill="x", expand=True)
        self._update_gap_field()

        # timeline
        self.timeline = th.TimelineCanvas(body, p, f, [t("lane_video"), t("lane_aaf"), self._track_name()],
                                          on_click=self._timeline_click, on_double=self._edit_tc)

        # tableau
        headers = {"num": "#", "tc": t("col_tc"), "type": t("col_type"), "file": t("col_file"),
                   "dur": t("col_dur"), "dir": t("col_dir"), "state": t("col_state")}
        self.table = th.PlanTable(body, p, f, headers, {
            "select": self._on_select, "double": self._edit_tc, "move": self.move, "move_to": self.move_to,
            "delete": self.remove_selected, "context": self._context_menu, "sort": self._sort_by,
            "can_reorder": lambda: not self.busy})

        # zone de dépôt (liste vide)
        self.drop = th.DropZone(body, p, f, t("drop_title"), t("drop_sub"),
                                extra_factory=lambda parent: th.ToggleRow(parent, self.recursive_var,
                                                                          t("subfolders"), p, f, bg=p["selrow"]))
        if TkinterDnD is None or not self.dnd_ok:
            self.drop.sub += "\n\n" + (t("dnd_missing") if TkinterDnD is None else
                                      t("dnd_error", e=self.dnd_error))

        # journal
        self.log_head = tk.Label(body, text=th.caps(t("journal")), font=f.cap, fg=p["txt3"], bg=bg, anchor="w")
        self.log_box = tk.Canvas(body, bg=bg, highlightthickness=0, bd=0, height=120)
        self.log = tk.Text(self.log_box, wrap="word", state="disabled", bg=p["well"], fg=p["txt2"],
                           insertbackground=p["txt"], relief="flat", borderwidth=0, highlightthickness=0,
                           font=f.log, padx=14, pady=10, spacing1=3, selectbackground=p["selrow"],
                           selectforeground=p["txt"])
        for tag, col in (("ok", p["grn"]), ("err", p["rec"]), ("info", p["txt3"]), ("cur", p["blu"]),
                         ("warn", p["amb"])):
            self.log.tag_configure(tag, foreground=col)
        self.log_box.bind("<Configure>", self._draw_log_box)

        self.hint = tk.Label(body, text=t("hint"), font=f.small, fg=p["txt3"], bg=bg, anchor="w")

    def _draw_log_box(self, e):
        c = self.log_box
        c.delete("all")
        th.round_rect(c, 0, 0, e.width, e.height, 12, fill=self.p["well"], outline="")
        c.create_window(4, 4, window=self.log, anchor="nw", width=max(10, e.width - 8),
                        height=max(10, e.height - 8))

    # ------------------------------------------------------------------ disposition selon l'état
    def _view(self):
        if self.busy:
            return "import"
        if self.result is not None:
            return "result"
        return "ready" if self.items else "empty"

    def _layout(self):
        v = self._view()
        key = (v, self.show_log, bool(self.result and self._remaining()), self.session is not None)
        if key == self._view_key:
            return
        self._view_key = key
        for w in (self.actions, self.title_row, self.progress, self.place_bar, self.timeline, self.table,
                  self.drop, self.log_head, self.log_box, self.hint):
            w.pack_forget()
        if v in ("empty", "ready"):
            self.actions.pack(fill="x", pady=(22, 0))
            self.title_row.pack(fill="x", pady=(14, 0))
        else:
            self.title_row.pack(fill="x", pady=(20, 0))
        if v == "import":
            self.progress.pack(fill="x", pady=(6, 0))
        if v == "ready":
            self.place_bar.pack(fill="x", pady=(10, 0))
        self.timeline.configure(height=130 if v in ("import", "result") else 150)
        self.timeline.pack(fill="x", pady=(16, 0))
        if v == "empty":
            self.drop.pack(fill="both", expand=True, pady=(16, 0))
        else:
            self.table.pack(fill="both", expand=True, pady=(14, 0))
        if v in ("import", "result") or self.show_log:
            self.log_head.pack(fill="x", pady=(14, 8))
            self.log_box.configure(height=190)
            self.log_box.pack(fill="x")
        elif v == "ready":
            self.hint.pack(fill="x", pady=(12, 0))
        self.import_btn.set_enabled(bool(self.items))
        self.export_link.pack_forget()
        if self.items:
            self.export_link.pack(side="right", padx=(0, 14))
        self._build_title_buttons(v)

    def _build_title_buttons(self, v):
        p, f, t = self.p, self.f, self.t
        for w in self.title_btns.winfo_children():
            w.destroy()
        self.stop_btn = self.resume_btn = None
        if v == "import":
            self.stop_btn = th.PillButton(self.title_btns, t("stop"), self._stop, p, f, kind="danger",
                                          font=f.body_b, padx=22, height=40)
            self.stop_btn.pack(side="right")
        elif v == "result":
            remaining = self._remaining()
            if remaining:
                self.resume_btn = th.PillButton(self.title_btns, self.tn("resume", len(remaining)),
                                                lambda: self.start_import(self._remaining()), p, f,
                                                kind="primary", font=f.body_b, padx=22, height=42)
                self.resume_btn.set_enabled(self.session is not None)
                self.resume_btn.pack(side="right")
                th.PillButton(self.title_btns, t("back"), self.back_to_plan, p, f,
                              height=42).pack(side="right", padx=(0, 10))
            else:
                th.PillButton(self.title_btns, t("done_btn"), self.back_to_plan, p, f, kind="primary",
                              font=f.body_b, padx=28, height=42).pack(side="right")
            th.PillButton(self.title_btns, t("export_log"), self.export_log, p, f,
                          kind="link").pack(side="right", padx=(0, 14))

    def _update_title(self):
        p, f, t = self.p, self.f, self.t
        v = self._view()
        n = len(self.items)
        n_missing = sum(not it["path"].exists() for it in self.items)
        done = sum(1 for s in self.states.values() if s == "done")
        segs = []
        if v == "empty":
            dot = p["txt3"]
            segs = [(t("empty_title"), f.h1, p["txt"], 0), (t("empty_sub"), f.sub, p["txt2"], 18)]
        elif v == "ready":
            dot = p["blu"]
            n_aaf = sum(core.kind_of(it["path"]) == "aaf" for it in self.items)
            parts = []
            if n_aaf:
                parts.append(f"{n_aaf} AAF / OMF")
            if n - n_aaf:
                parts.append(self.tn("n_videos", n - n_aaf))
            span = self._plan_span()
            if span:
                tc = core.TC(self.fps_base)
                parts.append(f"{tc.to_tc(span[0])} → {tc.to_tc(span[1])}")
            sub = " · ".join(parts)
            segs = [(self.tn("n_to_import", n), f.h1, p["txt"], 0), (sub, f.sub, p["txt2"], 18)]
            if n_missing:
                segs += [(" · ", f.sub, p["txt2"], 0), (self.tn("n_missing", n_missing), f.sub_b, p["amb"], 6)]
        elif v == "import":
            dot = p["blu"]
            cur = self.items[self.current] if self.current is not None and self.current < n else None
            segs = [(t("importing", i=min(done + 1, n), n=n), f.h1, p["txt"], 0)]
            if cur:
                segs += [(cur["path"].name + "  →", f.sub, p["txt2"], 18),
                         (cur["tc"] or "", f.mono_xl, p["txt2"], 8)]
        else:
            r = self.result
            failed = sum(1 for s in self.states.values() if s == "failed")
            if r["reason"] == "conn":
                dot, title, sub = p["rec"], t("res_interrupted", k=done, n=n), t("res_conn_sub")
            elif r["reason"] == "cancel":
                dot, title = p["amb"], t("res_stopped", k=done, n=n)
                sub = self.tn("res_cancel_sub", len(self._remaining()))
            elif failed:
                dot, title, sub = p["amb"], t("res_done", k=done, n=n), self.tn("res_failed_sub", failed)
            else:
                dot, title, sub = p["grn"], t("res_done", k=done, n=n), t("res_ok_sub", s=r.get("session") or "")
            segs = [(title, f.h1, p["txt"], 0), (sub, f.sub, p["txt2"], 18)]
            if n_missing:
                segs += [(" · ", f.sub, p["txt2"], 0), (self.tn("n_missing", n_missing), f.sub_b, p["amb"], 6)]
        self.title_dot.set_color(dot)
        self.title_line.set(segs)
        if v == "import":
            total = max(1, len(self.states))
            finished = sum(1 for s in self.states.values() if s in ("done", "failed"))
            self.progress.set((finished + (0.5 if self.current is not None else 0)) / total)
        if self.resume_btn is not None:
            self.resume_btn.set_enabled(self.session is not None)

    def _pulse(self, on=True):
        self._pulse_job = None
        if not self.busy:
            return
        with contextlib.suppress(tk.TclError):
            self.title_dot.set_color(self.p["blu"] if on else th.mix(self.p["blu"], self.p["bg"], .35))
        self._pulse_job = self.after(550, lambda: self._pulse(not on))

    # ------------------------------------------------------------------ réglages
    def toggle_settings(self):
        if self.settings is not None:
            self._close_settings()
        else:
            self._open_settings()

    def _close_settings(self):
        if self.settings is not None:
            self.settings.destroy()
            self.settings = None
            with contextlib.suppress(tk.TclError):
                self.gear.set_active(False)

    def _open_settings(self):
        if self.settings is not None:
            return
        self._close_editor(commit=True)
        p, f, t = self.p, self.f, self.t
        pn = p["panel"]
        self.update_idletasks()
        W, H = self.winfo_width(), self.winfo_height()
        w, h = min(920, W - 48), min(640, H - 108)
        ov = tk.Canvas(self.ui, bg=p["bg"], highlightthickness=0, bd=0)
        self.settings = ov
        ov.place(relx=1.0, x=-24, y=94, anchor="ne", width=w, height=h)
        th.round_rect(ov, 1, 1, w - 1, h - 1, 16, fill=pn, outline=p["line2"])
        inner = tk.Frame(ov, bg=pn)
        inner.place(x=36, y=30, width=w - 72, height=h - 58)

        top = tk.Frame(inner, bg=pn)
        top.pack(fill="x")
        tk.Label(top, text=t("settings_title"), font=f.h2, bg=pn, fg=p["txt"]).pack(side="left")
        th.Segmented(top, self.lang_var, [("fr", "Français"), ("en", "English")], p, f).pack(side="right")

        foot = tk.Frame(inner, bg=pn)
        foot.pack(side="bottom", fill="x")
        tk.Label(foot, text=t("settings_note"), font=f.small, bg=pn, fg=p["txt2"]).pack(side="left")
        th.PillButton(foot, t("close"), self._close_settings, p, f, kind="primary", font=f.body_b, padx=40,
                      height=44).pack(side="right")

        grid = tk.Frame(inner, bg=pn)
        grid.pack(fill="both", expand=True, pady=(26, 0))
        grid.columnconfigure(0, weight=1, uniform="c")
        grid.columnconfigure(1, weight=1, uniform="c")
        left, right = tk.Frame(grid, bg=pn), tk.Frame(grid, bg=pn)
        left.grid(row=0, column=0, sticky="nwe", padx=(0, 40))
        right.grid(row=0, column=1, sticky="nwe")

        def section(parent, title, first=False):
            tk.Label(parent, text=th.caps(title), font=f.cap, bg=pn, fg=p["txt3"]).pack(
                anchor="w", pady=(0 if first else 22, 8))

        def row(parent, var, text, enabled=True, note=None):
            th.ToggleRow(parent, var, text, p, f, enabled=enabled, note=note).pack(anchor="w", pady=6)

        section(left, t("sec_aaf"), first=True)
        th.DropField(left, t("audio"), self.aaf_audio_var,
                     [(k, t("aaf_mode_" + k)) for k in AAF_AUDIO_MODES], p, f, fill=p["field"]).pack(fill="x", pady=(0, 8))
        row(left, self.clip_gain_var, t("clip_gain"))
        row(left, self.vol_auto_var, t("vol_auto"))
        row(left, self.copy_video_var, t("copy_video"))
        row(left, self.match_var, t("match_tracks"))
        section(left, t("sec_list"))
        row(left, self.recursive_var, t("subfolders"))
        row(left, self.link_var, t("link_same_name"))

        section(right, t("sec_video"), first=True)
        row(right, self.video_audio_var, t("video_audio"))
        th.FieldBox(right, t("track"), self.audio_track_var, p, f, stretch=True, fill=p["field"]).pack(
            fill="x", padx=(56, 0), pady=(2, 4))
        ok = self.ffmpeg_ok
        row(right, self.ffmpeg_var, t("ffmpeg_extract") if ok else t("ffmpeg_absent"), enabled=ok)
        row(right, self.preconvert_var, t("preconvert"), enabled=ok)
        row(right, self.stereo_var, t("dual_mono"), enabled=ok)
        row(right, self.new_video_track_var, t("new_video_track"))
        section(right, t("sec_app"))
        row(right, self.check_updates_var, t("check_updates"))
        st = tk.Frame(right, bg=pn)
        st.pack(anchor="w", pady=(6, 0))
        th.Dot(st, 7, p["grn"] if ok else p["rec"], p).pack(side="left", padx=(2, 10))
        tk.Label(st, text=(t("ffmpeg_found") if ok else t("ffmpeg_missing")) + " · " +
                 t("installed_version", v=APP_VERSION), font=f.small, bg=pn, fg=p["txt2"]).pack(side="left")
        self.gear.set_active(True)
        tk.Misc.lift(ov)  # Canvas.lift() remonte un élément du canvas, pas le widget

    def _on_lang(self):
        lang = self.lang_var.get()
        if lang != self.lang and lang in STRINGS:
            self.lang = lang
            self._save_prefs()
            self.after_idle(lambda: self._rebuild(reopen_settings=self.settings is not None))

    def toggle_theme(self):
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        self._save_prefs()
        self._rebuild(reopen_settings=self.settings is not None)

    # ------------------------------------------------------------------ mises à jour
    def _check_updates(self):
        def work():
            try:
                req = urllib.request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json",
                                                                    "User-Agent": "PAAF"})
                with urllib.request.urlopen(req, timeout=6) as r:
                    data = json.load(r)
                tag = data.get("tag_name", "")
                if version_tuple(tag) > version_tuple(APP_VERSION):
                    self.q.put(("update", tag.lstrip("vV"), data.get("html_url")))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _show_update(self):
        self.update_btn.set_text(self.t("update_available", v=self.update_info[0]))
        self.update_btn.pack(side="left", padx=(24, 0))

    def _open_update(self):
        if self.update_info and self.update_info[1]:
            webbrowser.open(self.update_info[1])

    # ------------------------------------------------------------------ clavier / glisser-déposer
    def _bind_keys(self):
        self.bind_all("<Command-o>", lambda e: self.add_files())
        self.bind_all("<Command-O>", lambda e: self.add_folder())
        self.bind_all("<Command-Return>", lambda e: self.start_import())
        self.bind_all("<Command-i>", lambda e: self.open_inspector("file"))
        self.bind_all("<Command-l>", lambda e: self._toggle_log())
        self.bind_all("<Command-comma>", lambda e: self.toggle_settings())
        self.bind("<Escape>", lambda e: self._close_settings())

    def _setup_drop_global(self):
        # Fichiers déposés sur l'icône de l'app (Dock / Finder), menu PAAF > Réglages
        with contextlib.suppress(Exception):
            self.createcommand("::tk::mac::OpenDocument", lambda *paths: self._add_paths(paths))
        with contextlib.suppress(Exception):
            self.createcommand("::tk::mac::ShowPreferences", self.toggle_settings)
        self.dnd_ok, self.dnd_error = False, ""
        if TkinterDnD is None:
            return
        try:
            TkinterDnD._require(self)
            self.dnd_ok = True
        except Exception as e:
            self.dnd_error = str(e)

    def _register_drop(self):
        if not self.dnd_ok:
            return
        for w in (self.ui, self.body, self.table.body, self.table.head, self.drop, self.timeline, self.log):
            with contextlib.suppress(Exception):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<DropEnter>>", self._on_drop_enter)
                w.dnd_bind("<<DropLeave>>", self._on_drop_leave)
                w.dnd_bind("<<Drop>>", self._on_drop)

    def _on_drop_enter(self, event):
        self.drop.set_hot(True)
        return event.action

    def _on_drop_leave(self, event):
        self.drop.set_hot(False)
        return event.action

    def _on_drop(self, event):
        self.drop.set_hot(False)
        if not self.busy:
            self._add_paths(self.tk.splitlist(event.data))
        return event.action

    # ------------------------------------------------------------------ journal / file d'attente
    def write_log(self, text, tag=None):
        self._uncur()
        self.log_lines.append([text, tag])
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _uncur(self):
        """La ligne « en cours » repasse en gris dès qu'une autre arrive."""
        for line in self.log_lines:
            if line[1] == "cur":
                line[1] = "info"
        ranges = self.log.tag_ranges("cur")
        for a, b in zip(ranges[::2], ranges[1::2]):
            self.log.tag_remove("cur", a, b)
            self.log.tag_add("info", a, b)

    def _replay_log(self):
        self.log.configure(state="normal")
        for text, tag in self.log_lines:
            self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _toggle_log(self):
        self.show_log = not self.show_log
        self._layout()

    def export_log(self):
        f = filedialog.asksaveasfilename(title=self.t("export_log"), defaultextension=".txt",
                                         initialfile="PAAF_journal.txt")
        if f:
            Path(f).write_text("".join(text + "\n" for text, _ in self.log_lines), encoding="utf-8")

    def _poll_queue(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self.write_log(msg[1], msg[2])
                elif kind == "session":
                    self._on_session(msg[1])
                elif kind == "session_error":
                    self._on_session_error(msg[1])
                elif kind == "meta":
                    self.meta[msg[1]] = msg[2]
                    self._refresh()
                    if self._selected_path() == msg[1]:
                        self.show_inspector()
                elif kind == "prep":
                    self._refresh()
                    if self._selected_path() == msg[1]:
                        self.show_inspector()
                elif kind == "current":
                    self.current = msg[1]
                    self.states[msg[1]] = "current"
                    self._refresh()
                    self.table.see(msg[1])
                elif kind == "item":
                    self.states[msg[1]] = msg[2]
                    if self.current == msg[1]:
                        self.current = None
                    self._refresh()
                elif kind == "update":
                    self.update_info = (msg[1], msg[2])
                    self._show_update()
                elif kind == "done":
                    ok, n, reason = msg[1], msg[2], msg[3]
                    self.current = None
                    self.result = {"ok": ok, "n": n, "reason": reason, "session": self.session}
                    self._set_busy(False)
                    self.write_log(self.t("log_done", ok=ok, n=n), "ok" if ok == n else "err")
                    if reason == "conn":
                        self._on_session_error(self.t("conn_lost"))
                    self._refresh()
                    self.test_connection()
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    # ------------------------------------------------------------------ connexion
    def test_connection(self):
        if not self.session:
            self.chip_state = ("wait", self.t("connecting"), "")
            self._apply_chip()
        threading.Thread(target=self._connect_worker, daemon=True).start()

    def _connect_worker(self):
        try:
            with core.connect() as engine:
                try:
                    name = engine.session_name()
                except core.CommandError:
                    self.q.put(("session_error", self.t("no_session")))
                    return
                info = {"name": name, "rate": engine.session_timecode_rate(),
                        "version": engine.ptsl_version(), "fmt": core.session_audio_format(engine)}
                try:
                    info["tracks"] = core.session_tracks(engine)
                except Exception:
                    info["tracks"] = []
            self.q.put(("session", info))
        except Exception as e:  # Pro Tools fermé, PTSL indisponible…
            self.q.put(("session_error", str(e).splitlines()[0][:160] if str(e) else self.t("pt_unreachable")))

    def _apply_chip(self):
        state, name, detail = self.chip_state
        if state == "off":
            name = self.t("pt_not_connected")
        self.chip.set(state, name or self.t("connecting"), detail)

    def _on_session_error(self, text):
        self.session = None
        self.conn_error = text
        self.chip_state = ("off", None, "")
        self._apply_chip()
        self.tracks_var_set(self.t("not_connected"))
        self._layout()
        self._update_title()

    def _on_session(self, info):
        name, rate = info["name"], info["rate"]
        rate_name = pt.SessionTimeCodeRate.Name(rate) if rate in pt.SessionTimeCodeRate.values() else str(rate)
        fmt = info["fmt"]
        self.session_fmt = fmt
        self.session_tracks = info.get("tracks", [])
        self.conn_error = ""
        tail = f"{fmt['sr'] / 1000:g} kHz · PTSL {info['version']}"
        if rate not in core.RATES:
            self.session = None
            self.conn_error = self.t("rate_unsupported", r=rate_name)
            self.chip_state = ("warn", name, self.conn_error)
            self._apply_chip()
            self._layout()
            return
        base, real, drop = core.RATES[rate]
        self.session = name
        self.fps_base, self.real_fps, self.drop_frame = base, real, drop
        label = rate_name.replace("STCR_Fps", "").replace("Drop", " DF")
        if label.isdigit() and len(label) > 2:
            label = f"{label[:2]}.{label[2:]}"
        detail = f"{label} fps · {tail}" + (f" · {self.t('df_unsupported')}" if drop else "")
        self.chip_state = ("warn" if drop else "ok", name, detail)
        self._apply_chip()
        self._refresh_session_tree()
        self._update_prep()
        self.schedule_recompute()
        self._layout()
        self._update_title()

    def tracks_var_set(self, text):
        if self.insp_win is not None:
            self.tracks_var.set(text)

    def _refresh_session_tree(self):
        if self.insp_win is None:
            return
        p = self.p
        t = self.sess_tree
        t.delete(*t.get_children())
        if self.session is None and not self.session_tracks:
            self.tracks_var.set(self.t("not_connected"))
            return
        tracks = self.session_tracks
        targets = self._audio_track_names()
        aaf_names = set()
        if self.match_var.get():
            for it in self.items:
                m = self.meta.get(it["path"])
                if isinstance(m, dict) and m.get("tracks"):
                    aaf_names |= {tr["name"] for tr in m["tracks"]}
        types = STRINGS[self.lang]["track_types"]
        nodes = {}
        for tr in tracks:
            parent = nodes.get(tr["folder"], "")
            is_folder = "Folder" in tr["type"]
            tags = ("folder",) if is_folder else ("target",) if tr["name"] in targets else \
                ("match",) if tr["name"] in aaf_names else ()
            fmt = "" if tr["format"] in ("None", "Unknown") else tr["format"].replace("_", ".").lower()
            iid = t.insert(parent, "end", text=("▸ " if is_folder else "") + tr["name"], open=True, tags=tags,
                           values=(types.get(tr["type"], tr["type"]), fmt, "●" if tr["has_clips"] else ""))
            if is_folder:
                nodes[tr["name"]] = iid
        n_audio = sum(1 for tr in tracks if tr["type_id"] == pt.TT_Audio)
        legend = self.t("legend_target") + (" · " + self.t("legend_match") if self.match_var.get() else "")
        self.tracks_var.set(self.t("tracks_summary", n=len(tracks), a=n_audio, legend=legend))
        t.tag_configure("folder", foreground=p["txt3"], font=self.f.small_b)
        t.tag_configure("target", foreground=p["vaud"])
        t.tag_configure("match", foreground=p["grn"])

    def _track_name(self):
        return self.audio_track_var.get().strip() or "AUDIO VIDEO"

    def _audio_track_names(self):
        base = self._track_name()
        return {base, f"{base} MONO"} | {f"{base} {i}" for i in range(1, 17)}

    def _on_track_name(self):
        if hasattr(self, "timeline"):
            self.timeline.lane_labels[2] = self._track_name()
            self.timeline.redraw()
        self._refresh_session_tree()

    # ------------------------------------------------------------------ préconversion / analyses
    def _on_prep_option(self):
        self._update_prep()
        if hasattr(self, "table"):
            self._refresh()
        self.show_inspector()

    def _on_match(self):
        self._refresh_session_tree()
        self.show_inspector()

    def _update_prep(self):
        """(Re)crée l'extracteur ffmpeg au format de la session et lance la préconversion."""
        if not (self.session and self.session_fmt and self.session_fmt.get("audio_dir")):
            return
        if not (self.ffmpeg_var.get() and self.video_audio_var.get() and core.has_ffmpeg()):
            return
        key = core.AudioPrep.make_key(self.session_fmt, self.stereo_var.get())
        if self.prep is None or self.prep.key() != key:
            if self.prep:
                self.prep.shutdown()
            self.prep = core.AudioPrep(self.session_fmt, self.stereo_var.get())
        if self.preconvert_var.get():
            for it in self.items:
                self._prep_submit(it["path"])

    def _prep_submit(self, p):
        if self.prep is None or core.kind_of(p) != "video" or not p.exists():
            return
        known = p in self.prep.futures
        fut = self.prep.submit(p)
        if not known:
            fut.add_done_callback(lambda f, p=p: self.q.put(("prep", p)))

    def _load_meta(self, p):
        if p in self.meta:
            return
        self.meta[p] = None  # en cours
        k = core.kind_of(p)

        def work():
            try:
                if k == "video":
                    data = core.analyze_audio(p)
                    data["duration_s"] = core.media_duration_seconds(p)
                elif k == "aaf":
                    data = aaf_inspect.inspect(p) if aaf_inspect.available() else {
                        "error": self.t("pyaaf2_missing")}
                else:
                    data = {"error": "?"}
            except Exception as e:
                data = {"error": str(e)}
            self.q.put(("meta", p, data))

        if p.exists():
            self.bg_pool.submit(work)
        else:
            self.meta[p] = {"error": self.t("file_missing")}

    # ------------------------------------------------------------------ liste
    def _touch(self):
        """La liste change : on quitte le bilan du dernier import."""
        if self.result is not None:
            self.result = None
            self.states = {}

    def _add_paths(self, paths):
        if self.busy:
            return
        known = {it["path"] for it in self.items}
        added = 0
        for p in core.expand_paths([Path(x) for x in paths], self.recursive_var.get()):
            p = p.expanduser()
            if p in known or not core.kind_of(p):
                continue
            self.items.append({"path": p, "tc": None, "manual": None})
            known.add(p)
            added += 1
            self._load_meta(p)
            if self.preconvert_var.get():
                self._prep_submit(p)
        if added:
            self._touch()
            self.table.sort_col = None
            self.recompute()

    def add_files(self):
        if self.busy:
            return
        paths = filedialog.askopenfilenames(title="AAF / OMF / vidéos", filetypes=FILETYPES)
        if paths:
            self._add_paths(paths)

    def add_folder(self):
        if self.busy:
            return
        d = filedialog.askdirectory(title=self.t("add_folder"))
        if d:
            self._add_paths([d])

    def _post_csv_menu(self):
        m = tk.Menu(self, tearoff=False)
        m.add_command(label=self.t("csv_load"), command=self.load_csv)
        m.add_command(label=self.t("csv_export"), command=self.export_csv,
                      state="normal" if self.items else "disabled")
        b = self.csv_btn
        m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height() + 4)

    def selected_indexes(self):
        return sorted(i for i in self.table.selection if i < len(self.items))

    def _selected_path(self):
        idx = self.selected_indexes()
        return self.items[idx[0]]["path"] if idx else None

    def _on_select(self):
        self._update_timeline()
        self.show_inspector()

    def move(self, delta):
        idx = self.selected_indexes()
        if self.busy or not idx:
            return "break"
        if (delta < 0 and idx[0] == 0) or (delta > 0 and idx[-1] == len(self.items) - 1):
            return "break"
        order = idx if delta < 0 else list(reversed(idx))
        for i in order:
            self.items[i], self.items[i + delta] = self.items[i + delta], self.items[i]
        self._touch()
        self.table.sort_col = None
        self.recompute(select=[i + delta for i in idx])
        return "break"

    def move_to(self, idx, target):
        """Glisser-déposer dans le tableau : déplace les lignes `idx` avant la ligne `target`."""
        if self.busy or not idx:
            return
        moving = [self.items[i] for i in idx]
        rest = [it for i, it in enumerate(self.items) if i not in set(idx)]
        pos = target - sum(1 for i in idx if i < target)
        new = rest[:pos] + moving + rest[pos:]
        if new == self.items:
            return
        self.items = new
        self._touch()
        self.table.sort_col = None
        self.recompute(select=list(range(pos, pos + len(moving))))

    def _sort_by(self, key):
        if self.busy or not self.items:
            return
        if key == "file":
            self.items.sort(key=lambda it: core.natural_key(it["path"]))
        else:
            tc = core.TC(self.fps_base)

            def frames(it):
                try:
                    return tc.to_frames(it["tc"]) if it["tc"] else 0
                except ValueError:
                    return 0
            self.items.sort(key=frames)
        self._touch()
        self.table.sort_col = key
        self.recompute(select=[])

    def sort_items(self):
        self._sort_by("file")

    def remove_selected(self):
        if self.busy:
            return "break"
        idx = self.selected_indexes()
        if not idx:
            return "break"
        for i in reversed(idx):
            del self.items[i]
        self._touch()
        nxt = min(idx[0], len(self.items) - 1)
        self.recompute(select=[nxt] if nxt >= 0 else [])
        self.show_inspector()
        return "break"

    def clear_items(self):
        if self.busy:
            return
        self.items.clear()
        self._touch()
        self.table.sort_col = None
        self.recompute(select=[])
        self.show_inspector()

    def _context_menu(self, x, y):
        if self.busy:
            return
        t = self.t
        sel = self.selected_indexes()
        st = "normal" if sel else "disabled"
        m = tk.Menu(self, tearoff=False)
        m.add_command(label=t("ctx_inspect"), accelerator="⌘I", command=lambda: self.open_inspector("file"),
                      state=st)
        m.add_command(label=t("ctx_edit_tc"), accelerator="↩", state=st,
                      command=lambda: sel and self._edit_tc(sel[0]))
        m.add_separator()
        m.add_command(label=t("ctx_up"), accelerator="⌥↑", command=lambda: self.move(-1), state=st)
        m.add_command(label=t("ctx_down"), accelerator="⌥↓", command=lambda: self.move(1), state=st)
        m.add_command(label=t("ctx_sort"), command=self.sort_items)
        m.add_separator()
        m.add_command(label=t("ctx_remove"), accelerator="⌫", command=self.remove_selected, state=st)
        m.add_command(label=t("ctx_clear"), command=self.clear_items)
        m.tk_popup(x, y)

    def _timeline_click(self, i):
        self.table.select([i])

    def _zoom(self, factor):
        center = None
        sel = self.selected_indexes()
        if sel and self.items[sel[0]]["tc"]:
            with contextlib.suppress(ValueError):
                center = core.TC(self.fps_base).to_frames(self.items[sel[0]]["tc"])
        self.timeline.zoom_by(factor, center)

    # ------------------------------------------------------------------ édition du TC dans la liste
    def _edit_tc(self, i):
        if self.busy or i >= len(self.items):
            return
        self._close_editor(commit=False)
        if i not in self.table.selection:
            self.table.select([i])
        self.table.see(i)
        self.update_idletasks()
        bbox = self.table.cell_bbox(i, "tc")
        if not bbox:
            return
        x, y, w, h = bbox
        p = self.p
        var = tk.StringVar(value=self.items[i]["tc"] or self.start_var.get())
        e = tk.Entry(self.table.body, textvariable=var, font=self.f.mono_l, bg=p["chip"], fg=p["txt"],
                     insertbackground=p["txt"], relief="flat", bd=0, highlightthickness=1,
                     highlightbackground=p["blu"], highlightcolor=p["blu"], selectbackground=p["selrow"],
                     selectforeground=p["txt"])
        self.table.body.create_window(x, y, window=e, anchor="nw", width=w, height=h, tags="editor")
        e.focus_set()
        e.select_range(0, "end")
        e.icursor("end")
        self._editor = (e, var, i)
        e.bind("<Return>", lambda ev: (self._close_editor(commit=True), "break")[1])
        e.bind("<KP_Enter>", lambda ev: (self._close_editor(commit=True), "break")[1])
        e.bind("<Escape>", lambda ev: (self._close_editor(commit=False), "break")[1])
        e.bind("<FocusOut>", lambda ev: self._close_editor(commit=True))
        e.bind("<Tab>", lambda ev: (self._close_editor(commit=True, next_row=True), "break")[1])

    def _close_editor(self, commit, next_row=False):
        if not self._editor:
            return
        e, var, i = self._editor
        self._editor = None
        value = var.get()
        with contextlib.suppress(tk.TclError):
            self.table.body.delete("editor")
            e.destroy()
            self.table.body.focus_set()
        if not commit or i >= len(self.items):
            return
        tc = core.TC(self.fps_base)
        try:
            frames = tc.to_frames(value)
        except ValueError as err:
            self.plan_msg.set(str(err))
            return
        group = next((g for g in self.groups if i in g), [i])
        if self.items[i]["tc"] == tc.to_tc(frames) and not next_row:
            return
        if self.mode_var.get() != "manuel":
            self._freeze_manual()
            self.mode_var.set("manuel")
            self._update_gap_field()
        for j in group:
            self.items[j]["manual"] = frames
        self._touch()
        self.recompute(select=[i])
        if next_row:
            nxt = max(group) + 1
            if nxt < len(self.items):
                self.table.select([nxt])
                self.after(10, lambda: self._edit_tc(nxt))

    def _freeze_manual(self):
        tc = core.TC(self.fps_base)
        for it in self.items:
            if it["tc"] and it.get("manual") is None:
                with contextlib.suppress(ValueError):
                    it["manual"] = tc.to_frames(it["tc"])

    def _on_mode(self):
        if self.mode_var.get() == "manuel":
            self._freeze_manual()
        else:
            for it in self.items:
                it["manual"] = None
        self._update_gap_field()
        self._touch()
        self.schedule_recompute()

    def _update_gap_field(self):
        if hasattr(self, "f_gap"):
            manual = self.mode_var.get() == "manuel"
            self.f_gap.set_enabled(self.mode_var.get() == "bout")
            self.f_step.set_enabled(not manual)
            self.f_start.set_enabled(not manual)

    # ------------------------------------------------------------------ plan
    def schedule_recompute(self):
        if self._recompute_job:
            self.after_cancel(self._recompute_job)
        self._recompute_job = self.after(250, self.recompute)

    def _duration(self, p: Path):
        m = self.meta.get(p)
        if isinstance(m, dict) and m.get("duration_s") is not None:
            return m["duration_s"]
        if p not in self.durations:
            self.durations[p] = core.media_duration_seconds(p)
        return self.durations[p]

    def _known_duration(self, p: Path):
        """Durée déjà connue (sans lancer ffprobe) : pour l'affichage."""
        m = self.meta.get(p)
        if isinstance(m, dict) and m.get("duration_s") is not None:
            return m["duration_s"]
        return self.durations.get(p)

    def recompute(self, select=None):
        self._recompute_job = None
        tc = core.TC(self.fps_base)
        self.plan_msg.set("")
        self.plan_error = False
        try:
            start = tc.to_frames(self.start_var.get())
            step = tc.to_frames(self.step_var.get())
            gap = tc.to_frames(self.gap_var.get())
        except ValueError as e:
            self.plan_msg.set(str(e))
            self.plan_error = True
            self._refresh(select)
            return
        paths = [it["path"] for it in self.items]
        manual = {i: it.get("manual") for i, it in enumerate(self.items)}
        frames, unknown, self.groups = core.schedule(
            paths, self.mode_var.get(), start, step, gap, self.real_fps, self._duration,
            manual=manual, link_same_name=self.link_var.get())
        for it, f in zip(self.items, frames):
            it["tc"] = tc.to_tc(f)
        if unknown:
            self.plan_msg.set(self.tn("unknown_durations", unknown))
        self._refresh(select)
        if select is None:
            self.show_inspector()

    def _plan_frames(self):
        """[(indice, début, durée)] en images, pour la timeline et le titre."""
        tc = core.TC(self.fps_base)
        try:
            step = tc.to_frames(self.step_var.get()) or 600 * self.fps_base
        except ValueError:
            step = 600 * self.fps_base
        out = []
        for i, it in enumerate(self.items):
            if not it["tc"]:
                continue
            try:
                start = tc.to_frames(it["tc"])
            except ValueError:
                continue
            secs = self._known_duration(it["path"])
            out.append((i, start, round(secs * self.fps_base) if secs else step))
        return out

    def _plan_span(self):
        fr = self._plan_frames()
        if not fr:
            return None
        return min(s for _, s, _ in fr), max(s + d for _, s, d in fr)

    def _audio_label(self, p):
        if not self.video_audio_var.get():
            return None
        m = self.meta.get(p)
        if m is None:
            return "Audio" if p.exists() else None
        if not isinstance(m, dict) or m.get("error") or not m.get("streams"):
            return None
        try:
            layout, chans = core.audio_layout(m, self.session_fmt or {"interleaved": True}, self.stereo_var.get())
        except Exception:
            return None
        n = len(chans)
        return "A1" if n == 1 else f"A1–A{n}"

    def _update_timeline(self):
        sel = set(self.selected_indexes())
        items = []
        for i, start, dur in self._plan_frames():
            p = self.items[i]["path"]
            if not p.exists():
                st = "missing"
            else:
                st = self.states.get(i) or ("selected" if i in sel else "plan")
            k = core.kind_of(p)
            items.append({"index": i, "kind": k, "name": p.name, "start": start, "dur": dur, "state": st,
                          "audio": self._audio_label(p) if k == "video" else None})
        playhead = None
        if self.current is not None:
            playhead = next((s for i, s, _ in self._plan_frames() if i == self.current), None)
        try:
            empty_start = core.TC(self.fps_base).to_frames(self.start_var.get())
        except ValueError:
            empty_start = 3600 * self.fps_base
        self.timeline.set_items(items, fps=self.fps_base, playhead=playhead, empty_start=empty_start)

    def _rows(self):
        p, t = self.p, self.t
        group_of = {}
        for g in self.groups:
            for i in g:
                group_of[i] = g
        home = str(Path.home())
        rows = []
        for i, it in enumerate(self.items):
            path = it["path"]
            k = core.kind_of(path)
            exists = path.exists()
            st = self.states.get(i)
            name_color, state, state_color = None, "", p["txt3"]
            if not exists:
                state, state_color, name_color = t("st_missing"), p["rec"], p["rec"]
            elif st == "done":
                state, state_color = t("st_done"), p["grn"]
            elif st == "current":
                state, state_color = t("st_current"), p["blu"]
            elif st == "pending":
                state = t("st_pending")
            elif st == "failed":
                state, state_color, name_color = t("st_failed"), p["rec"], p["rec"]
            elif self.meta.get(path) is None:
                state = t("st_analyzing")
            elif k == "video" and self.prep is not None and self.video_audio_var.get() and self.ffmpeg_var.get():
                ps = self.prep.status(path)
                if ps == "…":
                    state = t("st_converting")
                elif ps == "erreur":
                    state, state_color = t("st_conv_err"), p["amb"]
            secs = self._known_duration(path)
            dur = fmt_clock(secs) if secs else ("…" if exists and self.meta.get(path) is None else "—")
            folder = str(path.parent)
            if folder.startswith(home):
                folder = "~" + folder[len(home):]
            rows.append({
                "num": i + 1, "tc": it["tc"] or "", "linked": len(group_of.get(i, [])) > 1,
                "kind": ("AAF" if path.suffix.lower() == ".aaf" else "OMF") if k == "aaf" else t("kind_video"),
                "kind_color": p["aaf"] if k == "aaf" else p["vid"], "name": path.name, "name_color": name_color,
                "dur": dur, "folder": folder, "state": state, "state_color": state_color})
        return rows

    def _refresh(self, select=None):
        if not hasattr(self, "table"):
            return
        rows = self._rows()
        self.table.set_rows(rows, show_state=any(r["state"] for r in rows))
        if select is not None:
            self.table.select([i for i in select if 0 <= i < len(self.items)], notify=False)
        self._update_timeline()
        self._layout()
        self._update_title()

    # ------------------------------------------------------------------ inspecteur (fenêtre)
    def open_inspector(self, tab="file"):
        self.insp_tab.set(tab)
        if self.insp_win is not None:
            self.insp_win.lift()
            self._insp_switch()
            return
        p, f, t = self.p, self.f, self.t
        pn = p["panel"]
        w = tk.Toplevel(self)
        w.title(t("inspector"))
        w.geometry("820x660")
        w.minsize(560, 420)
        w.configure(bg=pn)
        self.insp_win = w
        w.protocol("WM_DELETE_WINDOW", self._close_inspector)
        w.bind("<Command-w>", lambda e: self._close_inspector())
        w.bind("<Escape>", lambda e: self._close_inspector())
        top = tk.Frame(w, bg=pn)
        top.pack(fill="x", padx=24, pady=(20, 14))
        th.Segmented(top, self.insp_tab, [("file", t("insp_file")), ("session", t("insp_session"))], p, f,
                     command=self._insp_switch).pack(side="left")

        self.insp_file = tk.Frame(w, bg=pn)
        self.insp_text = tk.Text(self.insp_file, height=6, wrap="word", state="disabled", bg=pn, fg=p["txt"],
                                 relief="flat", borderwidth=0, highlightthickness=0, font=f.small, padx=0,
                                 pady=0, spacing1=3, selectbackground=p["selrow"])
        for tag, col in (("ok", p["grn"]), ("err", p["rec"]), ("warn", p["amb"]), ("info", p["txt3"]),
                         ("accent", p["blu"]), ("violet", p["aaf"])):
            self.insp_text.tag_configure(tag, foreground=col)
        self.insp_text.tag_configure("title", font=f.body_b, foreground=p["txt"])
        self.insp_text.pack(fill="x", padx=24)
        tw = tk.Frame(self.insp_file, bg=pn)
        tw.pack(fill="both", expand=True, padx=24, pady=(10, 20))
        self.insp_tree = ttk.Treeview(tw, columns=("pos", "len", "fmt", "info"))
        for c, key, width, st in [("#0", "insp_col_item", 190, True), ("pos", "insp_col_pos", 100, False),
                                  ("len", "insp_col_len", 90, False), ("fmt", "insp_col_fmt", 130, False),
                                  ("info", "insp_col_info", 120, True)]:
            self.insp_tree.heading(c, text=th.caps(t(key)), anchor="w")
            self.insp_tree.column(c, width=width, minwidth=40, stretch=st, anchor="w")
        self.insp_tree.tag_configure("head", foreground=p["blu"], font=f.small_b)
        self.insp_tree.tag_configure("match", foreground=p["grn"])
        self.insp_tree.tag_configure("muted", foreground=p["txt3"])
        isb = ttk.Scrollbar(tw, orient="vertical", command=self.insp_tree.yview,
                            style="Panel.Vertical.TScrollbar")
        self.insp_tree.configure(yscrollcommand=isb.set)
        isb.pack(side="right", fill="y")
        self.insp_tree.pack(side="left", fill="both", expand=True)

        self.insp_sess = tk.Frame(w, bg=pn)
        self.tracks_var = tk.StringVar(value=t("not_connected"))
        bar = tk.Frame(self.insp_sess, bg=pn)
        bar.pack(fill="x", padx=24, pady=(0, 8))
        tk.Label(bar, textvariable=self.tracks_var, font=f.small, bg=pn, fg=p["txt3"]).pack(side="left")
        th.PillButton(bar, t("refresh"), self.test_connection, p, f, kind="mini").pack(side="right")
        sw = tk.Frame(self.insp_sess, bg=pn)
        sw.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        self.sess_tree = ttk.Treeview(sw, columns=("type", "format", "clips"))
        for c, key, width, st in [("#0", "sess_col_track", 300, True), ("type", "sess_col_type", 130, False),
                                  ("format", "sess_col_fmt", 90, False), ("clips", "sess_col_clips", 60, False)]:
            self.sess_tree.heading(c, text=th.caps(t(key)), anchor="w")
            self.sess_tree.column(c, width=width, minwidth=40, stretch=st, anchor="w")
        ssb = ttk.Scrollbar(sw, orient="vertical", command=self.sess_tree.yview, style="Panel.Vertical.TScrollbar")
        self.sess_tree.configure(yscrollcommand=ssb.set)
        ssb.pack(side="right", fill="y")
        self.sess_tree.pack(side="left", fill="both", expand=True)
        if self.dnd_ok:
            with contextlib.suppress(Exception):
                self.insp_tree.drop_target_register(DND_FILES)
                self.insp_tree.dnd_bind("<<Drop>>", self._on_drop)
        self._insp_switch()
        self.show_inspector()
        self._refresh_session_tree()

    def _insp_switch(self):
        if self.insp_win is None:
            return
        file_tab = self.insp_tab.get() == "file"
        (self.insp_sess if file_tab else self.insp_file).pack_forget()
        (self.insp_file if file_tab else self.insp_sess).pack(fill="both", expand=True)

    def _close_inspector(self):
        if self.insp_win is not None:
            with contextlib.suppress(tk.TclError):
                self.insp_win.destroy()
            self.insp_win = None

    def _insp_write(self, parts):
        t = self.insp_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        for text, tag in parts:
            t.insert("end", text, tag)
        t.configure(state="disabled")
        self.after_idle(self._fit_insp_text)

    def _fit_insp_text(self):
        """Hauteur de la zone de texte ajustée au contenu (lignes affichées)."""
        if self.insp_win is None:
            return
        t = self.insp_text
        try:
            lines = t.count("1.0", "end-1c", "displaylines")
            lines = lines[0] if isinstance(lines, tuple) else lines
            t.configure(height=max(3, min(14, int(lines or 0) + 1)))
        except Exception:
            pass

    def show_inspector(self):
        if self.insp_win is None or not hasattr(self, "table"):
            return
        t = self.t
        self.insp_tree.delete(*self.insp_tree.get_children())
        idx = self.selected_indexes()
        if not idx:
            self._insp_write([(t("inspector") + "\n", "title"), (t("insp_empty"), "info")])
            return
        i = idx[0]
        it = self.items[i]
        p = it["path"]
        if p not in self.meta:
            self._load_meta(p)
        m = self.meta.get(p)
        partners = [j for g in self.groups if i in g for j in g if j != i]
        head = [(p.name + "\n", "title")]
        if partners:
            other = t("the_video") if core.kind_of(p) == "aaf" else t("the_aaf")
            head.append((t("insp_linked", other=other) + "\n", "violet"))
        if m is None:
            self._insp_write(head + [(t("insp_analyzing"), "info")])
            return
        if m.get("error"):
            self._insp_write(head + [(m["error"], "warn")])
            return
        if core.kind_of(p) == "aaf":
            self._inspect_aaf(head, it, m)
        else:
            self._inspect_video(head, it, m)

    def _inspect_aaf(self, head, it, m):
        t = self.t
        fps = self.fps_base
        tc = core.TC(fps)
        try:
            base = tc.to_frames(it["tc"]) if it["tc"] else 0
        except ValueError:
            base = 0
        rate = float(m["edit_rate"]) if m.get("edit_rate") else fps
        snd = [tr for tr in m["tracks"] if tr["kind"] == "Sound"]
        session_names = {tr["name"] for tr in self.session_tracks}
        matching = [tr for tr in snd if tr["name"] in session_names]
        comp = "" if core.pair_key(Path(m["name"] + ".aaf")) == core.pair_key(it["path"]) else \
            t("insp_comp", name=m["name"])
        parts = head + [
            (comp + t("insp_aaf_line", dur=fmt_duration(m["duration_s"], fps), tracks=len(snd),
                      media=len(m["sources"])) + (t("insp_embedded") if m.get("embedded") else "") + "\n", None),
            (t("insp_placed", tc=it["tc"], end=tc.to_tc(base + round((m["duration_s"] or 0) * fps))) + "\n",
             "accent"),
        ]
        if m.get("fps"):
            parts.append((t("insp_origin", tc=aaf_inspect.frames_to_tc(m["start_frames"], m["fps"])) + "\n",
                          "info"))
        if self.match_var.get():
            parts.append((t("insp_merge", a=len(matching), b=len(snd)) + "\n", "ok" if matching else "warn"))
        elif matching:
            parts.append((t("insp_samename", n=len(matching)) + "\n", "info"))
        for a, b in m.get("pairs", []):
            parts.append((t("insp_pair", a=m["tracks"][a]["name"], b=m["tracks"][b]["name"]) + "\n", "violet"))
        self._insp_write(parts)

        tree = self.insp_tree
        root = tree.insert("", "end", text=t("insp_tracks", n=len(m["tracks"])), open=True, tags=("head",))
        for tr in m["tracks"]:
            exists = tr["name"] in session_names
            status = (t("insp_st_merge") if self.match_var.get() else t("insp_st_taken")) \
                if exists else t("insp_st_new")
            node = tree.insert(root, "end", text=tr["name"], open=False, tags=("match",) if exists else (),
                               values=("", "", "audio" if tr["kind"] == "Sound" else "image",
                                       t("insp_clips", n=len(tr["clips"]), status=status)))
            for c in tr["clips"][:400]:
                src = m["sources"][c["source"]] if c.get("source") is not None else {}
                pos = base + round(c["pos"] / rate * fps)
                tree.insert(node, "end", text=c["name"], values=(
                    tc.to_tc(pos), fmt_duration(c["length"] / rate, fps), self._src_fmt(src),
                    self._src_info(src)))
            if len(tr["clips"]) > 400:
                tree.insert(node, "end", text=t("insp_more", n=len(tr["clips"]) - 400), tags=("muted",))
        media = tree.insert("", "end", text=t("insp_media", n=len(m["sources"])), open=True, tags=("head",))
        for s in m["sources"]:
            origin = fmt_duration(s["origin_s"], fps) if s.get("origin_s") is not None else ""
            tree.insert(media, "end", text=s.get("file") or s.get("name") or "?", values=(
                origin, fmt_duration(s.get("duration_s"), fps), self._src_fmt(s),
                self._src_info(s, with_location=True)))

    def _src_fmt(self, s):
        bits = []
        if s.get("sr"):
            bits.append(f"{s['sr'] / 1000:g} kHz")
        if s.get("bits"):
            bits.append(self.t("bits", n=s["bits"]))
        if s.get("channels"):
            bits.append({1: "mono", 2: self.t("stereo")}.get(s["channels"], self.t("n_ch", n=s["channels"])))
        return " · ".join(bits)

    def _src_info(self, s, with_location=False):
        info = [f"{k} {v}" for k, v in (s.get("comments") or {}).items()]
        if s.get("channel") and s.get("channels", 1) and s.get("channels", 1) > 1:
            info.append(self.t("channel", n=s["channel"]))
        if with_location:
            if s.get("embedded"):
                info.append(self.t("embedded"))
            elif s.get("path"):
                info.append(("" if s.get("exists") else self.t("src_missing")) + Path(s["path"]).name)
            if s.get("uses"):
                info.append(self.t("n_clips", n=s["uses"]))
        return " · ".join(info)

    def _inspect_video(self, head, it, m):
        t = self.t
        fps = self.fps_base
        parts = head + [(t("insp_video_line", dur=fmt_duration(m.get("duration_s"), fps), tc=it["tc"]) + "\n",
                         "accent")]
        streams = m.get("streams", [])
        if not streams:
            parts.append((t("insp_no_audio") + "\n", "warn"))
        else:
            desc = ", ".join(t("insp_stream_desc", codec=s["codec"], sr=s["sr"], ch=s["channels"]) for s in streams)
            parts.append((t("insp_audio", desc=desc) + "\n", None))
            parts.append((t("insp_channels", label=m.get("label", "")) + "\n",
                          "violet" if m.get("kind") in ("dual_mono", "left", "right") else None))
            if m.get("peaks"):
                l, r, d = m["peaks"]
                fmt = lambda v: "−∞" if v <= -199 else f"{v:.1f}"  # noqa: E731
                parts.append((t("insp_peaks", l=fmt(l), r=fmt(r), d=fmt(d)) + "\n", "info"))
            if self.video_audio_var.get():
                fmt_s = self.session_fmt or {"interleaved": True}
                try:
                    layout, _ = core.audio_layout(m, fmt_s, self.stereo_var.get())
                    base = self._track_name()
                    dest = {"mono": t("dest_mono", b=base), "stereo": t("dest_stereo", b=base),
                            "split": t("dest_split", b=base, n=m["channels"])}[layout]
                    parts.append((t("insp_dest", dest=dest) + "\n", "accent"))
                except RuntimeError as e:
                    parts.append((f"{e}\n", "warn"))
                st = self.prep.status(it["path"]) if self.prep else None
                if self.ffmpeg_var.get() and self.session_fmt:
                    label = {"prêt": t("conv_ready"), "…": t("conv_running"), "erreur": t("conv_failed"),
                             None: t("conv_later")}[st]
                    parts.append((t("insp_conv", sr=f"{self.session_fmt['sr'] / 1000:g}",
                                    fmt=self.session_fmt["label"], label=label) + "\n",
                                  "ok" if st == "prêt" else "warn" if st == "erreur" else "info"))
            else:
                parts.append((t("insp_audio_off") + "\n", "info"))
        self._insp_write(parts)
        tree = self.insp_tree
        root = tree.insert("", "end", text=t("insp_streams", n=len(streams)), open=True, tags=("head",))
        for s in streams:
            tree.insert(root, "end", text=t("insp_stream", n=s["index"] + 1), values=(
                "", "", f"{s['sr']} Hz · {t('n_ch', n=s['channels'])}", f"{s['codec']} {s['layout']}".strip()))

    # ------------------------------------------------------------------ plan CSV
    def load_csv(self):
        if self.busy:
            return
        f = filedialog.askopenfilename(title=self.t("csv_load_title"),
                                       filetypes=[("CSV", "*.csv *.txt"), ("*", "*")])
        if not f:
            return
        tc = core.TC(self.fps_base)
        try:
            plan = core.read_csv_plan(Path(f), tc)
        except ValueError as e:
            messagebox.showerror(self.t("csv_menu"), str(e), parent=self)
            return
        self.items = [{"path": p, "tc": t, "manual": tc.to_frames(t)} for p, t in plan]
        for it in self.items:
            self._load_meta(it["path"])
        self.mode_var.set("manuel")
        self._update_gap_field()
        self._touch()
        self.recompute(select=[])

    def export_csv(self):
        if not self.items:
            return
        f = filedialog.asksaveasfilename(title=self.t("export_plan"), defaultextension=".csv",
                                         initialfile="plan_import.csv")
        if f:
            Path(f).write_text("".join(f"{it['path']};{it['tc']}\n" for it in self.items), encoding="utf-8")
            self.write_log(self.t("log_plan_exported", f=f), "info")

    # ------------------------------------------------------------------ import
    def _set_busy(self, busy):
        self.busy = busy
        self._close_settings()
        if busy:
            self._pulse()
        self._refresh()

    def _remaining(self):
        return sorted(i for i, s in self.states.items() if s in ("pending", "failed", "current")
                      and i < len(self.items) and self.items[i]["path"].exists())

    def back_to_plan(self):
        self.result = None
        self.states = {}
        self._refresh()

    def _stop(self):
        self.cancel_flag.set()
        if self.stop_btn is not None:
            self.stop_btn.set_text(self.t("stopping"))
            self.stop_btn.set_enabled(False)

    def _build_args(self):
        mode = self.aaf_audio_var.get()
        return SimpleNamespace(
            copy_audio=(mode == "copy"),
            convert_audio=(mode == "convert"),
            consolidate=(mode == "consolidate"),
            handles=1000,
            match_tracks=self.match_var.get(),
            copy_video=self.copy_video_var.get(),
            no_clip_gain=not self.clip_gain_var.get(),
            no_volume_automation=not self.vol_auto_var.get(),
            new_video_track=self.new_video_track_var.get(),
            no_video_audio=not self.video_audio_var.get(),
            audio_track=self._track_name(),
            no_ffmpeg=not self.ffmpeg_var.get(),
            no_stereo_detect=not self.stereo_var.get(),
        )

    def start_import(self, indexes=None):
        if self.busy:
            return
        t = self.t
        self._close_editor(commit=True)
        if not self.items:
            messagebox.showinfo(APP_NAME, t("msg_no_files"), parent=self)
            return
        if self.session is None:
            messagebox.showwarning(APP_NAME, t("msg_not_connected"), parent=self)
            return
        if self.drop_frame:
            messagebox.showwarning(APP_NAME, t("msg_drop_frame"), parent=self)
            return
        if indexes is None:
            self.recompute()
            if self.plan_error:
                return
            missing = [it["path"].name for it in self.items if not it["path"].exists()]
            if missing and not messagebox.askyesno(APP_NAME, self.tn("msg_missing", len(missing)), parent=self):
                return
            todo = [i for i, it in enumerate(self.items) if it["path"].exists()]
            if not todo:
                return
            if not messagebox.askyesno(APP_NAME, self.tn("msg_confirm", len(todo), s=self.session), parent=self):
                return
            self.states = {}
        else:
            todo = list(indexes)
            if not todo or not messagebox.askyesno(APP_NAME, self.tn("msg_resume", len(todo), s=self.session),
                                                   parent=self):
                return
        for i in todo:
            self.states[i] = "pending"
        plan = [(i, self.items[i]["path"], self.items[i]["tc"]) for i in todo]
        self.result = None
        self.current = None
        self.cancel_flag.clear()
        self.write_log(self.tn("log_start", len(plan), s=self.session), "info")
        args = self._build_args()
        if not args.no_ffmpeg and not args.no_video_audio:
            self._update_prep()
        self._set_busy(True)
        threading.Thread(target=self._import_worker, args=(plan, args, self.prep), daemon=True).start()

    def _import_worker(self, plan, args, prep):
        ok, reason = 0, None
        try:
            with contextlib.redirect_stdout(QueueWriter(self.q)), core.connect() as engine:
                state = core.VideoAudioState(args.audio_track, prep)
                core.start_audio_extraction(engine, [(p, t) for _, p, t in plan], args, state)
                for i, p, t in plan:
                    if self.cancel_flag.is_set():
                        self.q.put(("log", self.t("log_cancelled"), "err"))
                        reason = "cancel"
                        break
                    self.q.put(("current", i))
                    self.q.put(("log", f"… {t}  {p.name}", "cur"))
                    try:
                        core.import_one(engine, p, t, args, state)
                        ok += 1
                        self.q.put(("log", f"✓ {t}  {p.name}", "ok"))
                        self.q.put(("item", i, "done"))
                    except Exception as e:
                        self.q.put(("log", f"✗ {t}  {p.name} : {e}", "err"))
                        self.q.put(("item", i, "failed"))
                state.shutdown()
        except Exception as e:
            self.q.put(("log", f"✗ Connexion Pro Tools : {e}", "err"))
            reason = "conn"
        self.q.put(("done", ok, len(plan), reason))

    def _on_close(self):
        if self.busy and not messagebox.askyesno(APP_NAME, self.t("msg_quit_busy"), parent=self):
            return
        if self.prep:
            self.prep.shutdown()
        self.bg_pool.shutdown(wait=False, cancel_futures=True)
        self.destroy()


if __name__ == "__main__":
    app = App()
    if len(sys.argv) > 1:  # fichiers / dossiers passés en arguments
        app.after(200, lambda: app._add_paths(sys.argv[1:]))
    app.mainloop()
