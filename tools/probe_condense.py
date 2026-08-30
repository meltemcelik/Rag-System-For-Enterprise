
# Depo koku dosyanin kendi konumundan turetilir; sabit yol YAZILMAZ
# (bu betikler once depo disinda yazildi, oradan tasindi).
from pathlib import Path as _Path
_REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import asyncio, os, sys
from pathlib import Path
ROOT = Path(_REPO_ROOT)
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)

from app import query
from app.config import settings
from app.ollama import OllamaClient
from app.rag import get_retriever, parse_sources

HISTORY = [
    {"role": "user", "content": "Yillik izin hakki kac gun?"},
    {"role": "assistant", "content": "20 is gunu."},
]
FOLLOWUP = "peki devri ne zamana kadar?"


async def main():
    ollama = OllamaClient(settings.ollama_base_url)
    print("model:", settings.default_model)
    print("needs_condensing:", query.needs_condensing(HISTORY, FOLLOWUP))

    raw = await ollama.complete(settings.default_model, [
        {"role": "system", "content": query._SYSTEM},
        {"role": "user", "content": f"Konusma:\nKullanici: {HISTORY[0]['content']}\nAsistan: {HISTORY[1]['content']}\n\nSon soru: {FOLLOWUP}\n\nArama sorgusu:"},
    ], temperature=0.0)
    print("\nHAM LLM CIKTISI:", repr(raw))
    print("TEMIZLENMIS:", repr(query._clean(raw, FOLLOWUP)))

    condensed = await query.condense(ollama, settings.default_model, HISTORY, FOLLOWUP)
    print("CONDENSE SONUCU:", repr(condensed))

    r = get_retriever()
    for q in (FOLLOWUP, condensed, "yillik izin devri ne zamana kadar kullanilmalidir"):
        ctx = await r.retrieve(q)
        print(f"\nsorgu={q!r}\n  -> {len(ctx)} parca, kaynaklar={[s['source'] for s in parse_sources(ctx)]}")


asyncio.run(main())
