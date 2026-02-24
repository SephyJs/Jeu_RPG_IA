from __future__ import annotations

import os


class BananaClient:
    """
    Minimal image client used by Telegram flow.

    The project can run without external image provider config; in that case
    this client simply returns no image.
    """

    def __init__(self) -> None:
        self.enabled = bool(str(os.getenv("BANANA_API_KEY") or "").strip())

    async def generate_image(self, prompt: str) -> bytes | None:
        text = str(prompt or "").strip()
        if not text:
            return None
        # Provider integration is optional. Return no image when not configured.
        if not self.enabled:
            return None
        return None
