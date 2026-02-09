ATARYXIA - Guide rapide (README.txt)

1) Prerequis
- Linux/macOS/Windows
- Python 3.10+
- Ollama installe et accessible en local
- Connexion internet pour telecharger les modeles Ollama (premiere fois)

Liens utiles:
- Ollama (installation): https://ollama.com/download
- Bibliotheque Ollama: https://ollama.com/library
- Doc commandes Ollama: https://github.com/ollama/ollama

2) Installation Python
Depuis la racine du projet:

Linux/macOS:
- python3 -m venv .venv
- source .venv/bin/activate
- pip install -r requirements.txt

Windows PowerShell:
- python -m venv .venv
- .\.venv\Scripts\Activate.ps1
- pip install -r requirements.txt

Option lock exact (reproductible):
- pip install -r requirements-lock.txt

3) Installation des modeles Ollama utilises par defaut
Le projet utilise ces modeles (voir app/gamemaster/models.py):
- rules: richardyoung/mistral-7b-instruct-v0.3-abliterated:Q4_K_M
- dialogue: huihui_ai/dolphin3-abliterated:8b
- narration: huihui_ai/dolphin3-abliterated:8b

Commandes:
- ollama pull richardyoung/mistral-7b-instruct-v0.3-abliterated:Q4_K_M
- ollama pull huihui_ai/dolphin3-abliterated:8b
- ollama pull qwen2.5:7b-instruct

Remarque:
- qwen2.5:7b-instruct est aussi configure dans le code et peut servir de fallback.

4) Lancer Ollama
Si Ollama n'est pas deja lance automatiquement:
- ollama serve

Verification rapide:
- ollama list

5) Lancer le jeu
Depuis la racine du projet:
- python3 -m app.main

Puis ouvrir dans le navigateur:
- http://127.0.0.1:8080

6) Variables d'environnement (optionnel)
Vous pouvez changer quel modele est utilise par role:
- ATARYXIA_RULES_MODEL_KEY
- ATARYXIA_DIALOGUE_MODEL_KEY
- ATARYXIA_NARRATION_MODEL_KEY

Le projet charge automatiquement le fichier `.env` a la racine.
Vous pouvez partir du template fourni:
- cp .env.example .env

Valeurs possibles dans l'etat actuel du code:
- mistral
- dolphin
- qwen

Exemple Linux/macOS:
- export ATARYXIA_RULES_MODEL_KEY=mistral
- export ATARYXIA_DIALOGUE_MODEL_KEY=dolphin
- export ATARYXIA_NARRATION_MODEL_KEY=dolphin

Exemple Windows PowerShell:
- $env:ATARYXIA_RULES_MODEL_KEY = "mistral"
- $env:ATARYXIA_DIALOGUE_MODEL_KEY = "dolphin"
- $env:ATARYXIA_NARRATION_MODEL_KEY = "dolphin"

Priorite:
- les variables exportees dans le shell gardent la priorite
- sinon les valeurs du fichier `.env` sont appliquees

7) Depannage rapide
- Si erreur de connexion Ollama: verifier ollama serve + port 11434.
- Si modele introuvable: refaire ollama pull <nom_modele>.
- Si dependances manquantes: reactiver .venv puis pip install -r requirements.txt.
