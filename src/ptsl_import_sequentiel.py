#!/usr/bin/env python3
"""
Import séquentiel d'AAF / OMF et de vidéos dans Pro Tools via le PTSL
(Pro Tools Scripting Library, API officielle Avid), sans GUI scripting.

Prérequis
  - Pro Tools 2023.x ou plus récent, session ouverte (PTSL écoute sur localhost:31416)
  - Python 3.9+  et  py-ptsl :   pip3 install py-ptsl
  - Optionnel : ffprobe (ffmpeg) pour lire les durées vidéo en mode "bout"

Exemples
  # Tout un dossier (AAF + vidéos qu'il contient, tri naturel)
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
import math
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    import ptsl.PTSL_pb2 as pt
    from ptsl import open_engine, CommandError
    from ptsl import ops
    from ptsl.ops.operation import Operation
except ImportError:
    sys.exit("py-ptsl n'est pas installé :  pip3 install py-ptsl")


AAF_EXT = {".aaf", ".omf", ".omfi"}
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mxf", ".avi"}


# py-ptsl n'a pas (encore) de méthode pour ImportVideo : on déclare l'opération.
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
        f = frames % self.base
        s_total = frames // self.base
        return f"{s_total // 3600:02d}:{(s_total // 60) % 60:02d}:{s_total % 60:02d}:{f:02d}"


# ------------------------------------------------------------------ fichiers

def natural_key(path: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


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


def build_plan(files, args, tc: TC, real_fps: float):
    if args.csv:
        return read_csv_plan(Path(args.csv).expanduser(), tc)

    files = sorted(files, key=natural_key) if not args.keep_order else files
    current = tc.to_frames(args.start)
    step = tc.to_frames(args.step)
    gap = tc.to_frames(args.gap)
    plan = []
    for i, p in enumerate(files):
        if args.mode == "interactif":
            default = tc.to_tc(current)
            while True:
                answer = input(f"Point d'entrée pour {p.name} [{default}] : ").strip() or default
                try:
                    current = tc.to_frames(answer)
                    break
                except ValueError as e:
                    print(f"  {e}")
        plan.append((p, tc.to_tc(current)))

        if args.mode == "bout":
            dur = media_duration_seconds(p) if kind_of(p) == "video" else None
            if dur is None:
                print(f"  ! Durée inconnue pour {p.name} : réservation de {args.step}")
                current += step
            else:
                current += math.ceil(dur * real_fps) + gap
        else:
            current += step
    return plan


# ------------------------------------------------------------------ imports

def import_aaf(engine, path: Path, tc: str, args):
    """Import Session Data d'un AAF/OMF : toutes les pistes en nouvelles pistes,
    avec clips et médias (sans clips_and_media, Pro Tools crée des pistes vides),
    gain de clip et automation de volume, calé sur le TC demandé."""
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
        match_options=pt.MT_ImportAsNewTrack,
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


def audio_channels(path: Path):
    if shutil.which("ffprobe"):
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                            "-show_entries", "stream=channels", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True)
        try:
            return int(r.stdout.strip().split()[0])
        except (ValueError, IndexError):
            pass
    return None


class VideoAudioState:
    """Mémorise le dossier de destination, les pistes audio communes entre deux vidéos
    et les extractions ffmpeg lancées en parallèle."""

    def __init__(self, base_name: str):
        self.base_name = base_name
        self.dest_candidates = None
        self.tracks = {}      # (nb_pistes, format) -> [noms]
        self.futures = {}     # Path vidéo -> Future(extraction ffmpeg)
        self.executor = None

    def shutdown(self):
        if self.executor:
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.executor = None


# ------------------------------------------------------------------ ffmpeg

PCM_CODECS = {
    getattr(pt, "Bit16", None): ("pcm_s16le", "16 bits"),
    getattr(pt, "Bit24", None): ("pcm_s24le", "24 bits"),
    getattr(pt, "Bit32Float", None): ("pcm_f32le", "32 bits flottant"),
}


def ffmpeg_path():
    return shutil.which("ffmpeg")


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


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    i = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def extract_audio_ffmpeg(path: Path, fmt: dict):
    """Extrait la 1re piste audio de la vidéo en WAV au format de la session.
    Mono/stéréo entrelacé -> 1 fichier ; plus de 2 canaux (ou session non entrelacée)
    -> 1 fichier mono par canal. Renvoie (liste de fichiers, nb de canaux)."""
    ch = audio_channels(path)
    if not ch:
        raise RuntimeError("aucune piste audio détectée (ffprobe)")
    out_dir = fmt["audio_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w\-. ]", "_", path.stem)
    base = ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y", "-i", str(path)]
    common = ["-vn", "-c:a", fmt["codec"], "-ar", str(fmt["sr"]), "-rf64", "auto", "-write_bext", "1"]

    if ch <= 2 and fmt["interleaved"]:
        out = _unique(out_dir / f"{stem}.wav")
        cmd = base + ["-map", "0:a:0"] + common + [str(out)]
        outputs = [out]
    else:
        outputs, cmd = [], list(base)
        for i in range(ch):
            out = _unique(out_dir / f"{stem}.A{i + 1}.wav")
            cmd += ["-map", "0:a:0", "-af", f"pan=mono|c0=c{i}"] + common + [str(out)]
            outputs.append(out)

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        for o in outputs:
            o.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg : " + (r.stderr.strip().splitlines() or ["erreur"])[-1])
    return outputs, ch


def start_audio_extraction(engine, plan, args, state):
    """Lance en tâche de fond l'extraction ffmpeg de toutes les vidéos du plan,
    pendant que Pro Tools importe les fichiers précédents."""
    if getattr(args, "no_video_audio", False) or getattr(args, "no_ffmpeg", False):
        return
    videos = [p for p, _ in plan if kind_of(p) == "video" and p.exists()]
    if not videos:
        return
    if not (ffmpeg_path() and shutil.which("ffprobe")):
        print("  ffmpeg/ffprobe introuvables : extraction de l'audio par Pro Tools (plus lent)")
        return
    fmt = session_audio_format(engine)
    if fmt["audio_dir"] is None:
        print("  Dossier Audio Files introuvable : extraction de l'audio par Pro Tools")
        return
    workers = max(1, min(4, (os.cpu_count() or 2) // 2))
    state.executor = ThreadPoolExecutor(max_workers=workers)
    for p in videos:
        state.futures[p] = state.executor.submit(extract_audio_ffmpeg, p, fmt)
    print(f"  ffmpeg : extraction de {len(videos)} audio(s) en parallèle "
          f"({fmt['sr']} Hz / {fmt['label']}, {workers} à la fois) vers {fmt['audio_dir']}")


def ensure_audio_tracks(engine, state, n_tracks: int, fmt):
    key = (n_tracks, fmt)
    if key in state.tracks:
        return state.tracks[key]
    names = [state.base_name] if n_tracks == 1 else [f"{state.base_name} {i}" for i in range(1, n_tracks + 1)]
    existing = {t.name for t in engine.track_list()}
    result = []
    for name in names:
        if name in existing:
            result.append(name)
            continue
        op = ops.CId_CreateNewTracks(
            number_of_tracks=1, track_name=name, track_format=fmt,
            track_type=pt.TT_Audio, track_timebase=pt.TTB_Samples,
            insertion_point_position=pt.TIPoint_Last,
        )
        engine.client.run(op)
        created = list(getattr(op.response, "created_track_names", []) or [])
        result.append(created[0] if created else name)
        print(f"    + piste créée : {result[-1]}")
    state.tracks[key] = result
    return result


def import_audio_to_clip_list(engine, path: Path, state):
    """Extrait l'audio du fichier vidéo dans la Clip List. Renvoie un clip par fichier audio créé."""
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


def spot_clip(engine, clip_ids, track: str, tc: str):
    if isinstance(clip_ids, str):
        clip_ids = [clip_ids]
    op = CId_SpotClipsByID(
        src_clips=list(clip_ids),
        dst_track_name=track,
        dst_location_data=pt.SpotLocationData(location_type=pt.Start,
                                              location_options=pt.TimeCode,
                                              location_value=tc),
    )
    engine.client.run(op)


def spot_group(engine, ids, track: str, tc: str):
    """Place un fichier sur une piste : d'abord tous ses clips ensemble, puis chaque
    clip seul (Pro Tools ne dit pas lequel correspond au format de la piste)."""
    attempts = [list(ids)] + ([[i] for i in ids] if len(ids) > 1 else [])
    last_error = None
    for attempt in attempts:
        try:
            spot_clip(engine, attempt, track, tc)
            return
        except CommandError as e:
            last_error = e
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


def place_clips(engine, groups, channels, tc, state):
    """groups : une liste de clips par fichier audio."""
    if len(groups) == 1:
        if channels > 2:
            raise RuntimeError(f"audio {channels} canaux entrelacé non géré (mono ou stéréo uniquement)")
        tracks = ensure_audio_tracks(engine, state, 1, pt.TF_Mono if channels == 1 else pt.TF_Stereo)
        spot_group(engine, groups[0], tracks[0], tc)
    else:
        # un fichier mono par canal : une piste mono par canal, toujours les mêmes
        tracks = ensure_audio_tracks(engine, state, len(groups), pt.TF_Mono)
        for ids, track in zip(groups, tracks):
            spot_group(engine, ids, track, tc)


def import_video_audio(engine, path: Path, tc: str, state):
    future = state.futures.pop(path, None)
    if future is not None:
        try:
            files, ch = future.result()
        except Exception as e:
            print(f"  ! ffmpeg : {e}")
            files = None
        if files:
            strategies = [((pt.AddAudio, pt.CopyAudio), "WAV ajouté tel quel"),
                          ((pt.ConvertAudio,), "WAV converti par Pro Tools")]
            for n, (operations, label) in enumerate(strategies):
                groups = []
                try:
                    groups = import_wav_files(engine, files, operations)
                    place_clips(engine, groups, ch, tc, state)
                    if n:
                        print(f"    (audio placé via : {label})")
                    return
                except Exception as e:
                    detail = f" | clips par fichier : {[len(g) for g in groups]}" if groups else ""
                    print(f"  ! {label} : {e}{detail}")
    # dernier recours : extraction et conversion par Pro Tools depuis la vidéo
    print("    extraction de l'audio par Pro Tools…")
    groups = import_audio_to_clip_list(engine, path, state)
    ch = (audio_channels(path) or 2) if len(groups) == 1 else 1
    place_clips(engine, groups, ch, tc, state)


def has_video_track(engine) -> bool:
    try:
        return any(t.type == pt.TT_Video for t in engine.track_list())
    except Exception:
        return False


def import_video(engine, path: Path, tc: str, args, state):
    # 1) image seule sur la piste vidéo, spottée au TC
    location = pt.SpotLocationData(location_type=pt.Start, location_options=pt.TimeCode,
                                   location_value=tc)
    if args.new_video_track or not has_video_track(engine):
        destination = pt.MD_NewTrack  # aucune piste vidéo : la première importée devient la principale
    else:
        destination = pt.MD_MainVideoTrack

    def run(dest):
        o = CId_ImportVideo(
            video_file_list=[str(path)],
            destination=dest,
            location=pt.ML_Spot,
            spot_location_data=location,
            import_audio_from_file=False,
        )
        engine.client.run(o)
        return o

    try:
        op = run(destination)
    except CommandError as e:
        if destination == pt.MD_MainVideoTrack and "NoVideoTrack" in str(e):
            op = run(pt.MD_NewTrack)
        else:
            raise
    failures = list(getattr(op.response, "failure_list", []) or [])
    if failures:
        raise RuntimeError("; ".join(f.failure_message for f in failures))

    # 2) audio du fichier -> Clip List -> spotté sur les pistes communes
    if not args.no_video_audio:
        import_video_audio(engine, path, tc, state)


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description="Import séquentiel AAF/vidéo dans Pro Tools via PTSL")
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
    ap.add_argument("--copy-audio", action="store_true", help="AAF : copier l'audio dans la session (défaut : lien)")
    ap.add_argument("--convert-audio", action="store_true",
                    help="AAF : convertir l'audio au format de la session (fréquence/résolution différentes)")
    ap.add_argument("--consolidate", action="store_true", help="AAF : consolider l'audio (avec --handles)")
    ap.add_argument("--handles", type=int, default=1000, help="AAF : poignées en ms pour la consolidation")
    ap.add_argument("--no-clip-gain", action="store_true", help="AAF : ne pas importer le gain de clip")
    ap.add_argument("--no-volume-automation", action="store_true", help="AAF : ne pas importer l'automation de volume")
    ap.add_argument("--copy-video", action="store_true", help="AAF : copier la vidéo référencée (défaut : lien)")
    ap.add_argument("--new-video-track", action="store_true",
                    help="vidéo : nouvelle piste au lieu de la piste vidéo principale")
    ap.add_argument("--no-video-audio", action="store_true",
                    help="vidéo : ne PAS importer l'audio contenu dans le fichier (importé par défaut)")
    ap.add_argument("--no-ffmpeg", action="store_true",
                    help="vidéo : laisser Pro Tools extraire l'audio (au lieu de ffmpeg, plus rapide)")
    ap.add_argument("--audio-track", default="AUDIO VIDEO",
                    help="vidéo : nom de la piste audio commune (créée si absente ; "
                         "« NOM 1 », « NOM 2 »… si l'audio est éclaté en mono)")
    ap.add_argument("--fps", type=int, help="base TC forcée (utile avec --dry-run sans Pro Tools)")
    ap.add_argument("--dry-run", action="store_true", help="afficher le plan sans importer")
    ap.add_argument("-y", "--yes", action="store_true", help="ne pas demander de confirmation")
    args = ap.parse_args()

    files = [] if args.csv else (args.files or choose_files_macos())
    files = expand_paths([p.expanduser() for p in files], args.recursive)
    if not args.csv and not files:
        sys.exit("Aucun fichier à importer.")

    offline = args.dry_run and args.fps
    engine_cm = None if offline else open_engine(company_name="Bernik", application_name="Import sequentiel")
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
            k = kind_of(p)
            try:
                if not p.exists():
                    raise FileNotFoundError("fichier introuvable")
                if k == "aaf":
                    import_aaf(engine, p, t, args)
                elif k == "video":
                    import_video(engine, p, t, args, video_state)
                else:
                    raise ValueError(f"extension non gérée : {p.suffix}")
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
