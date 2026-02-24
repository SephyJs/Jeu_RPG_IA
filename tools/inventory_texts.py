from __future__ import annotations

import argparse
import ast
import re
from dataclasses import dataclass
from pathlib import Path


_TEXT_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
_SIMPLE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./:@+-]+$")
_SPACES_RE = re.compile(r"\s+")


@dataclass
class TextHit:
    path: Path
    line: int
    context: str
    text: str
    category: str
    proposed_key: str
    proposed_file: str


def _safe_slug(text: str, max_words: int = 5) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text.casefold())
    if not words:
        return "text"
    return "_".join(words[:max_words])[:56] or "text"


def _infer_category(path: Path, context: str, text: str) -> tuple[str, str]:
    p = str(path).replace("\\", "/").casefold()
    c = context.casefold()
    t = text.casefold()

    if "/ui/" in p:
        return "ui", "data/libs/ui/labels.fr.json"
    if "/telegram/" in p:
        if any(x in t for x in ("bouton", "action", "menu", "retour", "inventaire")):
            return "ui", "data/libs/ui/labels.fr.json"
        return "system", "data/libs/system/messages.fr.json"
    if "npc" in p or "npc" in c:
        return "npc", "data/libs/npc/greetings.fr.json"
    if any(x in p for x in ("story", "narration", "dungeon_combat", "right_narrator")):
        return "narration", "data/libs/narration/ambience.fr.json"
    if any(x in c for x in ("error", "exception")):
        return "system", "data/libs/system/errors.fr.json"
    if any(x in t for x in ("erreur", "invalide", "impossible", "introuvable")):
        return "system", "data/libs/system/errors.fr.json"
    return "system", "data/libs/system/messages.fr.json"


def _should_keep(text: str) -> bool:
    raw = str(text)
    s = raw.strip()
    if not s:
        return False
    if len(s) < 6:
        return False
    if not _TEXT_RE.search(s):
        return False
    if s.startswith(("http://", "https://")):
        return False
    if s.startswith(("/", "./", "../")) and " " not in s:
        return False
    if _SIMPLE_TOKEN_RE.fullmatch(s):
        # garde seulement les tokens simples qui ressemblent a une phrase (contiennent au moins 2 mots)
        if "_" in s or "/" in s or "." in s or ":" in s:
            return False
    if s.isupper() and " " not in s:
        return False
    if s.count(" ") == 0 and len(s) < 16:
        return False
    return True


def _context_chain(tree: ast.AST) -> dict[ast.AST, list[str]]:
    contexts: dict[ast.AST, list[str]] = {tree: []}

    def walk(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            child_stack = stack
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                child_stack = stack + [child.name]
                contexts[child] = child_stack
            else:
                contexts[child] = stack
            walk(child, child_stack)

    walk(tree, [])
    return contexts


def collect_hits(py_path: Path) -> list[TextHit]:
    try:
        source = py_path.read_text(encoding="utf-8")
    except Exception:
        return []

    try:
        tree = ast.parse(source)
    except Exception:
        return []

    contexts = _context_chain(tree)
    hits: list[TextHit] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        text = node.value
        if not _should_keep(text):
            continue

        stack = contexts.get(node, [])
        context = ".".join(stack) if stack else "<module>"
        category, proposed_file = _infer_category(py_path, context, text)
        key = f"{category}.{_safe_slug(context, max_words=3)}.{_safe_slug(text)}"
        clean_text = _SPACES_RE.sub(" ", text).strip()
        hits.append(
            TextHit(
                path=py_path,
                line=int(getattr(node, "lineno", 0) or 0),
                context=context,
                text=clean_text,
                category=category,
                proposed_key=key,
                proposed_file=proposed_file,
            )
        )

    hits.sort(key=lambda h: (str(h.path), h.line, h.proposed_key))
    return hits


def build_report(root: Path, out: Path) -> tuple[int, int]:
    files = sorted(root.rglob("*.py"))
    all_hits: list[TextHit] = []
    for path in files:
        if any(part.startswith(".") for part in path.parts):
            continue
        if "__pycache__" in path.parts:
            continue
        all_hits.extend(collect_hits(path))

    out.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Inventaire Textes En Dur\n")
    lines.append("Rapport genere automatiquement.\n")
    lines.append(f"- Racine scannee: `{root}`")
    lines.append(f"- Fichiers scannes: {len(files)}")
    lines.append(f"- Occurrences candidates: {len(all_hits)}\n")

    lines.append("| Fichier | Fonction/Classe | Extrait | Categorie | Cle proposee | Fichier cible |")
    lines.append("|---|---|---|---|---|---|")
    for hit in all_hits:
        excerpt = hit.text.replace("|", "\\|")
        if len(excerpt) > 120:
            excerpt = excerpt[:117] + "..."
        lines.append(
            "| "
            + f"{hit.path}:{hit.line}"
            + " | "
            + f"`{hit.context}`"
            + " | "
            + excerpt
            + " | "
            + hit.category
            + " | "
            + f"`{hit.proposed_key}`"
            + " | "
            + f"`{hit.proposed_file}`"
            + " |"
        )

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(files), len(all_hits)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventorie les textes en dur dans le code Python")
    parser.add_argument("--root", default="app", help="Racine a scanner (defaut: app)")
    parser.add_argument("--out", default="data/libs/INVENTORY_REPORT.md", help="Fichier de sortie")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    out = Path(args.out).resolve()
    files, hits = build_report(root, out)
    print(f"Inventory done: files={files} hits={hits} out={out}")


if __name__ == "__main__":
    main()
