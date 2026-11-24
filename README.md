# PAAF-RELEASES

**PAAF** : import d'AAF et de vidéos dans Pro Tools (anciennement « Import Pro Tools »).

Importe à la suite plusieurs AAF / OMF et vidéos dans la session Pro Tools ouverte, chacun à
son point d'entrée timecode, via le **PTSL** (Pro Tools Scripting Library, l'API officielle
d'Avid). Aucune automatisation de l'interface de Pro Tools.

## Fonctions

- Glisser-déposer de fichiers ou de dossiers depuis le Finder
- Placement : espacement fixe, bout à bout avec blanc, ou TC manuel (édition directe dans la
  liste) ; plans CSV
- **AAF et vidéo de même nom calés au même TC** automatiquement
- **AAF / OMF** : Import Session Data, clips et médias, gain de clip, automation de volume,
  « Map start timecode to » ; en nouvelles pistes ou **fusionnés dans les pistes de même nom**
  de la session (Match Tracks)
- **Inspecteur** : pistes et clips d'un AAF (position dans Pro Tools), métadonnées des médias
  audio (fréquence, résolution, canaux, TC d'origine BWF, Scene/Take, embarqué ou lié), paires
  stéréo probables ; pour une vidéo, flux audio et analyse des canaux
- **Pistes de la session** affichées (type, format, clips) pour préparer l'import
- **Vidéos** : image sur la Main Video Track (créée si absente), spottée au TC
- **Audio des vidéos** : converti par ffmpeg au format de la session, dès l'ajout des fichiers
  (préconversion), puis posé sur des pistes communes (« AUDIO VIDEO » en stéréo,
  « AUDIO VIDEO MONO » en mono, « AUDIO VIDEO 1…n » en multicanal)
- **Détection stéréo** : une stéréo à canaux identiques (double mono) ou à un seul canal utile
  est importée en mono

## Installation

1. Télécharger le dernier `PAAF_vX.Y.dmg` dans
   [Releases](https://github.com/julessourzac-gif/PAAF-RELEASES/releases).
2. Ouvrir le DMG, glisser **PAAF** dans Applications (supprimer l'ancien « Import Pro Tools »).
   L'application est signée et notarisée : elle s'ouvre directement.
3. Rien à installer : Python (avec Tkinter), les bibliothèques et ffmpeg / ffprobe sont inclus.

Prérequis : macOS 11+ (Apple Silicon ou Intel), Pro Tools 2023 ou plus récent.

Journal technique : `~/Library/Application Support/PAAF/paaf.log`

## Développement

```
src/ptsl_import_sequentiel.py   moteur PTSL + ligne de commande
src/ptsl_import_gui.py          interface Tkinter
src/aaf_inspect.py              lecture des AAF (pyaaf2) pour l'inspecteur
src/assets/                     logo affiché dans l'interface
macos/                          lanceur, Info.plist, icône du bundle, make_icon.py (logo)
legacy/                         ancienne version AppleScript (GUI scripting)
build.sh                        construit dist/PAAF.app et le zip
VERSION                         numéro de version (source unique)
```

Build local :

```
./build.sh
open dist/PAAF.app
```

Interface depuis les sources :

```
python3 -m venv .venv && .venv/bin/pip install py-ptsl tkinterdnd2 pyaaf2
.venv/bin/python src/ptsl_import_gui.py [fichiers ou dossiers]
```

Ligne de commande (sans interface) :

```
.venv/bin/python src/ptsl_import_sequentiel.py --dry-run --fps 25 --mode fixe --start 01:00:00:00 --step 00:10:00:00 DOSSIER
```

Logo et icône : `.venv/bin/pip install pillow && .venv/bin/python macos/make_icon.py`.

## Publier une version

1. Mettre à jour `VERSION` et `CHANGELOG.md` (section `## X.Y`), commiter et pousser sur `main`.
2. Créer la release, au choix :
   - sur github.com : **Releases > Draft a new release**, tag `vX.Y` (le créer), **Publish** ;
   - ou en ligne de commande : `git tag vX.Y && git push origin vX.Y`.

Le workflow GitHub Actions construit l'application sur macOS et joint le zip à la release
(quelques minutes). Les notes de la section `## X.Y` du CHANGELOG sont reprises si la release
n'a pas de description.
