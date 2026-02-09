# Jeu - Lancement rapide

## Prérequis
- Python 3.10+ recommandé
- `venv` disponible

## Installation
```bash
python3 -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
```

## Démarrer le projet
### Windows PowerShell
```powershell
.\start.ps1
```

### Linux / macOS (ou Git Bash)
```bash
./start.sh
```

## Notes
- Sur Ubuntu, l’exécutable est souvent `python3`.
- Le script `start.sh` utilise `python3` si disponible.
## Troubleshooting
- Si `python` n'est pas reconnu sur Ubuntu/Debian, utilise `python3`.
- Si `pip` n'est pas reconnu, utilise `python -m pip` (ou `python3 -m pip`).
- Si l'activation PowerShell est bloquee: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` puis `./.venv/Scripts/Activate.ps1`.
- Si `.venv` manque, recree-le: `python -m venv .venv` (ou `python3 -m venv .venv`).
- Si des dependances manquent, reinstalle: `python -m pip install -r requirements.txt`.
