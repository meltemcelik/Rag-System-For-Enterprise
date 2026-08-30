"""Guardrail esigini VERIYLE onerir (tahmin degil).

Golden set'teki her soru icin en yuksek retrieval skorunu olcer, sonra
"cevaplanabilir" sorular ile "belgede yok" sorularini en iyi ayiran esigi bulur.

Kullanim (proje kokunden):
    RAG_MODE=keyword python eval/calibrate.py     # BM25 esigi (RAG_MIN_SCORE_KEYWORD)
    RAG_MODE=embed   python eval/calibrate.py     # kosinus esigi (RAG_MIN_SCORE_EMBED)

Cikan sayiyi .env'de ilgili degiskene yazin, sonra evaluate.py ile dogrulayin.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.rag import (  # noqa: E402
    BM25Retriever,
    EmbeddingRetriever,
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


async def _top_score(retriever, query: str) -> float:
    """Bir sorgunun en yuksek retrieval skoru (esik uygulanmadan)."""
    if isinstance(retriever, BM25Retriever):
        qt = _tokenize(query)
        if not qt:
            return 0.0
        return max((retriever._score(qt, i) for i in range(len(retriever.chunks))), default=0.0)
    if isinstance(retriever, EmbeddingRetriever):
        await retriever._ensure_index()
        if not retriever._doc_vecs:
            return 0.0
        from app.rag import _cosine
        q_vec = (await retriever._embed([query]))[0]
        return max((_cosine(q_vec, dv) for dv in retriever._doc_vecs), default=0.0)
    return 0.0


async def main() -> None:
    golden = _load()
    retriever = get_retriever()
    kind = type(retriever).__name__
    if not isinstance(retriever, (BM25Retriever, EmbeddingRetriever)):
        print(f"[hata] Kalibrasyon icin BM25 veya Embedding gerekli, gelen: {kind}")
        print("Belge var mi? RAG_MODE dogru mu? (embed icin Ollama + embed modeli acik olmali)")
        return

    good, junk = [], []
    for item in golden:
        s = await _top_score(retriever, item["question"])
        (junk if item.get("should_refuse") else good).append(s)

    if not good or not junk:
        print("[hata] Hem cevaplanabilir hem 'belgede yok' sorusu gerekli.")
        return

    good.sort(); junk.sort()
    print(f"retriever = {kind}\n")
    print(f"CEVAPLANABILIR skorlar (dusuk->yuksek): {[round(s, 2) for s in good]}")
    print(f"  en dusuk={good[0]:.2f}  ortanca={good[len(good)//2]:.2f}  en yuksek={good[-1]:.2f}")
    print(f"BELGEDE YOK skorlar   (dusuk->yuksek): {[round(s, 2) for s in junk]}")
    print(f"  en dusuk={junk[0]:.2f}  ortanca={junk[len(junk)//2]:.2f}  en yuksek={junk[-1]:.2f}")

    # En iyi ayiran esigi bul: adaylari tara, dogru siniflamayi maksimize et.
    cands = sorted(set(good + junk))
    best_t, best_correct = cands[0], -1
    for i in range(len(cands)):
        # aday esik: iki komsu skorun ortasi (ve uc noktalar)
        t = cands[i]
        # good >= t kabul, junk < t red dogru sayilir
        correct = sum(1 for s in good if s >= t) + sum(1 for s in junk if s < t)
        if correct > best_correct:
            best_correct, best_t = correct, t
    # esigi, en yuksek junk ile en dusuk gecen good arasinda biraz guvenli yere koy
    passing_good = [s for s in good if s >= best_t]
    lo = max([s for s in junk if s < best_t] + [0.0])
    hi = min(passing_good) if passing_good else best_t
    suggested = round((lo + hi) / 2, 2) if hi > lo else round(best_t, 2)

    total = len(good) + len(junk)
    var = "RAG_MIN_SCORE_KEYWORD" if isinstance(retriever, BM25Retriever) else "RAG_MIN_SCORE_EMBED"
    print("\n" + "=" * 60)
    print(f"ONERILEN ESIK: {suggested}")
    print(f"  -> .env'e yaz:  {var}={suggested}")
    print(f"  bu esikle golden set'te {best_correct}/{total} soru dogru siniflanir")
    if lo >= hi:
        print("  UYARI: iyi ve alakasiz skorlar cakisiyor -> temiz ayrim yok.")
        print("  Daha fazla/zor 'belgede yok' sorusu ekleyin veya reranking dusunun (Adim 3).")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
