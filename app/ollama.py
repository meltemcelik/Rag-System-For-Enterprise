import json
from collections.abc import AsyncIterator

import httpx


class OllamaError(RuntimeError):
    """Raised when Ollama is unreachable or returns an error."""


class OllamaClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def _tags(self) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(str(exc)) from exc
        return resp.json().get("models", [])

    async def list_models(self) -> list[str]:
        return [m["name"] for m in await self._tags()]

    async def list_models_info(self) -> list[dict]:
        """Her indirilmis model icin ad + boyut/parametre/kuantizasyon bilgisi."""
        info = []
        for m in await self._tags():
            details = m.get("details") or {}
            info.append(
                {
                    "name": m.get("name", ""),
                    "size": m.get("size"),
                    "parameter_size": details.get("parameter_size"),
                    "quantization_level": details.get("quantization_level"),
                }
            )
        return info

    async def complete(
        self, model: str, messages: list[dict], temperature: float = 0.0, timeout: int = 60
    ) -> str:
        """Akissiz tek seferlik cevap (sorgu sikistirma gibi ic gorevler icin)."""
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,  # dusunen modellerde (qwen3) hiz + temiz cikti
            "options": {"temperature": temperature},
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(str(exc)) from exc
        return (resp.json().get("message") or {}).get("content") or ""

    async def stream_chat(
        self,
        model: str,
        messages: list[dict],
        temperature: float,
    ) -> AsyncIterator[str]:
        """Yield assistant content chunks as they arrive from Ollama."""
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            # qwen3 gibi dusunen modeller aksi halde <think>...</think> izini de
            # akitir ve kullaniciya ham dusunme metni gorunur.
            "think": False,
            "options": {"temperature": temperature},
        }
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/api/chat", json=payload
                ) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode(errors="ignore")
                        raise OllamaError(f"Ollama {resp.status_code}: {body}")
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        if data.get("error"):
                            raise OllamaError(data["error"])
                        chunk = data.get("message", {}).get("content", "")
                        if chunk:
                            yield chunk
                        if data.get("done"):
                            return
        except httpx.HTTPError as exc:
            raise OllamaError(str(exc)) from exc
