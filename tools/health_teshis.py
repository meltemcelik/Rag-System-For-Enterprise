"""/api/health neden yavasladi? Bileslenlerini tek tek olc."""

# Depo koku dosyanin kendi konumundan turetilir; sabit yol YAZILMAZ
# (bu betikler once depo disinda yazildi, oradan tasindi).
from pathlib import Path as _Path
_REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import asyncio, os, statistics, sys, time
from pathlib import Path

ROOT = Path(_REPO_ROOT)
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)

import httpx
from app.config import settings
from app.rag import RagConfig, _embed_model_available


def olc(ad, fn, n=5):
    sureler = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        sureler.append((time.perf_counter() - t0) * 1000)
    print(f"  {ad:44} medyan {statistics.median(sureler):7.1f}ms  en kotu {max(sureler):7.1f}ms")
    return statistics.median(sureler)


base = settings.ollama_base_url.rstrip("/")
cfg = RagConfig()

print("Ollama uclari:")
olc("GET /api/tags (ham)", lambda: httpx.get(f"{base}/api/tags", timeout=10))
print("\nrag.status() bilesenleri:")
olc("_embed_model_available (tags + eslesme)", lambda: _embed_model_available(cfg))

print("\nSunucu uclari:")
olc("SONRA /api/health", lambda: httpx.get("http://localhost:8000/api/health", timeout=20))
olc("ONCE  /api/health", lambda: httpx.get("http://localhost:8010/api/health", timeout=20))
olc("SONRA /api/me (yetkisiz)", lambda: httpx.get("http://localhost:8000/api/me", timeout=10))
