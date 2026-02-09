import os
from pathlib import Path

from dotenv import load_dotenv


# Charge automatiquement un fichier .env a la racine du projet
# (sans ecraser les variables deja exportees dans le shell).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)

MODEL_NAME = {
    "qwen": "qwen2.5:7b-instruct",
    "dolphin": "huihui_ai/dolphin3-abliterated:8b",
    "mistral": "richardyoung/mistral-7b-instruct-v0.3-abliterated:Q4_K_M",
}

# Clés configurables par variables d'environnement.
# Par défaut:
# - règles JSON: mistral
# - dialogue: dolphin
# - narration: dolphin
RULES_MODEL_KEY = os.getenv("ATARYXIA_RULES_MODEL_KEY", "mistral")
DIALOGUE_MODEL_KEY = os.getenv("ATARYXIA_DIALOGUE_MODEL_KEY", "dolphin")
NARRATION_MODEL_KEY = os.getenv("ATARYXIA_NARRATION_MODEL_KEY", "dolphin")


def model_for(role: str) -> str:
    key_by_role = {
        "rules": RULES_MODEL_KEY,
        "dialogue": DIALOGUE_MODEL_KEY,
        "narration": NARRATION_MODEL_KEY,
    }
    key = key_by_role.get(role, role)
    return MODEL_NAME.get(key, MODEL_NAME["dolphin"])
