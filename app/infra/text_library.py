from __future__ import annotations

import json
import logging
import random
import re
from pathlib import Path
from threading import RLock
from typing import Any


_LOG = logging.getLogger(__name__)
_DEFAULT_ROOT = Path("data/libs")
_VAR_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")

_CACHE_LOCK = RLock()
_CACHE: dict[str, Any] = {
    "loaded": False,
    "root": str(_DEFAULT_ROOT),
    "langs": {},
}


def _safe_lang_from_filename(path: Path) -> str:
    name = path.name
    # labels.fr.json -> fr
    # labels.json -> fr (par defaut)
    parts = name.split(".")
    if len(parts) >= 3 and len(parts[-2]) == 2:
        return str(parts[-2]).strip().casefold() or "fr"
    return "fr"


def _safe_category_from_path(path: Path, root: Path) -> str:
    try:
        rel = path.relative_to(root)
    except Exception:
        return "misc"
    parts = rel.parts
    if len(parts) <= 1:
        return "misc"
    return str(parts[0]).strip().casefold() or "misc"


def _normalize_phrase_list(raw: object) -> list[str]:
    if isinstance(raw, str):
        txt = raw.strip()
        return [txt] if txt else []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for row in raw:
        if not isinstance(row, str):
            continue
        txt = row.strip()
        if txt:
            out.append(txt)
    return out


def _merge_entry(store: dict[str, Any], *, lang: str, category: str, key: str, phrases: list[str], source: str, version: int) -> None:
    if not phrases:
        return
    lang_map = store.setdefault(lang, {"keys": {}, "categories": {}, "sources": {}, "versions": {}})
    keys_map: dict[str, list[str]] = lang_map.setdefault("keys", {})
    categories_map: dict[str, dict[str, list[str]]] = lang_map.setdefault("categories", {})
    sources_map: dict[str, list[str]] = lang_map.setdefault("sources", {})
    versions_map: dict[str, int] = lang_map.setdefault("versions", {})

    current = keys_map.get(key)
    if isinstance(current, list):
        merged = list(current)
        for phrase in phrases:
            if phrase not in merged:
                merged.append(phrase)
        keys_map[key] = merged
    else:
        keys_map[key] = list(phrases)

    cat_rows = categories_map.setdefault(category, {})
    cat_current = cat_rows.get(key)
    if isinstance(cat_current, list):
        merged_cat = list(cat_current)
        for phrase in phrases:
            if phrase not in merged_cat:
                merged_cat.append(phrase)
        cat_rows[key] = merged_cat
    else:
        cat_rows[key] = list(phrases)

    src_rows = sources_map.setdefault(key, [])
    if source not in src_rows:
        src_rows.append(source)
    versions_map[key] = max(int(versions_map.get(key, 1)), int(version))


def _load_json(path: Path, root: Path, store: dict[str, Any]) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _LOG.warning("text_library: JSON invalide %s (%s)", path, exc)
        return

    if not isinstance(payload, dict):
        _LOG.warning("text_library: contenu inattendu (non-objet) dans %s", path)
        return

    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    entries = payload.get("entries") if isinstance(payload.get("entries"), dict) else {}

    lang = str(meta.get("lang") or _safe_lang_from_filename(path)).strip().casefold() or "fr"
    category = _safe_category_from_path(path, root)
    version = 1
    try:
        version = int(meta.get("version") or 1)
    except (TypeError, ValueError):
        version = 1

    for raw_key, raw_value in entries.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        phrases = _normalize_phrase_list(raw_value)
        _merge_entry(
            store,
            lang=lang,
            category=category,
            key=key,
            phrases=phrases,
            source=str(path),
            version=version,
        )


def _load_txt(path: Path, root: Path, store: dict[str, Any]) -> None:
    try:
        lines = [row.strip() for row in path.read_text(encoding="utf-8").splitlines()]
    except Exception as exc:
        _LOG.warning("text_library: TXT invalide %s (%s)", path, exc)
        return

    phrases = [row for row in lines if row and not row.startswith("#")]
    if not phrases:
        return

    lang = _safe_lang_from_filename(path)
    category = _safe_category_from_path(path, root)

    stem = path.stem
    # foo.fr.txt -> foo
    if "." in stem:
        maybe_name, maybe_lang = stem.rsplit(".", 1)
        if len(maybe_lang) == 2:
            stem = maybe_name
    key = f"{category}.{stem}".strip(".")

    _merge_entry(
        store,
        lang=lang,
        category=category,
        key=key,
        phrases=phrases,
        source=str(path),
        version=1,
    )


def load_all_libs(root: str = "data/libs") -> dict[str, Any]:
    root_path = Path(str(root or "data/libs")).resolve()
    with _CACHE_LOCK:
        store: dict[str, Any] = {}
        if not root_path.exists() or not root_path.is_dir():
            _LOG.warning("text_library: dossier introuvable %s", root_path)
            _CACHE["loaded"] = True
            _CACHE["root"] = str(root_path)
            _CACHE["langs"] = {}
            return {"root": str(root_path), "langs": 0, "keys": 0}

        for path in sorted(root_path.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.casefold() == ".json":
                _load_json(path, root_path, store)
            elif path.suffix.casefold() == ".txt":
                _load_txt(path, root_path, store)

        _CACHE["loaded"] = True
        _CACHE["root"] = str(root_path)
        _CACHE["langs"] = store

        key_count = 0
        for lang_map in store.values():
            keys_map = lang_map.get("keys") if isinstance(lang_map, dict) else {}
            if isinstance(keys_map, dict):
                key_count += len(keys_map)
        return {"root": str(root_path), "langs": len(store), "keys": key_count}


def reload_libs(root: str = "data/libs") -> dict[str, Any]:
    with _CACHE_LOCK:
        _CACHE["loaded"] = False
        _CACHE["langs"] = {}
    return load_all_libs(root=root)


def _ensure_loaded() -> None:
    with _CACHE_LOCK:
        if bool(_CACHE.get("loaded", False)):
            return
    load_all_libs(root=str(_CACHE.get("root") or "data/libs"))


def get_phrases(key: str, category: str | None = None, lang: str = "fr") -> list[str]:
    _ensure_loaded()
    clean_key = str(key or "").strip()
    if not clean_key:
        return []

    clean_lang = str(lang or "fr").strip().casefold() or "fr"
    clean_category = str(category or "").strip().casefold()

    with _CACHE_LOCK:
        langs_map = _CACHE.get("langs") if isinstance(_CACHE.get("langs"), dict) else {}
        lang_map = langs_map.get(clean_lang) if isinstance(langs_map.get(clean_lang), dict) else None
        if lang_map is None:
            lang_map = langs_map.get("fr") if isinstance(langs_map.get("fr"), dict) else None
        if lang_map is None:
            return []

        if clean_category:
            categories = lang_map.get("categories") if isinstance(lang_map.get("categories"), dict) else {}
            cat_map = categories.get(clean_category) if isinstance(categories.get(clean_category), dict) else {}
            rows = cat_map.get(clean_key)
            if isinstance(rows, list):
                return [str(x) for x in rows if isinstance(x, str)]
            return []

        keys_map = lang_map.get("keys") if isinstance(lang_map.get("keys"), dict) else {}
        rows = keys_map.get(clean_key)
        if isinstance(rows, list):
            return [str(x) for x in rows if isinstance(x, str)]
        return []


def format_vars(text: str, **vars: object) -> str:
    raw = str(text or "")

    def _replace(match: re.Match[str]) -> str:
        key = str(match.group(1) or "").strip()
        if not key:
            return match.group(0)
        if key in vars:
            value = vars.get(key)
            return str(value if value is not None else "")
        return match.group(0)

    return _VAR_RE.sub(_replace, raw)


def pick(
    key: str,
    fallback: str | list[str] | None = None,
    *,
    category: str | None = None,
    lang: str = "fr",
    **vars: object,
) -> str:
    phrases = get_phrases(key=key, category=category, lang=lang)
    if phrases:
        choice = random.choice(phrases)
        return format_vars(choice, **vars)

    if isinstance(fallback, list):
        fallback_rows = [str(x).strip() for x in fallback if isinstance(x, str) and str(x).strip()]
        if fallback_rows:
            return format_vars(random.choice(fallback_rows), **vars)
    elif isinstance(fallback, str) and fallback.strip():
        return format_vars(fallback.strip(), **vars)

    _LOG.warning("text_library: cle absente '%s' (lang=%s, category=%s)", key, lang, category or "")
    return format_vars(str(key or ""), **vars)


def list_keys(*, lang: str = "fr") -> set[str]:
    _ensure_loaded()
    clean_lang = str(lang or "fr").strip().casefold() or "fr"
    with _CACHE_LOCK:
        langs_map = _CACHE.get("langs") if isinstance(_CACHE.get("langs"), dict) else {}
        lang_map = langs_map.get(clean_lang) if isinstance(langs_map.get(clean_lang), dict) else {}
        keys_map = lang_map.get("keys") if isinstance(lang_map.get("keys"), dict) else {}
        return {str(k) for k in keys_map.keys() if isinstance(k, str) and str(k).strip()}
