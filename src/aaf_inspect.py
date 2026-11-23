#!/usr/bin/env python3
"""
Lecture du contenu d'un AAF (pyaaf2) pour l'aperçu de PAAF : pistes, clips, médias audio
et leurs métadonnées (fréquence, résolution, canaux, TC d'origine BWF, Scene/Take…).

Lecture seule ; l'OMF n'est pas lisible par pyaaf2.

    python3 aaf_inspect.py fichier.aaf     # résumé texte
"""

import struct
import sys
import threading
from fractions import Fraction
from pathlib import Path
from urllib.parse import unquote, urlparse

try:
    import aaf2
except Exception:  # bibliothèque absente : aperçu indisponible
    aaf2 = None

_cache = {}
_lock = threading.Lock()


def available():
    return aaf2 is not None


def _num(v):
    try:
        return Fraction(v.numerator, v.denominator)
    except AttributeError:
        try:
            return Fraction(v)
        except (TypeError, ValueError):
            return None


def _prop(obj, name, default=None):
    try:
        if name in obj:
            return obj[name].value
    except Exception:
        pass
    return default


def _length(comp):
    try:
        return int(comp.length or 0)
    except Exception:
        return int(_prop(comp, "Length", 0) or 0)


def _walk(seg, pos, out):
    """Aplatit un segment en [(position, SourceClip)] (séquences, effets, nested…)."""
    kind = type(seg).__name__
    if kind == "Sequence":
        p = pos
        for comp in seg.components:
            if type(comp).__name__ == "Transition":
                p -= _length(comp)
                continue
            _walk(comp, p, out)
            p += _length(comp)
    elif kind == "SourceClip":
        out.append((pos, seg))
    elif kind == "OperationGroup":
        segs = _prop(seg, "InputSegments", []) or []
        if segs:
            _walk(segs[0], pos, out)
    elif kind == "NestedScope":
        slots = _prop(seg, "Slots", []) or []
        if slots:
            _walk(slots[-1], pos, out)
    elif kind == "Selector":
        sel = _prop(seg, "Selected")
        if sel is not None:
            _walk(sel, pos, out)
    elif kind == "EssenceGroup":
        choices = _prop(seg, "Choices", []) or []
        if choices:
            _walk(choices[0], pos, out)


def _parse_wave_summary(data):
    """En-tête RIFF/WAVE -> (canaux, fréquence, bits)."""
    data = bytes(data or b"")
    i = data.find(b"fmt ")
    if i < 0 or len(data) < i + 24:
        return None
    ch, sr = struct.unpack_from("<HI", data, i + 10)
    bits = struct.unpack_from("<H", data, i + 22)[0]
    return ch, sr, bits


def _parse_aiff_summary(data):
    data = bytes(data or b"")
    i = data.find(b"COMM")
    if i < 0 or len(data) < i + 26:
        return None
    ch, _frames, bits = struct.unpack_from(">HIH", data, i + 8)
    exp, mant = struct.unpack_from(">HQ", data, i + 16)
    sr = int(mant * 2.0 ** ((exp & 0x7FFF) - 16383 - 63)) if mant else 0
    return ch, sr, bits


def _comments(mob):
    out = {}
    for key in ("UserComments", "MobAttributeList"):
        for tv in _prop(mob, key, []) or []:
            try:
                if tv.value not in (None, ""):
                    out[str(tv.name)] = str(tv.value)
            except Exception:
                pass
    return out


def _locator_path(url):
    if not url:
        return None
    if url.startswith("file:"):
        path = unquote(urlparse(url).path)
    else:
        path = url
    return path


def _resolve(clip, embedded_ids, aaf_dir):
    """Suit SourceClip -> MasterMob -> SourceMob fichier -> SourceMob bande/import."""
    info = {"name": None, "file": None, "sr": None, "bits": None, "channels": None,
            "duration_s": None, "embedded": False, "path": None, "exists": None,
            "origin_s": None, "comments": {}, "channel": clip.slot_id, "mob_id": None}
    mob, slot_id = clip.mob, clip.slot_id
    file_mob_seen = False
    for _ in range(10):
        if mob is None:
            break
        mtype = type(mob).__name__
        if mtype == "MasterMob":
            info["name"] = info["name"] or mob.name
            info["comments"].update(_comments(mob))
            info["mob_id"] = info["mob_id"] or str(mob.mob_id)
        elif mtype == "SourceMob":
            d = mob.descriptor if "EssenceDescription" in mob else None
            dname = type(d).__name__ if d is not None else ""
            info["comments"].update({k: v for k, v in _comments(mob).items() if k not in info["comments"]})
            for loc in (_prop(d, "Locator", []) or []) if d is not None else []:
                p = _locator_path(_prop(loc, "URLString"))
                if p and not info["path"]:
                    info["path"] = p
            if d is not None and not file_mob_seen and dname not in ("ImportDescriptor", "TapeDescriptor",
                                                                     "FilmDescriptor", "RecordingDescriptor"):
                file_mob_seen = True
                info["embedded"] = mob.mob_id in embedded_ids
                fmt = None
                if "Summary" in d:
                    fmt = (_parse_wave_summary(d["Summary"].value) if dname == "WAVEDescriptor"
                           else _parse_aiff_summary(d["Summary"].value))
                if fmt:
                    info["channels"], info["sr"], info["bits"] = fmt
                info["channels"] = _prop(d, "Channels", info["channels"])
                info["bits"] = _prop(d, "QuantizationBits", info["bits"])
                rate = _num(_prop(d, "AudioSamplingRate")) or _num(_prop(d, "SampleRate"))
                if rate:
                    info["sr"] = info["sr"] or int(rate)
                    length = _prop(d, "Length")
                    if length:
                        info["duration_s"] = float(int(length) / rate)
                info["file"] = mob.name or info["file"]
            elif dname in ("ImportDescriptor", "TapeDescriptor") and mob.name:
                info["file"] = info["file"] or mob.name
        try:
            slot = mob.slot_at(slot_id)
        except Exception:
            break
        seg = slot.segment
        if type(seg).__name__ == "Sequence":
            comps = [c for c in seg.components if type(c).__name__ == "SourceClip"]
            seg = comps[0] if comps else seg
        if type(seg).__name__ != "SourceClip":
            break
        if file_mob_seen and info["origin_s"] is None:
            rate = _num(slot.edit_rate)
            if rate:
                info["origin_s"] = float(seg.start / rate)
        mob, slot_id = seg.mob, seg.slot_id

    if info["path"]:
        p = Path(info["path"])
        cands = [p, aaf_dir / p.name] + [d / p.name for d in aaf_dir.iterdir() if d.is_dir()] \
            if aaf_dir.exists() else [p]
        for c in cands:
            try:
                if c.exists():
                    info["path"], info["exists"] = str(c), True
                    break
            except OSError:
                pass
        else:
            info["exists"] = False
    for prefix in ("Import",):
        if info["file"] and info["file"].startswith(prefix) and "." in info["file"]:
            info["file"] = info["file"][len(prefix):]
    return info


def inspect(path):
    """Renvoie un dict décrivant l'AAF (mis en cache par chemin + date de modification)."""
    path = Path(path)
    if aaf2 is None:
        raise RuntimeError("pyaaf2 non installé")
    if path.suffix.lower() != ".aaf":
        raise RuntimeError("aperçu disponible pour les AAF uniquement (pas l'OMF)")
    st = path.stat()
    key = (str(path), st.st_mtime, st.st_size)
    with _lock:
        if key in _cache:
            return _cache[key]

    import logging
    logging.getLogger().setLevel(logging.ERROR)  # pyaaf2 signale des écarts FAT bénins
    with aaf2.open(str(path), "r") as f:
        tops = list(f.content.toplevel()) or list(f.content.compositionmobs())
        if not tops:
            raise RuntimeError("aucune composition dans l'AAF")
        comp = tops[0]
        embedded = {e.mob_id for e in f.content.essencedata}
        result = {"name": comp.name or path.stem, "edit_rate": None, "start_frames": 0,
                  "fps": None, "length": 0, "duration_s": None, "tracks": [],
                  "sources": [], "pairs": [], "embedded": bool(embedded)}
        sources = {}
        for slot in comp.slots:
            seg = slot.segment
            kind = slot.media_kind
            rate = _num(slot.edit_rate)
            if type(seg).__name__ == "Timecode":
                result["start_frames"] = int(seg.start or 0)
                result["fps"] = int(seg.fps or 0) or None
                continue
            if kind not in ("Sound", "Picture"):
                continue
            result["edit_rate"] = result["edit_rate"] or rate
            clips = []
            _walk(seg, 0, clips)
            track = {"name": slot.name or f"{kind} {slot.slot_id}", "kind": kind, "clips": [],
                     "length": _length(seg), "rate": rate}
            if rate and result["edit_rate"] and rate == result["edit_rate"]:
                result["length"] = max(result["length"], track["length"])
            for pos, sc in clips:
                src = _resolve(sc, embedded, path.parent) if kind == "Sound" else {
                    "name": sc.mob.name if sc.mob is not None else None, "mob_id": None, "channel": sc.slot_id}
                skey = (src.get("file") or src.get("name"), src.get("channel"), src.get("origin_s"))
                if kind == "Sound":
                    if skey not in sources:
                        sources[skey] = len(result["sources"])
                        src["uses"] = 0
                        result["sources"].append(src)
                    result["sources"][sources[skey]]["uses"] += 1
                track["clips"].append({"pos": pos, "length": _length(sc),
                                       "name": src.get("name") or "(sans nom)",
                                       "source": sources.get(skey),
                                       "mob": src.get("mob_id"), "channel": src.get("channel")})
            result["tracks"].append(track)
        if result["edit_rate"]:
            result["duration_s"] = float(result["length"] / result["edit_rate"])
        result["pairs"] = _stereo_pairs(result["tracks"])

    with _lock:
        _cache[key] = result
    return result


def _stereo_pairs(tracks):
    """Pistes son voisines dont les clips viennent des mêmes masters, aux mêmes positions,
    sur des canaux différents : paire stéréo probable."""
    pairs = []
    snd = [(i, t) for i, t in enumerate(tracks) if t["kind"] == "Sound"]
    used = set()
    for (i, a), (j, b) in zip(snd, snd[1:]):
        if i in used or not a["clips"] or len(a["clips"]) != len(b["clips"]):
            continue
        same = sum(1 for x, y in zip(a["clips"], b["clips"])
                   if x["mob"] and x["mob"] == y["mob"] and x["pos"] == y["pos"]
                   and x["channel"] != y["channel"])
        if same >= 0.8 * len(a["clips"]):
            pairs.append((i, j))
            used.update((i, j))
    return pairs


def frames_to_tc(frames, fps):
    fps = max(1, int(round(fps)))
    frames = int(frames)
    f = frames % fps
    s = frames // fps
    return f"{s // 3600:02d}:{(s // 60) % 60:02d}:{s % 60:02d}:{f:02d}"


def summary_lines(info, fps=25):
    lines = [f"Composition : {info['name']}"]
    if info["duration_s"] is not None:
        lines.append(f"Durée : {frames_to_tc(round(info['duration_s'] * fps), fps)}")
    if info["fps"]:
        lines.append(f"TC de départ : {frames_to_tc(info['start_frames'], info['fps'])} ({info['fps']} i/s)")
    snd = [t for t in info["tracks"] if t["kind"] == "Sound"]
    lines.append(f"Pistes : {len(snd)} audio, {len(info['tracks']) - len(snd)} image")
    for i, j in info["pairs"]:
        lines.append(f"Paire stéréo probable : {info['tracks'][i]['name']} + {info['tracks'][j]['name']}")
    return lines


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        data = inspect(arg)
        print("\n".join(summary_lines(data)))
        for t in data["tracks"]:
            print(f"  [{t['kind']}] {t['name']} : {len(t['clips'])} clip(s)")
        for s in data["sources"]:
            print(f"  • {s.get('name')}  {s.get('sr')} Hz  {s.get('bits')} bits  {s.get('channels')} can.  "
                  f"{'embarqué' if s.get('embedded') else (s.get('path') or '')}  {s.get('comments')}")
