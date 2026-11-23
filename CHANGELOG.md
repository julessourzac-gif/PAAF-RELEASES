# Historique des versions

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
