# Import Pro Tools (PAAF) — notes pour Claude Code

Application macOS qui importe à la suite des AAF/OMF et des vidéos dans la session Pro Tools
ouverte, chacun à un point d'entrée timecode, via le PTSL (gRPC sur localhost:31416, bibliothèque
Python `py-ptsl`). Le propriétaire est ingénieur du son ; il échange en français.

## Structure

- `src/ptsl_import_sequentiel.py` : moteur + CLI. Points clés :
  - `import_aaf` : `CId_Import` (type Session) avec `TrackDataToImport(clips_and_media=True)`
    — sans ce flag Pro Tools crée des pistes vides — et `MapStartTimeCodeTo`.
  - `import_video` : `CId_ImportVideo` (opération déclarée localement, absente de py-ptsl),
    `MD_MainVideoTrack` + `ML_Spot`. Sans piste vidéo dans la session, Pro Tools renvoie
    `PT_NoVideoTrackFound` : on passe alors en `MD_NewTrack`.
  - Audio des vidéos : `start_audio_extraction` lance ffmpeg en parallèle (WAV BWF au format de
    la session, dans `Audio Files`), puis `CId_ImportAudioToClipList` (AddAudio) et
    `CId_SpotClipsByID` sur des pistes communes créées par `ensure_audio_tracks`.
    Pro Tools peut renvoyer plusieurs clip_id par fichier : `spot_group` essaie le groupe puis
    chaque id (erreur sinon : « incompatibilities between selected clips and destination track »).
    Replis : ConvertAudio sur le WAV, puis extraction par Pro Tools depuis la vidéo.
- `src/ptsl_import_gui.py` : interface Tkinter, glisser-déposer via `tkinterdnd2`,
  `::tk::mac::OpenDocument` pour les dépôts sur l'icône.
- `macos/launcher` : lanceur bash auto-installant. Crée le venv dans
  `~/Library/Application Support/Import Pro Tools/env` ; `DEPS_VERSION` à incrémenter uniquement
  si les dépendances Python changent. `@VERSION@` est remplacé par `build.sh`.
- `macos/Info.plist` : `@VERSION@` / `@BUILD@` remplacés au build.

## Build et release

- `./build.sh` → `dist/Import Pro Tools.app` et `dist/Import_Pro_Tools_v<VERSION>.zip`.
- Version unique dans `VERSION` ; notes dans `CHANGELOG.md` (`## X.Y`).
- Release : `git tag vX.Y && git push origin vX.Y` → `.github/workflows/release.yml` (macos-latest)
  vérifie tag = VERSION, construit et publie le zip.

## Limites connues

- Timecode drop-frame non géré.
- Audio entrelacé > 2 canaux : éclaté en mono par ffmpeg ; sans ffmpeg, refusé.
- Rien n'a été testé contre un vrai Pro Tools depuis le cloud : vérifier sur le Mac du
  propriétaire, et demander les lignes `✗` / `!` du journal en cas d'échec.
- Application non signée ni notarisée (Gatekeeper : clic droit > Ouvrir).
