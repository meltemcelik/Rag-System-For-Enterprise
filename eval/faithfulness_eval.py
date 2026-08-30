"""Sadakat (faithfulness) degerlendirmesi — cevap BAGLAMDA OLMAYAN bir sey uyduruyor mu?

answer_eval.py "dogru bilgi cevapta VAR mi?" diye bakar.
Bu script tersini sorar: "cevapta baglamda OLMAYAN (uydurma) bir bilgi var mi?"
Her cevaplanabilir soru icin:
  1) sistemin gercek cevabini uretir (uretim modeli, or. llama3.2:3b)
  2) bir yargic modele (qwen3) sorar: cevaptaki her bilgi baglamdan dogrulaniyor mu?
     EVET -> sadik (uydurma yok)   |   HAYIR -> uydurma var (halusinasyon)

Olctugu:
  * Sadakat orani : cevaplarin yuzde kaci tamamen baglamdan destekleniyor (ana metrik)
  * Uydurma       : baglamda olmayan bilgi eklenen cevaplar (halusinasyon riski)

Not: yargic bir LLM oldugu icin olcum yaklasiktir; amac mutlak deger degil,
bir DEGISIKLIK oncesi/sonrasi KARSILASTIRMADIR. Yargic modeli .env'deki
RAG_GROUNDCHECK_MODEL (qwen3:4b) ile ayni.

Kullanim (Ollama acik, proje kokunden):
  py eval/faithfulness_eval.py
  py eval/faithfulness_eval.py --limit 15     # hizli deneme
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
from app.rag import RagConfig, get_retriever, guard_reply, _parse_grounded, build_rag_messages  # noqa: E402


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


def _judge_messages(question: str, context: list[str], answer: str) -> list[dict]:
    block = "\n\n".join(context)
    user = (
        "Asagida bir BAGLAM ve bir CEVAP var. Gorevin: CEVAP'taki her bilgi "
        "(ozellikle sayilar, tarihler, kosullar) verilen BAGLAM'dan dogrulanabiliyor mu? "
        "Eger cevap baglamda OLMAYAN bir bilgi iceriyorsa bu bir uydurmadir. "
        "Sadece tek kelime yaz: her bilgi baglamdan dogrulaniyorsa EVET, "
        "baglamda olmayan bir bilgi varsa HAYIR. Baska hicbir sey yazma. /no_think\n\n"
        f"<baglam>\n{block}\n</baglam>\n\n<cevap>\n{answer}\n</cevap>"
    )
    return [
        {"role": "system", "content": "Sen titiz bir denetleyicisin; sadece EVET veya HAYIR dersin."},
        {"role": "user", "content": user},
    ]


async def _chat(model: str, messages: list[dict], think: bool = True) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": think,
        "options": {"temperature": 0},
    }
    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return ((resp.json().get("message") or {}).get("content") or "").strip()


async def _run(golden: list[dict], limit: int | None) -> None:
    retriever = get_retriever()
    judge_model = RagConfig().groundcheck_model
    answerable = [g for g in golden if not g.get("should_refuse", False)]
    if limit:
        answerable = answerable[:limit]
    print(f"cevap modeli = {settings.default_model}   |   yargic = {judge_model}   |   {len(answerable)} soru\n")

    n = 0
    n_faithful = 0
    n_notgen = 0
    rows: list[str] = []

    for item in answerable:
        q = item["question"]
        context = await retriever.retrieve(q)
        if guard_reply(retriever, context) is not None:
            n_notgen += 1
            rows.append(f"  URETILMEDI (guardrail reddetti)   | {q[:55]}")
            continue
        n += 1
        answer = await _chat(settings.default_model, build_rag_messages(q, context), think=False)
        verdict = await _chat(judge_model, _judge_messages(q, context, answer), think=False)
        faithful = _parse_grounded(verdict)   # EVET -> True (sadik), net HAYIR -> False (uydurma)
        if faithful:
            n_faithful += 1
            mark = "SADIK"
        else:
            mark = "UYDURMA (baglamda yok)"
        rows.append(f"  {mark:<32} | {q[:55]}")

    print("SORU BAZINDA SONUC")
    print("-" * 92)
    for r in rows:
        print(r)

    rate = (n_faithful / n * 100) if n else 0.0
    print("\n" + "=" * 92)
    print("OZET  (sadakat / faithfulness)")
    print("-" * 92)
    print(f"  Degerlendirilen cevap     : {n}")
    print(f"  Sadakat orani             : {n_faithful}/{n}  = %{rate:.1f}   (cevap tamamen baglamdan destekleniyor)")
    print(f"  Uydurma (halusinasyon)    : {n - n_faithful}                 (baglamda olmayan bilgi eklendi)")
    print(f"  Uretilemeyen (yanlis red) : {n_notgen}")
    print("=" * 92)
    print("\nIpucu: bir prompt/ayar degisiminden ONCE ve SONRA calistirip karsilastirin.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Sadakat (faithfulness) degerlendirmesi")
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
