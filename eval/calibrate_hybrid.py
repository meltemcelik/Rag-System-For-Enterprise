"""Hybrid modda IKI esigi BIRLIKTE kalibre eder (BM25 + embedding).

Hybrid'de bir soru "cevaplanabilir" sayilir if:  bm25_skor >= t_keyword  VEYA  kosinus >= t_embed
Yani esikleri ayri ayri degil, ikili olarak optimize etmek gerekir.

Bu script golden set'teki her soru icin en yuksek BM25 ve en yuksek kosinus skorunu olcer,
sonra (t_keyword, t_embed) ikilileri arasinda tarama yapip en iyi ayrimi bulur.

Kullanim (proje kokunden, Ollama + embed modeli calisir durumda):
    $env:RAG_MODE="hybrid"; $env:RAG_EMBED_MODEL="bge-m3"; py eval/calibrate_hybrid.py

Cikan iki sayiyi .env'e yazin:
    RAG_MIN_SCORE_KEYWORD=...
    RAG_MIN_SCORE_EMBED=...
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.rag import HybridRetriever, _cosine, _tokenize, get_retriever  # noqa: E402

GOLDEN = Path(__file__).parent / "golden_set.jsonl"


def _load() -> list[dict]:
    out = []
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(json.loads(line))
    return out


async def main() -> None:
    r = get_retriever()
    if not isinstance(r, HybridRetriever):
        print("HATA: hybrid modda calistirin ->  $env:RAG_MODE=\"hybrid\"")
        return

    items = _load()
    print(f"{len(items)} soru olculuyor (vektorler hazirlaniyor, biraz surebilir)...\n")
    await r.embed._ensure_index()
    vecs = r.embed._doc_vecs or []
    if not vecs:
        print("HATA: embedding vektorleri alinamadi (Ollama / model adini kontrol edin).")
        return

    rows: list[tuple[bool, float, float, str]] = []
    for it in items:
        q = it["question"]
        terms = _tokenize(q)
        bm = max((r.bm25._score(terms, i) for i in range(len(r.chunks))), default=0.0)
        qv = (await r.embed._embed([q]))[0]
        em = max((_cosine(qv, dv) for dv in vecs), default=0.0)
        rows.append((bool(it.get("should_refuse")), bm, em, q))

    ans = [(bm, em) for ref, bm, em, _ in rows if not ref]
    ref = [(bm, em) for ref, bm, em, _ in rows if ref]

    print("CEVAPLANABILIR sorular:")
    print(f"  BM25    en dusuk={min(b for b,_ in ans):.2f}  ortanca={sorted(b for b,_ in ans)[len(ans)//2]:.2f}")
    print(f"  Kosinus en dusuk={min(e for _,e in ans):.3f}  ortanca={sorted(e for _,e in ans)[len(ans)//2]:.3f}")
    print("BELGEDE YOK sorular:")
    print(f"  BM25    en yuksek={max(b for b,_ in ref):.2f}  ortanca={sorted(b for b,_ in ref)[len(ref)//2]:.2f}")
    print(f"  Kosinus en yuksek={max(e for _,e in ref):.3f}  ortanca={sorted(e for _,e in ref)[len(ref)//2]:.3f}")
    print()

    # Izgara taramasi
    k_grid = [i * 0.5 for i in range(0, 41)]          # 0.0 .. 20.0
    e_grid = [0.30 + i * 0.005 for i in range(0, 121)]  # 0.30 .. 0.90

    best = None
    for tk in k_grid:
        for te in e_grid:
            fa = sum(1 for is_ref, bm, em, _ in rows if is_ref and (bm >= tk or em >= te))
            fr = sum(1 for is_ref, bm, em, _ in rows if not is_ref and not (bm >= tk or em >= te))
            # Halusinasyon (yanlis kabul) daha agir cezalandirilir.
            cost = fa * 2 + fr
            cand = (cost, fa, fr, tk, te)
            if best is None or cand < best:
                best = cand

    cost, fa, fr, tk, te = best
    print("=" * 62)
    print("ONERILEN ESIKLER (yanlis kabul 2x agirlikla cezalandirildi):")
    print(f"  RAG_MIN_SCORE_KEYWORD={tk:.2f}")
    print(f"  RAG_MIN_SCORE_EMBED={te:.3f}")
    print(f"  -> yanlis kabul: {fa}/{len(ref)}   yanlis red: {fr}/{len(ans)}")
    print("=" * 62)
    print("\nBu iki satiri .env'e yazip evaluate.py ile dogrulayin.")


if __name__ == "__main__":
    asyncio.run(main())
