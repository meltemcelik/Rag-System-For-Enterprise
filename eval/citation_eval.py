"""Kaynak atifi degerlendirmesi — kullaniciya GOSTERILEN kaynak dogru mu?

Sistem artik her cevabin altinda hangi belge(ler)den yararlandigini gosteriyor.
Bu script olcer: cevaplanabilir sorularda, gosterilen kaynaklar arasinda BEKLENEN
kaynak var mi (kullanici dogru belgeye yonlendiriliyor mu?). Ayrica ILK gosterilen
kaynagin dogru olup olmadigina bakar (kullanici en ustte dogruyu goruyor mu?).

Onemli: bu script LLM cagirmaz (cevap uretmez) — sadece retrieval + kaynak cikarimi.
O yuzden HIZLIDIR (birkac saniye).

Kullanim (proje kokunden):
  py eval/citation_eval.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.rag import get_retriever, guard_reply, sources_of  # noqa: E402


def _file(label: str) -> str:
    return re.sub(r",\s*sayfa\s*\d+\s*$", "", label).strip()


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


async def _run(golden: list[dict]) -> None:
    retriever = get_retriever()
    items = [g for g in golden if not g.get("should_refuse", False) and g.get("expected_sources")]
    print(f"{len(items)} cevaplanabilir soru (beklenen kaynagi olan)\n")

    n = 0
    n_cited = 0        # beklenen kaynak, gosterilenler arasinda
    n_top1 = 0         # ilk gosterilen kaynak = beklenen
    n_notgen = 0       # guardrail reddetti (kaynak gosterilmez)
    rows: list[str] = []

    for item in items:
        q = item["question"]
        expected = {e.strip() for e in item.get("expected_sources", [])}
        context = await retriever.retrieve(q)
        if guard_reply(retriever, context) is not None:
            n_notgen += 1
            rows.append(f"  URETILMEDI (guardrail reddetti)   | {q[:52]}")
            continue
        n += 1
        shown = sources_of(context)
        files = [_file(s) for s in shown]
        cited_ok = any(f in expected for f in files)
        top1_ok = bool(files) and files[0] in expected
        if cited_ok:
            n_cited += 1
        if top1_ok:
            n_top1 += 1
        if top1_ok:
            mark = "OK    (ilk kaynak dogru)"
        elif cited_ok:
            mark = "OK-alt (dogru kaynak var ama ilk degil)"
        else:
            mark = f"YANLIS (gosterilen: {files[:2]}, beklenen: {sorted(expected)})"
        rows.append(f"  {mark:<44} | {q[:52]}")

    print("SORU BAZINDA SONUC")
    print("-" * 92)
    for r in rows:
        print(r)

    cited_rate = (n_cited / n * 100) if n else 0.0
    top1_rate = (n_top1 / n * 100) if n else 0.0
    print("\n" + "=" * 92)
    print("OZET  (kaynak atifi dogrulugu)")
    print("-" * 92)
    print(f"  Degerlendirilen soru       : {n}")
    print(f"  Kaynak dogru (gosterildi)  : {n_cited}/{n}  = %{cited_rate:.1f}   (beklenen kaynak gosterilenler arasinda)")
    print(f"  Birinci kaynak dogru       : {n_top1}/{n}  = %{top1_rate:.1f}   (kullanici en ustte dogruyu goruyor)")
    print(f"  Uretilemeyen (yanlis red)  : {n_notgen}")
    print("=" * 92)
    print("\nNot: kaynaklar kullaniciya cevap metninin ICINDE degil, WebSocket 'done'")
    print("mesajinin 'sources' alaninda {source, page, snippet} olarak gonderilir ve")
    print("veritabanina da oyle yazilir. Bu script o kaynagin dogrulugunu olcer")
    print("(retrieval + kaynak cikarimi; LLM cagrilmaz).")


def main() -> None:
    ap = argparse.ArgumentParser(description="Kaynak atifi dogrulugu degerlendirmesi")
    ap.add_argument("--set", default=str(Path(__file__).parent / "golden_set.jsonl"))
    args = ap.parse_args()
    path = Path(args.set)
    if not path.exists():
        print(f"[hata] golden set yok: {path}")
        sys.exit(1)
    golden = _load_golden(path)
    if not golden:
        print("[hata] golden set bos.")
        sys.exit(1)
    asyncio.run(_run(golden))


if __name__ == "__main__":
    main()
