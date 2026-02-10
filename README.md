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

## Mode Telegram (MVP)

Le projet inclut un bot Telegram minimal pour discuter avec le jeu depuis l'app Telegram.

### 1) Configuration
Dans `.env`:
```bash
TELEGRAM_BOT_TOKEN=ton_token_botfather
TELEGRAM_DEFAULT_SLOT=1
TELEGRAM_SLOT_COUNT=3
```

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

## Documentation
- Guide complet: `README.txt`
- Cartes/tiles: `assets/maps/README.txt`
