# PAAF (ex-Import Pro Tools) — notes pour Claude Code

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
  - Positions (`spot_location`) : Pro Tools 2025+/PTSL 2026 exige `SpotLocationData.location`
    (`TimelineLocation`, `TLType_TimeCode`) pour `CId_SpotClipsByID` ; l'ancien
    `location_value` donne l'erreur « location » (vérifié sur le Mac du propriétaire).
    `ImportVideo` fonctionne encore avec l'ancien format (le nouveau en secours).
  - Audio des vidéos : `analyze_audio` (ffprobe + astats sur G, D, G−D) classe l'audio
    (stéréo, double mono, gauche/droite seule, multicanal) ; `AudioPrep` lance ffmpeg en
    parallèle (WAV BWF au format de la session, dans `Audio Files`, écrit en `.part` puis
    renommé, réutilisé s'il existe déjà au bon format). L'interface le lance dès l'ajout.
    Puis `CId_ImportAudioToClipList` (AddAudio) et `CId_SpotClipsByID` sur des pistes communes
    (`ensure_audio_tracks`, jamais une piste existante d'un autre format : « NOM » stéréo,
    « NOM MONO », « NOM 1…n »). Pro Tools renvoie un clip_id par canal (G, D) : il faut les
    spotter ensemble ; `spot_group` essaie aussi les combinaisons.
    Replis : ConvertAudio sur le WAV, puis extraction par Pro Tools depuis la vidéo.
  - `schedule` / `link_groups` : un AAF et une vidéo de même nom (`pair_key`) partagent un TC.
  - AAF : `match_tracks` -> `MT_MatchTracks` (fusion dans les pistes de même nom).
- `src/ptsl_import_gui.py` : interface Tkinter (maquettes 1a, style MixCheck), glisser-déposer
  via `tkinterdnd2`, `::tk::mac::OpenDocument` pour les dépôts sur l'icône. Accepte des chemins
  en arguments. Vues selon l'état (`_view` : empty / ready / import / result) ; un changement de
  thème ou de langue reconstruit toute l'interface (`_rebuild`), les variables Tk survivent.
  Inspecteur et pistes de la session dans une fenêtre à part. Préférences (thème, langue,
  mises à jour) dans `~/Library/Application Support/PAAF/prefs.json`.
- `src/paaf_theme.py` : palettes DARK / LIGHT, polices (tailles en pixels), widgets dessinés sur
  Canvas (boutons, interrupteurs, segmenté, champs, timeline, tableau du plan `PlanTable`).
- `src/paaf_strings.py` : tous les textes, FR et EN (clés `_one` / `_other` pour les pluriels).
- `src/aaf_inspect.py` : lecture des AAF avec pyaaf2 (pistes, clips, médias, métadonnées).
- `src/conform.py` : comparaison de deux versions d'un montage (AAF / EDL CMX3600). Repères =
  tous les clips (calques imbriqués compris, calques de réglage / titres écartés), clé = nom du
  média source normalisé (les UMID changent d'un export Premiere à l'autre), TC source absolu.
  Vote par intervalle → `Segment`s (décalage nouveau − ancien), puis `Change`s
  (same / move / new / removed), rapport CSV et marqueurs (`create_memory_location`).
  Étape 2 (`apply_to_session`, après Save As) : copie des morceaux gardés dans une zone libre après
  la fin de session, passages supprimés rangés plus loin, puis effacement de l'ancienne zone et
  couper / coller du montage reconstruit à sa place. Validé dans Pro Tools 2026.1 (session TEST PAAF,
  ENGIE V1 → V3, décalage global +10 s) : vérifié par export AAF des pistes d'origine. Sessions de 24 h :
  zone de travail en fin de journée. Pro Tools refuse parfois les chemins de dossier (assertion
  « Sys_FileLocMacOS ») : `save_copy` essaie plusieurs écritures ; l'export AAF veut une barre finale et
  `CopyFromSourceMedia` (Link échoue sur des médias embarqués).
  La timeline Pro Tools n'est pas lisible clip par clip par PTSL (API privée).
- `macos/make_icon.py` : génère `macos/AppIcon.icns` et `src/assets/logo_*.png` (Pillow).
- `macos/stub.c` : exécutable compilé de l'app (`Contents/MacOS/PAAF`, universel arm64 + x86_64),
  qui lance `Contents/Resources/launcher` avec /bin/bash. Un vrai binaire est nécessaire pour la
  signature avec hardened runtime et la notarisation (un script en exécutable principal se signe mal).
- `macos/launcher` (copié dans `Contents/Resources`) : lanceur bash auto-installant. Crée le venv dans
  `~/Library/Application Support/PAAF/env` (reprend l'ancien dossier « Import Pro Tools ») ;
  `DEPS_VERSION` à incrémenter uniquement si les dépendances Python changent. `@VERSION@` est remplacé par `build.sh`.
- `macos/Info.plist` : `@VERSION@` / `@BUILD@` remplacés au build.

## Build et release

- `./build.sh` → `dist/PAAF.app` et `dist/PAAF_v<VERSION>.zip`.
- `./sign_notarize.sh` (sur le Mac du propriétaire) : build, signature Developer ID « BERNIK
  (S3X8746BP3) » avec hardened runtime, notarisation de l'app puis du DMG (`notarytool`, profil
  trousseau `PAAF`), agrafage, contrôle `spctl` → `dist/PAAF_v<VERSION>.dmg` (+ zip signé).
  `--sign-only` : sans envoi à Apple. Le profil se crée une fois par le propriétaire :
  `xcrun notarytool store-credentials PAAF --apple-id … --team-id S3X8746BP3` (ne jamais saisir
  son mot de passe à sa place). Le DMG signé est ensuite joint à la release à la main (`gh release
  upload`) ; le workflow GitHub ne fait qu'un build non signé.
  Ordre pour une release signée : PR fusionnée → `./sign_notarize.sh` → `gh release create vX.Y
  dist/PAAF_vX.Y.dmg dist/PAAF_vX.Y.zip --target main --notes-file …`. Le workflow, déclenché
  ensuite, voit que la release a déjà des fichiers et n'y joint pas son build non signé.
- Version unique dans `VERSION` ; notes dans `CHANGELOG.md` (`## X.Y`).
- Release : tag `vX.Y` poussé, ou release publiée depuis github.com → `.github/workflows/release.yml`
  (macos-latest) vérifie tag = VERSION, construit et joint le zip (crée la release si absente).
- Depuis une session Claude dans le cloud, le push de tags est refusé (403) : pousser `main`, et
  laisser le propriétaire créer la release sur github.com.

## Limites connues

- Timecode drop-frame non géré.
- Audio entrelacé > 2 canaux : éclaté en mono par ffmpeg ; sans ffmpeg, refusé.
- Depuis le cloud, rien ne peut être testé contre Pro Tools : vérifier sur le Mac du
  propriétaire, et demander les lignes `✗` / `!` du journal en cas d'échec. En session locale,
  Pro Tools tourne souvent (PTSL sur localhost:31416) : lecture libre, mais demander avant
  toute écriture dans la session ouverte.
- OMF : pas d'aperçu (pyaaf2 ne lit que l'AAF).
- Les builds de la CI (workflow) ne sont pas signés : seule la distribution faite avec
  `sign_notarize.sh` l'est.
