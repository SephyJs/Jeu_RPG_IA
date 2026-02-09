import httpx

class OllamaClient:
    def __init__(self, base_url: str = "http://127.0.0.1:11434"):
        self.base_url = base_url.rstrip("/")

    async def generate(
        self,
        model: str,
        prompt: str,
        *,
        temperature: float = 0.7,
        num_ctx: int = 2048,
        num_predict: int = 300,
        stop: list[str] | None = None,
    ) -> str:
        payload = {
            "model": model,
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

        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(f"{self.base_url}/api/generate", json=payload)
            r.raise_for_status()
            return (r.json().get("response") or "").strip()
