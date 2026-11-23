"""
PAAF — thème « style MixCheck » pour Tkinter (variante 1a « Liste d'abord »).
Palettes, polices, styles ttk et widgets dessinés sur Canvas (coins arrondis) :
boutons, interrupteurs, contrôle segmenté, champs TC, pastille de session,
timeline en pistes empilées et tableau du plan d'import.
"""

import math
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# --------------------------------------------------------------------------- palettes
# Valeurs reprises de MixCheck + couleurs des pistes du logo PAAF (rgba aplaties sur le fond).
DARK = dict(
    name="dark",
    bg="#0f0f11", panel="#1a1a1d", well="#0a0a0b", lane="#141416", hover="#18181b",
    line="#232326", line2="#323236",
    txt="#f2f2f4", txt2="#a6a6ae", txt3="#6e6e76",
    chip="#1b1b1e", field="#26262a", segon="#2c2c30", selrow="#14243b", toggleoff="#3a3a3e",
    blu="#3a8dff", blu_hi="#5aa1ff", grn="#30d158", amb="#ffb020", rec="#ff453a", recbg="#2a1414",
    play="#ff453a", vid="#5aaee6", aaf="#4cc9b0", vaud="#f2ce3c", clip_txt="#0f1115",
)
LIGHT = dict(
    name="light",
    bg="#f7f7f8", panel="#ffffff", well="#e9e9ed", lane="#dedee3", hover="#efeff1",
    line="#e1e1e3", line2="#d0d0d4",
    txt="#1c1c1e", txt2="#5d5d64", txt3="#8e8e94",
    chip="#ebebed", field="#f0f0f2", segon="#ffffff", selrow="#e4edfd", toggleoff="#d1d1d6",
    blu="#0a6cff", blu_hi="#2b80ff", grn="#1fa64a", amb="#c77700", rec="#d70015", recbg="#f8e6e8",
    play="#e0352b", vid="#4a9fdc", aaf="#38bfa4", vaud="#ecc52a", clip_txt="#0f1115",
)


def mix(c1, c2, t):
    """Mélange deux couleurs hex (t = part de c1)."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x * t + y * (1 - t)):02x}" for x, y in zip(a, b))


# --------------------------------------------------------------------------- polices
class Fonts(dict):
    __getattr__ = dict.__getitem__


def make_fonts(root):
    """Inter + JetBrains Mono si installées, sinon police système + SF Mono / Menlo.
    Tailles en pixels (valeurs négatives pour Tk), comme dans les maquettes."""
    have = set(tkfont.families(root))
    ui = "Inter" if "Inter" in have else tkfont.nametofont("TkDefaultFont").actual("family")
    mono = next((m for m in ("JetBrains Mono", "SF Mono", "Menlo", "Monaco") if m in have), "Courier")
    spec = dict(
        h1=(ui, 30, "bold"), h2=(ui, 24, "bold"), drop=(ui, 22, "bold"), sub=(ui, 17, "normal"),
        sub_b=(ui, 17, "bold"), mono_xl=(mono, 16, "normal"),
        body=(ui, 15, "normal"), body_b=(ui, 15, "bold"), small=(ui, 14, "normal"),
        small_b=(ui, 14, "bold"), mini=(ui, 13, "normal"), cap=(ui, 11, "bold"), clip=(ui, 12, "bold"),
        mono=(mono, 13, "normal"), mono_s=(mono, 12, "normal"), mono_l=(mono, 14, "normal"),
        log=(mono, 13, "normal"),
    )
    return Fonts({k: tkfont.Font(root, family=fam, size=-size, weight=w) for k, (fam, size, w) in spec.items()})


def caps(text):
    """Libellés de section : majuscules espacées (Tk n'a pas de letter-spacing)."""
    return "  ".join(" ".join(word) for word in text.upper().split(" "))


def fit(font, text, width, middle=False):
    """Tronque `text` avec « … » pour qu'il tienne dans `width` pixels."""
    if width <= 0:
        return ""
    if font.measure(text) <= width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        cand = (text[:mid // 2] + "…" + text[len(text) - (mid - mid // 2):]) if middle else text[:mid] + "…"
        if font.measure(cand) <= width:
            lo = mid
        else:
            hi = mid - 1
    if lo == 0:
        return ""
    return (text[:lo // 2] + "…" + text[len(text) - (lo - lo // 2):]) if middle else text[:lo] + "…"


# --------------------------------------------------------------------------- styles ttk
def apply_ttk(root, p, f):
    """Styles des widgets ttk restants (inspecteur, barres de défilement)."""
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=p["bg"], foreground=p["txt"], font=f.body, bordercolor=p["line"],
                lightcolor=p["bg"], darkcolor=p["bg"], troughcolor=p["bg"], focuscolor=p["blu"],
                selectbackground=p["selrow"], selectforeground=p["txt"], insertcolor=p["txt"])
    s.configure("TFrame", background=p["bg"])
    s.configure("Panel.TFrame", background=p["panel"])
    s.configure("TLabel", background=p["bg"], foreground=p["txt"])
    s.configure("Treeview", background=p["panel"], fieldbackground=p["panel"], foreground=p["txt"],
                rowheight=26, borderwidth=0, font=f.mini)
    s.map("Treeview", background=[("selected", p["selrow"])], foreground=[("selected", p["txt"])])
    s.configure("Treeview.Heading", background=p["panel"], foreground=p["txt3"], font=f.cap,
                borderwidth=0, relief="flat", padding=(8, 6))
    s.map("Treeview.Heading", background=[("active", p["panel"])])
    s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    for o in ("Vertical", "Horizontal"):
        s.configure(f"{o}.TScrollbar", background=p["line2"], troughcolor=p["bg"], bordercolor=p["bg"],
                    arrowcolor=p["txt3"], lightcolor=p["line2"], darkcolor=p["line2"], gripcount=0,
                    arrowsize=10)
        s.map(f"{o}.TScrollbar", background=[("active", p["txt3"])])
    s.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    s.layout("Horizontal.TScrollbar", [("Horizontal.Scrollbar.trough", {"sticky": "we", "children": [
        ("Horizontal.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    s.configure("Panel.Vertical.TScrollbar", troughcolor=p["panel"], bordercolor=p["panel"])
    s.layout("Panel.Vertical.TScrollbar", s.layout("Vertical.TScrollbar"))
    root.option_add("*Menu.font", f.small)
    return s


# --------------------------------------------------------------------------- dessin
def round_rect(cv, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def _parent_bg(master, default):
    try:
        return master.cget("bg")
    except tk.TclError:
        return default


class PillButton(tk.Canvas):
    """Bouton aux coins arrondis.
    kind : secondary | primary | danger | link | mini | mini_primary."""

    def __init__(self, master, text, command, p, f, kind="secondary", font=None, padx=None, height=None,
                 width=None, menu=False):
        self.p, self.f, self.kind, self.command, self.menu = p, f, kind, command, menu
        self.font = font or (f.mini if kind.startswith("mini") else f.small_b if kind != "link" else f.body_b)
        self.padx = padx if padx is not None else (10 if kind.startswith("mini") else 16 if kind != "link" else 6)
        h = height or (26 if kind.startswith("mini") else 38)
        super().__init__(master, highlightthickness=0, bd=0, height=h, bg=_parent_bg(master, p["bg"]),
                         cursor="hand2" if kind == "link" else "")
        self.text, self.fixed_w = text, width
        self.enabled, self.hover, self.pressed = True, False, False
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", lambda e: self.draw())
        self._resize()

    def _resize(self):
        w = self.fixed_w or self.font.measure(self.text) + 2 * self.padx + (16 if self.menu else 0)
        self.configure(width=w)
        self.draw()

    def set_text(self, text):
        self.text = text
        self._resize()

    def set_enabled(self, on):
        self.enabled = bool(on)
        self.draw()

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self.draw()

    def _release(self, e):
        was = self.pressed
        self._set(pressed=False)
        if was and self.enabled and 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height():
            if self.command:
                self.command()

    def draw(self):
        self.delete("all")
        p, k = self.p, self.kind
        w, h = int(self.cget("width")), int(self.cget("height"))
        hot = self.enabled and (self.hover or self.pressed)
        fill, outline, txt = "", p["line2"], p["txt"]
        if k in ("primary", "mini_primary"):
            fill, outline = (p["blu_hi"] if hot else p["blu"]), ""
            txt = "#ffffff"
            if not self.enabled:
                fill, txt = p["chip"], p["txt3"]
        elif k == "link":
            outline, txt = "", (p["blu_hi"] if hot else p["blu"]) if self.enabled else p["txt3"]
        else:
            fill = p["chip"] if hot else ""
            if k == "danger":
                txt = p["rec"]
            elif k == "mini":
                txt = p["txt2"] if not hot else p["txt"]
            if not self.enabled:
                txt, outline = p["txt3"], p["line"]
        r = 6 if k.startswith("mini") else 8
        if fill or outline:
            round_rect(self, 1, 1, w - 1, h - 1, r, fill=fill or self.cget("bg"), outline=outline,
                       width=1 if outline else 0)
        cx = w / 2 - (8 if self.menu else 0)
        self.create_text(cx, h / 2, text=self.text, font=self.font, fill=txt)
        if self.menu:  # chevron ⌄
            x, y = cx + self.font.measure(self.text) / 2 + 10, h / 2 + 1
            self.create_line(x - 4, y - 2, x, y + 2, x + 4, y - 2, fill=txt, width=1.5)


class IconButton(tk.Canvas):
    """Icône de l'en-tête (soleil, lune, réglages), 40 × 40."""

    def __init__(self, master, icon, command, p, size=40):
        super().__init__(master, width=size, height=size, highlightthickness=0, bd=0,
                         bg=_parent_bg(master, p["bg"]))
        self.icon, self.command, self.p, self.size = icon, command, p, size
        self.hover, self.active = False, False
        self.bind("<Enter>", lambda e: self._set(True))
        self.bind("<Leave>", lambda e: self._set(False))
        self.bind("<ButtonRelease-1>", lambda e: self.command and self.command())
        self.draw()

    def _set(self, hover):
        self.hover = hover
        self.draw()

    def set_active(self, on):
        self.active = on
        self.draw()

    def draw(self):
        self.delete("all")
        p, s = self.p, self.size
        c = s / 2
        bg = self.cget("bg")
        if self.hover or self.active:
            round_rect(self, 0, 0, s, s, 10, fill=p["chip"], outline="")
            bg = p["chip"]
        col = p["txt"] if (self.hover or self.active) else p["txt2"]
        if self.icon == "sun":
            self.create_oval(c - 5, c - 5, c + 5, c + 5, outline=col, width=2)
            for k in range(8):
                a = k * math.pi / 4
                self.create_line(c + 8 * math.cos(a), c + 8 * math.sin(a), c + 11 * math.cos(a),
                                 c + 11 * math.sin(a), fill=col, width=2, capstyle="round")
        elif self.icon == "moon":
            self.create_oval(c - 8, c - 8, c + 8, c + 8, fill=col, outline="")
            self.create_oval(c - 4, c - 12, c + 10, c + 2, fill=bg, outline="")
        elif self.icon == "gear":
            pts, teeth = [], 8
            for k in range(teeth * 4):
                a = k * 2 * math.pi / (teeth * 4) - math.pi / (teeth * 4) * 1.5
                r = 10 if (k % 4) in (1, 2) else 7.5
                pts += [c + r * math.cos(a), c + r * math.sin(a)]
            self.create_polygon(pts, fill=col, outline="")
            self.create_oval(c - 3.2, c - 3.2, c + 3.2, c + 3.2, fill=bg, outline="")


class Toggle(tk.Canvas):
    """Interrupteur 44 × 24 façon MixCheck, lié à une BooleanVar."""

    def __init__(self, master, variable, p, enabled=True):
        bg = _parent_bg(master, p["bg"])
        super().__init__(master, width=44, height=24, highlightthickness=0, bd=0, bg=bg)
        self.var, self.p, self.enabled = variable, p, enabled
        self.bind("<Button-1>", lambda e: self.flip())
        self._trace = self.var.trace_add("write", lambda *a: self.draw())
        self.bind("<Destroy>", self._untrace)
        self.draw()

    def _untrace(self, e):
        if e.widget is self:
            try:
                self.var.trace_remove("write", self._trace)
            except (tk.TclError, ValueError):
                pass

    def flip(self):
        if self.enabled:
            self.var.set(not self.var.get())

    def draw(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        on = bool(self.var.get())
        p = self.p
        track = p["blu"] if on else p["toggleoff"]
        knob = "#ffffff"
        if not self.enabled:
            track, knob = mix(track, self.cget("bg"), .4), mix("#ffffff", self.cget("bg"), .5)
        round_rect(self, 0, 0, 44, 24, 12, fill=track, outline="")
        x = 23 if on else 3
        self.create_oval(x, 3, x + 18, 21, fill=knob, outline="")


class ToggleRow(tk.Frame):
    """Interrupteur suivi de son libellé (cliquable lui aussi)."""

    def __init__(self, master, variable, text, p, f, enabled=True, note=None, bg=None):
        bg = bg or _parent_bg(master, p["bg"])
        super().__init__(master, bg=bg)
        self.toggle = Toggle(self, variable, p, enabled)
        self.toggle.configure(bg=bg)
        self.toggle.pack(side="left")
        lbl = tk.Label(self, text=text, font=f.body, bg=bg, fg=p["txt"] if enabled else p["txt3"])
        lbl.pack(side="left", padx=(12, 0))
        lbl.bind("<Button-1>", lambda e: self.toggle.flip())
        if note:
            tk.Label(self, text=note, font=f.small, bg=bg, fg=p["txt3"]).pack(side="left", padx=(8, 0))


class Segmented(tk.Canvas):
    """Contrôle segmenté lié à une StringVar."""

    def __init__(self, master, variable, options, p, f, command=None, height=38, padx=16):
        super().__init__(master, highlightthickness=0, bd=0, height=height, bg=_parent_bg(master, p["bg"]))
        self.var, self.options, self.p, self.f, self.command = variable, options, p, f, command
        self.padx, self.enabled = padx, True
        self.widths = [f.small_b.measure(label) + 2 * padx for _, label in options]
        self.configure(width=sum(self.widths) + 6)
        self.bind("<Button-1>", self._click)
        self._trace = self.var.trace_add("write", lambda *a: self.draw())
        self.bind("<Destroy>", lambda e: e.widget is self and self._untrace())
        self.draw()

    def _untrace(self):
        try:
            self.var.trace_remove("write", self._trace)
        except (tk.TclError, ValueError):
            pass

    def _click(self, e):
        if not self.enabled:
            return
        x = 3
        for (value, _), w in zip(self.options, self.widths):
            if x <= e.x < x + w:
                if self.var.get() != value:
                    self.var.set(value)
                    if self.command:
                        self.command()
                return
            x += w

    def draw(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        p = self.p
        h = int(self.cget("height"))
        round_rect(self, 0, 0, int(self.cget("width")), h, 9, fill=p["chip"], outline="")
        x = 3
        for (value, label), w in zip(self.options, self.widths):
            on = value == self.var.get()
            if on:
                round_rect(self, x, 3, x + w, h - 3, 7, fill=p["segon"], outline="")
            self.create_text(x + w / 2, h / 2, text=label, font=self.f.small_b if on else self.f.small,
                             fill=p["txt"] if on else p["txt3"])
            x += w


class FieldBox(tk.Canvas):
    """Champ sur fond `chip` avec libellé en capitales (PREMIER POINT, PISTE…)."""

    def __init__(self, master, label, variable, p, f, chars=11, font=None, stretch=False, height=38, fill=None):
        super().__init__(master, highlightthickness=0, bd=0, height=height, bg=_parent_bg(master, p["bg"]))
        self.p, self.f, self.label, self.enabled, self.focus = p, f, caps(label), True, False
        self.fill = fill or p["chip"]
        font = font or f.mono_l
        self.lw = f.cap.measure(self.label)
        self.entry = tk.Entry(self, textvariable=variable, font=font, bg=self.fill, fg=p["txt"], width=chars,
                              insertbackground=p["txt"], relief="flat", bd=0, highlightthickness=0,
                              disabledbackground=self.fill, disabledforeground=mix(p["txt"], self.fill, .3),
                              selectbackground=p["selrow"], selectforeground=p["txt"])
        self.entry.bind("<FocusIn>", lambda e: self._focus(True))
        self.entry.bind("<FocusOut>", lambda e: self._focus(False))
        self.entry.bind("<Return>", lambda e: self.focus_set())
        ew = self.entry.winfo_reqwidth()
        if not stretch:
            self.configure(width=14 + self.lw + 12 + ew + 12)
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", lambda e: self.enabled and self.entry.focus_set())
        self.draw()

    def _focus(self, on):
        self.focus = on
        self.draw()

    def set_enabled(self, on):
        self.enabled = on
        self.entry.configure(state="normal" if on else "disabled")
        self.draw()

    def draw(self):
        self.delete("bg")
        p = self.p
        w, h = self.winfo_width(), int(self.cget("height"))
        if w < 10:
            w = self.winfo_reqwidth()
        round_rect(self, 0, 0, w, h, 8, fill=self.fill, outline=p["blu"] if self.focus else "",
                   width=1, tags="bg")
        self.create_text(14, h / 2, text=self.label, anchor="w", font=self.f.cap, tags="bg",
                         fill=p["txt3"] if self.enabled else mix(p["txt3"], self.fill, .45))
        self.tag_lower("bg")
        self.delete("win")
        x = 14 + self.lw + 12
        self.create_window(x, h / 2, window=self.entry, anchor="w", width=max(20, w - x - 12), tags="win")


class DropField(tk.Canvas):
    """Menu déroulant sur fond `chip` : « AUDIO  Lier aux fichiers source ⌄ »."""

    def __init__(self, master, label, variable, options, p, f, height=38, fill=None):
        super().__init__(master, highlightthickness=0, bd=0, height=height, bg=_parent_bg(master, p["bg"]))
        self.p, self.f, self.label, self.var, self.options = p, f, caps(label), variable, options
        self.fill = fill or p["chip"]
        self.menu = tk.Menu(self, tearoff=False)
        for value, text in options:
            self.menu.add_radiobutton(label=text, value=value, variable=variable)
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", self._post)
        self._trace = variable.trace_add("write", lambda *a: self.draw())
        self.bind("<Destroy>", lambda e: e.widget is self and self._untrace())

    def _untrace(self):
        try:
            self.var.trace_remove("write", self._trace)
        except (tk.TclError, ValueError):
            pass

    def _post(self, e):
        self.menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height() + 2)

    def draw(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        p = self.p
        w, h = self.winfo_width(), int(self.cget("height"))
        round_rect(self, 0, 0, w, h, 8, fill=self.fill, outline="")
        self.create_text(14, h / 2, text=self.label, anchor="w", font=self.f.cap, fill=p["txt3"])
        x = 14 + self.f.cap.measure(self.label) + 14
        value = dict(self.options).get(self.var.get(), self.var.get())
        self.create_text(x, h / 2, text=fit(self.f.small_b, value, w - x - 36), anchor="w",
                         font=self.f.small_b, fill=p["txt"])
        cx, cy = w - 18, h / 2 + 1
        self.create_line(cx - 4, cy - 2, cx, cy + 2, cx + 4, cy - 2, fill=p["txt3"], width=1.5)


class Dot(tk.Canvas):
    def __init__(self, master, size, color, p):
        super().__init__(master, width=size, height=size, highlightthickness=0, bd=0,
                         bg=_parent_bg(master, p["bg"]))
        self.size = size
        self.oval = self.create_oval(0, 0, size, size, fill=color, outline="")

    def set_color(self, color):
        self.itemconfigure(self.oval, fill=color)


class ProgressLine(tk.Canvas):
    def __init__(self, master, p, height=6):
        super().__init__(master, height=height, highlightthickness=0, bd=0, bg=_parent_bg(master, p["bg"]))
        self.p, self.value = p, 0.0
        self.bind("<Configure>", lambda e: self.draw())

    def set(self, value):
        self.value = max(0.0, min(1.0, value))
        self.draw()

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), int(self.cget("height"))
        round_rect(self, 0, 0, w, h, h / 2, fill=self.p["chip"], outline="")
        if self.value > 0:
            round_rect(self, 0, 0, max(h, w * self.value), h, h / 2, fill=self.p["blu"], outline="")


class Tooltip:
    """Info-bulle simple au survol."""

    def __init__(self, widget, p, f, text_fn):
        self.w, self.p, self.f, self.text_fn = widget, p, f, text_fn
        self.tip, self.job = None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _schedule(self, e):
        self._hide()
        self.job = self.w.after(600, self._show)

    def _show(self):
        text = self.text_fn()
        if not text:
            return
        self.tip = tk.Toplevel(self.w)
        self.tip.wm_overrideredirect(True)
        tk.Label(self.tip, text=text, font=self.f.mini, bg=self.p["panel"], fg=self.p["txt2"], padx=10, pady=6,
                 justify="left", wraplength=420, highlightthickness=1,
                 highlightbackground=self.p["line2"]).pack()
        x = self.w.winfo_rootx()
        y = self.w.winfo_rooty() + self.w.winfo_height() + 6
        self.tip.wm_geometry(f"+{x}+{y}")

    def _hide(self, e=None):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


class SessionChip(tk.Canvas):
    """Pastille de session : point d'état, nom, détail en mono, bouton Actualiser."""

    def __init__(self, master, p, f, refresh_label, on_refresh, on_click=None):
        super().__init__(master, height=40, highlightthickness=0, bd=0, bg=_parent_bg(master, p["bg"]))
        self.p, self.f, self.refresh_label, self.on_refresh, self.on_click = p, f, refresh_label, on_refresh, on_click
        self.state, self.name, self.detail = "wait", "", ""
        self.btn_box, self.btn_hover = None, False
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._hover(False))

    def set(self, state, name, detail=""):
        self.state, self.name, self.detail = state, name, detail
        self.draw()

    def _in_btn(self, e):
        b = self.btn_box
        return b and b[0] <= e.x <= b[2] and b[1] <= e.y <= b[3]

    def _motion(self, e):
        self._hover(bool(self._in_btn(e)))

    def _hover(self, on):
        if on != self.btn_hover:
            self.btn_hover = on
            self.draw()

    def _click(self, e):
        if self._in_btn(e):
            self.on_refresh()
        elif self.on_click:
            self.on_click()

    def draw(self):
        self.delete("all")
        p, f = self.p, self.f
        off = self.state == "off"
        dot = {"ok": p["grn"], "warn": p["amb"], "wait": p["amb"], "off": p["rec"]}[self.state]
        name_w = f.body_b.measure(self.name)
        det_w = f.mono_s.measure(self.detail) if self.detail else 0
        btn_w = f.mini.measure(self.refresh_label) + 20
        w = 14 + 9 + 10 + name_w + (12 + det_w if det_w else 0) + 12 + btn_w + 8
        h = 40
        self.configure(width=w)
        round_rect(self, 0, 0, w, h, 10, fill=p["recbg"] if off else p["chip"], outline="")
        self.create_oval(14, h / 2 - 4.5, 23, h / 2 + 4.5, fill=dot, outline="")
        x = 33
        self.create_text(x, h / 2, text=self.name, anchor="w", font=f.body_b, fill=p["txt"])
        x += name_w
        if det_w:
            self.create_text(x + 12, h / 2 + 1, text=self.detail, anchor="w", font=f.mono_s, fill=p["txt3"])
            x += 12 + det_w
        x += 12
        bx1, by1, bx2, by2 = x, 7, x + btn_w, h - 7
        self.btn_box = (bx1, by1, bx2, by2)
        if off:
            fill, outline, txt = (p["blu_hi"] if self.btn_hover else p["blu"]), "", "#ffffff"
        else:
            fill, outline = (p["segon"] if self.btn_hover else p["chip"]), p["line2"]
            txt = p["txt"] if self.btn_hover else p["txt2"]
        round_rect(self, bx1, by1, bx2, by2, 6, fill=fill, outline=outline, width=1 if outline else 0)
        self.create_text((bx1 + bx2) / 2, h / 2, text=self.refresh_label, font=f.mini, fill=txt)


class RichLine(tk.Canvas):
    """Ligne de texte faite de segments [(texte, police, couleur)], tronquée à la largeur."""

    def __init__(self, master, p, height):
        super().__init__(master, height=height, highlightthickness=0, bd=0, bg=_parent_bg(master, p["bg"]))
        self.segments = []
        self.bind("<Configure>", lambda e: self.draw())

    def set(self, segments):
        self.segments = segments
        self.draw()

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), int(self.cget("height"))
        x = 0
        for text, font, color, gap in self.segments:
            x += gap
            room = w - x
            if room < 20:
                break
            t = fit(font, text, room)
            self.create_text(x, h / 2, text=t, anchor="w", font=font, fill=color)
            x += font.measure(t)


# --------------------------------------------------------------------------- timeline
class TimelineCanvas(tk.Canvas):
    """
    Timeline en pistes empilées : VIDÉO / AAF · OMF / AUDIO VIDEO, en lecture seule.
    items : [{"index", "kind": "aaf"|"video", "name", "start": frames, "dur": frames,
              "audio": libellé | None, "state"}]
    state ∈ plan | selected | done | current | pending | failed | missing
    """

    LABEL_W, RULER_H, PAD, GAP = 118, 22, 14, 6

    def __init__(self, master, p, f, lanes, on_click=None, on_double=None):
        super().__init__(master, highlightthickness=0, bd=0, bg=_parent_bg(master, p["bg"]), height=150)
        self.p, self.f, self.lane_labels = p, f, lanes
        self.on_click, self.on_double = on_click, on_double
        self.items, self.playhead, self.fps, self.empty_start = [], None, 25, 3600 * 25
        self.zoom, self.view_t0, self.boxes = 1.0, None, []
        self._wheel = 0
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", lambda e: self._hit(e, self.on_click))
        self.bind("<Double-Button-1>", lambda e: self._hit(e, self.on_double))
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Shift-MouseWheel>", self._on_wheel)

    # -- données
    def set_items(self, items, fps=25, playhead=None, empty_start=None):
        self.items, self.fps, self.playhead = items, fps, playhead
        if empty_start is not None:
            self.empty_start = empty_start
        self.redraw()

    def _span(self):
        if self.items:
            t0 = min(i["start"] for i in self.items)
            t1 = max(i["start"] + i["dur"] for i in self.items)
        else:
            t0 = self.empty_start
            t1 = t0 + 70 * 60 * self.fps
        span = max(t1 - t0, self.fps) * 1.03
        return t0, span

    # -- zoom
    def zoom_by(self, factor, center=None):
        t0, span = self._span()
        old_span = span / self.zoom
        vt0 = self.view_t0 if self.view_t0 is not None else t0
        if center is None:
            center = vt0 + old_span / 2
        self.zoom = max(1.0, min(256.0, self.zoom * factor))
        new_span = span / self.zoom
        self.view_t0 = center - new_span / 2
        self._clamp()
        self.redraw()

    def zoom_all(self):
        self.zoom, self.view_t0 = 1.0, None
        self.redraw()

    def _clamp(self):
        t0, span = self._span()
        vspan = span / self.zoom
        if self.zoom <= 1.0:
            self.view_t0 = None
            return
        self.view_t0 = max(t0, min(self.view_t0 if self.view_t0 is not None else t0, t0 + span - vspan))

    def _on_wheel(self, e):
        if self.zoom <= 1.0:
            return
        self._wheel += e.delta
        steps = int(self._wheel / 30)
        if not steps:
            return
        self._wheel -= steps * 30
        t0, span = self._span()
        vt0 = self.view_t0 if self.view_t0 is not None else t0
        self.view_t0 = vt0 - steps * (span / self.zoom) * 0.05
        self._clamp()
        self.redraw()

    def _hit(self, e, cb):
        if not cb:
            return
        for x1, y1, x2, y2, idx in reversed(self.boxes):
            if x1 <= e.x <= x2 and y1 <= e.y <= y2:
                cb(idx)
                return

    def _tc(self, frames, secs=False):
        s = int(frames // self.fps)
        return f"{s // 3600:02d}:{s // 60 % 60:02d}" + (f":{s % 60:02d}" if secs else "")

    # -- dessin
    def redraw(self):
        self.delete("all")
        self.boxes = []
        p, f = self.p, self.f
        w, h = self.winfo_width(), self.winfo_height()
        if w < 200 or h < 60:
            return
        round_rect(self, 0, 0, w, h, 12, fill=p["well"], outline="")
        x0, x1 = self.PAD + self.LABEL_W, w - self.PAD
        top = 10 + self.RULER_H + self.GAP
        lane_h = (h - top - self.PAD - 2 * self.GAP) / 3
        t0, span = self._span()
        vspan = span / self.zoom
        vt0 = self.view_t0 if self.view_t0 is not None else t0
        X = lambda t: x0 + (t - vt0) / vspan * (x1 - x0)  # noqa: E731

        # règle : pas adapté au zoom (au moins ~70 px entre deux repères)
        steps_s = [10, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200]
        step_s = next((s for s in steps_s if s * self.fps / vspan * (x1 - x0) >= 110), steps_s[-1])
        step = step_s * self.fps
        t = vt0 - vt0 % step
        while t <= vt0 + vspan:
            if t >= vt0 - 1:
                xx = X(t)
                self.create_line(xx, 10, xx, 10 + self.RULER_H, fill=p["line2"])
                if xx + 40 < x1 + 10:
                    self.create_text(xx + 5, 10 + self.RULER_H / 2, text=self._tc(t, step_s < 60), anchor="w",
                                     fill=p["txt3"], font=f.mono_s)
            t += step

        colors = [p["vid"], p["aaf"], p["vaud"]]
        for li, label in enumerate(self.lane_labels):
            y = top + li * (lane_h + self.GAP)
            color = colors[li]
            self.create_rectangle(self.PAD, y + lane_h / 2 - 4, self.PAD + 8, y + lane_h / 2 + 4,
                                  fill=color, outline="")
            self.create_text(self.PAD + 16, y + lane_h / 2, text=fit(f.cap, caps(label), self.LABEL_W - 20),
                             anchor="w", fill=p["txt3"], font=f.cap)
            round_rect(self, x0, y, x1, y + lane_h, 6, fill=p["lane"], outline="")
            for it in self.items:
                if li == 0 and it["kind"] != "video" or li == 1 and it["kind"] != "aaf":
                    continue
                if li == 2 and (it["kind"] != "video" or not it.get("audio")):
                    continue
                label_txt = it["name"] if li < 2 else it["audio"]
                if li == 2 and it["state"] == "missing":
                    label_txt = it["name"]
                xa, xb = X(it["start"]), X(it["start"] + it["dur"])
                if xb < x0 or xa > x1:
                    continue
                self._clip(it, max(xa, x0), min(xb, x1), y + 3, y + lane_h - 3, color, label_txt,
                           waveform=li > 0)
        if self.playhead is not None:
            px = X(self.playhead)
            if x0 <= px <= x1:
                self.create_line(px, top - 4, px, h - self.PAD, fill=p["play"], width=2)
                self.create_polygon(px - 6, 6, px + 6, 6, px, 14, fill=p["play"], outline="")

    def _clip(self, it, xa, xb, ya, yb, color, label, waveform):
        p, st = self.p, it["state"]
        if xb - xa < 2:
            xb = xa + 2
        fill, outline, txt, dash, width = color, "", p["clip_txt"], None, 1
        if st == "pending":
            fill, txt = mix(color, p["well"], .3), p["txt2"]
        elif st == "failed":
            fill, outline, txt, label = p["recbg"], p["rec"], p["rec"], "✗ " + label
        elif st == "missing":
            fill, outline, txt, dash, width = p["well"], p["rec"], p["rec"], (4, 3), 1
        elif st == "done":
            label = "✓ " + label
        elif st == "current":
            fill, outline, txt, width = mix(color, p["well"], .45), p["blu"], p["txt"], 2
        r = min(5, (yb - ya) / 2)
        round_rect(self, xa, ya, xb, yb, r, fill=fill, outline=outline, width=width, dash=dash)
        if waveform and st in ("plan", "selected", "done", "current") and xb - xa > 8:
            self._waveform(it["name"], xa, xb, ya, yb, mix(color, "#000000", .78) if st != "current"
                           else mix(color, p["well"], .75))
        if st == "selected":
            round_rect(self, xa - 2, ya - 2, xb + 2, yb + 2, r + 1, fill="", outline=p["txt"], width=2)
        text = fit(self.f.clip, label, xb - xa - 14)
        if text:
            if waveform and st in ("plan", "selected", "done"):
                tw = self.f.clip.measure(text)
                self.create_rectangle(xa + 4, ya + 2, xa + 12 + tw, yb - 2, fill=fill, outline="")
            self.create_text(xa + 8, (ya + yb) / 2, text=text, anchor="w", fill=txt, font=self.f.clip)
        self.boxes.append((xa, ya, xb, yb, it["index"]))

    def _waveform(self, seed, xa, xb, ya, yb, color):
        mid, half = (ya + yb) / 2, (yb - ya) / 2 - 2
        h = abs(hash(seed)) % 997
        x = xa + 4
        k = 0
        while x < xb - 3:
            v = 0.45 + 0.55 * abs(math.sin(h * 0.37 + k * 0.9) * math.cos(h * 0.11 + k * 0.23))
            self.create_line(x, mid - half * v, x, mid + half * v, fill=color, width=1)
            x += 3
            k += 1


# --------------------------------------------------------------------------- tableau
class PlanTable(tk.Frame):
    """
    Tableau du plan dessiné sur Canvas : lignes de 40 px séparées d'un filet, couleurs par
    cellule, sélection multiple, glisser pour réordonner, édition du TC par double-clic.
    rows : [{"num", "tc", "linked", "kind", "kind_color", "name", "name_color", "dur",
             "folder", "state", "state_color"}]
    """

    RH, HEAD_H = 40, 38

    def __init__(self, master, p, f, headers, callbacks):
        super().__init__(master, bg=p["bg"])
        self.p, self.f, self.headers, self.cb = p, f, headers, callbacks
        self.rows, self.selection, self.anchor = [], set(), None
        self.show_state, self.sort_col = False, None
        self.head = tk.Canvas(self, height=self.HEAD_H, highlightthickness=0, bd=0, bg=p["bg"])
        self.head.pack(fill="x")
        body = tk.Frame(self, bg=p["bg"])
        body.pack(fill="both", expand=True)
        self.body = tk.Canvas(body, highlightthickness=0, bd=0, bg=p["bg"], height=80, yscrollincrement=1,
                              takefocus=1)
        self.sb = ttk.Scrollbar(body, orient="vertical", command=self.body.yview)
        self.body.configure(yscrollcommand=self._on_yscroll)
        self.body.pack(side="left", fill="both", expand=True)
        self._sb_shown = False
        self._press, self._drag, self._wheel = None, None, 0
        self.body.bind("<Configure>", lambda e: self.redraw())
        self.head.bind("<ButtonRelease-1>", self._head_click)
        self.body.bind("<ButtonPress-1>", self._on_press)
        self.body.bind("<B1-Motion>", self._on_motion)
        self.body.bind("<ButtonRelease-1>", self._on_release)
        self.body.bind("<Double-Button-1>", self._on_double)
        for seq in ("<Button-2>", "<Button-3>", "<Control-Button-1>"):
            self.body.bind(seq, self._on_context)
        self.body.bind("<MouseWheel>", self._on_wheel)
        k = self.body.bind
        k("<Up>", lambda e: self._key_move(-1, False))
        k("<Down>", lambda e: self._key_move(1, False))
        k("<Shift-Up>", lambda e: self._key_move(-1, True))
        k("<Shift-Down>", lambda e: self._key_move(1, True))
        k("<Option-Up>", lambda e: self.cb["move"](-1))
        k("<Option-Down>", lambda e: self.cb["move"](1))
        k("<BackSpace>", lambda e: self.cb["delete"]())
        k("<Delete>", lambda e: self.cb["delete"]())
        k("<Return>", lambda e: self.selection and self.cb["double"](min(self.selection)))
        k("<Command-a>", lambda e: self.select(range(len(self.rows))))

    # -- géométrie
    def columns(self, width):
        """[(clé, x, largeur, ancrage)]"""
        spec = [("num", 40, 0, "w"), ("tc", 150, 0, "w"), ("type", 100, 0, "w"), ("file", 0, 5, "w"),
                ("dur", 80, 0, "w"), ("dir", 0, 4, "w"), ("state", 140 if self.show_state else 0, 0, "e")]
        pad = 12
        fixed = sum(s[1] for s in spec)
        weights = sum(s[2] for s in spec) or 1
        rest = max(0, width - 2 * pad - fixed)
        out, x = [], pad
        for key, w, weight, anchor in spec:
            cw = w + rest * weight / weights
            out.append((key, x, cw, anchor))
            x += cw
        return out

    def cell_bbox(self, index, key):
        for k, x, w, _ in self.columns(self.body.winfo_width()):
            if k == key:
                return x - 6, index * self.RH + 4, w - 4, self.RH - 8
        return None

    # -- données
    def set_rows(self, rows, show_state):
        self.rows = rows
        self.show_state = show_state
        self.selection = {i for i in self.selection if i < len(rows)}
        self.redraw()

    def select(self, indexes, notify=True, see=True):
        self.selection = set(indexes)
        if self.selection:
            self.anchor = min(self.selection) if self.anchor not in self.selection else self.anchor
            if see:
                self.see(min(self.selection))
        self.redraw()
        if notify:
            self.cb["select"]()

    def see(self, index):
        h = self.body.winfo_height()
        total = max(1, len(self.rows) * self.RH)
        top = self.body.canvasy(0)
        y1, y2 = index * self.RH, (index + 1) * self.RH
        if y1 < top:
            self.body.yview_moveto(y1 / total)
        elif y2 > top + h:
            self.body.yview_moveto((y2 - h) / total)

    def _on_yscroll(self, a, b):
        self.sb.set(a, b)
        need = float(a) > 0 or float(b) < 1
        if need and not self._sb_shown:
            self.sb.pack(side="right", fill="y")
            self._sb_shown = True
        elif not need and self._sb_shown:
            self.sb.pack_forget()
            self._sb_shown = False

    # -- dessin
    def redraw(self):
        p, f = self.p, self.f
        w = self.body.winfo_width()
        if w < 50:
            return
        cols = self.columns(w)
        hd = self.head
        hd.delete("all")
        hw = hd.winfo_width() or w
        for key, x, cw, anchor in cols:
            if cw <= 0:
                continue
            text = self.headers.get(key, "")
            if key == self.sort_col:
                text += " ▲"
            label = fit(f.cap, caps(text) if key != "num" else text, cw - 12)
            hx = x + cw - 12 if anchor == "e" else x
            hd.create_text(hx, self.HEAD_H / 2 + 2, text=label, anchor=anchor, font=f.cap, fill=p["txt3"])
        hd.create_line(0, self.HEAD_H - 1, hw, self.HEAD_H - 1, fill=p["line"])

        b = self.body
        b.addtag_all("cell")
        b.dtag("editor", "cell")
        b.delete("cell")
        RH = self.RH
        for i, r in enumerate(self.rows):
            y = i * RH
            if i in self.selection:
                b.create_rectangle(0, y, w, y + RH, fill=p["selrow"], outline="")
            b.create_line(0, y + RH - 1, w, y + RH - 1, fill=p["line"])
            cy = y + RH / 2
            for key, x, cw, anchor in cols:
                if cw <= 0:
                    continue
                room = cw - 14
                if key == "num":
                    b.create_text(x, cy, text=str(r["num"]), anchor="w", font=f.mono_s, fill=p["txt3"])
                elif key == "tc":
                    b.create_text(x, cy, text=r["tc"], anchor="w", font=f.mono_l, fill=p["txt"])
                    if r.get("linked"):
                        b.create_text(x + f.mono_l.measure(r["tc"]) + 8, cy, text="⇄", anchor="w",
                                      font=f.small, fill=p["txt3"])
                elif key == "type":
                    b.create_rectangle(x, cy - 4, x + 8, cy + 4, fill=r["kind_color"], outline="")
                    b.create_text(x + 16, cy, text=r["kind"], anchor="w", font=f.small, fill=p["txt"])
                elif key == "file":
                    b.create_text(x, cy, text=fit(f.body, r["name"], room), anchor="w", font=f.body,
                                  fill=r.get("name_color") or p["txt"])
                elif key == "dur":
                    b.create_text(x, cy, text=r["dur"], anchor="w", font=f.mono_s, fill=p["txt2"])
                elif key == "dir":
                    b.create_text(x, cy, text=fit(f.small, r["folder"], room, middle=True), anchor="w",
                                  font=f.small, fill=p["txt3"])
                elif key == "state" and r.get("state"):
                    b.create_text(x + cw - 12, cy, text=fit(f.small_b, r["state"], room), anchor="e",
                                  font=f.small_b, fill=r.get("state_color") or p["txt3"])
        if self._drag is not None:
            y = self._drag * RH
            b.create_line(0, y, w, y, fill=p["blu"], width=2)
        b.configure(scrollregion=(0, 0, w, max(len(self.rows) * RH, 1)))

    # -- interactions
    def _row_at(self, e):
        y = self.body.canvasy(e.y)
        i = int(y // self.RH)
        return i if 0 <= i < len(self.rows) else None

    def _on_press(self, e):
        self.body.focus_set()
        i = self._row_at(e)
        self._press = (e.y, i, e.state)
        self._drag = None
        if i is None:
            self.select([])
            return
        shift, cmd = e.state & 0x1, e.state & 0x8
        if shift and self.anchor is not None:
            a, b = sorted((self.anchor, i))
            self.select(range(a, b + 1), see=False)
        elif cmd:
            sel = set(self.selection)
            sel ^= {i}
            self.anchor = i
            self.select(sel, see=False)
        elif i not in self.selection:
            self.anchor = i
            self.select([i], see=False)

    def _on_motion(self, e):
        if not self._press or self._press[1] is None or not self.cb["can_reorder"]():
            return
        if self._drag is None and abs(e.y - self._press[0]) < 5:
            return
        y = self.body.canvasy(e.y)
        self._drag = max(0, min(len(self.rows), int(round(y / self.RH))))
        h = self.body.winfo_height()
        if e.y < 10:
            self.body.yview_scroll(-10, "units")
        elif e.y > h - 10:
            self.body.yview_scroll(10, "units")
        self.redraw()

    def _on_release(self, e):
        press, drag = self._press, self._drag
        self._press, self._drag = None, None
        if drag is not None:
            self.redraw()
            self.cb["move_to"](sorted(self.selection), drag)
            return
        if press and press[1] is not None and not (press[2] & 0x1 or press[2] & 0x8):
            if len(self.selection) > 1:
                self.anchor = press[1]
                self.select([press[1]], see=False)

    def _on_double(self, e):
        i = self._row_at(e)
        if i is not None:
            self.cb["double"](i)

    def _on_context(self, e):
        i = self._row_at(e)
        if i is not None and i not in self.selection:
            self.anchor = i
            self.select([i], see=False)
        self.cb["context"](e.x_root, e.y_root)

    def _on_wheel(self, e):
        self._wheel += e.delta
        steps = int(self._wheel / 2)
        if steps:
            self._wheel -= steps * 2
            self.body.yview_scroll(-steps * 6, "units")

    def _key_move(self, d, extend):
        if not self.rows:
            return "break"
        cur = (max(self.selection) if d > 0 else min(self.selection)) if self.selection else -1 if d > 0 else 0
        nxt = max(0, min(len(self.rows) - 1, cur + d))
        if extend and self.anchor is not None:
            a, b = sorted((self.anchor, nxt))
            self.select(range(a, b + 1))
        else:
            self.anchor = nxt
            self.select([nxt])
        return "break"

    def _head_click(self, e):
        for key, x, cw, _ in self.columns(self.body.winfo_width()):
            if x <= e.x < x + cw and key in ("tc", "file"):
                self.cb["sort"](key)
                return


# --------------------------------------------------------------------------- zone de dépôt
class DropZone(tk.Canvas):
    """État vide : bordure pointillée bleue (pleine au survol d'un dépôt), icône et textes."""

    def __init__(self, master, p, f, title, sub, extra_factory=None):
        super().__init__(master, highlightthickness=0, bd=0, bg=_parent_bg(master, p["bg"]), height=200)
        self.p, self.f, self.title, self.sub, self.hot = p, f, title, sub, False
        self.extra = extra_factory(self) if extra_factory else None
        self.bind("<Configure>", lambda e: self.draw())

    def set_hot(self, on):
        if on != self.hot:
            self.hot = on
            self.draw()

    def draw(self):
        self.delete("all")
        p, f = self.p, self.f
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50 or h < 50:
            return
        round_rect(self, 2, 2, w - 2, h - 2, 16, fill=p["selrow"], outline=p["blu"], width=2,
                   dash=None if self.hot else (6, 4))
        wrap = max(200, min(w - 120, 720))
        t = self.create_text(w / 2, 0, text=self.title, font=f.drop, fill=p["txt"], width=wrap, justify="center",
                             anchor="n")
        s = self.create_text(w / 2, 0, text=self.sub, font=f.body, fill=p["txt2"], width=wrap, justify="center",
                             anchor="n")
        th_ = self.bbox(t)[3] - self.bbox(t)[1]
        sh = self.bbox(s)[3] - self.bbox(s)[1]
        ex_h = 24 if self.extra else 0
        block = 48 + 22 + th_ + 10 + sh + (26 + ex_h if ex_h else 0)
        y = max(20, (h - block) / 2)
        cx, col = w / 2, p["blu"]
        self.create_line(cx, y, cx, y + 30, fill=col, width=3, capstyle="round")
        self.create_line(cx - 11, y + 19, cx, y + 30, cx + 11, y + 19, fill=col, width=3, capstyle="round",
                         joinstyle="round")
        self.create_line(cx - 20, y + 36, cx - 20, y + 46, cx + 20, y + 46, cx + 20, y + 36, fill=col, width=3,
                         capstyle="round", joinstyle="round")
        y += 48 + 22
        self.coords(t, cx, y)
        y += th_ + 10
        self.coords(s, cx, y)
        y += sh + 26
        if self.extra:
            self.create_window(cx, y, window=self.extra, anchor="n")
