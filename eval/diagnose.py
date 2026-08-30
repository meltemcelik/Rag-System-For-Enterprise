"""Yanlis red teshisi — hybrid modda reddedilen (cevabi belgede olan) sorularin
gercek skorlarini gosterir.

Her yanlis red icin:
  * en yuksek BM25 skoru vs keyword esigi
  * en yuksek embed (kosinus) skoru vs embed esigi
  * DOGRU parca hangi sirada ve hangi skorda (esige ne kadar yakin?)

Boylece karar veririz: skor esige yakinsa -> esigi dusur (ucuz); cok dusukse
veya parca hic yoksa -> reranking gerekir.

Kullanim (proje kokunden, Ollama + bge-m3 acikken):
    python eval/diagnose.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.rag import (  # noqa: E402
    HybridRetriever,
    _cosine,
    _tokenize,
    get_retriever,
)

GOLDEN = Path(__file__).parent / "golden_set.jsonl"


def _load() -> list[dict]:
    items = []
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            items.append(json.loads(line))
    return items


def _bm25_scored(bm25, query: str) -> list[tuple[int, float]]:
    """Esiksiz TUM BM25 skorlari, yuksekten dusuge."""
    qt = _tokenize(query)
    if not qt:
        return []
    scored = [(i, bm25._score(qt, i)) for i in range(len(bm25.chunks))]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


async def _embed_scored(embed, query: str) -> list[tuple[int, float]]:
    """Esiksiz TUM kosinus skorlari, yuksekten dusuge."""
    await embed._ensure_index()
    if not embed._doc_vecs:
        return []
    qv = (await embed._embed([query]))[0]
    scored = [(i, _cosine(qv, dv)) for i, dv in enumerate(embed._doc_vecs)]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def _rank_of_source(scored, chunks, expected: set[str]):
    """Beklenen kaynaktan gelen ilk parcanin (sira, skor)'u; yoksa (None, None)."""
    for rank, (i, sc) in enumerate(scored, start=1):
        if chunks[i].source in expected:
            return rank, sc
    return None, None


async def main() -> None:
    r = get_retriever()
    if not isinstance(r, HybridRetriever):
        print(f"[!] Bu teshis hybrid moda gore yazildi; su anki mod: {type(r).__name__}.")
        print("    .env'de RAG_MODE=hybrid ve RAG_EMBED_MODEL=bge-m3 olmali, Ollama acik olmali.")
        return

    bm25, embed, chunks, cfg = r.bm25, r.embed, r.chunks, r.cfg
    kt, et = cfg.keyword_threshold(), cfg.embed_threshold()
    print(f"Esikler:  keyword >= {kt}   embed(kosinus) >= {et}\n")

    answerable = [g for g in _load() if not g.get("should_refuse")]
    refused = []

    for g in answerable:
        q = g["question"]
        exp = set(g.get("expected_sources", []))
        bm = _bm25_scored(bm25, q)
        em = await _embed_scored(embed, q)
        bm_top = bm[0][1] if bm else 0.0
        em_top = em[0][1] if em else 0.0
        bm_pass = bm_top > 0 and bm_top >= kt
        em_pass = em_top >= et
        if not bm_pass and not em_pass:  # hybrid guardrail -> reddedilir
            br, bs = _rank_of_source(bm, chunks, exp)
            er, es = _rank_of_source(em, chunks, exp)
            refused.append((q, exp, bm_top, em_top, br, bs, er, es))

    if not refused:
        print("Hic yanlis red yok — tum cevaplanabilir sorular baglam donduruyor. 🎉")
        return

    print(f"{len(refused)} YANLIS RED bulundu:\n")
    for q, exp, bmt, emt, br, bs, er, es in refused:
        print("=" * 84)
        print(f"SORU: {q}")
        print(f"  beklenen kaynak : {', '.join(exp) or '-'}")
        print(f"  en yuksek BM25  : {bmt:6.2f}   (esik {kt})   -> {'GECERDI' if bmt >= kt else 'esigin altinda'}")
        print(f"  en yuksek embed : {emt:6.3f}  (esik {et})  -> {'GECERDI' if emt >= et else 'esigin altinda'}")
        if es is not None:
            near = "  <-- ESIGE COK YAKIN" if es >= et - 0.08 else ""
            print(f"  DOGRU parca embed'de {er}. sirada, kosinus = {es:.3f}{near}")
        else:
            print("  DOGRU parca embed adaylarinda YOK")
        if bs is not None:
            print(f"  DOGRU parca BM25'te  {br}. sirada, skor    = {bs:.2f}")
        else:
            print("  DOGRU parca BM25 adaylarinda YOK")

    print("=" * 84)
    print("\nNASIL OKUNUR:")
    print("  * DOGRU parcanin kosinusu esige yakinsa (~0.44-0.51) -> embed esigini dusurmek cozer (ucuz).")
    print("  * Kosinus cok dusukse (<0.4) ya da parca adaylarda hic yoksa -> reranking gerekir (asama 3).")


if __name__ == "__main__":
    asyncio.run(main())
