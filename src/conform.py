"""
PAAF — comparaison de deux versions d'un montage (AAF ou EDL CMX3600).

Chaque clip (image ou son) sert de repère : il est identifié par son média source (nom de
fichier sans extension, ou nom de clip / bobine pour une EDL) et sa position dans ce média
(TC source). Un repère retrouvé dans l'autre version donne un décalage ancien → nouveau TC ;
un vote sur tous les repères découpe la nouvelle version en morceaux (inchangés ou déplacés),
passages nouveaux, et passages de l'ancienne version supprimés.

Étape 1 : lecture seule (liste des changements, rapport, marqueurs). Rien ne modifie la matière.
"""

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

try:
    import aaf2
except Exception:  # pyaaf2 absent : EDL seulement
    aaf2 = None

MIN_MATCH_S = 0.04        # recouvrement minimal pour qu'un repère compte
MIN_SEGMENT_FRAMES = 6    # morceaux plus courts fondus dans leurs voisins (bruit)
GENERATED = re.compile(r"calque|adjustment|effets|effect|titre|title|cache|matte|black|noir|slug|"
                       r"color ?bars|mire|vide|gap", re.I)


@dataclass
class Anchor:
    key: str           # média source normalisé
    s0: float          # TC source (s) : début / fin
    s1: float
    rec: float         # position dans la séquence (s)
    kind: str          # Picture | Sound
    track: str
    name: str


@dataclass
class Edit:
    path: Path
    name: str
    fps: int
    start_frames: int                      # TC de début de la séquence (images)
    duration: float                        # s
    anchors: list = field(default_factory=list)
    kind: str = "AAF"


def normalize(name):
    """« ImportA001C008_2603065B.mxf » → « a001c008_2603065b »."""
    name = re.sub(r"^Import", "", (name or "").strip())
    stem, ext = os.path.splitext(name)
    if ext and len(ext) <= 5 and " " not in ext:
        name = stem
    return name.lower()


# ------------------------------------------------------------------ AAF

def _length(c):
    try:
        return int(c.length)
    except Exception:
        return 0


def _walk(seg, pos, out):
    """Tous les SourceClip d'un segment, calques imbriqués compris : [(position, clip)]."""
    k = type(seg).__name__
    if k == "Sequence":
        p = pos
        for c in seg.components:
            if type(c).__name__ == "Transition":
                p -= _length(c)
                continue
            _walk(c, p, out)
            p += _length(c)
    elif k == "SourceClip":
        out.append((pos, seg))
    elif k == "OperationGroup":
        segs = seg["InputSegments"].value if "InputSegments" in seg else []
        if segs:
            _walk(segs[0], pos, out)
    elif k == "NestedScope":
        for s in seg["Slots"].value:
            _walk(s, pos, out)
    elif k == "Selector":
        if "Selected" in seg:
            _walk(seg["Selected"].value, pos, out)
    elif k == "EssenceGroup":
        ch = seg["Choices"].value
        if ch:
            _walk(ch[0], pos, out)


def _resolve(sc):
    """Suit le clip jusqu'au média source : (nom, TC source en secondes)."""
    mob, sid = sc.mob, sc.slot_id
    try:
        rate = Fraction(mob.slot_at(sid).edit_rate)
    except Exception:
        rate = Fraction(1)
    t = Fraction(sc.start or 0) / rate
    name = mob.name if mob is not None else None
    for _ in range(8):
        if mob is None:
            break
        name = mob.name or name
        try:
            slot = mob.slot_at(sid)
        except Exception:
            break
        seg = slot.segment
        if type(seg).__name__ == "Sequence":
            cc = [c for c in seg.components if type(c).__name__ == "SourceClip"]
            seg = cc[0] if cc else seg
        if type(seg).__name__ != "SourceClip" or seg.mob is None:
            break
        t += Fraction(seg.start or 0) / Fraction(slot.edit_rate)
        mob, sid = seg.mob, seg.slot_id
    return name or "", float(t)


def load_aaf(path):
    if aaf2 is None:
        raise RuntimeError("pyaaf2 non installé")
    import logging
    logging.getLogger().setLevel(logging.ERROR)
    path = Path(path)
    with aaf2.open(str(path), "r") as f:
        tops = list(f.content.toplevel()) or list(f.content.compositionmobs())
        if not tops:
            raise RuntimeError("aucune composition dans l'AAF")
        comp = tops[0]
        edit = Edit(path, comp.name or path.stem, 25, 0, 0.0)
        anchors = []
        rate0 = None
        for slot in comp.slots:
            seg = slot.segment
            if type(seg).__name__ == "Timecode":
                edit.start_frames = int(seg.start or 0)
                edit.fps = int(seg.fps or 25) or 25
                continue
            kind = slot.media_kind
            if kind not in ("Sound", "Picture"):
                continue
            rate = Fraction(slot.edit_rate)
            rate0 = rate0 or rate
            edit.duration = max(edit.duration, float(_length(seg) / rate))
            clips = []
            _walk(seg, 0, clips)
            for pos, sc in clips:
                if sc.mob is None:
                    continue
                name, s0 = _resolve(sc)
                bare = re.sub(r"^Import", "", name)
                if kind == "Picture" and (not os.path.splitext(bare)[1] or GENERATED.search(bare)):
                    continue  # calque de réglage, titre, cache : pas un média source
                d = float(_length(sc) / rate)
                if d <= 0:
                    continue
                anchors.append(Anchor(normalize(name), s0, s0 + d, float(pos / rate), kind,
                                      slot.name or f"{kind} {slot.slot_id}", bare))
        edit.anchors = anchors
        if rate0 and not edit.fps:
            edit.fps = round(float(rate0))
    return edit


# ------------------------------------------------------------------ EDL (CMX3600)

EDL_EVENT = re.compile(r"^\s*(\d{1,4})\s+(\S+)\s+(\S+)\s+(C|D|W\d*|K\s*B?|K)\s*(\d+)?\s+"
                       r"(\d\d[:;.]\d\d[:;.]\d\d[:;.]\d\d)\s+(\d\d[:;.]\d\d[:;.]\d\d[:;.]\d\d)\s+"
                       r"(\d\d[:;.]\d\d[:;.]\d\d[:;.]\d\d)\s+(\d\d[:;.]\d\d[:;.]\d\d[:;.]\d\d)")


def tc_to_frames(tc, fps):
    h, m, s, f = (int(x) for x in re.split(r"[:;.]", tc))
    return ((h * 3600 + m * 60 + s) * fps) + f


def load_edl(path, fps=25):
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    title = path.stem
    events = []
    for line in text.splitlines():
        if line.upper().startswith("TITLE:"):
            title = line.split(":", 1)[1].strip() or title
            continue
        m = EDL_EVENT.match(line)
        if m:
            _, reel, track, _trans, _dur, si, so, ri, ro = m.groups()
            events.append({"reel": reel, "track": track.upper(), "si": si, "so": so, "ri": ri, "ro": ro,
                           "clip": None})
            continue
        cm = re.match(r"^\*\s*FROM CLIP NAME:\s*(.+)$", line.strip(), re.I)
        if cm and events:
            events[-1]["clip"] = cm.group(1).strip()
    if not events:
        raise RuntimeError("aucun événement lisible (EDL CMX3600 attendue)")
    rec0 = min(tc_to_frames(e["ri"], fps) for e in events)
    edit = Edit(path, title, fps, rec0, 0.0, kind="EDL")
    for e in events:
        if e["reel"].upper() in ("BL", "BLK", "BLACK", "AX") and not e["clip"]:
            continue
        si, so = tc_to_frames(e["si"], fps), tc_to_frames(e["so"], fps)
        ri, ro = tc_to_frames(e["ri"], fps), tc_to_frames(e["ro"], fps)
        if so <= si or ro <= ri:
            continue
        name = e["clip"] or e["reel"]
        kind = "Picture" if e["track"].startswith("V") or e["track"].startswith("B") else "Sound"
        edit.anchors.append(Anchor(normalize(name), si / fps, so / fps, (ri - rec0) / fps, kind, e["track"],
                                   name))
        edit.duration = max(edit.duration, (ro - rec0) / fps)
    return edit


def load_edit(path, fps=25):
    p = Path(path)
    if p.suffix.lower() == ".edl":
        return load_edl(p, fps)
    if p.suffix.lower() == ".aaf":
        return load_aaf(p)
    raise RuntimeError("format non géré (AAF ou EDL)")


# ------------------------------------------------------------------ comparaison

@dataclass
class Segment:
    new_in: int        # images, relatives au début de la nouvelle séquence
    new_out: int
    offset: int        # nouveau − ancien (images)
    votes: int = 0

    @property
    def old_in(self):
        return self.new_in - self.offset

    @property
    def old_out(self):
        return self.new_out - self.offset


@dataclass
class Change:
    kind: str          # same | move | new | removed
    old_in: int = None  # images relatives à chaque séquence (None si absent)
    old_out: int = None
    new_in: int = None
    new_out: int = None
    offset: int = 0
    label: str = ""    # plans concernés

    @property
    def length(self):
        if self.new_in is not None:
            return self.new_out - self.new_in
        return self.old_out - self.old_in


@dataclass
class Comparison:
    old: Edit
    new: Edit
    fps: int
    segments: list
    changes: list
    matched: int       # repères retrouvés


def _intervals_minus(total, covered):
    """Parties de [0, total) non couvertes par les intervalles `covered`."""
    out, cur = [], 0
    for a, b in sorted(covered):
        if a > cur:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < total:
        out.append((cur, total))
    return out


def compare(old, new, fps=None):
    fps = fps or new.fps or 25
    by_key = defaultdict(list)
    for o in old.anchors:
        by_key[o.key].append(o)
    pieces, matched = [], 0
    for c in new.anchors:
        hit = False
        for o in by_key.get(c.key, ()):
            a, b = max(c.s0, o.s0), min(c.s1, o.s1)
            if b - a < MIN_MATCH_S:
                continue
            hit = True
            nr = c.rec + (a - c.s0)
            orr = o.rec + (a - o.s0)
            pieces.append((round(nr * fps), round((nr + b - a) * fps), round((nr - orr) * fps)))
        matched += hit

    # vote sur chaque intervalle élémentaire de la nouvelle version
    pts = sorted({p[0] for p in pieces} | {p[1] for p in pieces})
    starts = sorted(pieces)
    segs, active, k = [], [], 0
    for x0, x1 in zip(pts, pts[1:]):
        while k < len(starts) and starts[k][0] <= x0:
            active.append(starts[k])
            k += 1
        active = [p for p in active if p[1] > x0]
        votes = defaultdict(int)
        for a, b, off in active:
            if a <= x0 and b >= x1:
                votes[off] += 1
        if not votes:
            continue
        prev = segs[-1].offset if segs else None
        off = max(votes, key=lambda o: (votes[o], o == prev, -abs(o)))
        if segs and segs[-1].offset == off and segs[-1].new_out == x0:
            segs[-1].new_out = x1
            segs[-1].votes = max(segs[-1].votes, votes[off])
        else:
            segs.append(Segment(x0, x1, off, votes[off]))

    # bruit : morceaux très courts entre deux morceaux de même décalage → fusionnés
    cleaned = []
    for s in segs:
        if cleaned and s.new_out - s.new_in < MIN_SEGMENT_FRAMES and cleaned[-1].new_out == s.new_in:
            cleaned[-1].new_out = s.new_out
            continue
        if cleaned and cleaned[-1].offset == s.offset and cleaned[-1].new_out == s.new_in:
            cleaned[-1].new_out = s.new_out
            continue
        cleaned.append(s)
    segs = cleaned

    # liste des changements, dans l'ordre de la nouvelle version
    changes = []
    total_new = round(new.duration * fps)
    total_old = round(old.duration * fps)
    gaps = _intervals_minus(total_new, [(s.new_in, s.new_out) for s in segs])
    rows = [("seg", s.new_in, s) for s in segs] + [("gap", a, (a, b)) for a, b in gaps]
    for typ, _, obj in sorted(rows, key=lambda r: r[1]):
        if typ == "seg":
            changes.append(Change("same" if obj.offset == 0 else "move", obj.old_in, obj.old_out,
                                  obj.new_in, obj.new_out, obj.offset,
                                  _names(new, obj.new_in / fps, obj.new_out / fps)))
        elif obj[1] - obj[0] >= 1:
            changes.append(Change("new", None, None, obj[0], obj[1], 0,
                                  _names(new, obj[0] / fps, obj[1] / fps)))
    for a, b in _intervals_minus(total_old, [(s.old_in, s.old_out) for s in segs]):
        if b - a >= 1:
            changes.append(Change("removed", a, b, None, None, 0, _names(old, a / fps, b / fps)))
    return Comparison(old, new, fps, segs, changes, matched)


def _names(edit, t0, t1, limit=3):
    """Plans image présents sur [t0, t1) (pour situer un changement)."""
    seen = []
    for a in sorted(edit.anchors, key=lambda a: (a.kind != "Picture", a.rec)):
        if a.rec < t1 and a.rec + (a.s1 - a.s0) > t0:
            n = os.path.splitext(a.name)[0]
            if n not in seen:
                seen.append(n)
        if len(seen) >= limit:
            break
    return ", ".join(seen)


# ------------------------------------------------------------------ rapport

def fmt_tc(frames, fps):
    frames = max(0, int(round(frames)))
    f = frames % fps
    s = frames // fps
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{f:02d}"


def fmt_offset(frames, fps):
    sign = "+" if frames >= 0 else "−"
    s = abs(frames) / fps
    return f"{sign}{s:.2f} s".replace(".", ",")


def summary(cmp):
    c = cmp.changes
    return {
        "same": sum(1 for x in c if x.kind == "same"),
        "move": sum(1 for x in c if x.kind == "move"),
        "new": sum(1 for x in c if x.kind == "new"),
        "removed": sum(1 for x in c if x.kind == "removed"),
        "new_s": sum(x.length for x in c if x.kind == "new") / cmp.fps,
        "removed_s": sum(x.length for x in c if x.kind == "removed") / cmp.fps,
    }


def report_rows(cmp, old_base, new_base):
    """Lignes du rapport avec TC absolus : old_base / new_base = TC (images) des débuts de séquence
    dans la session (ancienne) et dans la nouvelle version."""
    fps = cmp.fps
    rows = []
    for n, ch in enumerate(cmp.changes, 1):
        rows.append({
            "n": n, "kind": ch.kind,
            "old_in": fmt_tc(old_base + ch.old_in, fps) if ch.old_in is not None else "",
            "old_out": fmt_tc(old_base + ch.old_out, fps) if ch.old_out is not None else "",
            "new_in": fmt_tc(new_base + ch.new_in, fps) if ch.new_in is not None else "",
            "new_out": fmt_tc(new_base + ch.new_out, fps) if ch.new_out is not None else "",
            "length": fmt_tc(ch.length, fps),
            "offset": fmt_offset(ch.offset, fps) if ch.kind == "move" else "",
            "label": ch.label,
        })
    return rows


def write_csv(cmp, path, old_base, new_base, labels):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["#", "changement", "ancien début", "ancienne fin", "nouveau début", "nouvelle fin",
                    "durée", "décalage", "plans"])
        for r in report_rows(cmp, old_base, new_base):
            w.writerow([r["n"], labels[r["kind"]], r["old_in"], r["old_out"], r["new_in"], r["new_out"],
                        r["length"], r["offset"], r["label"]])


def marker_plan(cmp, old_base, version, labels):
    """Marqueurs à poser dans la session (ancienne version) : [(TC, nom, commentaire)].
    Les passages nouveaux sont marqués là où ils s'insèrent dans l'ancienne version."""
    fps = cmp.fps
    out = []
    prev_old_end = 0
    for n, ch in enumerate(cmp.changes, 1):
        if ch.kind == "same":
            prev_old_end = ch.old_out
            continue
        if ch.kind == "move":
            at, txt = ch.old_in, f"{labels['move']} {fmt_offset(ch.offset, fps)}"
            prev_old_end = ch.old_out
        elif ch.kind == "new":
            at, txt = prev_old_end, f"{labels['new']} {ch.length / fps:.2f} s".replace(".", ",")
        else:
            at, txt = ch.old_in, f"{labels['removed']} {ch.length / fps:.2f} s".replace(".", ",")
        out.append((fmt_tc(old_base + at, fps), f"{version} #{n} {txt}", ch.label))
    return out


def create_markers(engine, markers):
    """Pose les marqueurs dans la session ouverte (numéros libres). Renvoie le nombre posé."""
    import ptsl.PTSL_pb2 as pt
    used = set()
    try:
        used = {m.number for m in engine.get_memory_locations()}
    except Exception:
        pass
    num, done = 1, 0
    for tc, name, comment in markers:
        while num in used:
            num += 1
        engine.create_memory_location(
            start_time=tc, memory_number=num, name=name[:60], end_time=tc,
            time_properties=pt.TP_Marker, reference=pt.MLR_Absolute,
            location=pt.MLC_MainRuler, comments=comment[:250])
        used.add(num)
        done += 1
    return done


if __name__ == "__main__":  # python3 conform.py ancien.aaf nouveau.aaf
    import sys
    a, b = load_edit(sys.argv[1]), load_edit(sys.argv[2])
    cmp = compare(a, b)
    lab = {"same": "inchangé", "move": "déplacé", "new": "nouveau", "removed": "supprimé"}
    print(f"{a.name} → {b.name} : {cmp.matched}/{len(b.anchors)} repères retrouvés")
    for r in report_rows(cmp, a.start_frames, b.start_frames):
        print(f"{r['n']:3d}  {lab[r['kind']]:9s} {r['old_in']:>11s} → {r['new_in']:>11s}  {r['length']}  "
              f"{r['offset']:>9s}  {r['label'][:60]}")


# ------------------------------------------------------------------ étape 2 : appliquer à la session

def plan_apply(cmp, old_base, session_start, session_end, fps=None):
    """Zones de travail (images absolues) : montage reconstruit après la fin de la session,
    passages supprimés rangés encore après. Renvoie un dict."""
    fps = fps or cmp.fps
    minute = 60 * fps
    old_len = round(cmp.old.duration * fps)
    new_len = round(cmp.new.duration * fps)
    removed = sum(c.length for c in cmp.changes if c.kind == "removed") + 2 * fps * len(cmp.changes)
    need = new_len + minute + removed + minute
    day = 24 * 3600 * fps
    staging = max(session_end, old_base + old_len) + minute
    staging += (-staging) % minute                       # à la minute ronde, plus lisible
    if staging + need > day - minute:
        # session déjà longue (souvent 24 h) : zone de travail à la fin de la journée de 24 h
        staging = day - minute - need
        staging -= staging % minute
    parking = staging + new_len + minute
    parking += (-parking) % minute
    return {"old_base": old_base, "old_len": old_len, "new_len": new_len, "staging": staging,
            "parking": parking, "end_needed": parking + removed + minute, "grow": new_len - old_len,
            "session_start": session_start, "zone_end": parking + removed}


def _pt_tc(value):
    """« 01:00:00:00.00 » (sous-images) → « 01:00:00:00 »."""
    return re.sub(r"\.\d+$", "", (value or "").strip())


def save_copy(engine, name):
    """Fichier > Enregistrer sous dans le dossier de la session. Pro Tools est exigeant sur le format
    du dossier (assertion « Sys_FileLocMacOS » vue en 2026.1) : on essaie plusieurs écritures.
    Renvoie le chemin de la copie ; lève une erreur si aucune n'est acceptée (session inchangée)."""
    import ptsl_import_sequentiel as core
    raw = engine.session_path()
    posix = Path(core.hfs_to_posix(raw))
    folder = posix.parent
    hfs = raw.rsplit(":", 1)[0] if ":" in raw and not raw.startswith("/") else None
    tries = [str(folder), str(folder) + "/"]
    if hfs:
        tries += [hfs, hfs + ":"]
    else:
        tries.append("Macintosh HD" + str(folder).replace("/", ":"))
    errors = []
    for loc in tries:
        try:
            engine.save_session_as(loc, name)
            return folder / (name + ".ptx")
        except Exception as e:
            errors.append(f"{loc} : {str(e).splitlines()[0][:80]}")
    raise RuntimeError("Enregistrer sous refusé par Pro Tools ; rien n'a été modifié. Fais Fichier > "
                       "Enregistrer sous à la main, puis relance avec « Travailler sur la session ouverte ». ("
                       + " | ".join(errors) + ")")


def apply_to_session(engine, cmp, old_base, labels, version, keep_removed=True, markers=True,
                     guide=None):
    """Adapte la session ouverte à la nouvelle version (à lancer sur une copie de la session).
    1. chaque morceau gardé est COPIÉ, sur toutes les pistes, à sa nouvelle place dans une zone
       libre après la fin de la session (l'original reste intact tant que tout n'est pas copié) ;
    2. les passages supprimés sont copiés plus loin (rangés, pas perdus) ;
    3. la zone d'origine est effacée, puis le montage reconstruit y est ramené (couper / coller) ;
    4. marqueurs sur les changements ; nouvelle version importée sur des pistes guides (option).
    guide : fonction (engine) -> None qui importe la nouvelle version (fournie par l'interface).
    Écrit son avancement avec print() (journal de l'interface). Renvoie le plan des zones."""
    import ptsl_import_sequentiel as core
    fps = cmp.fps
    tcf = core.TC(fps)
    tc = lambda fr: tcf.to_tc(fr)  # noqa: E731

    tracks = [t.name for t in engine.track_list()]
    if not tracks:
        raise RuntimeError("aucune piste dans la session")
    start = tcf.to_frames(_pt_tc(engine.session_start_time()))
    length = tcf.to_frames(_pt_tc(engine.session_length()))
    plan = plan_apply(cmp, old_base, start, start + length, fps)
    if plan["end_needed"] > start + length and plan["end_needed"] - start < 24 * 3600 * fps:
        try:
            engine.set_session_length(tc(plan["end_needed"] - start))
            print(f"  longueur de session portée à {tc(plan['end_needed'] - start)}")
        except Exception as e:
            print(f"! longueur de session non modifiée ({e})")

    def select(a, b):
        engine.select_tracks_by_name(tracks)
        engine.set_timeline_selection(in_time=tc(a), out_time=tc(b))

    keep = [c for c in cmp.changes if c.kind in ("same", "move")]
    gone = [c for c in cmp.changes if c.kind == "removed"]
    with core._EditContext(engine, automation=True):
        # 1. reconstruction, par copies (l'original n'est pas touché)
        print(f"  reconstruction de la nouvelle version à partir de {tc(plan['staging'])} "
              f"({len(keep)} morceau(x), {len(tracks)} pistes)")
        for n, c in enumerate(keep, 1):
            try:
                select(old_base + c.old_in, old_base + c.old_out)
                engine.copy()
                select(plan["staging"] + c.new_in, plan["staging"] + c.new_in)
                engine.paste()
            except Exception as e:
                raise RuntimeError(f"morceau {n} ({tc(old_base + c.old_in)}) non copié : {e}. "
                                   f"La session d'origine n'a pas été modifiée ; la zone à partir de "
                                   f"{tc(plan['staging'])} contient une reconstruction partielle.")
            print(f"  ⇢ {tc(old_base + c.old_in)} → {tc(old_base + c.new_in)}  ({fmt_offset(c.offset, fps)})")
        # 2. passages supprimés, rangés après le montage reconstruit
        parked = []
        if keep_removed:
            pos = plan["parking"]
            for c in gone:
                try:
                    select(old_base + c.old_in, old_base + c.old_out)
                    engine.copy()
                    select(pos, pos)
                    engine.paste()
                    parked.append((pos, c))
                    print(f"  ⇢ supprimé {tc(old_base + c.old_in)} rangé à {tc(pos)}")
                    pos += c.length + 2 * fps
                except Exception as e:
                    print(f"! passage supprimé {tc(old_base + c.old_in)} non rangé ({e})")
        # 3. on remplace l'ancienne version par la nouvelle
        try:
            select(old_base, old_base + plan["old_len"])
            engine.clear()
            select(plan["staging"], plan["staging"] + plan["new_len"])
            engine.cut()
            select(old_base, old_base)
            engine.paste()
        except Exception as e:
            raise RuntimeError(f"remplacement interrompu ({e}). Édition > Annuler dans Pro Tools, ou "
                               f"rouvre la session d'origine. Montage reconstruit : à partir de "
                               f"{tc(plan['staging'])}.")
        print(f"  ✓ nouvelle version en place à {tc(old_base)} ({tc(plan['new_len'])})")

    if markers:
        marks = []
        for n, c in enumerate(cmp.changes, 1):
            if c.kind == "move":
                marks.append((tc(old_base + c.new_in), f"{version} #{n} {labels['move']} {fmt_offset(c.offset, fps)}",
                              c.label))
            elif c.kind == "new":
                marks.append((tc(old_base + c.new_in), f"{version} #{n} {labels['new']} — à compléter", c.label))
        for pos, c in parked:
            marks.append((tc(pos), f"{version} {labels['removed']} (ex {tc(old_base + c.old_in)})", c.label))
        try:
            print(f"  {create_markers(engine, marks)} marqueur(s)")
        except Exception as e:
            print(f"! marqueurs non posés ({e})")
    if guide:
        try:
            guide(engine)
            print("  nouvelle version importée sur des pistes guides")
        except Exception as e:
            print(f"! pistes guides non importées ({e})")
    return plan
