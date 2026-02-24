# Jeu_RPG_IA

## Description breve
Jeu de role narratif assiste par IA, avec interface web en Python (NiceGUI).
Le joueur interagit avec des PNJ, des quetes, des competences, du loot et une economie dynamique.
Les dialogues et certaines generations de contenu sont pilotes via Ollama (modeles locaux).

## Apercu
![Apercu du projet](assets/img_promo.png)

## Statut du projet
Projet en cours de developpement (WIP).

Ce depot evolue regulierement:
- des fonctionnalites sont encore en construction
- l'equilibrage est en cours
- des bugs et changements de compatibilite peuvent apparaitre

## Lancement rapide

### Prerequis
- Python 3.10+ recommande
- `venv` disponible
- Ollama installe et lance

### Installation
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Demarrage
```bash
python3 -m app.main
```

Puis ouvrir: `http://127.0.0.1:8080`

Pages principales:
- `http://127.0.0.1:8080/game`: jeu principal
- `http://127.0.0.1:8080/studio`: editeur de contenu (PNJ, monstres, items, marchands, carte monde)

Commandes chat utiles (dans `/game`):
- `/quest list` : afficher les quetes actives
- `/quest choose <quest_id> <option_id>` : choisir une branche de quete
- `/craft list` : lister les recettes de craft
- `/craft <recipe_id> [qty]` : fabriquer un objet
- `/story` : afficher l'etat de l'arc principal en chapitres

### Tests
```bash
python3 -m pytest -q
```

## Mode Telegram (MVP)

Le projet inclut un bot Telegram minimal pour discuter avec le jeu depuis l'app Telegram.

### 1) Configuration
Dans `.env`:
```bash
TELEGRAM_BOT_TOKEN=ton_token_botfather
TELEGRAM_DEFAULT_SLOT=1
TELEGRAM_SLOT_COUNT=3
TELEGRAM_PROFILE_KEY=
TELEGRAM_PROFILE_NAME=
ATARYXIA_TELEGRAM_TOKEN_SECRET=
```
`ATARYXIA_TELEGRAM_TOKEN_SECRET` est optionnel mais recommande: il sert a chiffrer les tokens Telegram stockes localement.

### 2) Lancer le bot
```bash
python3 -m app.telegram.bot
```

### 3) Boutons minimaux
- `👥 PNJ`: choisir le PNJ actif du lieu courant
- `🧭 Deplacer`: ouvrir la liste des trajets disponibles depuis le lieu courant
- `📍 Statut`: afficher lieu, PNJ actif, or, niveau, heure du monde
- `💾 Sauver`: sauvegarde immediate

Le bot accepte aussi du texte libre pour dialoguer avec le PNJ actif et propose des boutons inline
`Confirmer/Annuler` quand une transaction est en attente.

### 4) Reprendre une partie existante
- `TELEGRAM_PROFILE_KEY=sephy` permet d'utiliser la meme sauvegarde que l'UI web.
- Commandes utiles dans Telegram:
  - `/profiles` liste les profils detectes
  - `/useprofile <profil_key>` bascule de profil
  - `/slot <n>` change de slot actif
  - `/creation` affiche l'etat de creation du personnage

### 5) Lier un token depuis le chat du jeu (UI web)
Depuis la zone de dialogue du jeu, un joueur connecte peut taper:
- `/telegram <TOKEN_BOTFATHER>`: enregistre le token pour son profil et lance le bot
- `/telegram status`: etat du bot lie a ce profil
- `/telegram start`: demarre le bot avec la config deja enregistree
- `/telegram stop`: arrete le bot pour ce profil
- `/telegram slot <n>`: choisit le slot utilise par le bot
- `/telegram clear`: supprime la config Telegram du profil

## Documentation
- Guide complet: `README.txt`
- Cartes/tiles: `assets/maps/README.txt`
