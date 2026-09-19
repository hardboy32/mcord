from __future__ import annotations

from typing import Any
import httpx

class McordClient:
    """Small API adapter. Endpoint details stay here so the music logic is independent of Mcord's API version."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
            timeout=20,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self.client.request(method, path, **kwargs)
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()
