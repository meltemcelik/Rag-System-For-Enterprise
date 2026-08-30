"""Groundedness debug — LLM'in answerability sorusuna verdigi HAM cevabi gosterir.
Neden her seye HAYIR dedigini gormek icin.

Kullanim (Ollama acik, .env'de RAG_MODE=hybrid; RAG_GROUNDCHECK ister acik ister kapali):
    python eval/debug_ground.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.rag import GroundedRetriever, RagConfig, _parse_grounded, get_retriever  # noqa: E402

SAMPLES = [
    ("Tam zamanlı bir çalışan yılda kaç gün ücretli yıllık izne hak kazanır?", False),
    ("Şehir dışı görevlerde günlük yemek limiti nedir?", False),
    ("Makbuzumu kaybettim, param yine de geri ödenir mi?", False),
    ("Yıllık iznimi kullanmak yerine paraya çevirebilir miyim?", True),
    ("İşten ayrılırken kaç gün önceden ihbar etmem gerekiyor?", True),
    ("Python'da bir listeyi nasıl ters çeviririm?", True),
]


async def raw_answer(cfg: RagConfig, query: str, context: list[str]) -> str:
    import httpx
    # rag.py'deki GroundedRetriever ile AYNI few-shot prompt'u kullan (senkron kalsin).
    gr = GroundedRetriever(None, cfg)
    payload = {
        "model": cfg.groundcheck_model,
        "messages": gr._messages(query, context),
        "stream": False,
        "think": False,  # qwen3 gibi dusunen modellerde dusunmeyi kapat
        "options": {"temperature": 0},
    }
    url = f"{cfg.ollama_base_url.rstrip('/')}/api/chat"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return ((resp.json().get("message") or {}).get("content") or "")


async def main() -> None:
    r = get_retriever()
    cfg = getattr(r, "cfg", None) or RagConfig()
    inner = getattr(r, "inner", r)  # GroundedRetriever ise ic retriever'i al
    print(f"groundcheck modeli: {cfg.groundcheck_model}\n")
    for q, should_refuse in SAMPLES:
        ctx = await inner.retrieve(q)
        tag = "REDDET" if should_refuse else "cevap "
        print("=" * 80)
        print(f"[{tag}] {q}")
        print(f"  retrieval -> {len(ctx)} parca")
        if not ctx:
            print("  (baglam bos, LLM'e sorulmaz)")
            continue
        ans = await raw_answer(cfg, q, ctx)
        grounded = _parse_grounded(ans)
        karar = "EVET -> baglam KORUNUR" if grounded else "HAYIR -> baglam BOSALIR (reddet)"
        # dusunme izini uzunsa kisalt, sadece son 120 karakteri goster
        kisa = ans if len(ans) < 200 else "..." + ans[-160:]
        print(f"  LLM (son): {kisa!r}")
        print(f"  KARAR: {karar}")


if __name__ == "__main__":
    asyncio.run(main())
