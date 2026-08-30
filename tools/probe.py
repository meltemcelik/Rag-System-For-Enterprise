"""Neden reddedildi? BM25 ve embed skorlarini esiklerle karsilastir.

  python tools/probe.py "incelenecek soru"

Soru verilmezse ornek bir soru kullanilir.
"""

# Depo koku dosyanin kendi konumundan turetilir; sabit yol YAZILMAZ
# (bu betikler once depo disinda yazildi, oradan tasindi).
from pathlib import Path as _Path
_REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import asyncio, sys
from pathlib import Path

ROOT = Path(_REPO_ROOT)
sys.path.insert(0, str(ROOT))
import os
os.chdir(ROOT)

from app.rag import get_retriever, _tokenize

Q = sys.argv[1] if len(sys.argv) > 1 else "Yillik izin hakki kac gun?"


async def main():
    r = get_retriever()
    cfg = r.cfg if hasattr(r, "cfg") else None
    print("retriever sinifi:", type(r).__name__)
    if cfg:
        print("mode:", cfg.mode, "| embed_model:", cfg.embed_model,
              "| top_k:", cfg.top_k)
        print("min_score_keyword:", cfg.min_score, "| embed esigi:", cfg.embed_threshold())
        print("strict:", getattr(cfg, "strict", "?"))

    print("\nsoru tokenlari:", _tokenize(Q))

    ctx = await r.retrieve(Q)
    print("\n--- retrieve() sonucu ---")
    print(repr(ctx)[:1500] if ctx else "(BOS - hicbir parca esigi gecemedi)")

    # BM25 ham skorlar
    bm = getattr(r, "bm25", None) or getattr(getattr(r, "embed", None), "bm25", None)
    if bm is None and hasattr(r, "chunks"):
        from app.rag import BM25Retriever
        bm = BM25Retriever(r.chunks, cfg)
    if bm is not None:
        try:
            scores = bm._scores(Q) if hasattr(bm, "_scores") else None
            if scores:
                top = sorted(enumerate(scores), key=lambda x: -x[1])[:5]
                print("\n--- en iyi 5 BM25 ---")
                for i, s in top:
                    src = r.chunks[i].source if hasattr(r.chunks[i], "source") else "?"
                    print(f"  {s:7.3f}  {src}")
        except Exception as e:
            print("bm25 probe hatasi:", e)

    # embed calisiyor mu?
    emb = getattr(r, "embed", None)
    if emb is not None:
        try:
            v = await emb._embed([Q])
            print(f"\nembed OK - vektor boyutu {len(v[0])}")
        except Exception as e:
            print(f"\nEMBED HATASI -> {type(e).__name__}: {e}")


asyncio.run(main())
