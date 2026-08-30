"""Kendi iddialarimi olc. Uc soru:

A) Takip sorusu sikistirma GERCEKTEN ise yariyor mu? (tek ornekle iddia etmistim)
B) Sikistirma, zaten bagimsiz olan kisa sorulara ne kadar zarar/gecikme veriyor?
C) Rol bazli suzme, yetkili kullanicinin aldigi baglami ne kadar daraltiyor?
"""

# Depo koku dosyanin kendi konumundan turetilir; sabit yol YAZILMAZ
# (bu betikler once depo disinda yazildi, oradan tasindi).
from pathlib import Path as _Path
_REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import asyncio, os, statistics, sys, time
from pathlib import Path

ROOT = Path(_REPO_ROOT)
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)

from app import docs, query
from app.config import settings
from app.ollama import OllamaClient
from app.rag import get_retriever, parse_sources

# Gercek takip sorulari: tek baslarina HICBIR seye benzemiyorlar.
MULTITURN = [
    (["Yillik izin kac gun?", "Tam zamanli calisanlar icin 20 is gunu."],
     "peki devri?", "sirket_izin_politikasi.md"),
    (["Yillik izin kac gun?", "Tam zamanli calisanlar icin 20 is gunu."],
     "ne zaman talep etmeliyim?", "sirket_izin_politikasi.md"),
    (["Sehir ici ulasim masrafi nasil hesaplanir?", "Kilometre basina 7 TL."],
     "onayi kim veriyor?", "masraf_yonetmeligi.md"),
    (["Sehir ici ulasim masrafi nasil hesaplanir?", "Kilometre basina 7 TL."],
     "makbuz sart mi?", "masraf_yonetmeligi.md"),
    (["Parola kurallari nedir?", "En az 12 karakter olmalidir."],
     "zorunlu mu?", "bilgi_guvenligi.txt"),
    (["Parola kurallari nedir?", "En az 12 karakter olmalidir."],
     "tasinabilir bellek serbest mi?", "bilgi_guvenligi.txt"),
    (["Yillik izin devri nasil isliyor?", "En fazla 10 gun devredilebilir."],
     "son tarihi ne?", "sirket_izin_politikasi.md"),
    (["Masraf onay esigi nedir?", "5.000 TL uzeri direktor onayi ister."],
     "altindakiler icin kim onaylar?", "masraf_yonetmeligi.md"),
]

# Zaten bagimsiz ama KISA (<8 kelime) sorular -> gereksiz sikistirma riski
SHORT_STANDALONE = [
    ("Yillik izin kac gun?", "sirket_izin_politikasi.md"),
    ("Parola kurallari nedir?", "bilgi_guvenligi.txt"),
    ("Masraf onay esigi nedir?", "masraf_yonetmeligi.md"),
    ("Gunluk yemek limiti nedir?", "masraf_yonetmeligi.md"),
    ("2FA zorunlu mu?", "bilgi_guvenligi.txt"),
]


def hist(pairs):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": c}
            for i, c in enumerate(pairs)]


async def sources_for(retriever, q):
    ctx = await retriever.retrieve(q)
    return ctx, {s["source"] for s in parse_sources(ctx)}


async def main():
    retriever = get_retriever()
    ollama = OllamaClient(settings.ollama_base_url)
    model = settings.default_model
    print(f"model = {model}\n")

    # ---------- A) Cok turlu: sikistirmali vs sikistirmasiz ----------
    print("=" * 72)
    print("A) TAKIP SORUSU SIKISTIRMA — A/B")
    print("=" * 72)
    raw_hits, cond_hits, latencies = 0, 0, []
    for pairs, followup, expected in MULTITURN:
        h = hist(pairs)
        _, raw_src = await sources_for(retriever, followup)
        t0 = time.perf_counter()
        condensed = await query.condense(ollama, model, h, followup)
        latencies.append(time.perf_counter() - t0)
        _, cond_src = await sources_for(retriever, condensed)
        raw_ok, cond_ok = expected in raw_src, expected in cond_src
        raw_hits += raw_ok
        cond_hits += cond_ok
        flag = "  " if raw_ok == cond_ok else ("+ " if cond_ok else "- ")
        print(f"{flag}{followup!r}")
        print(f"     sikistirilmis: {condensed!r}")
        print(f"     ham -> {sorted(raw_src) or 'BOS'}  ({'isabet' if raw_ok else 'ISKA'})")
        print(f"     sik. -> {sorted(cond_src) or 'BOS'}  ({'isabet' if cond_ok else 'ISKA'})")
    n = len(MULTITURN)
    print(f"\n  ham sorguyla kaynak isabeti      : {raw_hits}/{n}")
    print(f"  sikistirilmis sorguyla isabet    : {cond_hits}/{n}")
    print(f"  sikistirma gecikmesi (medyan)    : {statistics.median(latencies):.1f} sn")

    # ---------- B) Kisa ama bagimsiz sorular ----------
    print("\n" + "=" * 72)
    print("B) KISA AMA BAGIMSIZ SORULAR — sikistirma zarar veriyor mu?")
    print("=" * 72)
    fake_hist = hist(["Merhaba", "Merhaba, nasil yardimci olabilirim?"])
    same, changed, hurt, helped, lat2 = 0, 0, 0, 0, []
    for q, expected in SHORT_STANDALONE:
        gated = query.needs_condensing(fake_hist, q)
        _, base_src = await sources_for(retriever, q)
        t0 = time.perf_counter()
        cond = await query.condense(ollama, model, fake_hist, q)
        lat2.append(time.perf_counter() - t0)
        _, cond_src = await sources_for(retriever, cond)
        if cond.strip().lower() == q.strip().lower():
            same += 1
        else:
            changed += 1
        b_ok, c_ok = expected in base_src, expected in cond_src
        if b_ok and not c_ok:
            hurt += 1
        if c_ok and not b_ok:
            helped += 1
        print(f"  {q!r}  (kapi: {'ACIK' if gated else 'kapali'})")
        print(f"     -> {cond!r}   {'AYNI' if cond == q else 'DEGISTI'}   "
              f"{'ZARAR' if (b_ok and not c_ok) else 'sorun yok'}")
    print(f"\n  degismeden kalan: {same}/{len(SHORT_STANDALONE)}   degisen: {changed}")
    print(f"  isabeti bozdugu vaka: {hurt}   duzelttigi vaka: {helped}")
    print(f"  bosa harcanan gecikme (medyan): {statistics.median(lat2):.1f} sn/soru")

    # ---------- C) Rol suzmesinin baglam maliyeti ----------
    print("\n" + "=" * 72)
    print("C) ROL BAZLI SUZME — yetkili kullanici ne kaybediyor?")
    print("=" * 72)
    admin = {"email": "a", "is_admin": 1, "role": "user"}
    calisan = {"email": "b", "is_admin": 0, "role": "ik"}
    docs.set_roles("masraf_yonetmeligi.md", ["finans"])
    try:
        probes = [
            "masraf limiti ve onay sureci",       # kisitli belgeye odakli
            "sirket politikalari genel bilgi",    # karisik
            "yillik izin ve devir kurallari",     # kisitsiz belgeye odakli
        ]
        for q in probes:
            ctx, _ = await sources_for(retriever, q)
            a_ctx = docs.filter_context(ctx, admin)
            c_ctx = docs.filter_context(ctx, calisan)
            kayip = len(a_ctx) - len(c_ctx)
            print(f"  {q!r}")
            print(f"     admin  : {len(a_ctx)} parca  {sorted({s['source'] for s in parse_sources(a_ctx)})}")
            print(f"     calisan: {len(c_ctx)} parca  {sorted({s['source'] for s in parse_sources(c_ctx)})}")
            print(f"     -> top_k'dan kaybedilen: {kayip}"
                  f"{'   (BOS BAGLAM -> red)' if not c_ctx else ''}")
    finally:
        docs.set_roles("masraf_yonetmeligi.md", [])
        print("\n  (rol kurali geri alindi)")


asyncio.run(main())
