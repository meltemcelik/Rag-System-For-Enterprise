"""Embed esigi taramasi — yanlis red vs yanlis kabul takasini TEK calistirmada olcer.

Her aday embed esigi icin simule eder (hybrid guardrail mantigi: bir soru,
BM25 skoru keyword-esigi gecerse YA DA embed kosinusu embed-esigi gecerse
baglam doner; ikisi de gecmezse reddedilir):
  * yanlis_red   = cevabi belgede olan ama yine de reddedilen soru sayisi
  * yanlis_kabul = belgede olmayan ama cevap uretilen soru sayisi (halusinasyon)

Amac: yanlis_kabul'u 0 tutup yanlis_red'i en aza indiren en dusuk esigi bulmak.

Kullanim (proje kokunden, Ollama + bge-m3 acikken):
    python eval/sweep_embed.py
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
    items = []
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            items.append(json.loads(line))
    return items


def _bm25_top(bm25, query: str) -> float:
    qt = _tokenize(query)
    if not qt:
        return 0.0
    return max((bm25._score(qt, i) for i in range(len(bm25.chunks))), default=0.0)


async def _embed_top(embed, query: str) -> float:
    await embed._ensure_index()
    if not embed._doc_vecs:
        return 0.0
    qv = (await embed._embed([query]))[0]
    return max((_cosine(qv, dv) for dv in embed._doc_vecs), default=0.0)


async def main() -> None:
    r = get_retriever()
    if not isinstance(r, HybridRetriever):
        print(f"[!] Hybrid mod gerekli; su an {type(r).__name__}. .env: RAG_MODE=hybrid + Ollama acik.")
        return

    bm25, embed, cfg = r.bm25, r.embed, r.cfg
    kt = cfg.keyword_threshold()
    cur_et = cfg.embed_threshold()
    golden = _load()

    # Her soru icin (should_refuse, bm25_top, embed_top) — sorgu embed'i bir kez alinir.
    rows = []
    for g in golden:
        q = g["question"]
        sr = bool(g.get("should_refuse"))
        bt = _bm25_top(bm25, q)
        et = await _embed_top(embed, q)
        rows.append((sr, bt, et))

    n_answer = sum(1 for sr, _, _ in rows if not sr)
    n_refuse = sum(1 for sr, _, _ in rows if sr)
    print(f"keyword esigi sabit = {kt}   |   {n_answer} cevaplanabilir, {n_refuse} 'belgede yok'\n")
    print(f"{'embed_esik':>10} | {'yanlis_red':>10} | {'yanlis_kabul':>12}")
    print("-" * 40)

    best = None  # (false_refuse, -et) -> yanlis_kabul=0 sartiyla en iyi
    for i in range(20, 61):  # 0.20 .. 0.60
        et = i / 100
        false_refuse = sum(1 for sr, b, e in rows if not sr and not (b >= kt or e >= et))
        false_accept = sum(1 for sr, b, e in rows if sr and (b >= kt or e >= et))
        mark = "  << mevcut" if abs(et - cur_et) < 1e-9 else ""
        if 0.40 <= et <= 0.56:  # ekrani sade tutmak icin sadece ilgili araligi bas
            print(f"{et:>10.2f} | {false_refuse:>10} | {false_accept:>12}{mark}")
        if false_accept == 0:
            key = (false_refuse, -et)  # once en az yanlis red, sonra en yuksek (guvenli) esik
            if best is None or key < best[0]:
                best = (key, et, false_refuse)

    print("-" * 40)
    if best:
        _, bet, bfr = best
        print(f"\nONERI: RAG_MIN_SCORE_EMBED={bet:.2f}")
        print(f"  -> yanlis_kabul=0 korunur, yanlis_red {sum(1 for sr,b,e in rows if not sr and not (b>=kt or e>=cur_et))}'ten {bfr}'e iner.")
        print("  .env'de bu satiri guncelle, sonra: python eval/evaluate.py ile dogrula.")
    else:
        print("\nUYARI: yanlis_kabul'u 0 tutan esik yok -> saf esik ayari yetmiyor, reranking (asama 3) gerek.")


if __name__ == "__main__":
    asyncio.run(main())
