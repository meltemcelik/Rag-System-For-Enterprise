"""Uctan uca CEVAP degerlendirmesi — retrieval degil, modelin URETTIGI cevabi olcer.

evaluate.py "dogru belge geldi mi?" sorusuna bakar (retrieval).
Bu script bir adim oteye gecer: model gercekten dogru CEVABI yazdi mi?
Her cevaplanabilir soru icin sistemin urettigi cevabi alir ve golden set'teki
answer_keywords'lerin cevap METNINDE gecip gecmedigini kontrol eder.

Olctugu:
  * Cevap dogrulugu (tam)  : TUM anahtar kelimeler cevapta geciyor mu? (ana metrik)
  * Ortalama kelime kapsami: kelimelerin ortalama yuzde kaci cevapta gecti
  * Uretilemeyen           : guardrail yanlislikla reddettigi icin cevap uretilemeyenler

Not: anahtar kelime eslesmesi yaklasik bir olcumdur (metin ici arama); amac
mutlak dogruluk degil, bir DEGISIKLIK oncesi/sonrasi KARSILASTIRMA yapmaktir.

Kullanim (Ollama acik, proje kokunden):
  py eval/answer_eval.py
  py eval/answer_eval.py --limit 20      # hizli deneme (ilk 20 soru)

Bir prompt/ayar degisiminden ONCE ve SONRA calistirip skorlari karsilastirin.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402
from app.rag import get_retriever, guard_reply, build_rag_messages  # noqa: E402

# Turkce-duyarli kucuk harf (I->i buyuk-kucuk tuzagi) — kelime eslesmesi icin.
_TR = str.maketrans({"I": "ı", "İ": "i", "Ş": "ş", "Ğ": "ğ", "Ü": "ü", "Ö": "ö", "Ç": "ç"})


def _lower(s: str) -> str:
    return s.translate(_TR).lower()


def _load_golden(path: Path) -> list[dict]:
    items: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return items


async def _generate(question: str, context: list[str]) -> str:
    payload = {
        "model": settings.default_model,
        "messages": build_rag_messages(question, context),
        "stream": False,
        # URETIMLE AYNI olmali: app/ollama.py da think=False gonderir. Aksi halde
        # qwen3 gibi dusunen modeller cevabin onune <think>...</think> bloku koyar;
        # hem olcum uretimden farkli bir konfigurasyonu olcmus olur hem de anahtar
        # kelimeler dusunme metninde eslesip skoru yaniltir.
        "think": False,
        "options": {"temperature": 0},  # olcum icin deterministik
    }
    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return ((resp.json().get("message") or {}).get("content") or "").strip()


async def _run(golden: list[dict], limit: int | None) -> None:
    retriever = get_retriever()
    answerable = [g for g in golden if not g.get("should_refuse", False) and g.get("answer_keywords")]
    if limit:
        answerable = answerable[:limit]
    print(f"model = {settings.default_model}   |   {len(answerable)} cevaplanabilir soru\n")

    n = 0
    n_full = 0        # tum anahtar kelimeler gecen
    cov_sum = 0.0     # ortalama kapsam
    n_notgen = 0      # guardrail yanlis reddetti -> cevap yok
    rows: list[str] = []

    for item in answerable:
        q = item["question"]
        keywords = [str(k) for k in item.get("answer_keywords", [])]
        n += 1
        context = await retriever.retrieve(q)
        if guard_reply(retriever, context) is not None:
            n_notgen += 1
            rows.append(f"  URETILMEDI (guardrail reddetti)              | {q[:52]}")
            continue
        answer = await _generate(q, context)
        alow = _lower(answer)
        hits = [kw for kw in keywords if _lower(kw) in alow]
        cov_sum += len(hits) / len(keywords)
        if len(hits) == len(keywords):
            n_full += 1
            mark = "OK    (tum kelimeler var)"
        elif hits:
            missing = [k for k in keywords if k not in hits]
            mark = f"KISMI ({len(hits)}/{len(keywords)}, eksik: {missing})"
        else:
            mark = f"YANLIS (0/{len(keywords)} kelime yok)"
        rows.append(f"  {mark:<44} | {q[:52]}")

    print("SORU BAZINDA SONUC")
    print("-" * 92)
    for r in rows:
        print(r)

    full_rate = (n_full / n * 100) if n else 0.0
    cov_rate = (cov_sum / n * 100) if n else 0.0
    print("\n" + "=" * 92)
    print("OZET  (uctan uca cevap kalitesi)")
    print("-" * 92)
    print(f"  Degerlendirilen soru      : {n}")
    print(f"  Cevap dogrulugu (tam)     : {n_full}/{n}  = %{full_rate:.1f}   (TUM anahtar kelimeler cevapta)")
    print(f"  Ortalama kelime kapsami   : %{cov_rate:.1f}           (kelimelerin ort. yuzde kaci gecti)")
    print(f"  Uretilemeyen (yanlis red) : {n_notgen}                 (guardrail cevabi engelledi)")
    print("=" * 92)
    print("\nIpucu: bir prompt/ayar degisiminden ONCE ve SONRA calistirip karsilastirin.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Uctan uca cevap degerlendirmesi")
    ap.add_argument("--set", default=str(Path(__file__).parent / "golden_set.jsonl"))
    ap.add_argument("--limit", type=int, default=None, help="sadece ilk N cevaplanabilir soru")
    args = ap.parse_args()
    path = Path(args.set)
    if not path.exists():
        print(f"[hata] golden set yok: {path}")
        sys.exit(1)
    golden = _load_golden(path)
    if not golden:
        print("[hata] golden set bos.")
        sys.exit(1)
    asyncio.run(_run(golden, args.limit))


if __name__ == "__main__":
    main()
