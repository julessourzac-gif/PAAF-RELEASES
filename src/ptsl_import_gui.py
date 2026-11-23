#!/usr/bin/env python3
"""
Interface graphique pour l'import séquentiel AAF / vidéos dans Pro Tools (PTSL).
Doit se trouver dans le même dossier que ptsl_import_sequentiel.py.
"""

import contextlib
import math
import queue
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

try:  # glisser-déposer depuis le Finder
    from tkinterdnd2 import TkinterDnD, DND_FILES
except Exception:
    TkinterDnD = None
    DND_FILES = None

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptsl_import_sequentiel as core  # noqa: E402

pt = core.pt
APP_NAME = "Import séquentiel Pro Tools"
FILETYPES = [("AAF / OMF / Vidéo", "*.aaf *.omf *.omfi *.mov *.mp4 *.m4v *.mxf *.avi"),
             ("Tous les fichiers", "*")]
AAF_AUDIO_MODES = ["Lier aux fichiers source", "Copier dans la session",
                   "Convertir au format de la session", "Consolider"]


class QueueWriter:
    """Redirige les print() du module vers le journal de l'interface."""

    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s.strip():
            self.q.put(("log", s.rstrip(), None))

    def flush(self):
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1080x840")
        self.minsize(900, 700)

        self.items = []          # [{"path": Path, "tc": str}]
        self.durations = {}      # cache des durées vidéo
        self.fps_base = 25
        self.real_fps = 25.0
        self.drop_frame = False
        self.session = None
        self.q = queue.Queue()
        self.cancel_flag = threading.Event()
        self.busy = False
        self._recompute_job = None

        self._build_ui()
        self._setup_drop()
        self.after(100, self._poll_queue)
        self.after(300, self.test_connection)
        self.lift()
        self.attributes("-topmost", True)
        self.after(600, lambda: self.attributes("-topmost", False))

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # Session
        top = ttk.LabelFrame(self, text="Session Pro Tools")
        top.pack(fill="x", **pad)
        self.session_var = tk.StringVar(value="Connexion…")
        ttk.Label(top, textvariable=self.session_var).pack(side="left", padx=8, pady=6)
        ttk.Button(top, text="Actualiser", command=self.test_connection).pack(side="right", padx=8)

        # Liste + boutons
        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, **pad)

        cols = ("n", "tc", "type", "file", "folder")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended", height=12)
        for c, label, w, anchor in [("n", "#", 40, "e"), ("tc", "Point d'entrée", 110, "center"),
                                    ("type", "Type", 60, "center"), ("file", "Fichier", 430, "w"),
                                    ("folder", "Dossier", 280, "w")]:
            self.tree.heading(c, text=label)
            self.tree.column(c, width=w, anchor=anchor, stretch=(c in ("file", "folder")))
        self.tree.tag_configure("missing", foreground="#c0392b")
        self.tree.bind("<Double-1>", self.edit_tc)
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)

        style = ttk.Style(self)
        style.configure("Drop.TLabel", foreground="#7f8c8d", padding=10, anchor="center")
        style.configure("DropActive.TLabel", foreground="#ffffff", background="#2e86c1",
                        padding=10, anchor="center")
        self.drop_zone = ttk.Label(self, text="", style="Drop.TLabel", anchor="center")

        side = ttk.Frame(mid)
        side.pack(side="right", fill="y", padx=(8, 0))
        self.recursive_var = tk.BooleanVar(value=False)
        for text, cmd in [("Ajouter des fichiers…", self.add_files),
                          ("Ajouter un dossier…", self.add_folder)]:
            ttk.Button(side, text=text, command=cmd).pack(fill="x", pady=2)
        ttk.Checkbutton(side, text="Inclure les sous-dossiers",
                        variable=self.recursive_var).pack(anchor="w", pady=(0, 8))
        for text, cmd in [("Monter", lambda: self.move(-1)), ("Descendre", lambda: self.move(1)),
                          ("Trier par nom", self.sort_items), ("Retirer", self.remove_selected),
                          ("Tout vider", self.clear_items)]:
            ttk.Button(side, text=text, command=cmd).pack(fill="x", pady=2)
        ttk.Separator(side).pack(fill="x", pady=8)
        ttk.Button(side, text="Charger un plan CSV…", command=self.load_csv).pack(fill="x", pady=2)
        ttk.Button(side, text="Exporter le plan CSV…", command=self.export_csv).pack(fill="x", pady=2)

        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.drop_zone.pack(fill="x", padx=8)

        # Placement
        place = ttk.LabelFrame(self, text="Placement sur la timeline")
        place.pack(fill="x", **pad)
        self.mode_var = tk.StringVar(value="fixe")
        modes = ttk.Frame(place)
        modes.pack(fill="x", padx=8, pady=4)
        for val, label in [("fixe", "Espacement fixe entre points d'entrée"),
                           ("bout", "Bout à bout + blanc"),
                           ("manuel", "Manuel (double-clic sur une ligne)")]:
            ttk.Radiobutton(modes, text=label, value=val, variable=self.mode_var,
                            command=self.schedule_recompute).pack(side="left", padx=(0, 16))

        fields = ttk.Frame(place)
        fields.pack(fill="x", padx=8, pady=(0, 6))
        self.start_var = tk.StringVar(value="01:00:00:00")
        self.step_var = tk.StringVar(value="00:10:00:00")
        self.gap_var = tk.StringVar(value="00:00:10:00")
        for label, var in [("Premier point d'entrée", self.start_var),
                           ("Espacement / durée réservée", self.step_var),
                           ("Blanc (bout à bout)", self.gap_var)]:
            ttk.Label(fields, text=label).pack(side="left")
            e = ttk.Entry(fields, textvariable=var, width=12, justify="center")
            e.pack(side="left", padx=(4, 18))
            var.trace_add("write", lambda *a: self.schedule_recompute())
        self.plan_msg = tk.StringVar()
        ttk.Label(fields, textvariable=self.plan_msg, foreground="#c0392b").pack(side="left")

        # Options
        opts = ttk.LabelFrame(self, text="Options")
        opts.pack(fill="x", **pad)
        row1 = ttk.Frame(opts)
        row1.pack(fill="x", padx=8, pady=3)
        ttk.Label(row1, text="AAF – audio :").pack(side="left")
        self.aaf_audio_var = tk.StringVar(value=AAF_AUDIO_MODES[0])
        ttk.Combobox(row1, textvariable=self.aaf_audio_var, values=AAF_AUDIO_MODES,
                     state="readonly", width=32).pack(side="left", padx=(4, 16))
        self.clip_gain_var = tk.BooleanVar(value=True)
        self.vol_auto_var = tk.BooleanVar(value=True)
        self.copy_video_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row1, text="Gain de clip", variable=self.clip_gain_var).pack(side="left", padx=6)
        ttk.Checkbutton(row1, text="Automation de volume", variable=self.vol_auto_var).pack(side="left", padx=6)
        ttk.Checkbutton(row1, text="Copier la vidéo référencée", variable=self.copy_video_var).pack(side="left", padx=6)

        row2 = ttk.Frame(opts)
        row2.pack(fill="x", padx=8, pady=3)
        self.video_audio_var = tk.BooleanVar(value=True)
        self.new_video_track_var = tk.BooleanVar(value=False)
        self.audio_track_var = tk.StringVar(value="AUDIO VIDEO")
        ttk.Checkbutton(row2, text="Vidéo – importer l'audio sur la piste commune :",
                        variable=self.video_audio_var).pack(side="left")
        ttk.Entry(row2, textvariable=self.audio_track_var, width=20).pack(side="left", padx=(4, 16))
        ttk.Checkbutton(row2, text="Nouvelle piste vidéo à chaque import",
                        variable=self.new_video_track_var).pack(side="left", padx=6)
        row3 = ttk.Frame(opts)
        row3.pack(fill="x", padx=8, pady=3)
        has_ffmpeg = bool(core.ffmpeg_path() and core.shutil.which("ffprobe"))
        self.ffmpeg_var = tk.BooleanVar(value=has_ffmpeg)
        ttk.Checkbutton(row3, text="Vidéo – extraire l'audio avec ffmpeg (WAV au format de la session, en parallèle)"
                        if has_ffmpeg
                        else "ffmpeg absent (brew install ffmpeg)",
                        variable=self.ffmpeg_var,
                        state="normal" if has_ffmpeg else "disabled").pack(side="left")

        # Lancement + journal
        run = ttk.Frame(self)
        run.pack(fill="x", **pad)
        self.progress = ttk.Progressbar(run, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.cancel_btn = ttk.Button(run, text="Arrêter", command=self.cancel_flag.set, state="disabled")
        self.cancel_btn.pack(side="right")
        self.import_btn = ttk.Button(run, text="Importer dans Pro Tools", command=self.start_import)
        self.import_btn.pack(side="right", padx=6)

        logf = ttk.Frame(self)
        logf.pack(fill="both", expand=False, **pad)
        self.log = tk.Text(logf, height=9, wrap="word", state="disabled",
                           font=("Menlo", 11), borderwidth=1, relief="solid")
        self.log.tag_configure("ok", foreground="#1e8449")
        self.log.tag_configure("err", foreground="#c0392b")
        self.log.tag_configure("info", foreground="#566573")
        lsb = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        self.log.pack(side="left", fill="both", expand=True)
        lsb.pack(side="left", fill="y")

    # ------------------------------------------------------------------ glisser-déposer
    def _setup_drop(self):
        # Fichiers déposés sur l'icône de l'app (Dock / Finder)
        try:
            self.createcommand("::tk::mac::OpenDocument", lambda *paths: self._add_paths(paths))
        except Exception:
            pass
        # Fichiers / dossiers glissés dans la fenêtre
        self.drop_hint = tk.StringVar()
        self.drop_zone.configure(textvariable=self.drop_hint)
        if TkinterDnD is None:
            self.drop_hint.set("(glisser-déposer indisponible : pip install tkinterdnd2)")
            return
        try:
            TkinterDnD._require(self)
        except Exception as e:
            self.drop_hint.set(f"(glisser-déposer indisponible : {e})")
            return
        for w in (self.tree, self.drop_zone, self.log):
            w.drop_target_register(DND_FILES)
            w.dnd_bind("<<DropEnter>>", self._on_drop_enter)
            w.dnd_bind("<<DropLeave>>", self._on_drop_leave)
            w.dnd_bind("<<Drop>>", self._on_drop)
        self.drop_hint.set("Glisse ici des fichiers ou des dossiers depuis le Finder")

    def _on_drop_enter(self, event):
        self.drop_zone.configure(style="DropActive.TLabel")
        return event.action

    def _on_drop_leave(self, event):
        self.drop_zone.configure(style="Drop.TLabel")
        return event.action

    def _on_drop(self, event):
        self.drop_zone.configure(style="Drop.TLabel")
        paths = self.tk.splitlist(event.data)
        if self.busy:
            return event.action
        self._add_paths(paths)
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
                elif kind == "session":
                    self._on_session(*msg[1:])
                elif kind == "session_error":
                    self.session = None
                    self.session_var.set(f"⚠︎ Non connecté : {msg[1]}")
                elif kind == "progress":
                    i, n = msg[1], msg[2]
                    self.progress.configure(maximum=max(n, 1), value=i)
                elif kind == "done":
                    self._set_busy(False)
                    ok, n = msg[1], msg[2]
                    self.write_log(f"Terminé : {ok}/{n} import(s) réussi(s).", "info")
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    # ------------------------------------------------------------------ connexion
    def test_connection(self):
        self.session_var.set("Connexion à Pro Tools…")
        threading.Thread(target=self._connect_worker, daemon=True).start()

    def _connect_worker(self):
        try:
            with core.open_engine(company_name="Bernik", application_name="Import sequentiel") as engine:
                try:
                    name = engine.session_name()
                except core.CommandError:
                    self.q.put(("session_error", "aucune session ouverte"))
                    return
                rate = engine.session_timecode_rate()
                version = engine.ptsl_version()
            self.q.put(("session", name, rate, version))
        except Exception as e:  # Pro Tools fermé, PTSL indisponible…
            self.q.put(("session_error", str(e).splitlines()[0][:120] or "Pro Tools injoignable"))

    def _on_session(self, name, rate, version):
        rate_name = pt.SessionTimeCodeRate.Name(rate) if rate in pt.SessionTimeCodeRate.values() else str(rate)
        if rate not in core.RATES:
            self.session = None
            self.session_var.set(f"⚠︎ {name} : cadence TC non gérée ({rate_name})")
            return
        base, real, drop = core.RATES[rate]
        self.session = name
        self.fps_base, self.real_fps, self.drop_frame = base, real, drop
        label = rate_name.replace("STCR_Fps", "").replace("Drop", " DF")
        warn = "  —  ⚠︎ drop-frame non géré" if drop else ""
        self.session_var.set(f"✓ {name}   |   TC {label}   |   PTSL v{version}{warn}")
        self.schedule_recompute()

    # ------------------------------------------------------------------ liste
    def _add_paths(self, paths):
        known = {it["path"] for it in self.items}
        added = 0
        for p in core.expand_paths([Path(x) for x in paths], self.recursive_var.get()):
            p = p.expanduser()
            if p in known or not core.kind_of(p):
                continue
            self.items.append({"path": p, "tc": None})
            known.add(p)
            added += 1
        if added:
            self.recompute()

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

    def clear_items(self):
        self.items.clear()
        self.recompute()

    def edit_tc(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        i = int(row)
        tc = core.TC(self.fps_base)
        current = self.items[i]["tc"] or self.start_var.get()
        while True:
            answer = simpledialog.askstring("Point d'entrée", f"{self.items[i]['path'].name}\n\nTC (HH:MM:SS:FF) :",
                                            initialvalue=current, parent=self)
            if answer is None:
                return
            try:
                self.items[i]["tc"] = tc.to_tc(tc.to_frames(answer))
                break
            except ValueError as e:
                messagebox.showerror("Timecode invalide", str(e), parent=self)
        self.mode_var.set("manuel")
        self.recompute(select=[i])

    # ------------------------------------------------------------------ plan
    def schedule_recompute(self):
        if self._recompute_job:
            self.after_cancel(self._recompute_job)
        self._recompute_job = self.after(300, self.recompute)

    def _duration(self, p: Path):
        if p not in self.durations:
            self.durations[p] = core.media_duration_seconds(p) if core.kind_of(p) == "video" else None
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

        mode = self.mode_var.get()
        current = start
        unknown = 0
        for it in self.items:
            if mode == "manuel" and it["tc"]:
                try:
                    current = tc.to_frames(it["tc"])
                except ValueError:
                    it["tc"] = tc.to_tc(current)
            else:
                it["tc"] = tc.to_tc(current)
            if mode == "bout":
                d = self._duration(it["path"])
                if d is None:
                    unknown += 1
                    current += step
                else:
                    current += math.ceil(d * self.real_fps) + gap
            else:
                current += step
        if unknown:
            self.plan_msg.set(f"{unknown} durée(s) inconnue(s) : espacement utilisé")
        self._refresh(select)

    def _refresh(self, select=None):
        self.tree.delete(*self.tree.get_children())
        for i, it in enumerate(self.items):
            p = it["path"]
            tags = () if p.exists() else ("missing",)
            self.tree.insert("", "end", iid=str(i), tags=tags,
                             values=(i + 1, it["tc"] or "", (core.kind_of(p) or "?").upper(),
                                     p.name, str(p.parent)))
        if select:
            self.tree.selection_set([str(i) for i in select if 0 <= i < len(self.items)])

    def load_csv(self):
        f = filedialog.askopenfilename(title="Plan CSV (chemin;TC)",
                                       filetypes=[("CSV / texte", "*.csv *.txt"), ("Tous", "*")])
        if not f:
            return
        try:
            plan = core.read_csv_plan(Path(f), core.TC(self.fps_base))
        except ValueError as e:
            messagebox.showerror("Plan CSV", str(e), parent=self)
            return
        self.items = [{"path": p, "tc": t} for p, t in plan]
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
            copy_video=self.copy_video_var.get(),
            no_clip_gain=not self.clip_gain_var.get(),
            no_volume_automation=not self.vol_auto_var.get(),
            new_video_track=self.new_video_track_var.get(),
            no_video_audio=not self.video_audio_var.get(),
            audio_track=self.audio_track_var.get().strip() or "AUDIO VIDEO",
            no_ffmpeg=not self.ffmpeg_var.get(),
        )

    def start_import(self):
        if self.busy:
            return
        if not self.items:
            messagebox.showinfo(APP_NAME, "Ajoute d'abord des fichiers.", parent=self)
            return
        if self.session is None:
            messagebox.showwarning(APP_NAME, "Pro Tools n'est pas connecté (session ouverte ?). "
                                             "Clique sur « Actualiser ».", parent=self)
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
        self.write_log(f"— Import de {len(plan)} fichier(s) —", "info")
        threading.Thread(target=self._import_worker, args=(plan, self._build_args()), daemon=True).start()

    def _import_worker(self, plan, args):
        ok = 0
        done = 0
        try:
            with contextlib.redirect_stdout(QueueWriter(self.q)), \
                    core.open_engine(company_name="Bernik", application_name="Import sequentiel") as engine:
                state = core.VideoAudioState(args.audio_track)
                core.start_audio_extraction(engine, plan, args, state)
                for i, (p, t) in enumerate(plan):
                    if self.cancel_flag.is_set():
                        state.shutdown()
                        self.q.put(("log", "Interrompu par l'utilisateur.", "err"))
                        break
                    self.q.put(("progress", i, len(plan)))
                    self.q.put(("log", f"… {t}  {p.name}", "info"))
                    try:
                        if not p.exists():
                            raise FileNotFoundError("fichier introuvable")
                        kind = core.kind_of(p)
                        if kind == "aaf":
                            core.import_aaf(engine, p, t, args)
                        elif kind == "video":
                            core.import_video(engine, p, t, args, state)
                        else:
                            raise ValueError(f"extension non gérée : {p.suffix}")
                        ok += 1
                        self.q.put(("log", f"✓ {t}  {p.name}", "ok"))
                    except Exception as e:
                        self.q.put(("log", f"✗ {t}  {p.name} : {e}", "err"))
                    done = i + 1
                    self.q.put(("progress", done, len(plan)))
                state.shutdown()
        except Exception as e:
            self.q.put(("log", f"✗ Connexion Pro Tools : {e}", "err"))
        self.q.put(("done", ok, len(plan)))


if __name__ == "__main__":
    App().mainloop()
