"""Gecikme (latency) olcumu — retrieval ne kadar suruyor + sorgu onbellegi kazanci.

Profil: retrieval suresinin ~%98'i sorgu embedding'i (Ollama). Bizim IN-PROCESS
skorlamamiz (BM25+kosinus+RRF) yalnizca birkac ms. Bu yuzden optimizasyon:
tekrar gelen sorgularin embedding'ini ONBELLEKTEN vermek.

Bu script iki gecis yapar:
  * SOGUK gecis : her sorgu ILK kez (embedding hesaplanir + onbellege alinir)
  * SICAK gecis : ayni sorgular TEKRAR (onbellekten -> embedding atlanir)

SOGUK vs SICAK farki = onbellegin kazanci. SOGUK - SICAK ~ embedding maliyeti.

Kullanim (Ollama acik, proje kokunden):
  py eval/latency_eval.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.rag import get_retriever  # noqa: E402


def _load_queries(path: Path) -> list[str]:
    qs: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            qs.append(json.loads(line)["question"])
        except Exception:
            pass
    return qs


def _stats(xs: list[float]) -> tuple[float, float, float]:
    xs2 = sorted(xs)
    p95 = xs2[min(len(xs2) - 1, int(0.95 * len(xs2)))]
    return statistics.mean(xs2), statistics.median(xs2), p95


async def _pass(r, queries: list[str]) -> list[float]:
    out: list[float] = []
    for q in queries:
        t0 = time.perf_counter()
        await r.retrieve(q)
        out.append((time.perf_counter() - t0) * 1000)
    return out


async def _run(queries: list[str]) -> None:
    r = get_retriever()
    # Isinma: index'i kur (bu sorgu onbellege alinmasin diye set disindan bir metin).
    await r.retrieve("isinma amacli ornek sorgu 12345")

    cold = await _pass(r, queries)   # ilk kez: embed + onbellege al
    warm = await _pass(r, queries)   # tekrar: onbellekten

    c_avg, c_med, c_p95 = _stats(cold)
    w_avg, w_med, w_p95 = _stats(warm)
    speedup = (c_avg / w_avg) if w_avg else 0.0

    print(f"{len(queries)} sorgu, 2 gecis (soguk + sicak)\n")
    print("=" * 82)
    print("OZET  (retrieval gecikmesi, ms)")
    print("-" * 82)
    print(f"  SOGUK (ilk sorgu)   : ort {c_avg:7.1f}  |  medyan {c_med:7.1f}  |  p95 {c_p95:7.1f}")
    print(f"  SICAK (tekrar sorgu): ort {w_avg:7.1f}  |  medyan {w_med:7.1f}  |  p95 {w_p95:7.1f}")
    print("-" * 82)
    print(f"  Onbellek hizlanmasi : {speedup:5.1f}x   (tekrar gelen sorgular)")
    print(f"  Atlanan embed suresi: ~{c_avg - w_avg:7.1f} ms/sorgu   (soguk - sicak)")
    print("=" * 82)
    print("\nNot: SOGUK ~ embedding maliyeti (Ollama); SICAK ~ yalnizca IN-PROCESS skorlama.")
    print("Ayni/tekrar sorulan sorularda (SSS) kullanici artik o embedding suresini beklemiyor.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Retrieval gecikme + sorgu onbellegi olcumu")
    ap.add_argument("--set", default=str(Path(__file__).parent / "golden_set.jsonl"))
    args = ap.parse_args()
    path = Path(args.set)
    if not path.exists():
        print(f"[hata] golden set yok: {path}")
        sys.exit(1)
    qs = _load_queries(path)
    if not qs:
        print("[hata] sorgu yok.")
        sys.exit(1)
    asyncio.run(_run(qs))


if __name__ == "__main__":
    main()
