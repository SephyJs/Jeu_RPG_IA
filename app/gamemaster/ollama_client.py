from __future__ import annotations

import asyncio
import time

import httpx


_RETRYABLE_HTTP_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        *,
        timeout_seconds: float = 180.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.35,
        circuit_breaker_failures: int = 4,
        circuit_breaker_cooldown_seconds: float = 8.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_seconds = max(0.05, float(retry_backoff_seconds))
        self.circuit_breaker_failures = max(1, int(circuit_breaker_failures))
        self.circuit_breaker_cooldown_seconds = max(1.0, float(circuit_breaker_cooldown_seconds))
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._health_cache_value = False
        self._health_cache_until = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if isinstance(self._client, httpx.AsyncClient):
            return self._client

        async with self._client_lock:
            if not isinstance(self._client, httpx.AsyncClient):
                self._client = httpx.AsyncClient(timeout=self.timeout_seconds)
        return self._client

    async def aclose(self) -> None:
        if not isinstance(self._client, httpx.AsyncClient):
            return
        await self._client.aclose()
        self._client = None

    async def _record_success(self) -> None:
        async with self._state_lock:
            self._consecutive_failures = 0
            self._circuit_open_until = 0.0
            self._health_cache_value = True
            self._health_cache_until = time.monotonic() + 2.0

    async def _record_failure(self) -> None:
        async with self._state_lock:
            self._consecutive_failures += 1
            self._health_cache_value = False
            self._health_cache_until = time.monotonic() + 1.5
            if self._consecutive_failures >= self.circuit_breaker_failures:
                self._circuit_open_until = time.monotonic() + self.circuit_breaker_cooldown_seconds

    async def is_available(
        self,
        *,
        cache_ttl_seconds: float = 3.0,
        probe_timeout_seconds: float = 2.0,
    ) -> bool:
        now = time.monotonic()
        if now < self._health_cache_until:
            return bool(self._health_cache_value)
        if now < self._circuit_open_until:
            return False

        ok = False
        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.base_url}/api/tags",
                timeout=max(0.5, float(probe_timeout_seconds)),
            )
            ok = 200 <= int(response.status_code) < 500
        except Exception:
            ok = False

        async with self._state_lock:
            self._health_cache_value = bool(ok)
            self._health_cache_until = time.monotonic() + max(0.5, float(cache_ttl_seconds))
            if ok:
                self._consecutive_failures = 0
                self._circuit_open_until = 0.0
            else:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.circuit_breaker_failures:
                    self._circuit_open_until = time.monotonic() + self.circuit_breaker_cooldown_seconds

        return bool(ok)

    async def generate(
        self,
        model: str,
        prompt: str,
        *,
        temperature: float = 0.7,
        num_ctx: int = 2048,
        num_predict: int = 300,
        stop: list[str] | None = None,
        fallback_models: list[str] | None = None,
    ) -> str:
        model_candidates: list[str] = []
        for candidate in [model, *(fallback_models or [])]:
            name = str(candidate or "").strip()
            if not name or name in model_candidates:
                continue
            model_candidates.append(name)

        if not model_candidates:
            raise RuntimeError("Aucun modèle Ollama valide fourni.")

        if time.monotonic() < self._circuit_open_until:
            raise RuntimeError("Circuit Ollama ouvert: service temporairement indisponible.")

        last_error: Exception | None = None
        for model_name in model_candidates:
            payload = {
                "model": model_name,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_ctx": num_ctx,
                    "num_predict": num_predict,
                },
            }
            if stop:
                payload["options"]["stop"] = stop

            for attempt in range(self.max_retries + 1):
                try:
                    client = await self._get_client()
                    response = await client.post(f"{self.base_url}/api/generate", json=payload)
                    response.raise_for_status()

                    decoded = response.json()
                    if not isinstance(decoded, dict):
                        raise RuntimeError("Réponse Ollama invalide (JSON objet attendu).")

                    await self._record_success()
                    return str(decoded.get("response") or "").strip()
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    status = int(exc.response.status_code) if exc.response is not None else 0
                    if status in _RETRYABLE_HTTP_STATUS and attempt < self.max_retries:
                        await asyncio.sleep(self.retry_backoff_seconds * (2 ** attempt))
                        continue
                    break
                except (httpx.TransportError, httpx.TimeoutException, RuntimeError) as exc:
                    last_error = exc
                    if attempt < self.max_retries:
                        await asyncio.sleep(self.retry_backoff_seconds * (2 ** attempt))
                        continue
                    break

        await self._record_failure()
        if isinstance(last_error, Exception):
            raise RuntimeError(f"Echec Ollama après retries/fallback: {last_error}") from last_error
        raise RuntimeError("Echec Ollama: aucune réponse exploitable.")
