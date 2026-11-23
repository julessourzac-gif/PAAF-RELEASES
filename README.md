# PAAF-RELEASES

Logiciel d'import AAF et vidéo pour Pro Tools : **Import Pro Tools**.

Importe à la suite plusieurs AAF / OMF et vidéos dans la session Pro Tools ouverte, chacun à
son point d'entrée timecode, via le **PTSL** (Pro Tools Scripting Library, l'API officielle
d'Avid). Aucune automatisation de l'interface de Pro Tools.

## Fonctions

- Glisser-déposer de fichiers ou de dossiers depuis le Finder
- Placement : espacement fixe, bout à bout avec blanc, ou TC manuel par fichier ; plans CSV
- **AAF / OMF** : Import Session Data, toutes les pistes en nouvelles pistes, clips et médias,
  gain de clip, automation de volume, « Map start timecode to »
- **Vidéos** : image sur la Main Video Track (créée si absente), spottée au TC
- **Audio des vidéos** : extrait par ffmpeg en parallèle, au format de la session, posé sur des
  pistes communes (« AUDIO VIDEO », ou une piste mono par canal)

## Installation

1. Télécharger le dernier `Import_Pro_Tools_vX.Y.zip` dans
   [Releases](https://github.com/julessourzac-gif/PAAF-RELEASES/releases).
2. Décompresser, glisser **Import Pro Tools** dans Applications.
3. Premier lancement : clic droit > **Ouvrir** (application non signée). Si macOS bloque :
   Réglages Système > Confidentialité et sécurité > **Ouvrir quand même**, ou
   `xattr -dr com.apple.quarantine "/Applications/Import Pro Tools.app"`.
4. L'application prépare son environnement Python (1 à 2 min) et propose d'installer via
   Homebrew ce qui manque (Tkinter, ffmpeg).

Prérequis : macOS 11+, [Homebrew](https://brew.sh), Pro Tools 2023 ou plus récent.

Journal technique : `~/Library/Application Support/Import Pro Tools/ptimport.log`

## Développement

```
src/ptsl_import_sequentiel.py   moteur PTSL + ligne de commande
src/ptsl_import_gui.py          interface Tkinter
macos/                          lanceur, Info.plist, icône du bundle
legacy/                         ancienne version AppleScript (GUI scripting)
build.sh                        construit dist/Import Pro Tools.app et le zip
VERSION                         numéro de version (source unique)
```

Build local :

```
./build.sh
open "dist/Import Pro Tools.app"
```

Ligne de commande (sans interface) :

```
python3 -m venv .venv && .venv/bin/pip install py-ptsl
.venv/bin/python src/ptsl_import_sequentiel.py --dry-run --mode fixe --start 01:00:00:00 --step 00:10:00:00 DOSSIER
```

## Publier une version

1. Mettre à jour `VERSION` et `CHANGELOG.md` (section `## X.Y`), commiter et pousser sur `main`.
2. Créer la release, au choix :
   - sur github.com : **Releases > Draft a new release**, tag `vX.Y` (le créer), **Publish** ;
   - ou en ligne de commande : `git tag vX.Y && git push origin vX.Y`.

Le workflow GitHub Actions construit l'application sur macOS et joint le zip à la release
(quelques minutes). Les notes de la section `## X.Y` du CHANGELOG sont reprises si la release
n'a pas de description.
