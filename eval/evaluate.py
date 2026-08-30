"""RAG degerlendirme (eval) scripti — golden set uzerinde retrieval kalitesini olcer.

Ne olcer:
  * hit@k        : dogru belge, dondurulen ilk k parca icinde var mi? (temel metrik)
  * MRR          : dogru belge kacinci sirada geldi? (1/rank ortalamasi — 1.0 = hep ilk sirada)
  * guardrail    : "belgede yok" sorulari dogru reddedildi mi? yanlis red/yanlis kabul sayilari

Nasil calisir:
  Proje kokunden calistirin (app/ klasorunu goren yerden):

      py eval/evaluate.py                    # varsayilan golden set
      py eval/evaluate.py --k 4              # hit@4 (varsayilan)
      py eval/evaluate.py --set eval/golden_set.jsonl

  Backend secmek icin (rag.py env degiskenlerini okur):

      RAG_MODE=keyword py eval/evaluate.py   # BM25 (Ollama gerekmez)
      RAG_MODE=embed   py eval/evaluate.py   # Ollama embeddings

Bir degisiklik yapmadan ONCE ve SONRA calistirip skorlari karsilastirin.
Golden set'i buyutmek icin: eval/golden_set.jsonl'a yeni satir ekleyin (asagidaki README).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Proje kokunu path'e ekle ki "from app.rag import ..." calissin (script alt klasorde).
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asyncio  # noqa: E402

from app.rag import get_retriever, guard_reply  # noqa: E402

# Dondurulen parca "[kaynak: dosya.md, sayfa 3]\n..." kalibiyla basliyor; dosya adini cek.
# Dosya adinda virgul olabilir (or. "...2026_ Use Cases, Metrics, and...pdf"),
# bu yuzden ilk virgulde kesmiyoruz; opsiyonel ", sayfa N" ekini ayirip gerisini aliyoruz.
_SOURCE_RE = re.compile(r"\[kaynak:\s*(.+?)(?:,\s*sayfa\s*\d+)?\]")


def _source_of(chunk_text: str) -> str:
    m = _SOURCE_RE.match(chunk_text.strip())
    return m.group(1).strip() if m else ""


def _load_golden(path: Path) -> list[dict]:
    items: list[dict] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError as exc:
            print(f"[uyari] {path.name} satir {line_no} atlandi (gecersiz JSON): {exc}")
    return items


async def _run(golden: list[dict], k: int) -> None:
    retriever = get_retriever()
    print(f"retriever = {type(retriever).__name__}   |   hit@{k}   |   {len(golden)} soru\n")

    # Sayaclar
    n_answerable = 0          # belgede cevabi olan sorular
    n_hit = 0                 # bunlardan dogru belgeyi ilk k'da bulanlar
    mrr_sum = 0.0
    n_refuse_total = 0        # "belgede yok" sorulari
    n_refuse_correct = 0      # dogru reddedilenler
    false_refuse = 0          # cevabi belgede olmasina ragmen reddedilenler
    false_accept = 0          # belgede olmamasina ragmen cevap uretilenler

    rows: list[str] = []

    for item in golden:
        q = item["question"]
        expected = set(item.get("expected_sources", []))
        should_refuse = bool(item.get("should_refuse", False))

        chunks = await retriever.retrieve(q)
        refused = guard_reply(retriever, chunks) is not None
        got_sources = [_source_of(c) for c in chunks[:k]]

        if should_refuse:
            n_refuse_total += 1
            if refused:
                n_refuse_correct += 1
                mark = "OK  (dogru red)"
            else:
                false_accept += 1
                mark = f"HATA (reddetmedi -> {got_sources})"
        else:
            n_answerable += 1
            if refused:
                false_refuse += 1
                mark = "HATA (yanlis red — cevap belgede vardi)"
            else:
                # ilk kacinci sirada dogru kaynak geldi?
                rank = next(
                    (i + 1 for i, s in enumerate(got_sources) if s in expected), None
                )
                if rank is not None:
                    n_hit += 1
                    mrr_sum += 1.0 / rank
                    mark = f"OK  (sira {rank}, kaynak {got_sources[rank - 1]})"
                else:
                    mark = f"ISKA (beklenen {expected}, gelen {got_sources})"

        rows.append(f"  {mark:<48} | {q[:60]}")

    print("SORU BAZINDA SONUC")
    print("-" * 100)
    for r in rows:
        print(r)

    # --- Ozet ---
    hit_rate = (n_hit / n_answerable * 100) if n_answerable else 0.0
    mrr = (mrr_sum / n_answerable) if n_answerable else 0.0
    guard_rate = (n_refuse_correct / n_refuse_total * 100) if n_refuse_total else 0.0

    print("\n" + "=" * 100)
    print("OZET")
    print("-" * 100)
    print(f"  Cevaplanabilir soru      : {n_answerable}")
    print(f"  hit@{k}                    : {n_hit}/{n_answerable}  = %{hit_rate:.1f}   (dogru belgeyi ilk {k}'da buldu)")
    print(f"  MRR                      : {mrr:.3f}            (1.0 = hep ilk sirada)")
    print(f"  Yanlis red (false refuse): {false_refuse}                (cevap belgede vardi ama reddetti)")
    print()
    print(f"  Reddedilmesi gereken     : {n_refuse_total}")
    print(f"  Guardrail dogrulugu      : {n_refuse_correct}/{n_refuse_total}  = %{guard_rate:.1f}")
    print(f"  Yanlis kabul(false accept): {false_accept}                (belgede yoktu ama cevap uretti -> halusinasyon riski)")
    print("=" * 100)
    print("\nIpucu: bir degisiklikten once ve sonra bu skorlari karsilastirin.")


def main() -> None:
    ap = argparse.ArgumentParser(description="RAG golden-set degerlendirme")
    ap.add_argument("--set", default=str(Path(__file__).parent / "golden_set.jsonl"),
                    help="golden set jsonl yolu")
    ap.add_argument("--k", type=int, default=int(os.getenv("RAG_TOP_K", "4")),
                    help="hit@k icin k (varsayilan: RAG_TOP_K veya 4)")
    args = ap.parse_args()

    path = Path(args.set)
    if not path.exists():
        print(f"[hata] golden set bulunamadi: {path}")
        sys.exit(1)

    golden = _load_golden(path)
    if not golden:
        print("[hata] golden set bos.")
        sys.exit(1)

    asyncio.run(_run(golden, args.k))


if __name__ == "__main__":
    main()
