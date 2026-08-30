"""Rerank esigi kalibrasyonu — cross-encoder skorlarina gore guvenli esigi bulur.

Her soru icin en yuksek rerank skorunu (0-1) alir; sonra esikleri tarayarak
yanlis_red (cevabi belgede olan ama reddedilen) ve yanlis_kabul (belgede olmayan
ama cevap uretilen) sayilarini raporlar. Amac: yanlis_kabul=0 tutup yanlis_red'i
en aza indiren esik.

On kosul: sentence-transformers kurulu + .env'de RAG_MODE=rerank.
Kullanim (proje kokunden, Ollama + bge-m3 acik):
    python eval/calibrate_rerank.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.rag import RerankRetriever, get_retriever  # noqa: E402

GOLDEN = Path(__file__).parent / "golden_set.jsonl"


def _load() -> list[dict]:
    items = []
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            items.append(json.loads(line))
    return items


async def main() -> None:
    r = get_retriever()
    if not isinstance(r, RerankRetriever):
        print(f"[!] RerankRetriever gerekli; su an {type(r).__name__}.")
        print("    .env'de RAG_MODE=rerank olmali ve `pip install sentence-transformers` yapilmis olmali.")
        return

    golden = _load()
    cur = r.cfg.rerank_threshold()
    print(f"Rerank modeli: {r.cfg.rerank_model}   (ilk calistirma modeli indirir, sabirli olun)\n")

    # (should_refuse, en_yuksek_skor, dogru_parca_skoru|None, dogru_parca_sira|None)
    rows = []
    for g in golden:
        q = g["question"]
        sr = bool(g.get("should_refuse"))
        exp = set(g.get("expected_sources", []))
        ranked = await r.rerank_scored(q)
        best = ranked[0][1] if ranked else 0.0
        cbest, crank = None, None
        for rank, (idx, sc) in enumerate(ranked, start=1):
            if r.chunks[idx].source in exp:
                cbest, crank = sc, rank
                break
        rows.append((sr, best, cbest, crank))
        tag = "REDDET" if sr else "cevap "
        extra = f" | dogru_parca skor={cbest:.3f} sira={crank}" if cbest is not None else ""
        print(f"  [{tag}] en_iyi={best:.3f}{extra}  | {q[:48]}")

    # --- Dagilim: gercek cevaplar ile 'belgede yok' skorlari ayrisiyor mu? ---
    ans = sorted(b for sr, b, c, k in rows if not sr)
    ref = sorted(b for sr, b, c, k in rows if sr)
    print("\nCEVAPLANABILIR en-iyi skorlar (dusuk->yuksek):")
    print("  " + ", ".join(f"{s:.3f}" for s in ans))
    print(f"  min={ans[0]:.3f}  ortanca={ans[len(ans)//2]:.3f}  max={ans[-1]:.3f}")
    print("BELGEDE YOK   en-iyi skorlar (dusuk->yuksek):")
    print("  " + ", ".join(f"{s:.3f}" for s in ref))
    print(f"  min={ref[0]:.3f}  ortanca={ref[len(ref)//2]:.3f}  max={ref[-1]:.3f}")

    # --- Kaba tablo (okumak icin) ---
    print(f"\n{'rerank_esik':>11} | {'yanlis_red':>10} | {'yanlis_kabul':>12}")
    print("-" * 42)
    for i in range(0, 101, 5):  # 0.00 .. 1.00
        t = i / 100
        fr = sum(1 for sr, b, c, k in rows if not sr and b < t)
        fa = sum(1 for sr, b, c, k in rows if sr and b >= t)
        mark = "  << mevcut" if abs(t - cur) < 1e-9 else ""
        print(f"{t:>11.2f} | {fr:>10} | {fa:>12}{mark}")
    print("-" * 42)

    # --- Kesin oneri: veriden turetilmis esikler uzerinde en iyisi ---
    cands = sorted(set(round(b, 3) for sr, b, c, k in rows))
    best0 = None   # yanlis_kabul=0 sartiyla en az yanlis_red
    best1 = None   # en fazla 1 yanlis_kabul'e izin verirsek
    for t in cands:
        fr = sum(1 for sr, b, c, k in rows if not sr and b < t)
        fa = sum(1 for sr, b, c, k in rows if sr and b >= t)
        if fa == 0 and (best0 is None or (fr, -t) < best0[0]):
            best0 = ((fr, -t), t, fr)
        if fa <= 1 and (best1 is None or (fr, -t) < best1[0]):
            best1 = ((fr, -t), t, fr, fa)

    print()
    if best0:
        print(f"ONERI (halusinasyon=0): RAG_RERANK_MIN_SCORE={best0[1]:.3f}  -> yanlis_red={best0[2]}")
    else:
        print("halusinasyon=0 tutan esik yok.")
    if best1 and (not best0 or best1[2] < best0[2]):
        print(f"ALTERNATIF (1 halusinasyona izin): RAG_RERANK_MIN_SCORE={best1[1]:.3f}  -> yanlis_red={best1[2]}, yanlis_kabul={best1[3]}")
    print("\nSectigin degeri .env'e yaz, sonra: python eval/evaluate.py")


if __name__ == "__main__":
    asyncio.run(main())
