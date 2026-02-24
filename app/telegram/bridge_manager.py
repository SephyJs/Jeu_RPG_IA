from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
from typing import TextIO

from app.core.save import SaveManager
from app.infra import text_library as _text_library


_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{20,}$")
_TOKEN_SECRET_ENV = "ATARYXIA_TELEGRAM_TOKEN_SECRET"
_TOKEN_SECRET_FILE = ".telegram_token_secret"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(key: str, **vars: object) -> str:
    return _text_library.pick(key, **vars)


@dataclass
class _RunningBot:
    process: subprocess.Popen
    log_handle: TextIO
    started_at: str


class TelegramBridgeManager:
    """Manage per-profile Telegram bot configs and local bot subprocesses."""

    def __init__(
        self,
        *,
        project_dir: str = ".",
        saves_dir: str = "saves",
        slot_count: int = 3,
        python_executable: str | None = None,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        raw_saves = Path(saves_dir)
        if raw_saves.is_absolute():
            self.saves_dir = raw_saves.resolve()
        else:
            self.saves_dir = (self.project_dir / raw_saves).resolve()
        self.save_manager = SaveManager(saves_dir=str(self.saves_dir), slot_count=max(1, int(slot_count)))
        self.slot_count = max(1, int(slot_count))
        self.python_executable = str(python_executable or sys.executable or "python3")
        self._running: dict[str, _RunningBot] = {}
        self._lock = threading.Lock()
        self._token_secret = self._load_token_secret()

    def normalize_profile_key(self, profile_key: str) -> str:
        return self.save_manager.normalize_profile_id(profile_key)

    def validate_token(self, token: str) -> bool:
        return bool(_TOKEN_RE.match(str(token or "").strip()))

    def status(self, profile_key: str) -> dict:
        key = self.normalize_profile_key(profile_key)
        config = self.load_config(key)
        running = self._running_status(key)
        return {
            "profile_key": key,
            "has_token": bool(config.get("token")),
            "token_hint": str(config.get("token_mask") or ""),
            "slot": self._clamp_slot(config.get("slot", 1)),
            "running": bool(running.get("running")),
            "pid": running.get("pid"),
            "log_path": str(self._log_path(key)),
            "updated_at": str(config.get("updated_at") or ""),
        }

    def configure(
        self,
        *,
        profile_key: str,
        token: str,
        profile_name: str = "",
        slot: int = 1,
    ) -> None:
        key = self.normalize_profile_key(profile_key)
        clean_token = str(token or "").strip()
        if not self.validate_token(clean_token):
            raise ValueError(_text("error.telegram.token_invalid"))

        payload = self.load_config(key)
        payload["profile_key"] = key
        payload["profile_name"] = str(profile_name or "").strip()[:80]
        payload["slot"] = self._clamp_slot(slot)
        payload["token"] = clean_token
        payload["updated_at"] = _now_iso()
        self._write_config_payload(key, payload)

    def configure_and_start(
        self,
        *,
        profile_key: str,
        token: str,
        profile_name: str = "",
        slot: int = 1,
    ) -> tuple[bool, str]:
        self.configure(profile_key=profile_key, token=token, profile_name=profile_name, slot=slot)
        self.stop(profile_key=profile_key)
        return self.start(profile_key=profile_key)

    def set_slot(self, *, profile_key: str, slot: int) -> None:
        key = self.normalize_profile_key(profile_key)
        payload = self.load_config(key)
        payload["profile_key"] = key
        payload["slot"] = self._clamp_slot(slot)
        payload["updated_at"] = _now_iso()
        self._write_config_payload(key, payload)

    def clear_config(self, *, profile_key: str) -> None:
        key = self.normalize_profile_key(profile_key)
        path = self._config_path(key)
        if path.exists():
            path.unlink()

    def load_config(self, profile_key: str) -> dict:
        key = self.normalize_profile_key(profile_key)
        path = self._config_path(key)
        if not path.exists():
            return {
                "profile_key": key,
                "profile_name": "",
                "slot": 1,
                "token": "",
                "token_mask": "",
                "token_enc": "",
                "updated_at": "",
            }
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        token_enc = str(raw.get("token_enc") or "").strip()
        token_plain = ""
        if token_enc:
            token_plain = self._decrypt_token(key, token_enc)
        if not token_plain:
            token_plain = str(raw.get("token") or "").strip()

        token_mask = str(raw.get("token_mask") or "").strip()
        if not token_mask and token_plain:
            token_mask = self._mask_token(token_plain)

        return {
            "profile_key": key,
            "profile_name": str(raw.get("profile_name") or "").strip()[:80],
            "slot": self._clamp_slot(raw.get("slot", 1)),
            "token": token_plain,
            "token_mask": token_mask,
            "token_enc": token_enc,
            "updated_at": str(raw.get("updated_at") or ""),
        }

    def start(self, *, profile_key: str) -> tuple[bool, str]:
        key = self.normalize_profile_key(profile_key)
        config = self.load_config(key)
        token = str(config.get("token") or "").strip()
        if not self.validate_token(token):
            return False, _text("error.telegram.no_token_configured")

        with self._lock:
            self._cleanup_dead_locked(key)
            current = self._running.get(key)
            if current is not None and current.process.poll() is None:
                return True, _text("system.telegram.bot_already_running", pid=current.process.pid)

            env = os.environ.copy()
            env["TELEGRAM_BOT_TOKEN"] = token
            env["TELEGRAM_PROFILE_KEY"] = key
            env["TELEGRAM_PROFILE_NAME"] = str(config.get("profile_name") or "").strip()
            env["TELEGRAM_DEFAULT_SLOT"] = str(self._clamp_slot(config.get("slot", 1)))
            env["TELEGRAM_SLOT_COUNT"] = str(self.slot_count)

            log_path = self._log_path(key)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = log_path.open("a", encoding="utf-8")
            log_handle.write(f"\n[{_now_iso()}] start bot for profile={key}\n")
            log_handle.flush()

            try:
                proc = subprocess.Popen(
                    [self.python_executable, "-m", "app.telegram.bot"],
                    cwd=str(self.project_dir),
                    env=env,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                )
            except Exception as e:
                try:
                    log_handle.close()
                except Exception:
                    pass
                return False, _text("error.telegram.bot_start_failed", error=e)

            self._running[key] = _RunningBot(
                process=proc,
                log_handle=log_handle,
                started_at=_now_iso(),
            )
            return True, _text("system.telegram.bot_started", pid=proc.pid)

    def stop(self, *, profile_key: str) -> tuple[bool, str]:
        key = self.normalize_profile_key(profile_key)
        with self._lock:
            runner = self._running.get(key)
            if runner is None:
                return False, _text("error.telegram.bot_not_running")

            proc = runner.process
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=8)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

            pid = proc.pid
            self._close_runner_locked(key)
            return True, _text("system.telegram.bot_stopped", pid=pid)

    def _running_status(self, key: str) -> dict:
        with self._lock:
            self._cleanup_dead_locked(key)
            runner = self._running.get(key)
            if runner is None:
                return {"running": False, "pid": None}
            if runner.process.poll() is None:
                return {"running": True, "pid": runner.process.pid}
            return {"running": False, "pid": None}

    def _cleanup_dead_locked(self, key: str) -> None:
        runner = self._running.get(key)
        if runner is None:
            return
        if runner.process.poll() is None:
            return
        self._close_runner_locked(key)

    def _close_runner_locked(self, key: str) -> None:
        runner = self._running.pop(key, None)
        if runner is None:
            return
        try:
            runner.log_handle.close()
        except Exception:
            pass

    def _write_config_payload(self, profile_key: str, payload: dict) -> None:
        key = self.normalize_profile_key(profile_key)
        token_plain = str(payload.get("token") or "").strip()
        token_enc = str(payload.get("token_enc") or "").strip()
        if token_plain and not token_enc:
            token_enc = self._encrypt_token(key, token_plain)

        token_mask = str(payload.get("token_mask") or "").strip()
        if not token_mask and token_plain:
            token_mask = self._mask_token(token_plain)

        to_save = {
            "profile_key": key,
            "profile_name": str(payload.get("profile_name") or "").strip()[:80],
            "slot": self._clamp_slot(payload.get("slot", 1)),
            "updated_at": str(payload.get("updated_at") or _now_iso()),
        }
        if token_mask:
            to_save["token_mask"] = token_mask
        if token_enc:
            to_save["token_enc"] = token_enc
        elif token_plain:
            # Fallback legacy (si chiffrement indisponible).
            to_save["token"] = token_plain

        self._config_path(key).write_text(json.dumps(to_save, ensure_ascii=False, indent=2), encoding="utf-8")

    def _secret_path(self) -> Path:
        return self.saves_dir / _TOKEN_SECRET_FILE

    def _load_token_secret(self) -> bytes:
        env_secret = str(os.getenv(_TOKEN_SECRET_ENV) or "").strip()
        if env_secret:
            return env_secret.encode("utf-8")

        path = self._secret_path()
        try:
            if path.exists():
                raw = path.read_text(encoding="utf-8").strip()
                if raw:
                    return raw.encode("utf-8")
        except Exception:
            pass

        generated = secrets.token_hex(32)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(generated, encoding="utf-8")
            os.chmod(path, 0o600)
        except Exception:
            pass
        return generated.encode("utf-8")

    def _derive_token_key(self, profile_key: str) -> bytes:
        key = self.normalize_profile_key(profile_key).encode("utf-8")
        return hashlib.pbkdf2_hmac("sha256", self._token_secret, key, 120_000, dklen=32)

    def _keystream(self, key: bytes, nonce: bytes, length: int) -> bytes:
        out = bytearray()
        counter = 0
        while len(out) < length:
            block = hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest()
            out.extend(block)
            counter += 1
        return bytes(out[:length])

    def _encrypt_token(self, profile_key: str, token: str) -> str:
        plain = str(token or "").strip().encode("utf-8")
        if not plain:
            return ""

        key = self._derive_token_key(profile_key)
        nonce = secrets.token_bytes(16)
        stream = self._keystream(key, nonce, len(plain))
        cipher = bytes(a ^ b for a, b in zip(plain, stream))
        mac = hmac.new(key, nonce + cipher, digestmod=hashlib.sha256).digest()
        return "v1:{nonce}:{cipher}:{mac}".format(
            nonce=base64.urlsafe_b64encode(nonce).decode("ascii"),
            cipher=base64.urlsafe_b64encode(cipher).decode("ascii"),
            mac=base64.urlsafe_b64encode(mac).decode("ascii"),
        )

    def _decrypt_token(self, profile_key: str, token_enc: str) -> str:
        raw = str(token_enc or "").strip()
        if not raw:
            return ""
        parts = raw.split(":", 3)
        if len(parts) != 4 or parts[0] != "v1":
            return ""

        try:
            nonce = base64.urlsafe_b64decode(parts[1].encode("ascii"))
            cipher = base64.urlsafe_b64decode(parts[2].encode("ascii"))
            mac = base64.urlsafe_b64decode(parts[3].encode("ascii"))
        except Exception:
            return ""

        key = self._derive_token_key(profile_key)
        expected_mac = hmac.new(key, nonce + cipher, digestmod=hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected_mac):
            return ""

        stream = self._keystream(key, nonce, len(cipher))
        plain = bytes(a ^ b for a, b in zip(cipher, stream))
        try:
            return plain.decode("utf-8").strip()
        except UnicodeDecodeError:
            return ""

    def _mask_token(self, token: str) -> str:
        raw = str(token or "").strip()
        if not raw:
            return ""
        if len(raw) <= 10:
            return "*" * len(raw)
        return f"{raw[:4]}...{raw[-4:]}"

    def _profile_dir(self, profile_key: str) -> Path:
        key = self.normalize_profile_key(profile_key)
        base = self.saves_dir / "profiles" / key
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _config_path(self, profile_key: str) -> Path:
        return self._profile_dir(profile_key) / "telegram_bridge.json"

    def _log_path(self, profile_key: str) -> Path:
        return self._profile_dir(profile_key) / "telegram_bot.log"

    def _clamp_slot(self, value: object) -> int:
        try:
            slot = int(value)
        except (TypeError, ValueError):
            slot = 1
        if slot < 1:
            return 1
        if slot > self.slot_count:
            return self.slot_count
        return slot
