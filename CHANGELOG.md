# Historique des versions

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
