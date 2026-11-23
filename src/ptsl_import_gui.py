#!/usr/bin/env python3
"""
PAAF — interface graphique de l'import séquentiel AAF / vidéos dans Pro Tools (PTSL).
Doit se trouver dans le même dossier que ptsl_import_sequentiel.py et aaf_inspect.py.
"""

import contextlib
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import tkinter as tk
import tkinter.font as tkfont
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

pt = core.pt
APP_NAME = "PAAF"
APP_TAGLINE = "Import AAF & vidéo dans Pro Tools"
FILETYPES = [("AAF / OMF / Vidéo", "*.aaf *.omf *.omfi *.mov *.mp4 *.m4v *.mxf *.avi"),
             ("Tous les fichiers", "*")]
AAF_AUDIO_MODES = ["Lier aux fichiers source", "Copier dans la session",
                   "Convertir au format de la session", "Consolider"]

TRACK_TYPES = {"AudioTrack": "Audio", "VideoTrack": "Vidéo", "RoutingFolder": "Dossier",
               "BasicFolder": "Dossier", "Aux": "Aux", "Master": "Master", "Vca": "VCA", "Midi": "MIDI",
               "Instrument": "Instrument"}

# Palette (thème sombre)
C = SimpleNamespace(
    bg="#0e1120", surface="#161a2c", surface2="#1d2238", raised="#252b47", border="#2b3150",
    text="#e7eaf5", muted="#8b93b4", faint="#5d6488",
    teal="#2dd4bf", teal_dark="#14b8a6", violet="#8b5cf6", orange="#ff8a3d",
    ok="#34d399", err="#f87171", warn="#fbbf24", select="#2c3566",
)


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


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1320x880")
        self.minsize(1100, 720)
        self.configure(bg=C.bg)

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
        self.prep = None         # core.AudioPrep (préconversion)
        self.q = queue.Queue()
        self.cancel_flag = threading.Event()
        self.busy = False
        self._recompute_job = None
        self._editor = None
        self.bg_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="paaf-meta")

        self._load_images()
        self._setup_style()
        self._build_ui()
        self._setup_drop()
        self._bind_keys()
        self.after(100, self._poll_queue)
        self.after(300, self.test_connection)
        self.lift()
        self.attributes("-topmost", True)
        self.after(600, lambda: self.attributes("-topmost", False))
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ style
    def _load_images(self):
        self.logo = None
        for name in ("logo_64.png", "logo_128.png"):
            p = HERE / "assets" / name
            if p.exists():
                try:
                    img = tk.PhotoImage(file=str(p))
                    self.logo = img.subsample(max(1, img.width() // 56)) if img.width() > 64 else img
                    try:
                        self.iconphoto(True, tk.PhotoImage(file=str(HERE / "assets" / "logo_256.png")))
                    except Exception:
                        pass
                    break
                except Exception:
                    pass

    def _setup_style(self):
        base = tkfont.nametofont("TkDefaultFont")
        family = base.actual("family")
        self.f_body = tkfont.Font(family=family, size=13)
        self.f_small = tkfont.Font(family=family, size=11)
        self.f_bold = tkfont.Font(family=family, size=13, weight="bold")
        self.f_title = tkfont.Font(family=family, size=22, weight="bold")
        self.f_h2 = tkfont.Font(family=family, size=12, weight="bold")
        self.f_mono = tkfont.Font(family="Menlo", size=12)
        self.f_tc = tkfont.Font(family="Menlo", size=13, weight="bold")
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            with contextlib.suppress(Exception):
                tkfont.nametofont(name).configure(family=family, size=13)

        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=C.bg, foreground=C.text, fieldbackground=C.surface2,
                    bordercolor=C.border, lightcolor=C.border, darkcolor=C.border,
                    troughcolor=C.surface2, focuscolor=C.teal, selectbackground=C.select,
                    selectforeground=C.text, insertcolor=C.text, font=self.f_body)
        s.configure("TFrame", background=C.bg)
        s.configure("Card.TFrame", background=C.surface, relief="flat")
        s.configure("Header.TFrame", background=C.bg)
        s.configure("TLabel", background=C.bg, foreground=C.text)
        s.configure("Card.TLabel", background=C.surface, foreground=C.text)
        s.configure("Muted.TLabel", background=C.bg, foreground=C.muted, font=self.f_small)
        s.configure("CardMuted.TLabel", background=C.surface, foreground=C.muted, font=self.f_small)
        s.configure("CardErr.TLabel", background=C.surface, foreground=C.err, font=self.f_small)
        s.configure("Title.TLabel", background=C.bg, foreground=C.text, font=self.f_title)
        s.configure("H2.TLabel", background=C.surface, foreground=C.muted, font=self.f_h2)
        s.configure("Pill.TFrame", background=C.surface2)
        s.configure("Pill.TLabel", background=C.surface2, foreground=C.text, font=self.f_bold)
        s.configure("PillMuted.TLabel", background=C.surface2, foreground=C.muted, font=self.f_small)
        s.configure("Drop.TLabel", background=C.surface, foreground=C.faint, font=self.f_body,
                    anchor="center", justify="center")

        # boutons
        s.configure("TButton", background=C.raised, foreground=C.text, borderwidth=0,
                    focusthickness=0, padding=(12, 6), relief="flat", width=-1)
        s.map("TButton", background=[("disabled", C.surface2), ("pressed", C.border), ("active", "#30375a")],
              foreground=[("disabled", C.faint)])
        s.configure("Accent.TButton", background=C.teal, foreground="#06221e", font=self.f_bold,
                    padding=(22, 10))
        s.map("Accent.TButton", background=[("disabled", C.surface2), ("pressed", C.teal_dark),
                                            ("active", "#5eead4")],
              foreground=[("disabled", C.faint)])
        s.configure("Danger.TButton", background=C.raised, foreground=C.err, padding=(14, 10))
        s.map("Danger.TButton", background=[("disabled", C.surface2), ("active", "#3a2433")],
              foreground=[("disabled", C.faint)])
        s.configure("Tool.TButton", background=C.surface, foreground=C.text, padding=(8, 4), width=-1)
        s.map("Tool.TButton", background=[("active", C.raised), ("pressed", C.border)])
        s.configure("Tool.TMenubutton", background=C.surface, foreground=C.text, padding=(8, 4),
                    arrowcolor=C.muted, borderwidth=0, relief="flat", width=-1)
        s.map("Tool.TMenubutton", background=[("active", C.raised)])
        s.configure("Seg.Toolbutton", background=C.surface2, foreground=C.muted, padding=(14, 6),
                    borderwidth=0, relief="flat")
        s.map("Seg.Toolbutton", background=[("selected", C.violet), ("active", C.raised)],
              foreground=[("selected", "#ffffff"), ("active", C.text)])

        for w in ("TCheckbutton", "TRadiobutton"):
            s.configure(w, background=C.surface, foreground=C.text, indicatorbackground=C.surface2,
                        indicatorforeground=C.teal, indicatorrelief="flat", padding=(2, 3))
            s.map(w, background=[("active", C.surface)],
                  indicatorbackground=[("selected", C.teal), ("active", C.raised)],
                  indicatorforeground=[("selected", "#06221e")],
                  foreground=[("disabled", C.faint)])
        s.configure("TEntry", fieldbackground=C.surface2, foreground=C.text, bordercolor=C.border,
                    lightcolor=C.border, darkcolor=C.border, padding=(6, 4))
        s.map("TEntry", bordercolor=[("focus", C.teal)], lightcolor=[("focus", C.teal)])
        s.configure("TC.TEntry", font=self.f_tc)
        s.configure("TCombobox", fieldbackground=C.surface2, background=C.raised, foreground=C.text,
                    arrowcolor=C.text, bordercolor=C.border, padding=(6, 4))
        s.map("TCombobox", fieldbackground=[("readonly", C.surface2)], foreground=[("readonly", C.text)],
              selectbackground=[("readonly", C.surface2)], selectforeground=[("readonly", C.text)])
        self.option_add("*TCombobox*Listbox.background", C.surface2)
        self.option_add("*TCombobox*Listbox.foreground", C.text)
        self.option_add("*TCombobox*Listbox.selectBackground", C.select)
        self.option_add("*TCombobox*Listbox.font", self.f_body)

        s.configure("Treeview", background=C.surface, fieldbackground=C.surface, foreground=C.text,
                    rowheight=30, borderwidth=0, font=self.f_body)
        s.map("Treeview", background=[("selected", C.select)], foreground=[("selected", "#ffffff")])
        s.configure("Treeview.Heading", background=C.surface, foreground=C.muted, font=self.f_h2,
                    relief="flat", borderwidth=0, padding=(6, 6))
        s.map("Treeview.Heading", background=[("active", C.surface2)])
        s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        s.configure("Small.Treeview", rowheight=24, font=self.f_small)

        s.configure("TNotebook", background=C.surface, borderwidth=0, tabmargins=(8, 8, 8, 0))
        s.configure("TNotebook.Tab", background=C.surface, foreground=C.muted, padding=(14, 6),
                    borderwidth=0, font=self.f_bold)
        s.map("TNotebook.Tab", background=[("selected", C.surface2)], foreground=[("selected", C.text)],
              lightcolor=[("selected", C.surface2)])
        s.configure("Horizontal.TProgressbar", background=C.teal, troughcolor=C.surface2,
                    bordercolor=C.surface2, lightcolor=C.teal, darkcolor=C.teal, thickness=8)
        for o in ("Vertical", "Horizontal"):
            s.configure(f"{o}.TScrollbar", background=C.raised, troughcolor=C.surface, bordercolor=C.surface,
                        arrowcolor=C.muted, lightcolor=C.raised, darkcolor=C.raised, gripcount=0)
            s.map(f"{o}.TScrollbar", background=[("active", C.border)])
        s.configure("TPanedwindow", background=C.bg)
        s.configure("Sash", sashthickness=10, background=C.bg, gripcount=0)
        s.configure("TSeparator", background=C.border)

    # ------------------------------------------------------------------ UI
    def _card(self, parent, title=None, **pack):
        outer = tk.Frame(parent, bg=C.border, highlightthickness=0, bd=0)
        inner = ttk.Frame(outer, style="Card.TFrame", padding=(14, 10))
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        if title:
            ttk.Label(inner, text=title.upper(), style="H2.TLabel").pack(anchor="w", pady=(0, 6))
        if pack:
            outer.pack(**pack)
        outer.inner = inner
        return outer

    def _build_ui(self):
        root = ttk.Frame(self, padding=(16, 12, 16, 12))
        root.pack(fill="both", expand=True)

        # ---- en-tête : logo, titre, état de la session
        head = ttk.Frame(root, style="Header.TFrame")
        head.pack(fill="x", pady=(0, 12))
        if self.logo:
            ttk.Label(head, image=self.logo).pack(side="left", padx=(0, 12))
        titles = ttk.Frame(head)
        titles.pack(side="left")
        ttk.Label(titles, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text=APP_TAGLINE, style="Muted.TLabel").pack(anchor="w")

        pill = ttk.Frame(head, style="Pill.TFrame", padding=(14, 8))
        pill.pack(side="right")
        self.dot = tk.Canvas(pill, width=12, height=12, bg=C.surface2, highlightthickness=0)
        self.dot.pack(side="left", padx=(0, 10))
        self._dot = self.dot.create_oval(1, 1, 11, 11, fill=C.warn, outline="")
        txt = ttk.Frame(pill, style="Pill.TFrame")
        txt.pack(side="left")
        self.session_var = tk.StringVar(value="Connexion à Pro Tools…")
        self.session_sub = tk.StringVar(value="")
        ttk.Label(txt, textvariable=self.session_var, style="Pill.TLabel").pack(anchor="w")
        ttk.Label(txt, textvariable=self.session_sub, style="PillMuted.TLabel").pack(anchor="w")
        ttk.Button(pill, text="↻", width=2, command=self.test_connection,
                   style="Tool.TButton").pack(side="left", padx=(12, 0))

        # ---- pied : import + progression (packé avant le centre pour rester visible)
        foot = ttk.Frame(root)
        foot.pack(side="bottom", fill="x", pady=(12, 0))
        self.import_btn = ttk.Button(foot, text="Importer dans Pro Tools  →", style="Accent.TButton",
                                     command=self.start_import)
        self.import_btn.pack(side="right")
        self.cancel_btn = ttk.Button(foot, text="Arrêter", style="Danger.TButton",
                                     command=self.cancel_flag.set, state="disabled")
        self.cancel_btn.pack(side="right", padx=8)
        prog = ttk.Frame(foot)
        prog.pack(side="left", fill="x", expand=True, padx=(0, 16))
        self.status_var = tk.StringVar(value="Prêt.")
        ttk.Label(prog, textvariable=self.status_var, style="Muted.TLabel").pack(anchor="w")
        self.progress = ttk.Progressbar(prog, mode="determinate")
        self.progress.pack(fill="x", pady=(4, 0))

        # ---- réglages : placement + options
        bottom = ttk.Frame(root)
        bottom.pack(side="bottom", fill="x", pady=(12, 0))
        self._build_placement(bottom)
        self._build_options(bottom)

        # ---- centre : liste | inspecteur
        center = ttk.Frame(root)
        center.pack(fill="both", expand=True)
        right = self._card(center)
        right.configure(width=530)
        right.pack_propagate(False)
        right.pack(side="right", fill="y", padx=(12, 0))
        left = self._card(center, side="left", fill="both", expand=True)
        self.list_card = left
        self._build_list(left.inner)
        self._build_side(right.inner)


    def _build_list(self, parent):
        bar = ttk.Frame(parent, style="Card.TFrame")
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="FICHIERS À IMPORTER", style="H2.TLabel").pack(side="left")
        self.count_var = tk.StringVar(value="")
        self.recursive_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="sous-dossiers", variable=self.recursive_var).pack(side="right", padx=(6, 0))
        ttk.Button(bar, text="＋ Dossier", command=self.add_folder).pack(side="right", padx=4)
        ttk.Button(bar, text="＋ Fichiers", command=self.add_files).pack(side="right")

        tools = ttk.Frame(parent, style="Card.TFrame")
        tools.pack(fill="x", pady=(0, 8))
        csv_btn = ttk.Menubutton(tools, text="Plan CSV", style="Tool.TMenubutton")
        menu = tk.Menu(csv_btn, tearoff=False)
        menu.add_command(label="Charger un plan CSV…", command=self.load_csv)
        menu.add_command(label="Exporter le plan CSV…", command=self.export_csv)
        csv_btn["menu"] = menu
        csv_btn.pack(side="right")
        for text, cmd in [("↑ Monter", lambda: self.move(-1)), ("↓ Descendre", lambda: self.move(1)),
                          ("Trier par nom", self.sort_items), ("Retirer", self.remove_selected),
                          ("Tout vider", self.clear_items)]:
            ttk.Button(tools, text=text, command=cmd, style="Tool.TButton").pack(side="left", padx=(0, 2))

        wrap = ttk.Frame(parent, style="Card.TFrame")
        wrap.pack(fill="both", expand=True)
        cols = ("tc", "type", "file", "audio", "link")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="extended")
        for c, label, w, anchor, stretch in [
                ("tc", "Point d'entrée", 112, "center", False), ("type", "Type", 62, "center", False),
                ("file", "Fichier", 250, "w", True), ("audio", "Contenu", 200, "w", True),
                ("link", "Calage", 70, "center", False)]:
            self.tree.heading(c, text=label, anchor=anchor)
            self.tree.column(c, width=w, minwidth=50, anchor=anchor, stretch=stretch)
        self.tree.tag_configure("g0", background=C.surface)
        self.tree.tag_configure("g1", background="#1a1f35")
        self.tree.tag_configure("missing", foreground=C.err)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_inspector())

        self.drop_zone = ttk.Label(wrap, style="Drop.TLabel",
                                   text="Glisse ici des AAF, des vidéos ou des dossiers\n\n"
                                        "Un AAF et une vidéo de même nom sont calés au même TC.")
        self.plan_msg = tk.StringVar()
        status = ttk.Frame(parent, style="Card.TFrame")
        status.pack(fill="x", pady=(6, 0))
        ttk.Label(status, textvariable=self.count_var, style="CardMuted.TLabel").pack(side="left")
        ttk.Label(status, textvariable=self.plan_msg, style="CardErr.TLabel").pack(side="right")

    def _build_side(self, parent):
        self.nb = ttk.Notebook(parent)
        self.nb.pack(fill="both", expand=True)

        # Inspecteur
        insp = ttk.Frame(self.nb, style="Card.TFrame", padding=(4, 8))
        self.nb.add(insp, text="Inspecteur")
        self.insp_text = self._text(insp, height=8, small=True)
        self.insp_text.pack(fill="x")
        tw = ttk.Frame(insp, style="Card.TFrame")
        tw.pack(fill="both", expand=True, pady=(8, 0))
        self.insp_tree = ttk.Treeview(tw, columns=("pos", "len", "fmt", "info"), style="Small.Treeview")
        for c, label, w, st in [("#0", "Élément", 150, True), ("pos", "Position PT", 88, False),
                                ("len", "Durée", 80, False), ("fmt", "Format", 118, False),
                                ("info", "Infos", 80, True)]:
            self.insp_tree.heading(c, text=label, anchor="w")
            self.insp_tree.column(c, width=w, minwidth=40, stretch=st, anchor="w")
        self.insp_tree.tag_configure("head", foreground=C.teal, font=self.f_bold)
        self.insp_tree.tag_configure("match", foreground=C.ok)
        self.insp_tree.tag_configure("muted", foreground=C.muted)
        isb = ttk.Scrollbar(tw, orient="vertical", command=self.insp_tree.yview)
        self.insp_tree.configure(yscrollcommand=isb.set)
        isb.pack(side="right", fill="y")
        self.insp_tree.pack(side="left", fill="both", expand=True)

        # Session Pro Tools
        sess = ttk.Frame(self.nb, style="Card.TFrame", padding=(4, 8))
        self.nb.add(sess, text="Pistes de la session")
        top = ttk.Frame(sess, style="Card.TFrame")
        top.pack(fill="x", pady=(0, 6))
        self.tracks_var = tk.StringVar(value="Non connecté.")
        ttk.Label(top, textvariable=self.tracks_var, style="CardMuted.TLabel").pack(side="left")
        ttk.Button(top, text="Actualiser", style="Tool.TButton", command=self.test_connection).pack(side="right")
        sw = ttk.Frame(sess, style="Card.TFrame")
        sw.pack(fill="both", expand=True)
        self.sess_tree = ttk.Treeview(sw, columns=("type", "format", "clips"), style="Small.Treeview")
        for c, label, w, st in [("#0", "Piste", 250, True), ("type", "Type", 120, False),
                                ("format", "Format", 80, False), ("clips", "Clips", 50, False)]:
            self.sess_tree.heading(c, text=label, anchor="w")
            self.sess_tree.column(c, width=w, minwidth=40, stretch=st, anchor="w")
        self.sess_tree.tag_configure("folder", foreground=C.muted, font=self.f_bold)
        self.sess_tree.tag_configure("target", foreground=C.teal)
        self.sess_tree.tag_configure("match", foreground=C.ok)
        ssb = ttk.Scrollbar(sw, orient="vertical", command=self.sess_tree.yview)
        self.sess_tree.configure(yscrollcommand=ssb.set)
        ssb.pack(side="right", fill="y")
        self.sess_tree.pack(side="left", fill="both", expand=True)

        # Journal
        logf = ttk.Frame(self.nb, style="Card.TFrame", padding=(4, 8))
        self.nb.add(logf, text="Journal")
        self.log = self._text(logf, height=10, mono=True)
        lsb = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.show_inspector()

    def _text(self, parent, height, mono=False, small=False):
        font = self.f_mono if mono else self.f_small if small else self.f_body
        t = tk.Text(parent, height=height, wrap="word", state="disabled", bg=C.surface, fg=C.text,
                    insertbackground=C.text, relief="flat", borderwidth=0, highlightthickness=0,
                    font=font, padx=6, pady=4, spacing1=2, spacing3=1)
        t.tag_configure("ok", foreground=C.ok)
        t.tag_configure("err", foreground=C.err)
        t.tag_configure("warn", foreground=C.warn)
        t.tag_configure("info", foreground=C.muted)
        t.tag_configure("title", font=self.f_bold, foreground=C.text)
        t.tag_configure("accent", foreground=C.teal)
        t.tag_configure("violet", foreground="#b9a3ff")
        return t

    def _build_placement(self, parent):
        card = self._card(parent, "Placement sur la timeline", side="left", fill="y", padx=(0, 6))
        p = card.inner
        seg = ttk.Frame(p, style="Card.TFrame")
        seg.pack(fill="x")
        self.mode_var = tk.StringVar(value="fixe")
        for val, label in [("fixe", "Espacement fixe"), ("bout", "Bout à bout"), ("manuel", "Manuel")]:
            ttk.Radiobutton(seg, text=label, value=val, variable=self.mode_var, style="Seg.Toolbutton",
                            command=self._on_mode).pack(side="left", padx=(0, 2))

        fields = ttk.Frame(p, style="Card.TFrame")
        fields.pack(fill="x", pady=(10, 4))
        self.start_var = tk.StringVar(value="01:00:00:00")
        self.step_var = tk.StringVar(value="00:10:00:00")
        self.gap_var = tk.StringVar(value="00:00:10:00")
        for col, (label, var) in enumerate([("Premier point d'entrée", self.start_var),
                                            ("Espacement / durée réservée", self.step_var),
                                            ("Blanc (bout à bout)", self.gap_var)]):
            ttk.Label(fields, text=label, style="CardMuted.TLabel").grid(row=0, column=col, sticky="w", padx=(0, 16))
            ttk.Entry(fields, textvariable=var, width=12, justify="center", style="TC.TEntry",
                      font=self.f_tc).grid(row=1, column=col, sticky="w", padx=(0, 16), pady=(2, 0))
            var.trace_add("write", lambda *a: self.schedule_recompute())
        self.link_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(p, text="Caler au même TC un AAF et une vidéo de même nom",
                        variable=self.link_var, command=self.schedule_recompute).pack(anchor="w", pady=(8, 0))
        ttk.Label(p, text="Pour modifier un point d'entrée : double-clic dans la liste (Tab : suivant).",
                  style="CardMuted.TLabel").pack(anchor="w", pady=(4, 0))

    def _build_options(self, parent):
        card = self._card(parent, "Options", side="left", fill="both", expand=True, padx=(6, 0))
        p = ttk.Frame(card.inner, style="Card.TFrame")
        p.pack(fill="both", expand=True)
        p.columnconfigure(1, weight=1)

        ttk.Label(p, text="AAF", style="H2.TLabel").grid(row=0, column=0, sticky="nw", padx=(0, 14), pady=3)
        a = ttk.Frame(p, style="Card.TFrame")
        a.grid(row=0, column=1, sticky="w")
        self.aaf_audio_var = tk.StringVar(value=AAF_AUDIO_MODES[0])
        ttk.Combobox(a, textvariable=self.aaf_audio_var, values=AAF_AUDIO_MODES, state="readonly",
                     width=26, font=self.f_body).pack(side="left")
        self.match_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(p, text="Fusionner dans les pistes de même nom de la session", variable=self.match_var,
                        command=self._on_match).grid(row=1, column=1, sticky="w", pady=(4, 0))
        a2 = ttk.Frame(p, style="Card.TFrame")
        a2.grid(row=2, column=1, sticky="w")
        self.clip_gain_var = tk.BooleanVar(value=True)
        self.vol_auto_var = tk.BooleanVar(value=True)
        self.copy_video_var = tk.BooleanVar(value=False)
        for text, var in [("Gain de clip", self.clip_gain_var), ("Automation de volume", self.vol_auto_var),
                          ("Copier la vidéo référencée", self.copy_video_var)]:
            ttk.Checkbutton(a2, text=text, variable=var).pack(side="left", padx=(0, 12))

        ttk.Separator(p).grid(row=3, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Label(p, text="VIDÉO", style="H2.TLabel").grid(row=4, column=0, sticky="nw", padx=(0, 14), pady=3)
        v = ttk.Frame(p, style="Card.TFrame")
        v.grid(row=4, column=1, sticky="w")
        self.video_audio_var = tk.BooleanVar(value=True)
        self.audio_track_var = tk.StringVar(value="AUDIO VIDEO")
        ttk.Checkbutton(v, text="Importer l'audio sur la piste", variable=self.video_audio_var,
                        command=self.show_inspector).pack(side="left")
        ttk.Entry(v, textvariable=self.audio_track_var, width=16).pack(side="left", padx=(6, 12))
        self.new_video_track_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(p, text="Nouvelle piste vidéo à chaque import",
                        variable=self.new_video_track_var).grid(row=6, column=1, sticky="w")
        v2 = ttk.Frame(p, style="Card.TFrame")
        v2.grid(row=5, column=1, sticky="w")
        ffmpeg_ok = core.has_ffmpeg()
        self.ffmpeg_var = tk.BooleanVar(value=ffmpeg_ok)
        self.stereo_var = tk.BooleanVar(value=True)
        self.preconvert_var = tk.BooleanVar(value=ffmpeg_ok)
        st = "normal" if ffmpeg_ok else "disabled"
        ttk.Checkbutton(v2, text="Convertir avec ffmpeg" if ffmpeg_ok else "ffmpeg absent (brew install ffmpeg)",
                        variable=self.ffmpeg_var, state=st, command=self._on_prep_option).pack(side="left")
        ttk.Checkbutton(v2, text="dès l'ajout", variable=self.preconvert_var, state=st,
                        command=self._on_prep_option).pack(side="left", padx=(8, 12))
        ttk.Checkbutton(v2, text="Double mono → mono", variable=self.stereo_var, state=st,
                        command=self._on_prep_option).pack(side="left")

    # ------------------------------------------------------------------ clavier / glisser-déposer
    def _bind_keys(self):
        self.bind_all("<Command-o>", lambda e: self.add_files())
        self.bind_all("<Command-Return>", lambda e: self.start_import())
        self.tree.bind("<BackSpace>", lambda e: self.remove_selected())
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        self.tree.bind("<Command-a>", lambda e: self.tree.selection_set(self.tree.get_children()))

    def _setup_drop(self):
        # Fichiers déposés sur l'icône de l'app (Dock / Finder)
        with contextlib.suppress(Exception):
            self.createcommand("::tk::mac::OpenDocument", lambda *paths: self._add_paths(paths))
        if TkinterDnD is None:
            self.drop_zone.configure(text=self.drop_zone.cget("text") +
                                     "\n\n(glisser-déposer indisponible : pip install tkinterdnd2)")
            return
        try:
            TkinterDnD._require(self)
        except Exception as e:
            self.drop_zone.configure(text=f"Glisser-déposer indisponible : {e}")
            return
        for w in (self.tree, self.drop_zone, self.insp_tree, self.list_card):
            w.drop_target_register(DND_FILES)
            w.dnd_bind("<<DropEnter>>", self._on_drop_enter)
            w.dnd_bind("<<DropLeave>>", self._on_drop_leave)
            w.dnd_bind("<<Drop>>", self._on_drop)

    def _on_drop_enter(self, event):
        self.list_card.configure(bg=C.teal)
        return event.action

    def _on_drop_leave(self, event):
        self.list_card.configure(bg=C.border)
        return event.action

    def _on_drop(self, event):
        self.list_card.configure(bg=C.border)
        if not self.busy:
            self._add_paths(self.tk.splitlist(event.data))
        return event.action

    # ------------------------------------------------------------------ journal / file d'attente
    def write_log(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll_queue(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self.write_log(msg[1], msg[2])
                    if msg[2] != "info":
                        self.status_var.set(msg[1].strip()[:140])
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
                elif kind == "progress":
                    i, n = msg[1], msg[2]
                    self.progress.configure(maximum=max(n, 1), value=i)
                elif kind == "done":
                    self._set_busy(False)
                    ok, n = msg[1], msg[2]
                    tag = "ok" if ok == n else "err"
                    self.write_log(f"Terminé : {ok}/{n} import(s) réussi(s).", tag)
                    self.status_var.set(f"Terminé : {ok}/{n} import(s) réussi(s).")
                    self.test_connection()
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    # ------------------------------------------------------------------ connexion
    def test_connection(self):
        if not self.session:
            self.session_var.set("Connexion à Pro Tools…")
        threading.Thread(target=self._connect_worker, daemon=True).start()

    def _connect_worker(self):
        try:
            with core.connect() as engine:
                try:
                    name = engine.session_name()
                except core.CommandError:
                    self.q.put(("session_error", "aucune session ouverte"))
                    return
                info = {"name": name, "rate": engine.session_timecode_rate(),
                        "version": engine.ptsl_version(), "fmt": core.session_audio_format(engine)}
                try:
                    info["tracks"] = core.session_tracks(engine)
                except Exception:
                    info["tracks"] = []
            self.q.put(("session", info))
        except Exception as e:  # Pro Tools fermé, PTSL indisponible…
            self.q.put(("session_error", str(e).splitlines()[0][:120] if str(e) else "Pro Tools injoignable"))

    def _on_session_error(self, text):
        self.session = None
        self.dot.itemconfigure(self._dot, fill=C.err)
        self.session_var.set("Pro Tools non connecté")
        self.session_sub.set(text)
        self.tracks_var.set("Non connecté.")

    def _on_session(self, info):
        name, rate = info["name"], info["rate"]
        rate_name = pt.SessionTimeCodeRate.Name(rate) if rate in pt.SessionTimeCodeRate.values() else str(rate)
        fmt = info["fmt"]
        self.session_fmt = fmt
        self.session_tracks = info.get("tracks", [])
        detail = f"{fmt['sr'] / 1000:g} kHz · {fmt['label']} · PTSL {info['version']}"
        if rate not in core.RATES:
            self.session = None
            self.dot.itemconfigure(self._dot, fill=C.warn)
            self.session_var.set(name)
            self.session_sub.set(f"cadence TC non gérée ({rate_name})")
            return
        base, real, drop = core.RATES[rate]
        self.session = name
        self.fps_base, self.real_fps, self.drop_frame = base, real, drop
        label = rate_name.replace("STCR_Fps", "").replace("Drop", " DF")
        if label.isdigit() and len(label) > 2:
            label = f"{label[:2]}.{label[2:]}"
        self.dot.itemconfigure(self._dot, fill=C.warn if drop else C.ok)
        self.session_var.set(name)
        self.session_sub.set(f"{label} i/s · {detail}" + ("  ·  drop-frame non géré" if drop else ""))
        self._refresh_session_tree()
        self._update_prep()
        self.schedule_recompute()

    def _refresh_session_tree(self):
        t = self.sess_tree
        t.delete(*t.get_children())
        tracks = self.session_tracks
        targets = self._audio_track_names()
        aaf_names = set()
        if self.match_var.get():
            for it in self.items:
                m = self.meta.get(it["path"])
                if isinstance(m, dict) and m.get("tracks"):
                    aaf_names |= {tr["name"] for tr in m["tracks"]}
        nodes = {}
        for tr in tracks:
            parent = nodes.get(tr["folder"], "")
            is_folder = "Folder" in tr["type"]
            tags = ("folder",) if is_folder else ("target",) if tr["name"] in targets else \
                ("match",) if tr["name"] in aaf_names else ()
            fmt = "" if tr["format"] in ("None", "Unknown") else tr["format"].replace("_", ".").lower()
            tlabel = TRACK_TYPES.get(tr["type"], tr["type"])
            iid = t.insert(parent, "end", text=("▸ " if is_folder else "") + tr["name"], open=True, tags=tags,
                           values=(tlabel, fmt, "●" if tr["has_clips"] else ""))
            if is_folder:
                nodes[tr["name"]] = iid
        n_audio = sum(1 for tr in tracks if tr["type_id"] == pt.TT_Audio)
        legend = "turquoise : audio des vidéos" + (" · vert : fusion AAF" if self.match_var.get() else "")
        self.tracks_var.set(f"{len(tracks)} pistes, dont {n_audio} audio  ·  {legend}")

    def _audio_track_names(self):
        base = self.audio_track_var.get().strip() or "AUDIO VIDEO"
        return {base, f"{base} MONO"} | {f"{base} {i}" for i in range(1, 17)}

    # ------------------------------------------------------------------ préconversion / analyses
    def _on_prep_option(self):
        self._update_prep()
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
                        "error": "aperçu indisponible (pyaaf2 non installé)"}
                else:
                    data = {"error": "type inconnu"}
            except Exception as e:
                data = {"error": str(e)}
            self.q.put(("meta", p, data))

        if p.exists():
            self.bg_pool.submit(work)
        else:
            self.meta[p] = {"error": "fichier introuvable"}

    # ------------------------------------------------------------------ liste
    def _add_paths(self, paths):
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
            self.recompute()
            self.status_var.set(f"{added} fichier(s) ajouté(s).")

    def add_files(self):
        paths = filedialog.askopenfilenames(title="AAF / OMF / vidéos", filetypes=FILETYPES)
        if paths:
            self._add_paths(paths)

    def add_folder(self):
        d = filedialog.askdirectory(title="Dossier contenant les AAF / vidéos")
        if d:
            self._add_paths([d])

    def selected_indexes(self):
        return sorted(int(i) for i in self.tree.selection())

    def _selected_path(self):
        idx = self.selected_indexes()
        return self.items[idx[0]]["path"] if idx and idx[0] < len(self.items) else None

    def move(self, delta):
        idx = self.selected_indexes()
        if not idx:
            return
        if (delta < 0 and idx[0] == 0) or (delta > 0 and idx[-1] == len(self.items) - 1):
            return
        order = idx if delta < 0 else list(reversed(idx))
        for i in order:
            self.items[i], self.items[i + delta] = self.items[i + delta], self.items[i]
        self.recompute(select=[i + delta for i in idx])

    def sort_items(self):
        self.items.sort(key=lambda it: core.natural_key(it["path"]))
        self.recompute()

    def remove_selected(self):
        for i in reversed(self.selected_indexes()):
            del self.items[i]
        self.recompute()
        self.show_inspector()

    def clear_items(self):
        self.items.clear()
        self.recompute()
        self.show_inspector()

    # ------------------------------------------------------------------ édition du TC dans la liste
    def _on_double_click(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self._edit_tc(int(row))

    def _edit_tc(self, i):
        self._close_editor(commit=False)
        iid = str(i)
        self.tree.see(iid)
        bbox = self.tree.bbox(iid, "tc")
        if not bbox:
            return
        x, y, w, h = bbox
        var = tk.StringVar(value=self.items[i]["tc"] or self.start_var.get())
        e = ttk.Entry(self.tree, textvariable=var, justify="center", font=self.f_tc)
        e.place(x=x, y=y, width=w, height=h)
        e.focus_set()
        e.select_range(0, "end")
        self._editor = (e, var, i)
        e.bind("<Return>", lambda ev: self._close_editor(commit=True))
        e.bind("<KP_Enter>", lambda ev: self._close_editor(commit=True))
        e.bind("<Escape>", lambda ev: self._close_editor(commit=False))
        e.bind("<FocusOut>", lambda ev: self._close_editor(commit=True))
        e.bind("<Tab>", lambda ev: (self._close_editor(commit=True, next_row=True), "break")[1])

    def _close_editor(self, commit, next_row=False):
        if not self._editor:
            return
        e, var, i = self._editor
        self._editor = None
        e.destroy()
        if not commit or i >= len(self.items):
            return
        tc = core.TC(self.fps_base)
        try:
            frames = tc.to_frames(var.get())
        except ValueError as err:
            self.plan_msg.set(str(err))
            return
        if self.mode_var.get() != "manuel":
            self._freeze_manual()
            self.mode_var.set("manuel")
        group = next((g for g in self.groups if i in g), [i])
        for j in group:
            self.items[j]["manual"] = frames
        self.recompute(select=[i])
        if next_row:
            nxt = max(group) + 1
            if nxt < len(self.items):
                self.tree.selection_set(str(nxt))
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
        self.schedule_recompute()

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

    def recompute(self, select=None):
        self._recompute_job = None
        tc = core.TC(self.fps_base)
        self.plan_msg.set("")
        try:
            start = tc.to_frames(self.start_var.get())
            step = tc.to_frames(self.step_var.get())
            gap = tc.to_frames(self.gap_var.get())
        except ValueError as e:
            self.plan_msg.set(str(e))
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
            self.plan_msg.set(f"{unknown} durée(s) inconnue(s) : espacement utilisé")
        self._refresh(select)
        if select is None:
            self.show_inspector()

    def _content_label(self, p):
        m = self.meta.get(p)
        k = core.kind_of(p)
        if m is None:
            return "analyse…" if p.exists() else "introuvable"
        if m.get("error"):
            return m["error"]
        if k == "aaf":
            snd = [t for t in m["tracks"] if t["kind"] == "Sound"]
            srs = {s.get("sr") for s in m["sources"] if s.get("sr")}
            bits = {s.get("bits") for s in m["sources"] if s.get("bits")}
            fmt = ""
            if len(srs) == 1 and len(bits) <= 1:
                fmt = f" · {next(iter(srs)) / 1000:g}k" + (f"/{next(iter(bits))}" if bits else "")
            return f"{len(snd)} piste(s) · {fmt_duration(m['duration_s'], self.fps_base)}{fmt}"
        label = m.get("label", "")
        if self.video_audio_var.get() and self.ffmpeg_var.get() and self.prep is not None:
            st = self.prep.status(p)
            if st == "prêt":
                label += "  ✓ converti"
            elif st == "…":
                label += "  ⟳ conversion"
            elif st == "erreur":
                label += "  ⚠ conversion"
        return label

    def _refresh(self, select=None):
        if select is None:
            select = self.selected_indexes()
        self.tree.delete(*self.tree.get_children())
        group_of = {}
        for gi, g in enumerate(self.groups):
            for i in g:
                group_of[i] = (gi, g)
        for i, it in enumerate(self.items):
            p = it["path"]
            gi, g = group_of.get(i, (i, [i]))
            tags = [f"g{gi % 2}"]
            if not p.exists():
                tags.append("missing")
            link = ""
            if len(g) > 1:
                link = "⇄ vidéo" if core.kind_of(p) == "aaf" else "⇄ AAF"
            kind = {"aaf": "AAF" if p.suffix.lower() == ".aaf" else "OMF", "video": "VIDÉO"}.get(core.kind_of(p), "?")
            self.tree.insert("", "end", iid=str(i), tags=tags,
                             values=(it["tc"] or "", kind, p.name, self._content_label(p), link))
        if select:
            self.tree.selection_set([str(i) for i in select if 0 <= i < len(self.items)])
        n = len(self.items)
        n_aaf = sum(core.kind_of(it["path"]) == "aaf" for it in self.items)
        self.count_var.set(f"{n} fichier(s) — {n_aaf} AAF/OMF, {n - n_aaf} vidéo(s)" if n else "")
        if n:
            self.drop_zone.place_forget()
        else:
            self.drop_zone.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.9, relheight=0.8)

    # ------------------------------------------------------------------ inspecteur
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
        t = self.insp_text
        try:
            lines = t.count("1.0", "end-1c", "displaylines")
            lines = lines[0] if isinstance(lines, tuple) else lines
            t.configure(height=max(3, min(14, int(lines or 0) + 1)))
        except Exception:
            pass

    def show_inspector(self):
        if not hasattr(self, "insp_tree"):
            return
        self.insp_tree.delete(*self.insp_tree.get_children())
        idx = self.selected_indexes()
        if not idx or idx[0] >= len(self.items):
            self._insp_write([("Inspecteur\n", "title"),
                              ("Sélectionne un fichier pour voir son contenu : pistes et clips d'un AAF, "
                               "métadonnées des médias audio (fréquence, résolution, canaux, TC d'origine, "
                               "Scene/Take), analyse des canaux de l'audio des vidéos.", "info")])
            return
        i = idx[0]
        it = self.items[i]
        p = it["path"]
        if p not in self.meta:
            self._load_meta(p)
        m = self.meta.get(p)
        partners = [self.items[j]["path"].name for g in self.groups if i in g for j in g if j != i]
        head = [(p.name + "\n", "title")]
        if partners:
            other = "la vidéo" if core.kind_of(p) == "aaf" else "l'AAF"
            head.append((f"⇄ calé au même TC que {other} de même nom\n", "violet"))
        if m is None:
            self._insp_write(head + [("Analyse en cours…", "info")])
            return
        if m.get("error"):
            self._insp_write(head + [(m["error"], "warn")])
            return
        if core.kind_of(p) == "aaf":
            self._inspect_aaf(head, it, m)
        else:
            self._inspect_video(head, it, m)

    def _inspect_aaf(self, head, it, m):
        fps = self.fps_base
        tc = core.TC(fps)
        try:
            base = tc.to_frames(it["tc"]) if it["tc"] else 0
        except ValueError:
            base = 0
        rate = float(m["edit_rate"]) if m.get("edit_rate") else fps
        snd = [t for t in m["tracks"] if t["kind"] == "Sound"]
        session_names = {t["name"] for t in self.session_tracks}
        matching = [t for t in snd if t["name"] in session_names]
        comp = "" if core.pair_key(Path(m["name"] + ".aaf")) == core.pair_key(it["path"]) else \
            f"Composition « {m['name']} »  ·  "
        parts = head + [
            (f"{comp}AAF  ·  durée {fmt_duration(m['duration_s'], fps)}  ·  "
             f"{len(snd)} piste(s) audio  ·  {len(m['sources'])} média(s)"
             f"{'  ·  médias embarqués' if m.get('embedded') else ''}\n", None),
            (f"Calé à {it['tc']} → fin vers {tc.to_tc(base + round((m['duration_s'] or 0) * fps))}\n", "accent"),
        ]
        if m.get("fps"):
            parts.append((f"TC d'origine de l'AAF : {aaf_inspect.frames_to_tc(m['start_frames'], m['fps'])}\n",
                          "info"))
        if self.match_var.get():
            parts.append((f"Fusion : {len(matching)}/{len(snd)} piste(s) existent dans la session, "
                          f"les autres seront créées.\n", "ok" if matching else "warn"))
        elif matching:
            parts.append((f"{len(matching)} piste(s) portent le nom d'une piste existante "
                          f"(option « Fusionner » pour importer dedans).\n", "info"))
        for a, b in m.get("pairs", []):
            parts.append((f"Paire stéréo probable : {m['tracks'][a]['name']} + {m['tracks'][b]['name']}\n",
                          "violet"))
        self._insp_write(parts)

        t = self.insp_tree
        root = t.insert("", "end", text=f"Pistes ({len(m['tracks'])})", open=True, tags=("head",))
        for tr in m["tracks"]:
            exists = tr["name"] in session_names
            status = ("→ fusion dans la piste existante" if self.match_var.get() else "nom déjà pris") \
                if exists else "nouvelle piste"
            node = t.insert(root, "end", text=tr["name"], open=False, tags=("match",) if exists else (),
                            values=("", "", "audio" if tr["kind"] == "Sound" else "image",
                                    f"{len(tr['clips'])} clip(s) · {status}"))
            for c in tr["clips"][:400]:
                src = m["sources"][c["source"]] if c.get("source") is not None else {}
                pos = base + round(c["pos"] / rate * fps)
                t.insert(node, "end", text=c["name"], values=(
                    tc.to_tc(pos), fmt_duration(c["length"] / rate, fps), self._src_fmt(src),
                    self._src_info(src)))
            if len(tr["clips"]) > 400:
                t.insert(node, "end", text=f"… {len(tr['clips']) - 400} clip(s) de plus", tags=("muted",))
        media = t.insert("", "end", text=f"Médias audio ({len(m['sources'])})", open=True, tags=("head",))
        for s in m["sources"]:
            origin = fmt_duration(s["origin_s"], fps) if s.get("origin_s") is not None else ""
            t.insert(media, "end", text=s.get("file") or s.get("name") or "?", values=(
                origin, fmt_duration(s.get("duration_s"), fps), self._src_fmt(s),
                self._src_info(s, with_location=True)))

    @staticmethod
    def _src_fmt(s):
        bits = []
        if s.get("sr"):
            bits.append(f"{s['sr'] / 1000:g} kHz")
        if s.get("bits"):
            bits.append(f"{s['bits']} bits")
        if s.get("channels"):
            bits.append({1: "mono", 2: "stéréo"}.get(s["channels"], f"{s['channels']} can."))
        return " · ".join(bits)

    @staticmethod
    def _src_info(s, with_location=False):
        info = [f"{k} {v}" for k, v in (s.get("comments") or {}).items()]
        if s.get("channel") and s.get("channels", 1) and s.get("channels", 1) > 1:
            info.append(f"canal {s['channel']}")
        if with_location:
            if s.get("embedded"):
                info.append("embarqué")
            elif s.get("path"):
                info.append(("" if s.get("exists") else "⚠ introuvable : ") + Path(s["path"]).name)
            if s.get("uses"):
                info.append(f"{s['uses']} clip(s)")
        return " · ".join(info)

    def _inspect_video(self, head, it, m):
        fps = self.fps_base
        parts = head + [(f"Vidéo  ·  durée {fmt_duration(m.get('duration_s'), fps)}  ·  calée à {it['tc']}\n",
                         "accent")]
        streams = m.get("streams", [])
        if not streams:
            parts.append(("Aucun audio dans le fichier.\n", "warn"))
        else:
            desc = ", ".join(f"{s['codec']} {s['sr']} Hz {s['channels']} can." for s in streams)
            parts.append((f"Audio : {desc}\n", None))
            parts.append((f"Analyse des canaux : {m.get('label', '')}\n",
                          "violet" if m.get("kind") in ("dual_mono", "left", "right") else None))
            if m.get("peaks"):
                l, r, d = m["peaks"]
                fmt = lambda v: "−∞" if v <= -199 else f"{v:.1f}"  # noqa: E731
                parts.append((f"Crêtes : G {fmt(l)} dB · D {fmt(r)} dB · G−D {fmt(d)} dB\n", "info"))
            if self.video_audio_var.get():
                fmt_s = self.session_fmt or {"interleaved": True}
                try:
                    layout, _ = core.audio_layout(m, fmt_s, self.stereo_var.get())
                    base = self.audio_track_var.get().strip() or "AUDIO VIDEO"
                    dest = {"mono": f"« {base} MONO » (mono)", "stereo": f"« {base} » (stéréo)",
                            "split": f"« {base} 1 » à « {base} {m['channels']} » (mono)"}[layout]
                    parts.append((f"→ importé sur {dest}\n", "accent"))
                except RuntimeError as e:
                    parts.append((f"{e}\n", "warn"))
                st = self.prep.status(it["path"]) if self.prep else None
                if self.ffmpeg_var.get() and self.session_fmt:
                    label = {"prêt": "WAV prêt dans Audio Files", "…": "conversion en cours…",
                             "erreur": "échec (Pro Tools prendra le relais)",
                             None: "à l'import"}[st]
                    parts.append((f"Conversion ffmpeg ({self.session_fmt['sr'] / 1000:g} kHz / "
                                  f"{self.session_fmt['label']}) : {label}\n",
                                  "ok" if st == "prêt" else "warn" if st == "erreur" else "info"))
            else:
                parts.append(("Audio non importé (option désactivée).\n", "info"))
        self._insp_write(parts)
        t = self.insp_tree
        root = t.insert("", "end", text=f"Flux audio ({len(streams)})", open=True, tags=("head",))
        for s in streams:
            t.insert(root, "end", text=f"Flux {s['index'] + 1}", values=(
                "", "", f"{s['sr']} Hz · {s['channels']} can.", f"{s['codec']} {s['layout']}".strip()))

    # ------------------------------------------------------------------ plan CSV
    def load_csv(self):
        f = filedialog.askopenfilename(title="Plan CSV (chemin;TC)",
                                       filetypes=[("CSV / texte", "*.csv *.txt"), ("Tous", "*")])
        if not f:
            return
        tc = core.TC(self.fps_base)
        try:
            plan = core.read_csv_plan(Path(f), tc)
        except ValueError as e:
            messagebox.showerror("Plan CSV", str(e), parent=self)
            return
        self.items = [{"path": p, "tc": t, "manual": tc.to_frames(t)} for p, t in plan]
        for it in self.items:
            self._load_meta(it["path"])
        self.mode_var.set("manuel")
        self.recompute()

    def export_csv(self):
        if not self.items:
            return
        f = filedialog.asksaveasfilename(title="Exporter le plan", defaultextension=".csv",
                                         initialfile="plan_import.csv")
        if f:
            Path(f).write_text("".join(f"{it['path']};{it['tc']}\n" for it in self.items), encoding="utf-8")
            self.write_log(f"Plan exporté : {f}", "info")

    # ------------------------------------------------------------------ import
    def _set_busy(self, busy):
        self.busy = busy
        self.import_btn.configure(state="disabled" if busy else "normal")
        self.cancel_btn.configure(state="normal" if busy else "disabled")

    def _build_args(self):
        mode = self.aaf_audio_var.get()
        return SimpleNamespace(
            copy_audio=(mode == AAF_AUDIO_MODES[1]),
            convert_audio=(mode == AAF_AUDIO_MODES[2]),
            consolidate=(mode == AAF_AUDIO_MODES[3]),
            handles=1000,
            match_tracks=self.match_var.get(),
            copy_video=self.copy_video_var.get(),
            no_clip_gain=not self.clip_gain_var.get(),
            no_volume_automation=not self.vol_auto_var.get(),
            new_video_track=self.new_video_track_var.get(),
            no_video_audio=not self.video_audio_var.get(),
            audio_track=self.audio_track_var.get().strip() or "AUDIO VIDEO",
            no_ffmpeg=not self.ffmpeg_var.get(),
            no_stereo_detect=not self.stereo_var.get(),
        )

    def start_import(self):
        if self.busy:
            return
        self._close_editor(commit=True)
        if not self.items:
            messagebox.showinfo(APP_NAME, "Ajoute d'abord des fichiers.", parent=self)
            return
        if self.session is None:
            messagebox.showwarning(APP_NAME, "Pro Tools n'est pas connecté (session ouverte ?). "
                                             "Clique sur ↻.", parent=self)
            return
        if self.drop_frame:
            messagebox.showwarning(APP_NAME, "Session en drop-frame : non gérée.", parent=self)
            return
        self.recompute()
        if self.plan_msg.get() and "inconnue" not in self.plan_msg.get():
            return
        missing = [it["path"].name for it in self.items if not it["path"].exists()]
        if missing and not messagebox.askyesno(
                APP_NAME, f"{len(missing)} fichier(s) introuvable(s) seront ignorés. Continuer ?", parent=self):
            return
        if not messagebox.askyesno(APP_NAME, f"Importer {len(self.items)} fichier(s) dans « {self.session} » ?",
                                   parent=self):
            return

        plan = [(it["path"], it["tc"]) for it in self.items]
        self.cancel_flag.clear()
        self._set_busy(True)
        self.nb.select(2)
        self.write_log(f"— Import de {len(plan)} fichier(s) dans « {self.session} » —", "info")
        args = self._build_args()
        if not args.no_ffmpeg and not args.no_video_audio:
            self._update_prep()
        threading.Thread(target=self._import_worker, args=(plan, args, self.prep), daemon=True).start()

    def _import_worker(self, plan, args, prep):
        ok = 0
        try:
            with contextlib.redirect_stdout(QueueWriter(self.q)), core.connect() as engine:
                state = core.VideoAudioState(args.audio_track, prep)
                core.start_audio_extraction(engine, plan, args, state)
                for i, (p, t) in enumerate(plan):
                    if self.cancel_flag.is_set():
                        self.q.put(("log", "Interrompu par l'utilisateur.", "err"))
                        break
                    self.q.put(("progress", i, len(plan)))
                    self.q.put(("log", f"… {t}  {p.name}", "info"))
                    try:
                        core.import_one(engine, p, t, args, state)
                        ok += 1
                        self.q.put(("log", f"✓ {t}  {p.name}", "ok"))
                    except Exception as e:
                        self.q.put(("log", f"✗ {t}  {p.name} : {e}", "err"))
                    self.q.put(("progress", i + 1, len(plan)))
                state.shutdown()
        except Exception as e:
            self.q.put(("log", f"✗ Connexion Pro Tools : {e}", "err"))
        self.q.put(("done", ok, len(plan)))

    def _on_close(self):
        if self.busy and not messagebox.askyesno(APP_NAME, "Un import est en cours. Quitter quand même ?",
                                                 parent=self):
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
