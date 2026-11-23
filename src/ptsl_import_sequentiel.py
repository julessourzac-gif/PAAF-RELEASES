#!/usr/bin/env python3
"""
PAAF — import séquentiel d'AAF / OMF et de vidéos dans Pro Tools via le PTSL
(Pro Tools Scripting Library, API officielle Avid), sans GUI scripting.

Prérequis
  - Pro Tools 2023.x ou plus récent, session ouverte (PTSL écoute sur localhost:31416)
  - Python 3.9+  et  py-ptsl :   pip3 install py-ptsl   (pyaaf2 en option : aperçu des AAF)
  - Recommandé : ffmpeg / ffprobe (audio des vidéos, durées, détection stéréo)

Exemples
  # Tout un dossier (AAF + vidéos qu'il contient, tri naturel ; AAF et vidéo de même nom
  # calés au même TC)
  python3 ptsl_import_sequentiel.py --mode fixe "/Volumes/MEDIA/THE BRIDGE S04/EP01"

  # Sélecteur de fichiers macOS, un point d'entrée toutes les 10 min à partir de 01:00:00:00
  python3 ptsl_import_sequentiel.py --mode fixe --start 01:00:00:00 --step 00:10:00:00

  # Bout à bout avec 10 s de blanc entre deux médias
  python3 ptsl_import_sequentiel.py --mode bout --gap 00:00:10:00 ~/Montage/*.aaf ~/Montage/*.mov

  # TC saisi pour chaque fichier
  python3 ptsl_import_sequentiel.py --mode interactif

  # Plan imposé par un CSV  (chemin;TC  ou  chemin,TC — une ligne par fichier, dans l'ordre)
  python3 ptsl_import_sequentiel.py --csv plan.csv

  # Simulation sans Pro Tools (affiche le plan)
  python3 ptsl_import_sequentiel.py --dry-run --fps 25 --mode fixe fichiers...
"""

import argparse
import itertools
import math
import os
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    import ptsl.PTSL_pb2 as pt
    from ptsl import open_engine, CommandError
    from ptsl import ops
    from ptsl.ops.operation import Operation
except ImportError:
    sys.exit("py-ptsl n'est pas installé :  pip3 install py-ptsl")

APP_NAME = "PAAF"
COMPANY = "Bernik"
AAF_EXT = {".aaf", ".omf", ".omfi"}
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mxf", ".avi"}


def connect():
    return open_engine(company_name=COMPANY, application_name=APP_NAME)


# py-ptsl n'a pas (encore) de méthode pour ces commandes : on déclare les opérations.
# La classe Operation déduit la commande et le corps de requête de son nom.
class CId_ImportVideo(Operation):
    pass


class CId_ImportAudioToClipList(Operation):
    pass


class CId_SpotClipsByID(Operation):
    pass


# ------------------------------------------------------------------ timecode

def _rate(name, base, real, drop=False):
    value = getattr(pt, name, None)
    return (value, (base, real, drop)) if value is not None else None


RATES = dict(r for r in [
    _rate("STCR_Fps23976", 24, 24000 / 1001),
    _rate("STCR_Fps24", 24, 24),
    _rate("STCR_Fps25", 25, 25),
    _rate("STCR_Fps2997", 30, 30000 / 1001),
    _rate("STCR_Fps2997Drop", 30, 30000 / 1001, True),
    _rate("STCR_Fps30", 30, 30),
    _rate("STCR_Fps30Drop", 30, 30, True),
    _rate("STCR_Fps47952", 48, 48000 / 1001),
    _rate("STCR_Fps48", 48, 48),
    _rate("STCR_Fps50", 50, 50),
    _rate("STCR_Fps5994", 60, 60000 / 1001),
    _rate("STCR_Fps5994Drop", 60, 60000 / 1001, True),
    _rate("STCR_Fps60", 60, 60),
    _rate("STCR_Fps60Drop", 60, 60, True),
] if r)


class TC:
    """Timecode non drop-frame sur une base entière (24, 25, 30…)."""

    def __init__(self, base: int):
        self.base = base

    def to_frames(self, tc: str) -> int:
        parts = re.split(r"[:;.,]", tc.strip())
        if len(parts) != 4 or not all(p.isdigit() for p in parts):
            raise ValueError(f"Timecode invalide : {tc!r} (attendu HH:MM:SS:FF)")
        h, m, s, f = map(int, parts)
        if m > 59 or s > 59 or f >= self.base:
            raise ValueError(f"Timecode invalide à {self.base} i/s : {tc!r}")
        return ((h * 3600 + m * 60 + s) * self.base) + f

    def to_tc(self, frames: int) -> str:
        frames = max(0, int(frames))
        f = frames % self.base
        s_total = frames // self.base
        return f"{s_total // 3600:02d}:{(s_total // 60) % 60:02d}:{s_total % 60:02d}:{f:02d}"


# ------------------------------------------------------------------ fichiers

def natural_key(path: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


def pair_key(path: Path) -> str:
    """Clé de rapprochement AAF ↔ vidéo : nom sans extension, casse et séparateurs ignorés."""
    return re.sub(r"[\s_\-.]+", "_", path.stem.strip().lower())


def choose_files_macos():
    lines = [
        'set fs to choose file with prompt "Sélectionne les AAF / OMF / vidéos à importer" with multiple selections allowed',
        'set out to ""',
        'repeat with f in fs',
        'set out to out & POSIX path of f & linefeed',
        'end repeat',
        'return out',
    ]
    cmd = ["osascript"]
    for line in lines:
        cmd += ["-e", line]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("Sélection annulée.")
    return [Path(p) for p in r.stdout.splitlines() if p.strip()]


def media_duration_seconds(path: Path):
    """Durée d'une vidéo (ffprobe / Spotlight) ou d'un AAF (pyaaf2)."""
    if kind_of(path) == "aaf":
        try:
            import aaf_inspect
            return aaf_inspect.inspect(path)["duration_s"]
        except Exception:
            return None
    if shutil.which("ffprobe"):
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True)
        try:
            return float(r.stdout.strip())
        except ValueError:
            pass
    if shutil.which("mdls"):
        r = subprocess.run(["mdls", "-raw", "-name", "kMDItemDurationSeconds", str(path)],
                           capture_output=True, text=True)
        try:
            return float(r.stdout.strip())
        except ValueError:
            pass
    return None


def expand_paths(paths, recursive=False):
    """Remplace chaque dossier par les AAF/OMF/vidéos qu'il contient
    (fichiers cachés et ._AppleDouble ignorés)."""
    out = []
    for p in paths:
        if p.is_dir():
            pattern = "**/*" if recursive else "*"
            found = [f for f in p.glob(pattern)
                     if f.is_file() and not f.name.startswith(".") and kind_of(f)]
            if not found:
                print(f"  ! Aucun AAF/OMF/vidéo dans {p}")
            out += sorted(found, key=natural_key)
        else:
            out.append(p)
    return out


def kind_of(path: Path):
    ext = path.suffix.lower()
    if ext in AAF_EXT:
        return "aaf"
    if ext in VIDEO_EXT:
        return "video"
    return None


# ------------------------------------------------------------------ plan

def read_csv_plan(csv_path: Path, tc: TC):
    """Une ligne par fichier : chemin;TC  (ou , ou tabulation). Le séparateur
    pris en compte est le dernier de la ligne, les chemins peuvent donc contenir des virgules."""
    plan = []
    for n, line in enumerate(csv_path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(.*)[;,\t]\s*(\d{1,2}[:;.]\d{2}[:;.]\d{2}[:;.]\d{2})\s*$", line)
        if not m:
            raise ValueError(f"Ligne {n} du CSV illisible : {line!r}")
        p = Path(m.group(1).strip().strip('"')).expanduser()
        plan.append((p, tc.to_tc(tc.to_frames(m.group(2)))))
    return plan


def link_groups(paths, enabled=True):
    """Regroupe les fichiers qui partagent un point d'entrée : un AAF et une vidéo de même
    nom. Renvoie une liste de groupes (listes d'indices), dans l'ordre d'apparition."""
    groups, by_key = [], {}
    for i, p in enumerate(paths):
        key = pair_key(p) if enabled else None
        g = by_key.get(key) if key else None
        # on ne lie qu'un fichier de chaque type par groupe (AAF + vidéo)
        if g is not None and kind_of(p) not in {kind_of(paths[j]) for j in g}:
            g.append(i)
        else:
            g = [i]
            groups.append(g)
            if key:
                by_key[key] = g
    return groups


def schedule(paths, mode, start, step, gap, real_fps, duration_of,
             manual=None, link_same_name=True):
    """Calcule les points d'entrée (en images).
    mode : fixe | bout | manuel. manual : {indice: images} pour le mode manuel.
    Renvoie (liste d'images par fichier, nb de durées inconnues, groupes)."""
    groups = link_groups(paths, link_same_name)
    frames = [0] * len(paths)
    current = start
    unknown = 0
    manual = manual or {}
    for g in groups:
        if mode == "manuel":
            fixed = [manual[i] for i in g if manual.get(i) is not None]
            if fixed:
                current = fixed[0]
        for i in g:
            frames[i] = current
        if mode == "bout":
            durs = [d for d in (duration_of(paths[i]) for i in g) if d is not None]
            if durs:
                current += math.ceil(max(durs) * real_fps) + gap
            else:
                unknown += 1
                current += step
        else:
            current += step
    return frames, unknown, groups


def build_plan(files, args, tc: TC, real_fps: float):
    if args.csv:
        return read_csv_plan(Path(args.csv).expanduser(), tc)

    files = sorted(files, key=natural_key) if not args.keep_order else list(files)
    link = not getattr(args, "no_link_names", False)
    if args.mode == "interactif":
        groups = link_groups(files, link)
        frames = [0] * len(files)
        current = tc.to_frames(args.start)
        for g in groups:
            default = tc.to_tc(current)
            names = " + ".join(files[i].name for i in g)
            while True:
                answer = input(f"Point d'entrée pour {names} [{default}] : ").strip() or default
                try:
                    current = tc.to_frames(answer)
                    break
                except ValueError as e:
                    print(f"  {e}")
            for i in g:
                frames[i] = current
            current += tc.to_frames(args.step)
    else:
        frames, unknown, _ = schedule(
            files, args.mode, tc.to_frames(args.start), tc.to_frames(args.step),
            tc.to_frames(args.gap), real_fps, media_duration_seconds, link_same_name=link)
        if unknown:
            print(f"  ! {unknown} durée(s) inconnue(s) : réservation de {args.step}")
    return [(p, tc.to_tc(f)) for p, f in zip(files, frames)]


# ------------------------------------------------------------------ PTSL : positions

def spot_location(tc: str, legacy=False):
    """Position de placement au TC. Pro Tools 2025+ attend le champ `location`
    (TimelineLocation) ; les versions antérieures, location_value / location_options."""
    if legacy or not hasattr(pt, "TimelineLocation"):
        return pt.SpotLocationData(location_type=pt.Start, location_options=pt.TimeCode,
                                   location_value=tc)
    return pt.SpotLocationData(location_type=pt.Start,
                               location=pt.TimelineLocation(location=tc, time_type=pt.TLType_TimeCode))


def _is_location_error(e) -> bool:
    return "location" in str(e).lower() and "incompatib" not in str(e).lower()


# ------------------------------------------------------------------ PTSL : AAF

def import_aaf(engine, path: Path, tc: str, args):
    """Import Session Data d'un AAF/OMF : toutes les pistes (nouvelles pistes, ou fusion
    avec les pistes de même nom de la session), avec clips et médias (sans clips_and_media,
    Pro Tools crée des pistes vides), gain de clip et automation de volume, calé sur le TC."""
    if args.copy_audio:
        audio_opt = pt.CopyFromSourceAudio
    elif args.convert_audio:
        audio_opt = pt.ForceToTargetSessionFormat
    elif args.consolidate:
        audio_opt = pt.ConsolidateFromSourceAudio
    else:
        audio_opt = pt.LinkToSourceAudio

    session_data = pt.SessionData(
        audio_options=audio_opt,
        audio_handle_size=args.handles,
        video_options=pt.CopyFromSourceVideo if args.copy_video else pt.LinkToSourceVideo,
        match_options=pt.MT_MatchTracks if getattr(args, "match_tracks", False) else pt.MT_ImportAsNewTrack,
        playlist_options=pt.ImportOverlayNewOnExistingPlaylists,
        track_data_to_import=pt.TrackDataToImport(
            clips_and_media=True,
            clip_gain=not args.no_clip_gain,
            volume_automation=not args.no_volume_automation,
        ),
        timecode_mapping_units=pt.MapStartTimeCodeTo,
        timecode_mapping_start_time=tc,
        adjust_session_start_time_to_match_source=False,
    )
    op = ops.CId_Import(
        session_path=str(path),
        import_type=pt.Session,
        session_data=session_data,
    )
    engine.client.run(op)


# ------------------------------------------------------------------ session

def hfs_to_posix(hfs: str) -> str:
    """'Macintosh HD:Users:x:Sess:Sess.ptx' -> '/Users/x/Sess/Sess.ptx' (ou /Volumes/...)."""
    if hfs.startswith("/"):
        return hfs
    volume, _, rest = hfs.partition(":")
    rest = rest.replace(":", "/")
    boot = Path("/")
    for v in Path("/Volumes").iterdir() if Path("/Volumes").exists() else []:
        if v.name == volume and v.is_symlink() and v.resolve() == boot:
            return "/" + rest
    return f"/Volumes/{volume}/{rest}"


def audio_dest_candidates(engine):
    """Dossier Audio Files de la session, en POSIX puis en HFS (selon ce qu'accepte Pro Tools)."""
    try:
        hfs = engine.session_path()
    except Exception:
        return [""]
    posix_dir = str(Path(hfs_to_posix(hfs)).parent / "Audio Files")
    hfs_dir = hfs.rsplit(":", 1)[0] + ":Audio Files" if (":" in hfs and not hfs.startswith("/")) else None
    return [d for d in (posix_dir, hfs_dir) if d]


PCM_CODECS = {
    getattr(pt, "Bit16", None): ("pcm_s16le", "16 bits"),
    getattr(pt, "Bit24", None): ("pcm_s24le", "24 bits"),
    getattr(pt, "Bit32Float", None): ("pcm_f32le", "32 bits flottant"),
}


def session_audio_format(engine):
    """Fréquence, résolution, entrelacement et dossier Audio Files de la session."""
    try:
        sr = engine.session_sample_rate() or 48000
    except Exception:
        sr = 48000
    try:
        codec, label = PCM_CODECS.get(engine.session_bit_depth(), ("pcm_s24le", "24 bits"))
    except Exception:
        codec, label = "pcm_s24le", "24 bits"
    try:
        interleaved = bool(engine.session_interleaved_state())
    except Exception:
        interleaved = True
    audio_dir = None
    try:
        audio_dir = Path(hfs_to_posix(engine.session_path())).parent / "Audio Files"
    except Exception:
        pass
    return {"sr": int(sr), "codec": codec, "label": label,
            "interleaved": interleaved, "audio_dir": audio_dir}


TRACK_FORMAT_LABEL = {getattr(pt, "TF_Mono", -1): "mono", getattr(pt, "TF_Stereo", -2): "stéréo"}


def session_tracks(engine):
    """Pistes de la session : [{name, type, format, has_clips, folder}]."""
    out = []
    for t in engine.track_list():
        try:
            tname = pt.TrackType.Name(t.type).replace("TT_", "")
        except ValueError:
            tname = str(t.type)
        try:
            fname = pt.TrackFormat.Name(t.format).replace("TF_", "")
        except ValueError:
            fname = ""
        out.append({"name": t.name, "type": tname, "format": fname, "format_id": t.format,
                    "type_id": t.type, "has_clips": bool(t.track_attributes.contains_clips),
                    "folder": t.parent_folder_name or ""})
    return out


# ------------------------------------------------------------------ ffmpeg : analyse

def ffmpeg_path():
    return shutil.which("ffmpeg")


def has_ffmpeg():
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def audio_streams(path: Path):
    """[{index (parmi les flux audio), channels, layout, sr, codec}] via ffprobe."""
    if not shutil.which("ffprobe"):
        return None
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=channels,channel_layout,sample_rate,codec_name",
                        "-of", "compact=p=0:nk=0", str(path)], capture_output=True, text=True)
    streams = []
    for n, line in enumerate(l for l in r.stdout.splitlines() if l.strip()):
        d = dict(kv.split("=", 1) for kv in line.split("|") if "=" in kv)
        try:
            ch = int(d.get("channels") or 0)
        except ValueError:
            ch = 0
        streams.append({"index": n, "channels": ch, "layout": d.get("channel_layout", ""),
                        "sr": d.get("sample_rate", ""), "codec": d.get("codec_name", "")})
    return streams


def audio_channels(path: Path):
    streams = audio_streams(path)
    if streams:
        return streams[0]["channels"] or None
    return None


SILENT_DB = -70.0
DUAL_MONO_MARGIN_DB = 45.0
_analysis_cache = {}
_analysis_lock = threading.Lock()


def _two_channel_input(streams):
    """Graphe ffmpeg qui produit les 2 canaux d'une vidéo stéréo (1 flux stéréo ou 2 flux mono)."""
    if len(streams) == 1:
        return f"[0:a:{streams[0]['index']}]"
    return f"[0:a:{streams[0]['index']}][0:a:{streams[1]['index']}]amerge=inputs=2,"


def analyze_audio(path: Path, seconds=600):
    """Décrit l'audio d'une vidéo. kind : none | mono | stereo | dual_mono | left | right |
    silent | multi. Pour 2 canaux, compare G, D et G−D (crêtes) sur les `seconds` premières
    secondes : canaux identiques -> dual_mono, un seul canal utile -> left / right."""
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return {"kind": "none", "streams": [], "channels": 0, "label": "introuvable"}
    key = (str(path), st.st_mtime, st.st_size)
    with _analysis_lock:
        if key in _analysis_cache:
            return _analysis_cache[key]

    if not shutil.which("ffprobe"):
        return {"kind": "none", "streams": [], "channels": 0, "peaks": None, "label": "ffprobe absent"}
    streams = [s for s in (audio_streams(path) or []) if s["channels"] > 0]
    total = sum(s["channels"] for s in streams)
    res = {"kind": "none", "streams": streams, "channels": total, "peaks": None}
    if total == 0:
        res["label"] = "pas d'audio"
    elif total == 1:
        res["kind"], res["label"] = "mono", "mono"
    elif total == 2 and (len(streams) == 1 or all(s["channels"] == 1 for s in streams)) and ffmpeg_path():
        graph = (_two_channel_input(streams) +
                 "pan=3c|c0=c0|c1=c1|c2=c0-c1,astats=measure_perchannel=Peak_level:measure_overall=none")
        cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-vn", "-sn", "-dn",
               "-t", str(seconds), "-i", str(path), "-filter_complex", graph, "-f", "null", "-"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        peaks = []
        for m in re.finditer(r"Peak level dB:\s*(-?inf|-?[\d.]+)", r.stderr):
            v = m.group(1)
            peaks.append(-200.0 if "inf" in v else float(v))
        if len(peaks) >= 3:
            left, right, diff = peaks[:3]
            res["peaks"] = (left, right, diff)
            loud = max(left, right)
            if loud < SILENT_DB:
                res["kind"], res["label"] = "silent", "2 can. silencieux"
            elif right < SILENT_DB:
                res["kind"], res["label"] = "left", "stéréo, gauche seule"
            elif left < SILENT_DB:
                res["kind"], res["label"] = "right", "stéréo, droite seule"
            elif diff <= loud - DUAL_MONO_MARGIN_DB:
                res["kind"], res["label"] = "dual_mono", "double mono (G = D)"
            else:
                res["kind"], res["label"] = "stereo", "stéréo"
        else:
            res["kind"], res["label"] = "stereo", "stéréo (non analysée)"
    elif total == 2:
        res["kind"], res["label"] = "stereo", "stéréo"
    else:
        res["kind"] = "multi"
        layout = streams[0]["layout"] if len(streams) == 1 else f"{len(streams)} flux"
        res["label"] = f"{total} canaux ({layout})" if layout else f"{total} canaux"
    with _analysis_lock:
        _analysis_cache[key] = res
    return res


def audio_layout(analysis, fmt, stereo_detect=True):
    """Choisit ce qu'on extrait : (layout, sources) avec layout mono | stereo | split et
    sources = [(indice du flux audio, canal)]."""
    streams = analysis["streams"]
    chans = [(s["index"], c) for s in streams for c in range(s["channels"])]
    kind = analysis["kind"]
    if not chans:
        raise RuntimeError("aucune piste audio dans la vidéo")
    if len(chans) == 1:
        return "mono", chans
    if len(chans) == 2:
        if stereo_detect and kind in ("dual_mono", "left", "silent"):
            return "mono", [chans[0]]
        if stereo_detect and kind == "right":
            return "mono", [chans[1]]
        return ("stereo", chans) if fmt["interleaved"] else ("split", chans)
    return "split", chans


# ------------------------------------------------------------------ ffmpeg : extraction

def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    i = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def _reusable(wav: Path, video: Path, fmt, channels: int) -> bool:
    """Un WAV déjà extrait (lancement précédent) est réutilisé s'il est plus récent que la
    vidéo et au bon format."""
    try:
        if not wav.exists() or wav.stat().st_size < 1024 or wav.stat().st_mtime < video.stat().st_mtime:
            return False
    except OSError:
        return False
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                        "stream=codec_name,sample_rate,channels", "-of", "compact=p=0:nk=0", str(wav)],
                       capture_output=True, text=True)
    d = dict(kv.split("=", 1) for kv in r.stdout.strip().split("|") if "=" in kv)
    return (d.get("codec_name") == fmt["codec"] and d.get("sample_rate") == str(fmt["sr"])
            and d.get("channels") == str(channels))


def extract_audio_ffmpeg(path: Path, fmt: dict, stereo_detect=True):
    """Extrait l'audio de la vidéo en WAV BWF au format de la session, dans Audio Files.
    Renvoie (fichiers, layout, libellé). layout : mono | stereo | split (un mono par canal)."""
    analysis = analyze_audio(path)
    layout, sources = audio_layout(analysis, fmt, stereo_detect)
    out_dir = fmt["audio_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w\-. ]", "_", path.stem)
    common = ["-c:a", fmt["codec"], "-ar", str(fmt["sr"]), "-rf64", "auto", "-write_bext", "1", "-f", "wav"]

    if layout in ("mono", "stereo"):
        names = [f"{stem}.wav"]
        n_ch = [1 if layout == "mono" else 2]
    else:
        names = [f"{stem}.A{i + 1}.wav" for i in range(len(sources))]
        n_ch = [1] * len(sources)

    finals = [out_dir / n for n in names]
    if all(_reusable(f, path, fmt, c) for f, c in zip(finals, n_ch)):
        return finals, layout, analysis.get("label", "") + " — déjà converti"
    finals = [_unique(f) for f in finals]
    if len(set(finals)) != len(finals):
        raise RuntimeError("noms de fichiers en conflit")
    parts = [f.with_name("." + f.name + ".part") for f in finals]

    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y", "-vn", "-sn", "-dn", "-i", str(path)]
    stream_ch = {s["index"]: s["channels"] for s in analysis["streams"]}
    if layout == "stereo":
        if len({s for s, _ in sources}) == 1:
            cmd += ["-map", f"0:a:{sources[0][0]}"]
        else:
            cmd += ["-filter_complex", f"[0:a:{sources[0][0]}][0:a:{sources[1][0]}]amerge=inputs=2[st]",
                    "-map", "[st]"]
        cmd += common + [str(parts[0])]
    else:
        for (s, c), part in zip(sources, parts):
            cmd += ["-map", f"0:a:{s}"]
            if stream_ch.get(s, 1) > 1:
                cmd += ["-af", f"pan=mono|c0=c{c}"]
            cmd += common + [str(part)]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        for o in parts:
            o.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg : " + (r.stderr.strip().splitlines() or ["erreur"])[-1])
    for part, final in zip(parts, finals):
        os.replace(part, final)
    return finals, layout, analysis.get("label", "")


class AudioPrep:
    """Extractions ffmpeg en tâche de fond, au format d'une session. Peut être lancé dès
    l'ajout des vidéos (préconversion) et réutilisé au moment de l'import."""

    def __init__(self, fmt, stereo_detect=True, workers=None):
        self.fmt = fmt
        self.stereo_detect = stereo_detect
        workers = workers or max(1, min(4, (os.cpu_count() or 2) // 2))
        self.workers = workers
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="paaf-ffmpeg")
        self.futures = {}
        self.lock = threading.Lock()

    @staticmethod
    def make_key(fmt, stereo_detect):
        return (fmt["sr"], fmt["codec"], fmt["interleaved"], str(fmt["audio_dir"]), bool(stereo_detect))

    def key(self):
        return self.make_key(self.fmt, self.stereo_detect)

    def submit(self, path: Path):
        with self.lock:
            fut = self.futures.get(path)
            if fut is None or (fut.done() and fut.exception() is not None):
                fut = self.executor.submit(extract_audio_ffmpeg, path, self.fmt, self.stereo_detect)
                self.futures[path] = fut
            return fut

    def status(self, path: Path):
        fut = self.futures.get(path)
        if fut is None:
            return None
        if not fut.done():
            return "…"
        return "erreur" if fut.exception() else "prêt"

    def shutdown(self):
        self.executor.shutdown(wait=False, cancel_futures=True)


class VideoAudioState:
    """Pistes audio communes entre deux vidéos et extractions ffmpeg en cours."""

    def __init__(self, base_name: str, prep: AudioPrep = None):
        self.base_name = base_name
        self.dest_candidates = None
        self.tracks = {}      # (nb_pistes, format) -> [noms]
        self.prep = prep
        self.own_prep = False

    def shutdown(self):
        if self.prep and self.own_prep:
            self.prep.shutdown()


def start_audio_extraction(engine, plan, args, state):
    """Lance en tâche de fond l'extraction ffmpeg de toutes les vidéos du plan,
    pendant que Pro Tools importe les fichiers précédents."""
    if getattr(args, "no_video_audio", False) or getattr(args, "no_ffmpeg", False):
        return
    videos = [p for p, _ in plan if kind_of(p) == "video" and p.exists()]
    if not videos:
        return
    if not has_ffmpeg():
        print("  ffmpeg/ffprobe introuvables : extraction de l'audio par Pro Tools (plus lent)")
        return
    fmt = session_audio_format(engine)
    if fmt["audio_dir"] is None:
        print("  Dossier Audio Files introuvable : extraction de l'audio par Pro Tools")
        return
    detect = not getattr(args, "no_stereo_detect", False)
    if state.prep is None or state.prep.key() != AudioPrep.make_key(fmt, detect):
        state.prep = AudioPrep(fmt, detect)
        state.own_prep = True
    for p in videos:
        state.prep.submit(p)
    print(f"  ffmpeg : audio de {len(videos)} vidéo(s) au format de la session "
          f"({fmt['sr']} Hz / {fmt['label']}, {state.prep.workers} à la fois) vers {fmt['audio_dir']}")


# ------------------------------------------------------------------ PTSL : pistes et clips

def ensure_audio_tracks(engine, state, n_tracks: int, fmt):
    """Pistes audio communes au format demandé (mono / stéréo). Une piste existante de même
    nom mais d'un autre format n'est jamais utilisée (Pro Tools refuserait les clips)."""
    key = (n_tracks, fmt)
    if key in state.tracks:
        return state.tracks[key]
    base = state.base_name
    if n_tracks == 1:
        names = [base] if fmt == pt.TF_Stereo else [f"{base} MONO"]
    else:
        names = [f"{base} {i}" for i in range(1, n_tracks + 1)]
    tracks = {t.name: t for t in engine.track_list()}

    def usable(name):
        t = tracks.get(name)
        return t is not None and t.type == pt.TT_Audio and t.format == fmt

    result = []
    for name in names:
        candidate, n = name, 2
        while candidate in tracks and not usable(candidate):
            candidate = f"{name} ({n})"
            n += 1
        if usable(candidate):
            result.append(candidate)
            continue
        op = ops.CId_CreateNewTracks(
            number_of_tracks=1, track_name=candidate, track_format=fmt,
            track_type=pt.TT_Audio, track_timebase=pt.TTB_Samples,
            insertion_point_position=pt.TIPoint_Last,
        )
        engine.client.run(op)
        created = list(getattr(op.response, "created_track_names", []) or [])
        result.append(created[0] if created else candidate)
        print(f"    + piste créée : {result[-1]} ({TRACK_FORMAT_LABEL.get(fmt, '')})")
    state.tracks[key] = result
    return result


def spot_clip(engine, clip_ids, track: str, tc: str):
    if isinstance(clip_ids, str):
        clip_ids = [clip_ids]
    last = None
    for legacy in (False, True):
        op = CId_SpotClipsByID(src_clips=list(clip_ids), dst_track_name=track,
                               dst_location_data=spot_location(tc, legacy))
        try:
            engine.client.run(op)
            return
        except CommandError as e:
            last = e
            if not _is_location_error(e) or not hasattr(pt, "TimelineLocation"):
                raise
    raise last


def spot_group(engine, ids, track: str, tc: str, channels=1):
    """Place un fichier sur une piste. Pro Tools renvoie un clip par canal (G, D) et parfois
    un clip de groupe : on essaie l'ensemble, puis chaque combinaison de `channels` clips."""
    ids = list(ids)
    attempts = [ids]
    if len(ids) > channels:
        attempts += [list(c) for c in itertools.combinations(ids, channels)]
    if channels > 1:
        attempts += [[i] for i in ids]
    last_error = None
    for attempt in attempts:
        try:
            spot_clip(engine, attempt, track, tc)
            return
        except CommandError as e:
            last_error = e
    raise last_error


def import_audio_to_clip_list(engine, path: Path, state):
    """Extrait l'audio du fichier vidéo dans la Clip List. Renvoie un groupe de clips par
    fichier audio créé."""
    if state.dest_candidates is None:
        state.dest_candidates = audio_dest_candidates(engine)
    last_error = None
    for dest in state.dest_candidates:
        op = CId_ImportAudioToClipList(file_list=[str(path)], audio_operations=pt.ConvertAudio,
                                       destination_path=dest)
        try:
            engine.client.run(op)
        except CommandError as e:
            last_error = e
            continue
        state.dest_candidates = [dest]  # chemin accepté : on le garde pour la suite
        failures = list(getattr(op.response, "failure_list", []) or [])
        if failures:
            raise RuntimeError("; ".join(f.failure_message for f in failures))
        groups = []
        for entry in (getattr(op.response, "file_list", []) or []):
            for fdesc in entry.destination_file_list:
                if fdesc.clip_id_list:
                    groups.append(list(fdesc.clip_id_list))
        if not groups:
            raise RuntimeError("aucun audio extrait du fichier vidéo")
        return groups
    raise last_error


def import_wav_files(engine, files, operations=(pt.AddAudio, pt.CopyAudio)):
    """Ajoute des WAV dans la Clip List. Renvoie, pour chaque fichier (dans l'ordre),
    la liste de ses clips."""
    paths = [str(f) for f in files]
    last_error = None
    for operation in operations:
        op = CId_ImportAudioToClipList(file_list=paths, audio_operations=operation,
                                       destination_path=str(Path(paths[0]).parent))
        try:
            engine.client.run(op)
        except CommandError as e:
            last_error = e
            continue
        failures = list(getattr(op.response, "failure_list", []) or [])
        if failures:
            raise RuntimeError("; ".join(f.failure_message for f in failures))
        by_input = {}
        for entry in (getattr(op.response, "file_list", []) or []):
            by_input[entry.original_input_path] = [c for fd in entry.destination_file_list
                                                   for c in fd.clip_id_list]
        if all(p in by_input for p in paths):
            groups = [by_input[p] for p in paths]
        else:  # chemins renvoyés sous une autre forme : ordre de la réponse
            groups = list(by_input.values())
        if not groups or not all(groups):
            raise RuntimeError("Pro Tools n'a renvoyé aucun clip")
        return groups
    raise last_error


def place_clips(engine, groups, layout, tc, state):
    """groups : une liste de clips par fichier audio ; layout : mono | stereo | split."""
    if layout in ("mono", "stereo"):
        fmt = pt.TF_Mono if layout == "mono" else pt.TF_Stereo
        track = ensure_audio_tracks(engine, state, 1, fmt)[0]
        spot_group(engine, groups[0], track, tc, 1 if layout == "mono" else 2)
        print(f"    audio {'mono' if layout == 'mono' else 'stéréo'} -> {track}")
    else:
        # un fichier mono par canal : une piste mono par canal, toujours les mêmes
        tracks = ensure_audio_tracks(engine, state, len(groups), pt.TF_Mono)
        for ids, track in zip(groups, tracks):
            spot_group(engine, ids, track, tc, 1)
        print(f"    audio {len(groups)} × mono -> {', '.join(tracks)}")


def import_video_audio(engine, path: Path, tc: str, state):
    fut = state.prep.submit(path) if state.prep else None
    if fut is not None:
        try:
            files, layout, label = fut.result()
            print(f"    audio : {label}")
        except Exception as e:
            print(f"  ! ffmpeg : {e}")
            files = None
        if files:
            strategies = [((pt.AddAudio, pt.CopyAudio), "WAV ajouté tel quel"),
                          ((pt.ConvertAudio,), "WAV converti par Pro Tools")]
            for n, (operations, slabel) in enumerate(strategies):
                groups = []
                try:
                    groups = import_wav_files(engine, files, operations)
                    place_clips(engine, groups, layout, tc, state)
                    if n:
                        print(f"    (audio placé via : {slabel})")
                    return
                except Exception as e:
                    detail = f" | clips par fichier : {[len(g) for g in groups]}" if groups else ""
                    print(f"  ! {slabel} : {e}{detail}")
    # dernier recours : extraction et conversion par Pro Tools depuis la vidéo
    print("    extraction de l'audio par Pro Tools…")
    groups = import_audio_to_clip_list(engine, path, state)
    if len(groups) == 1:
        layout = "mono" if (audio_channels(path) or 2) == 1 else "stereo"
    else:
        layout = "split"
    place_clips(engine, groups, layout, tc, state)


def has_video_track(engine) -> bool:
    try:
        return any(t.type == pt.TT_Video for t in engine.track_list())
    except Exception:
        return False


def import_video(engine, path: Path, tc: str, args, state):
    # 1) image seule sur la piste vidéo, spottée au TC
    if args.new_video_track or not has_video_track(engine):
        destination = pt.MD_NewTrack  # aucune piste vidéo : la première importée devient la principale
    else:
        destination = pt.MD_MainVideoTrack

    def run(dest, legacy):
        o = CId_ImportVideo(
            video_file_list=[str(path)],
            destination=dest,
            location=pt.ML_Spot,
            spot_location_data=spot_location(tc, legacy),
            import_audio_from_file=False,
        )
        engine.client.run(o)
        return o

    def run_any_location(dest):
        # l'ancien format de position fonctionne pour ImportVideo ; le nouveau en secours
        try:
            return run(dest, True)
        except CommandError as e:
            if _is_location_error(e) and hasattr(pt, "TimelineLocation"):
                return run(dest, False)
            raise

    try:
        op = run_any_location(destination)
    except CommandError as e:
        if destination == pt.MD_MainVideoTrack and "NoVideoTrack" in str(e):
            op = run_any_location(pt.MD_NewTrack)
        else:
            raise
    failures = list(getattr(op.response, "failure_list", []) or [])
    if failures:
        raise RuntimeError("; ".join(f.failure_message for f in failures))

    # 2) audio du fichier -> Clip List -> spotté sur les pistes communes
    if not args.no_video_audio:
        import_video_audio(engine, path, tc, state)


def import_one(engine, path: Path, tc: str, args, state):
    if not path.exists():
        raise FileNotFoundError("fichier introuvable")
    k = kind_of(path)
    if k == "aaf":
        import_aaf(engine, path, tc, args)
    elif k == "video":
        import_video(engine, path, tc, args, state)
    else:
        raise ValueError(f"extension non gérée : {path.suffix}")


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description="PAAF — import séquentiel AAF/vidéo dans Pro Tools via PTSL")
    ap.add_argument("files", nargs="*", type=Path,
                    help="fichiers et/ou dossiers à importer (sinon sélecteur macOS)")
    ap.add_argument("-r", "--recursive", action="store_true", help="parcourir aussi les sous-dossiers")
    ap.add_argument("--mode", choices=["fixe", "bout", "interactif"], default="fixe",
                    help="fixe : start + n×step | bout : bout à bout + gap | interactif : TC demandé par fichier")
    ap.add_argument("--csv", help="plan imposé : chemin;TC par ligne (ignore --mode)")
    ap.add_argument("--start", default="01:00:00:00", help="TC du premier fichier")
    ap.add_argument("--step", default="00:10:00:00",
                    help="espacement entre points d'entrée (mode fixe) ou durée réservée si inconnue (mode bout)")
    ap.add_argument("--gap", default="00:00:10:00", help="blanc entre deux médias (mode bout)")
    ap.add_argument("--keep-order", action="store_true", help="garder l'ordre donné au lieu du tri naturel")
    ap.add_argument("--no-link-names", action="store_true",
                    help="ne pas caler au même TC un AAF et une vidéo de même nom")
    ap.add_argument("--copy-audio", action="store_true", help="AAF : copier l'audio dans la session (défaut : lien)")
    ap.add_argument("--convert-audio", action="store_true",
                    help="AAF : convertir l'audio au format de la session (fréquence/résolution différentes)")
    ap.add_argument("--consolidate", action="store_true", help="AAF : consolider l'audio (avec --handles)")
    ap.add_argument("--handles", type=int, default=1000, help="AAF : poignées en ms pour la consolidation")
    ap.add_argument("--match-tracks", action="store_true",
                    help="AAF : importer dans les pistes de même nom de la session (Match Tracks)")
    ap.add_argument("--no-clip-gain", action="store_true", help="AAF : ne pas importer le gain de clip")
    ap.add_argument("--no-volume-automation", action="store_true", help="AAF : ne pas importer l'automation de volume")
    ap.add_argument("--copy-video", action="store_true", help="AAF : copier la vidéo référencée (défaut : lien)")
    ap.add_argument("--new-video-track", action="store_true",
                    help="vidéo : nouvelle piste au lieu de la piste vidéo principale")
    ap.add_argument("--no-video-audio", action="store_true",
                    help="vidéo : ne PAS importer l'audio contenu dans le fichier (importé par défaut)")
    ap.add_argument("--no-ffmpeg", action="store_true",
                    help="vidéo : laisser Pro Tools extraire l'audio (au lieu de ffmpeg, plus rapide)")
    ap.add_argument("--no-stereo-detect", action="store_true",
                    help="vidéo : garder en stéréo les audios à canaux identiques (double mono)")
    ap.add_argument("--audio-track", default="AUDIO VIDEO",
                    help="vidéo : nom de la piste audio commune (créée si absente ; « NOM MONO » "
                         "pour le mono, « NOM 1 », « NOM 2 »… si l'audio est éclaté en mono)")
    ap.add_argument("--fps", type=int, help="base TC forcée (utile avec --dry-run sans Pro Tools)")
    ap.add_argument("--dry-run", action="store_true", help="afficher le plan sans importer")
    ap.add_argument("-y", "--yes", action="store_true", help="ne pas demander de confirmation")
    args = ap.parse_args()

    files = [] if args.csv else (args.files or choose_files_macos())
    files = expand_paths([p.expanduser() for p in files], args.recursive)
    if not args.csv and not files:
        sys.exit("Aucun fichier à importer.")

    offline = args.dry_run and args.fps
    engine_cm = None if offline else connect()
    engine = None
    try:
        if engine_cm:
            engine = engine_cm.__enter__()
            try:
                session = engine.session_name()
            except CommandError:
                sys.exit("Aucune session ouverte dans Pro Tools.")
            rate = engine.session_timecode_rate()
            if rate not in RATES:
                sys.exit(f"Cadence TC non gérée : {pt.SessionTimeCodeRate.Name(rate)}")
            base, real_fps, drop = RATES[rate]
            if drop:
                sys.exit("Session en drop-frame : non gérée par ce script.")
            print(f"Session : {session}  |  TC {pt.SessionTimeCodeRate.Name(rate)}  |  PTSL v{engine.ptsl_version()}")
        else:
            base, real_fps = args.fps, float(args.fps)

        tc = TC(base)
        plan = build_plan(files, args, tc, real_fps)

        print("\nPlan d'import :")
        for i, (p, t) in enumerate(plan, 1):
            k = kind_of(p) or "??"
            flag = "" if p.exists() else "  [INTROUVABLE]"
            print(f"  {i:2d}. {t}  {k:5s}  {p.name}{flag}")
        if args.dry_run:
            return
        if not args.yes and input("\nLancer l'import ? [o/N] ").strip().lower() not in ("o", "oui", "y", "yes"):
            return

        report = []
        video_state = VideoAudioState(args.audio_track)
        start_audio_extraction(engine, plan, args, video_state)
        for p, t in plan:
            try:
                import_one(engine, p, t, args, video_state)
                report.append(f"✓ {t}  {p.name}")
                print(report[-1])
            except (CommandError, RuntimeError, ValueError, FileNotFoundError) as e:
                report.append(f"✗ {t}  {p.name} : {e}")
                print(report[-1])

        video_state.shutdown()
        ok = sum(r.startswith("✓") for r in report)
        print(f"\nTerminé : {ok}/{len(report)} import(s) réussi(s).")
    finally:
        if engine_cm:
            engine_cm.__exit__(None, None, None)


if __name__ == "__main__":
    main()
