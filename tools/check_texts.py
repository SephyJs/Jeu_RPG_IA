from __future__ import annotations

import argparse
import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path


_KEY_RE = re.compile(r"^[a-z0-9_.]+$")
_HAS_LETTER_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
_SIMPLE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./:@+-]+$")


@dataclass
class KeyUse:
    key: str
    path: Path
    line: int


@dataclass
class HardcodedHit:
    path: Path
    line: int
    text: str


def _iter_py_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        if any(part.startswith(".") for part in path.parts):
            continue
        if "__pycache__" in path.parts:
            continue
        files.append(path)
    return files


def _load_lib_keys(libs_root: Path) -> tuple[set[str], dict[str, list[Path]]]:
    keys: set[str] = set()
    sources: dict[str, list[Path]] = {}

    if not libs_root.exists() or not libs_root.is_dir():
        return keys, sources

    for path in sorted(libs_root.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.casefold()
        if suffix == ".json":
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            entries = payload.get("entries") if isinstance(payload, dict) else None
            if not isinstance(entries, dict):
                continue
            for raw_key in entries.keys():
                key = str(raw_key or "").strip()
                if not key:
                    continue
                keys.add(key)
                row = sources.setdefault(key, [])
                if path not in row:
                    row.append(path)
        elif suffix == ".txt":
            rel = path.relative_to(libs_root)
            category = rel.parts[0] if len(rel.parts) > 1 else "misc"
            stem = path.stem
            if "." in stem:
                maybe_name, maybe_lang = stem.rsplit(".", 1)
                if len(maybe_lang) == 2:
                    stem = maybe_name
            key = f"{category}.{stem}".strip(".")
            if key:
                keys.add(key)
                row = sources.setdefault(key, [])
                if path not in row:
                    row.append(path)
    return keys, sources


def _is_docstring(node: ast.Constant, parent: ast.AST | None, grand_parent: ast.AST | None) -> bool:
    if not isinstance(parent, ast.Expr):
        return False
    if not isinstance(grand_parent, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    body = grand_parent.body if isinstance(grand_parent.body, list) else []
    return bool(body) and body[0] is parent


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _looks_like_hardcoded_text(text: str, *, min_len: int) -> bool:
    clean = str(text or "").strip()
    if len(clean) < max(1, min_len):
        return False
    if not _HAS_LETTER_RE.search(clean):
        return False
    if _KEY_RE.fullmatch(clean):
        return False
    if clean.startswith(("http://", "https://")):
        return False
    if clean.startswith(("/", "./", "../")) and " " not in clean:
        return False
    if clean.isupper() and " " not in clean:
        return False
    if _SIMPLE_TOKEN_RE.fullmatch(clean):
        if " " not in clean and any(ch in clean for ch in ("_", "/", ".", ":", "@")):
            return False
    if " " not in clean and ("{" in clean or "}" in clean):
        return False
    if "\\" in clean and " " not in clean:
        return False
    return True


def scan_file(path: Path, *, min_len: int) -> tuple[list[KeyUse], list[HardcodedHit]]:
    try:
        source = path.read_text(encoding="utf-8")
    except Exception:
        return [], []

    try:
        tree = ast.parse(source)
    except Exception:
        return [], []

    parents: dict[ast.AST, ast.AST | None] = {tree: None}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    key_uses: list[KeyUse] = []
    hardcoded: list[HardcodedHit] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in {"_text", "pick", "get_phrases"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    key = str(first.value or "").strip()
                    if key and "." in key and _KEY_RE.fullmatch(key):
                        key_uses.append(
                            KeyUse(
                                key=key,
                                path=path,
                                line=int(getattr(first, "lineno", 0) or 0),
                            )
                        )

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            parent = parents.get(node)
            grand_parent = parents.get(parent) if parent is not None else None
            great_grand_parent = parents.get(grand_parent) if grand_parent is not None else None
            raw = str(node.value)

            if _is_docstring(node, parent, grand_parent):
                continue
            if not _looks_like_hardcoded_text(raw, min_len=min_len):
                continue

            # Exclure les strings deja dans les appels de librairie texte.
            if isinstance(parent, ast.Call) and _call_name(parent.func) in {"_text", "pick", "get_phrases"}:
                continue

            # Exclure logs techniques.
            if isinstance(parent, ast.Call) and _call_name(parent.func) in {
                "debug",
                "info",
                "warning",
                "error",
                "exception",
                "critical",
                "write",
            }:
                continue
            if isinstance(parent, ast.JoinedStr) and isinstance(grand_parent, ast.Call) and _call_name(grand_parent.func) == "write":
                continue
            if isinstance(parent, ast.JoinedStr) and isinstance(grand_parent, ast.FormattedValue) and isinstance(great_grand_parent, ast.Call):
                if _call_name(great_grand_parent.func) == "write":
                    continue

            excerpt = re.sub(r"\s+", " ", raw).strip()
            hardcoded.append(
                HardcodedHit(
                    path=path,
                    line=int(getattr(node, "lineno", 0) or 0),
                    text=excerpt[:220],
                )
            )

    return key_uses, hardcoded


def main() -> None:
    parser = argparse.ArgumentParser(description="Verifie coherence des bibliotheques de texte")
    parser.add_argument("--root", default="app", help="Racine du code Python (defaut: app)")
    parser.add_argument("--libs-root", default="data/libs", help="Racine des bibliotheques texte (defaut: data/libs)")
    parser.add_argument("--min-len", type=int, default=24, help="Longueur minimale pour detecter une string en dur")
    parser.add_argument("--max-report", type=int, default=120, help="Nombre max de lignes detaillees par section")
    parser.add_argument("--strict", action="store_true", help="Code de sortie non-zero si strings en dur ou orphelines")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    libs_root = Path(args.libs_root).resolve()
    files = _iter_py_files(root)

    lib_keys, lib_sources = _load_lib_keys(libs_root)
    uses: list[KeyUse] = []
    hardcoded: list[HardcodedHit] = []

    for path in files:
        file_uses, file_hardcoded = scan_file(path, min_len=max(1, int(args.min_len)))
        uses.extend(file_uses)
        hardcoded.extend(file_hardcoded)

    used_keys = {row.key for row in uses}
    missing = sorted(k for k in used_keys if k not in lib_keys)
    orphans = sorted(k for k in lib_keys if k not in used_keys)

    print("check_texts summary")
    print(f"- code_root: {root}")
    print(f"- libs_root: {libs_root}")
    print(f"- python_files: {len(files)}")
    print(f"- used_keys: {len(used_keys)}")
    print(f"- lib_keys: {len(lib_keys)}")
    print(f"- missing_keys: {len(missing)}")
    print(f"- orphan_keys: {len(orphans)}")
    print(f"- hardcoded_candidates: {len(hardcoded)}")

    if missing:
        print("\nMissing keys (used in code, absent from libs):")
        uses_by_key: dict[str, list[KeyUse]] = {}
        for row in uses:
            uses_by_key.setdefault(row.key, []).append(row)
        for key in missing[: max(1, int(args.max_report))]:
            refs = uses_by_key.get(key, [])
            if refs:
                ref = refs[0]
                print(f"- {key} -> {ref.path}:{ref.line}")
            else:
                print(f"- {key}")

    if orphans:
        print("\nOrphan keys (present in libs, unused in code):")
        for key in orphans[: max(1, int(args.max_report))]:
            src = lib_sources.get(key, [])
            src_txt = str(src[0]) if src else "?"
            print(f"- {key} -> {src_txt}")

    if hardcoded:
        print("\nHardcoded text candidates:")
        for row in hardcoded[: max(1, int(args.max_report))]:
            print(f"- {row.path}:{row.line} :: {row.text}")

    exit_code = 0
    if missing:
        exit_code = 1
    if args.strict and (hardcoded or orphans):
        exit_code = 1
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
