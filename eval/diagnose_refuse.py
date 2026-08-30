"""Sizinti teshisi — 'belgede yok' sorularinin hangi kapidan (BM25 mi embed mi)
gectigini gosterir. Yanlis kabul (halusinasyon) hangi esikle onlenir, netlesir.

Kullanim (proje kokunden, Ollama + bge-m3 acik):
    python eval/diagnose_refuse.py
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
        print(f"[!] Hybrid mod gerekli; su an {type(r).__name__}. .env: RAG_MODE=hybrid.")
        return
    bm25, embed, cfg = r.bm25, r.embed, r.cfg
    kt, et = cfg.keyword_threshold(), cfg.embed_threshold()
    print(f"Esikler: keyword>={kt}  embed>={et}\n")
    print("REDDEDILMESI GEREKEN sorular (sizanlar = yanlis kabul):")
    print("-" * 84)
    for g in _load():
        if not g.get("should_refuse"):
            continue
        q = g["question"]
        bt = _bm25_top(bm25, q)
        et_ = await _embed_top(embed, q)
        bm_pass = bt > 0 and bt >= kt
        em_pass = et_ >= et
        leak = bm_pass or em_pass
        gate = []
        if bm_pass:
            gate.append("BM25")
        if em_pass:
            gate.append("embed")
        flag = f"SIZIYOR ({'+'.join(gate)})" if leak else "reddedilir"
        print(f"  BM25={bt:6.2f}  embed={et_:.3f}  -> {flag:<18} | {q[:44]}")
    print("-" * 84)
    print("\nYORUM: 'SIZIYOR (BM25)' varsa keyword esigini (RAG_MIN_SCORE_KEYWORD) yukselt;")
    print("       'SIZIYOR (embed)' varsa embed esigini (RAG_MIN_SCORE_EMBED) yukselt.")


if __name__ == "__main__":
    asyncio.run(main())
