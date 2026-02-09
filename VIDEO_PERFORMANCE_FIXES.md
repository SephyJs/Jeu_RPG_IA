# Corrections pour les saccades vidéo 🎬

## Problèmes identifiés et fixes appliqués:

### 1. **Rendu CSS (APPLIQUÉ)**
- Ajout de `will-change: transform` pour préparer le navigateur
- Ajout de `transform: translate3d(0, 0, 0)` pour activer l'accélération matérielle (GPU)
- Accélération matérielle CSS dans `main.py` avec `backface-visibility` et `perspective`

### 2. **Preload des vidéos (APPLIQUÉ)**
- IDLE vidéo: `preload="metadata"` (charge les métadonnées sans tout télécharger)
- Action vidéo: `preload="auto"` (précharge complètement)
- Ajout de `onloadeddata="this.play()"` pour forcer la lecture dès que possible

### 3. **Gestion d'erreurs (APPLIQUÉ)**
- Ajout de `onerror` sur la vidéo action pour détecter les problèmes de chargement

---

## Solutions supplémentaires à considérer:

### A. Vérifier les spécifications vidéo
```bash
# Vérifier les propriétés des vidéos (codec, résolution, fps)
ffprobe assets/videos/idle.mp4 -v error -select_streams v:0 -show_entries stream=codec_name,width,height,r_frame_rate -of default=noprint_wrappers=1
```

**Recommandations:**
- Résolution optimale: 1280x720 (720p) ou 1920x1080
- Codec: `h264` ou `vp9` (meilleure compression)
- FPS: 30 ou 60 (éviter 24 si possible, cause des saccades sur écrans 60Hz)
- Bitrate: 2000-5000 kbps pour 720p

### B. Re-encoder les vidéos
Si les vidéos saccadent toujours:
```bash
# Convertir avec optimisations
ffmpeg -i input.mp4 -c:v libx264 -preset slow -crf 18 -r 30 -pix_fmt yuv420p output.mp4
```

### C. Diminuer la résolution du conteneur
Dans `right_narrator.py`, réduire `height: 55vh` si c'est trop grand (moins de pixels = moins de calculs)

### D. Implémenter un preloader
Ajouter un système de cache des vidéos au démarrage pour éviter les chargements synchrones.

### E. Vérifier les ressources système
- Fermer les apps consommant du CPU
- Vérifier que la GPU accélération est active dans le navigateur
- Sur Linux: `glxinfo | grep "direct rendering"`

---

## Comment tester:
1. Ouvrir le DevTools (F12) → Console
2. Observer les messages d'erreur vidéo
3. Onglet Network pour voir la bande passante
4. Onglet Performance pour profiler les frames

## Fichiers modifiés:
- ✅ `app/ui/components/right_narrator.py` - Optimisations HTML5 vidéo
- ✅ `app/main.py` - CSS d'accélération matérielle
