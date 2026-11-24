# Historique des versions

## 1.4
- Nouvelle interface (maquettes 1a « Liste d'abord ») : en-tête avec pastille de session,
  titre d'état (vide, prêt, import en cours, bilan), barre de placement à contrôle segmenté,
  timeline en trois pistes (vidéo, AAF / OMF, audio des vidéos) avec zoom, tableau du plan
  avec colonne d'état, journal pendant et après l'import.
- Thèmes sombre et clair (icône soleil), interface en français ou en anglais ; choix enregistrés.
- Panneau Réglages (⌘,) : options AAF / OMF, vidéo, liste et application.
- Tableau : glisser une ligne pour réordonner, ⌥↑ / ⌥↓, ⌫, ↩ ou double-clic pour le point
  d'entrée, clic droit pour le menu ; clic sur un en-tête pour trier.
- Inspecteur et pistes de la session dans une fenêtre à part (⌘I, ou clic sur la session).
- Après un import : « Reprendre » relance les fichiers en échec ou non traités ; export du journal.
- Les fichiers introuvables sont écartés de l'import au lieu d'échouer un à un.
- Vérification facultative des mises à jour au lancement (releases GitHub).
- AAF dans la timeline : la piste AAF / OMF se déplie (▸) en une sous-piste par piste commune
  (« Audio 1 »…). L'« Audio 1 » de chaque AAF va sur une seule piste Pro Tools (option « Pistes
  communes », activée par défaut). Clic sur un bloc : importer ou non (⌘ / ⇧ : plusieurs) ;
  clic sur le nom d'une sous-piste : toute la sous-piste ; glisser un bloc sur une autre
  sous-piste : il la rejoint. Après l'import, PAAF coupe / colle au même TC, renomme ou retire
  les pistes (liens de sélection et mode Slip activés le temps de l'opération, puis rétablis).
- Mode « Versions » (changement de montage, étape 1) : compare deux AAF ou EDL (CMX3600),
  ancienne et nouvelle version. Tous les clips image et son servent de repères (média source +
  TC source) : liste des passages inchangés, déplacés, nouveaux et supprimés, vue des deux
  versions reliées, rapport CSV, marqueurs posés dans la session.
- Versions, étape 2 (validée sur un décalage global ; à confirmer sur des montages plus remaniés) : « Appliquer à une copie » enregistre une copie de la session, recopie
  chaque morceau gardé (toutes pistes, automation comprise) à sa nouvelle place, range les
  passages supprimés après la fin, pose des marqueurs et importe la nouvelle version sur des
  pistes guides pour compléter les passages nouveaux.
- Historique : annuler / rétablir les modifications du plan (⌘Z / ⇧⌘Z, boutons ↶ ↷, menu
  Édition > Historique pour revenir à une étape).
- Défilement au trackpad et à la molette dans la liste et la timeline ; ⌥ + défilement : zoom.
- Nouvelle icône d'application, d'après le logo PAAF.
- Journal : pistes créées par chaque import AAF, avec alerte si aucune piste audio n'est créée.


## 1.3
- L'application s'appelle désormais **PAAF**, avec un nouveau logo.
- Nouvelle interface : thème sombre, état de la session en en-tête, inspecteur, pistes de la
  session, journal en onglets, point d'entrée modifiable directement dans la liste.
- Correction : « incompatibilities between selected clips and destination track type or
  format » à l'import de l'audio des vidéos. Pro Tools 2025+ attend la position au nouveau
  format (`TimelineLocation`) ; les pistes communes sont aussi choisies selon leur format
  (mono / stéréo).
- AAF et vidéo de même nom calés automatiquement au même TC.
- Inspecteur AAF : pistes, clips et position dans Pro Tools, métadonnées des médias audio
  (fréquence, résolution, canaux, TC d'origine, Scene/Take, embarqué ou lié), paires stéréo.
- AAF : option « Fusionner dans les pistes de même nom de la session » (Match Tracks).
- Audio des vidéos : détection des stéréo à canaux identiques ou à un seul canal → mono ;
  flux audio multiples gérés ; conversion ffmpeg dès l'ajout des fichiers (préconversion),
  bien plus rapide (image ignorée), WAV déjà convertis réutilisés.
- Dépendance ajoutée : pyaaf2 (environnement réinstallé au premier lancement).


## 1.2
- Audio des vidéos : placement robuste des WAV extraits par ffmpeg (essai de chaque clip
  renvoyé par Pro Tools jusqu'à celui qui correspond au format de la piste).
- Repli en deux temps : WAV converti par Pro Tools, puis extraction par Pro Tools depuis la vidéo.
- WAV écrits en BWF (chunk bext).
- L'environnement Python n'est plus réinstallé à chaque version de l'application.

## 1.1
- Application autonome : installation des dépendances au premier lancement
  (~/Library/Application Support/Import Pro Tools), icône dédiée.
- Extraction de l'audio des vidéos avec ffmpeg, en parallèle, au format de la session.
- Audio des vidéos sur des pistes communes (« AUDIO VIDEO », ou une piste mono par canal).
- Glisser-déposer depuis le Finder (fenêtre et icône de l'app).

## 1.0
- Interface graphique : liste de fichiers, placement fixe / bout à bout / manuel, plans CSV.
- Import AAF/OMF via PTSL (Import Session Data, Map start timecode to, clips et médias).
- Import vidéo via PTSL (Main Video Track, Spot au TC ; piste vidéo créée si absente).
