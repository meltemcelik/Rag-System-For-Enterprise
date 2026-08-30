"""ACL suzmesi izinli parcalari top_k'dan disari itiyor mu?

Once suz sonra kes (dogru) ile once kes sonra suz (mevcut) farkini olcer.
Buyuk PDF kisitlanir; PDF cogu sorguda top_k'yi doldurdugu icin izinli kucuk
belgelerin sirasi asagi duser.
"""

# Depo koku dosyanin kendi konumundan turetilir; sabit yol YAZILMAZ
# (bu betikler once depo disinda yazildi, oradan tasindi).
from pathlib import Path as _Path
_REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import asyncio, os, sys
from pathlib import Path

ROOT = Path(_REPO_ROOT)
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)

from app import docs
from app.rag import RagConfig, get_retriever, parse_sources

PDF = "Enterprise RAG and Agentic AI in 2026_ Use Cases, Metrics, and Case Studies with Turkey Context.pdf"
CALISAN = {"email": "b", "is_admin": 0, "role": "ik"}

PROBES = [
    "kurumsal rag sistemlerinde retrieval kalitesi ve izin sureci",
    "sirket politikasi ve dokuman yonetimi",
    "guvenlik politikasi ve erisim kontrolu",
    "maliyet ve limit degerlendirmesi",
    "calisan haklari ve sure sinirlari",
]


async def main():
    docs.set_roles(PDF, ["ust-yonetim"])   # calisan goremez
    try:
        r = get_retriever()
        top_k = r.cfg.top_k
        print(f"top_k = {top_k}   kisitli belge = PDF (154 sayfa)\n")

        crowded = 0
        for q in PROBES:
            # MEVCUT davranis: top_k al, sonra suz
            ctx_now = await r.retrieve(q)
            got_now = docs.filter_context(ctx_now, CALISAN)

            # IDEAL davranis: genis havuz al, suz, sonra top_k kadar kes
            r.cfg.top_k = 20
            ctx_wide = await r.retrieve(q)
            r.cfg.top_k = top_k
            got_ideal = docs.filter_context(ctx_wide, CALISAN)[:top_k]

            n_now, n_ideal = len(got_now), len(got_ideal)
            kayip = n_ideal - n_now
            if kayip > 0:
                crowded += 1
            src_now = sorted({s["source"] for s in parse_sources(got_now)})
            src_ideal = sorted({s["source"] for s in parse_sources(got_ideal)})
            flag = "KAYIP" if kayip > 0 else "  ok "
            print(f"{flag} {q!r}")
            print(f"       mevcut (kes->suz): {n_now} parca {src_now or 'BOS'}")
            print(f"       ideal  (suz->kes): {n_ideal} parca {src_ideal or 'BOS'}")
            if kayip > 0:
                print(f"       -> kullanici {kayip} izinli parcayi KAYBEDIYOR")

        print(f"\n  {crowded}/{len(PROBES)} sorguda izinli parca top_k'dan disari itildi")
    finally:
        docs.set_roles(PDF, [])
        print("  (rol kurali geri alindi)")


asyncio.run(main())
