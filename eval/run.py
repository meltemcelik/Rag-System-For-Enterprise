"""Altin set regresyon kapisi — tek komutla olcum, CI'da kirar.

Onceki teshis scriptleri (diagnose*.py, sweep_embed.py) tek tek arastirma icin;
bu script tersini yapar: TEK bir ozet metrik uretir ve esigin altina duserse
sifir olmayan cikis kodu verir. Boylece retrieval regresyonu fark edilmeden
gecmez.

Olculenler:
  * kaynak isabeti  — dogru belge getirilen parcalar arasinda mi (retrieval)
  * red dogrulugu   — cevaplanabilir soru reddedildi mi / reddedilmesi gereken gecti mi
  * anahtar kelime  — (--answers ile) uretilen cevap beklenen degeri iceriyor mu

ONEMLI — kapsam: varsayilan olarak RETRIEVER olculur, sohbet hatti degil.
Takip sorusu sikistirmasi (app/query.py) main.py'de, retriever'in disindadir;
bu yuzden --multiturn olmadan bu ozellik OLCULMEZ. Regresyon kapisini sohbet
hattini da kapsayacak sekilde kurmak icin --multiturn kullanin.

Kullanim (proje kokunden, Ollama + embedding modeli acikken):
    python eval/run.py                      # yalnizca retrieval (hizli, LLM yok)
    python eval/run.py --multiturn          # takip sorulari da olculur (LLM gerekir)
    python eval/run.py --answers            # cevap uretimi de olculur (yavas)
    python eval/run.py --fail-under 0.80    # CI kapisi
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.rag import get_retriever, guard_reply, parse_sources  # noqa: E402

GOLDEN = Path(__file__).parent / "golden_set.jsonl"
MULTITURN = Path(__file__).parent / "multiturn_set.jsonl"


def load_golden() -> list[dict]:
    items = []
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            items.append(json.loads(line))
    return items


async def _answer(question: str, context: list[str]) -> str:
    """Cevap uretimi (--answers). main.py ile ayni prompt'u kullanir."""
    from app.config import settings
    from app.main import build_messages
    from app.ollama import OllamaClient

    client = OllamaClient(settings.ollama_base_url)
    messages = build_messages(settings.system_prompt, [], question, context)
    return await client.complete(settings.default_model, messages, temperature=0.0, timeout=180)


async def evaluate_multiturn() -> dict:
    """Sohbet hattini olcer: ham takip sorusu vs sikistirilmis sorgu.

    Retriever testinin goremedigi kismi kapatir — app/query.py devrede.
    """
    from app.config import settings
    from app.ollama import OllamaClient
    from app.query import condense

    if not MULTITURN.exists():
        return {"atlandi": f"{MULTITURN.name} yok"}

    items = [json.loads(l) for l in MULTITURN.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")]
    retriever = get_retriever()
    ollama = OllamaClient(settings.ollama_base_url)
    rows = []
    for item in items:
        history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": turn}
            for i, turn in enumerate(item["history"])
        ]
        followup, expected = item["followup"], set(item.get("expected_sources") or [])
        raw_src = {s["source"] for s in parse_sources(await retriever.retrieve(followup))}
        condensed = await condense(ollama, settings.default_model, history, followup)
        new_src = {s["source"] for s in parse_sources(await retriever.retrieve(condensed))}
        rows.append({
            "followup": followup,
            "condensed": condensed,
            "raw_hit": bool(expected & raw_src),
            "condensed_hit": bool(expected & new_src),
        })
    n = len(rows)
    return {
        "vaka": n,
        "ham_isabet": round(sum(r["raw_hit"] for r in rows) / n, 4) if n else None,
        "sikistirilmis_isabet": round(sum(r["condensed_hit"] for r in rows) / n, 4) if n else None,
        "bozulan": sum(1 for r in rows if r["raw_hit"] and not r["condensed_hit"]),
        "rows": rows,
    }


async def evaluate(with_answers: bool) -> dict:
    retriever = get_retriever()
    items = load_golden()
    rows = []
    for item in items:
        question = item["question"]
        context = await retriever.retrieve(question)
        refused = guard_reply(retriever, context) is not None or not context
        got = {s["source"] for s in parse_sources(context)}
        expected = set(item.get("expected_sources") or [])
        should_refuse = bool(item.get("should_refuse"))

        row = {
            "question": question,
            "should_refuse": should_refuse,
            "refused": refused,
            "refusal_ok": refused == should_refuse,
            "sources": sorted(got),
            "source_hit": bool(expected & got) if expected and not should_refuse else None,
        }
        if with_answers and not refused:
            answer = await _answer(question, context)
            keywords = item.get("answer_keywords") or []
            row["answer"] = answer
            row["keyword_hit"] = all(k.lower() in answer.lower() for k in keywords) if keywords else None
        rows.append(row)
    return {"rows": rows, "metrics": summarize(rows)}


def _ratio(hits: list[bool]) -> float | None:
    return round(sum(hits) / len(hits), 4) if hits else None


def summarize(rows: list[dict]) -> dict:
    refusal = [r["refusal_ok"] for r in rows]
    source = [r["source_hit"] for r in rows if r["source_hit"] is not None]
    keyword = [r["keyword_hit"] for r in rows if r.get("keyword_hit") is not None]
    metrics = {
        "toplam": len(rows),
        "red_dogrulugu": _ratio(refusal),
        "kaynak_isabeti": _ratio(source),
        "yanlis_red": sum(1 for r in rows if r["refused"] and not r["should_refuse"]),
        "kacan_red": sum(1 for r in rows if not r["refused"] and r["should_refuse"]),
    }
    if keyword:
        metrics["anahtar_kelime_isabeti"] = _ratio(keyword)
    scores = [v for k, v in metrics.items() if k.endswith("isabeti") or k == "red_dogrulugu"]
    metrics["skor"] = round(sum(scores) / len(scores), 4) if scores else 0.0
    return metrics


def main() -> int:
    ap = argparse.ArgumentParser(description="Altin set regresyon kapisi")
    ap.add_argument("--answers", action="store_true", help="cevap uretimini de olc (yavas)")
    ap.add_argument("--multiturn", action="store_true",
                    help="takip sorularini da olc (sohbet hatti; LLM gerekir)")
    ap.add_argument("--fail-under", type=float, default=None, help="skor bu degerin altindaysa hata ver")
    ap.add_argument("--json", type=Path, default=None, help="ayrintili sonucu JSON olarak yaz")
    ap.add_argument("--verbose", action="store_true", help="basarisiz satirlari listele")
    args = ap.parse_args()

    result = asyncio.run(evaluate(args.answers))
    metrics = result["metrics"]

    print("\n=== Altin set (retriever) ===")
    for key, value in metrics.items():
        print(f"  {key:24} {value}")

    if args.multiturn:
        mt = asyncio.run(evaluate_multiturn())
        print("\n=== Takip sorulari (sohbet hatti) ===")
        if "atlandi" in mt:
            print(f"  atlandi: {mt['atlandi']}")
        else:
            print(f"  {'vaka':24} {mt['vaka']}")
            print(f"  {'ham sorgu isabeti':24} {mt['ham_isabet']}")
            print(f"  {'sikistirilmis isabet':24} {mt['sikistirilmis_isabet']}")
            print(f"  {'sikistirma bozdu':24} {mt['bozulan']}")
            result["multiturn"] = mt
            if args.verbose:
                for row in mt["rows"]:
                    mark = "+" if row["condensed_hit"] and not row["raw_hit"] else (
                        "-" if row["raw_hit"] and not row["condensed_hit"] else " ")
                    print(f"   {mark} {row['followup']!r} -> {row['condensed']!r}")

    failures = [
        r for r in result["rows"]
        if not r["refusal_ok"] or r["source_hit"] is False or r.get("keyword_hit") is False
    ]
    if failures and args.verbose:
        print(f"\n--- basarisiz ({len(failures)}) ---")
        for r in failures:
            reason = "red" if not r["refusal_ok"] else ("kaynak" if r["source_hit"] is False else "kelime")
            print(f"  [{reason}] {r['question'][:80]}  -> {r['sources']}")

    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nAyrintili sonuc: {args.json}")

    if args.fail_under is not None and metrics["skor"] < args.fail_under:
        print(f"\nHATA: skor {metrics['skor']} < esik {args.fail_under}")
        return 1
    print(f"\nOK (skor {metrics['skor']}, basarisiz {len(failures)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
