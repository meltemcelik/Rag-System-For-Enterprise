"""Tarafsiz olcer: HEM eski HEM yeni rag.py ile calisir.

Yalnizca her iki surumde de bulunan get_retriever/guard_reply kullanir; kaynak
basligini kendi icinde ayristirir (yeni parse_sources'a bagimli degil). Boylece
iki agac ayni olcutle karsilastirilir.

Kullanim: python neutral_scorer.py <agac_kokü>
"""
import asyncio, json, os, re, sys, time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.rag import get_retriever, guard_reply  # noqa: E402

SOURCE_RE = re.compile(r"^\[kaynak:\s*(.+?)(?:,\s*sayfa\s*\d+)?\]\s*$")
GOLDEN = ROOT / "eval" / "golden_set.jsonl"

# Cok turlu takip sorulari — iki agacta da HAM sorguyla aranir (eski davranis).
MULTITURN = [
    "peki devri?", "ne zaman talep etmeliyim?", "onayi kim veriyor?",
    "makbuz sart mi?", "zorunlu mu?", "tasinabilir bellek serbest mi?",
    "son tarihi ne?", "altindakiler icin kim onaylar?",
]
MULTITURN_EXPECTED = [
    "sirket_izin_politikasi.md", "sirket_izin_politikasi.md", "masraf_yonetmeligi.md",
    "masraf_yonetmeligi.md", "bilgi_guvenligi.txt", "bilgi_guvenligi.txt",
    "sirket_izin_politikasi.md", "masraf_yonetmeligi.md",
]


def srcs(context):
    out = set()
    for piece in context:
        m = SOURCE_RE.match(piece.split("\n", 1)[0].strip())
        if m:
            out.add(m.group(1))
    return out


async def main():
    r = get_retriever()
    items = [json.loads(l) for l in GOLDEN.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")]

    refusal_ok, source_hits, source_total, wrong_refuse, missed_refuse = 0, 0, 0, 0, 0
    t0 = time.perf_counter()
    for it in items:
        ctx = await r.retrieve(it["question"])
        refused = guard_reply(r, ctx) is not None or not ctx
        should = bool(it.get("should_refuse"))
        refusal_ok += (refused == should)
        wrong_refuse += (refused and not should)
        missed_refuse += (not refused and should)
        exp = set(it.get("expected_sources") or [])
        if exp and not should:
            source_total += 1
            source_hits += bool(exp & srcs(ctx))
    elapsed = time.perf_counter() - t0

    mt_hits = 0
    for q, exp in zip(MULTITURN, MULTITURN_EXPECTED):
        mt_hits += exp in srcs(await r.retrieve(q))

    print(json.dumps({
        "agac": str(ROOT),
        "retriever": type(r).__name__,
        "altin_set_soru": len(items),
        "red_dogrulugu": round(refusal_ok / len(items), 4),
        "kaynak_isabeti": round(source_hits / source_total, 4) if source_total else None,
        "yanlis_red": wrong_refuse,
        "kacan_red": missed_refuse,
        "cok_turlu_isabet": f"{mt_hits}/{len(MULTITURN)}",
        "sure_sn": round(elapsed, 1),
    }, ensure_ascii=False, indent=2))


asyncio.run(main())
