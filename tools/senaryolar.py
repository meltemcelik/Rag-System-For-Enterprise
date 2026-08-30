"""Gercek kullanim senaryolari — ayni script iki surume de uygulanir.

    python senaryolar.py <base_url> <etiket>

Her senaryo bir KULLANICI YOLCULUGU. Desteklenmiyorsa "YOK" yazar (eski surumde
ozellik hic olmayabilir); bu bir hata degil, fark kaydidir.
"""

# Depo koku dosyanin kendi konumundan turetilir; sabit yol YAZILMAZ
# (bu betikler once depo disinda yazildi, oradan tasindi).
from pathlib import Path as _Path
_REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import asyncio, io, json, sys, time
from pathlib import Path
import httpx, websockets

BASE = sys.argv[1].rstrip("/")
ETIKET = sys.argv[2]
WSURL = BASE.replace("http://", "ws://") + "/ws/chat"

# Admin bilgileri ilgili surumun .env dosyasindan okunur (sabit yazilmaz).
_ENV_YOL = (Path(_REPO_ROOT) / ".env"
            if BASE.endswith("8000") else
            Path(__file__).parent / "before" / ".env")
_env = {}
if _ENV_YOL.exists():
    for _s in _ENV_YOL.read_text(encoding="utf-8").splitlines():
        _s = _s.strip()
        if _s and not _s.startswith("#") and "=" in _s:
            _k, _v = _s.split("=", 1)
            _env[_k.strip()] = _v.strip()
ADMIN_EMAIL = _env.get("ADMIN_EMAIL", "admin@example.com")
ADMIN_PASS = _env.get("ADMIN_PASSWORD", "admin")

sonuclar = []


def kaydet(senaryo, durum, detay="", sure=None):
    sonuclar.append({"senaryo": senaryo, "durum": durum, "detay": detay, "sure": sure})
    s = f" [{sure:.1f}s]" if sure else ""
    print(f"  {durum:8} {senaryo}{s}")
    if detay:
        print(f"           {detay}")


async def sor(cookie, mesaj, conversation_id=None, timeout=300):
    payload = {"message": mesaj}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    cikti = {"metin": "", "kaynaklar": None, "mesaj_id": None, "konusma": None,
             "ilk_token": None}
    parcalar = []
    t0 = time.perf_counter()
    async with websockets.connect(WSURL, additional_headers={"Cookie": cookie}) as ws:
        await ws.send(json.dumps(payload))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            if m["type"] == "conversation":
                cikti["konusma"] = m["id"]
            elif m["type"] == "token":
                if cikti["ilk_token"] is None:
                    cikti["ilk_token"] = time.perf_counter() - t0
                parcalar.append(m["content"])
            elif m["type"] == "done":
                cikti["kaynaklar"] = m.get("sources")
                cikti["mesaj_id"] = m.get("message_id")
                break
            elif m["type"] == "error":
                cikti["hata"] = m["content"]
                break
    cikti["metin"] = "".join(parcalar)
    cikti["toplam"] = time.perf_counter() - t0
    return cikti


async def main():
    print(f"\n{'=' * 70}\n{ETIKET}  ({BASE})\n{'=' * 70}")
    async with httpx.AsyncClient(base_url=BASE, timeout=600) as c:
        # --- S1: giris
        t0 = time.perf_counter()
        r = await c.post("/api/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS})
        sure = time.perf_counter() - t0
        if r.status_code != 200:
            kaydet("S1 Giris", "COKTU", f"HTTP {r.status_code}")
            return
        kaydet("S1 Giris", "OK", "", sure)
        cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())

        # --- S2: temel soru + kaynak atfi
        y = await sor(cookie, "Yillik izin hakki kac gun?")
        dogru = "20" in y["metin"]
        kaynak = [s["source"] for s in (y["kaynaklar"] or [])]
        kaydet("S2 Temel soru", "OK" if dogru else "YANLIS",
               f"cevap={y['metin'][:70]!r} | kaynak={kaynak or 'YOK'} | "
               f"ilk token={y['ilk_token']:.1f}s", y["toplam"])
        konusma = y["konusma"]

        # --- S3: takip sorusu (tek basina anlamsiz)
        t = await sor(cookie, "peki devri?", conversation_id=konusma)
        buldu = bool(t["kaynaklar"]) or ("mart" in t["metin"].lower() or "10" in t["metin"])
        reddetti = "bulamad" in t["metin"].lower()
        kaydet("S3 Takip sorusu", "OK" if (buldu and not reddetti) else "BASARISIZ",
               f"cevap={t['metin'][:70]!r} | kaynak={[s['source'] for s in (t['kaynaklar'] or [])] or 'YOK'}",
               t["toplam"])

        # --- S4: alakasiz soru -> reddetmeli
        a = await sor(cookie, "Kirmizi pandalar ne yer?")
        red = "bulamad" in a["metin"].lower()
        kaydet("S4 Alakasiz soru reddi", "OK" if red else "HALUSINASYON",
               f"cevap={a['metin'][:70]!r}", a["toplam"])

        # --- S5: Turkce karakterli soru
        tr = await sor(cookie, "Şirket çalışanlarının yıllık izin hakkı nedir?")
        kaydet("S5 Turkce karakterli soru", "OK" if "20" in tr["metin"] else "YANLIS",
               f"cevap={tr['metin'][:70]!r}", tr["toplam"])

        # --- S6: sohbet gecmisi kalici mi
        r = await c.get("/api/conversations")
        if r.status_code == 404:
            kaydet("S6 Sohbet gecmisi", "YOK", "endpoint yok (HTTP 404)")
        else:
            data = r.json()
            var = any(x["id"] == konusma for x in data.get("conversations", []))
            kaydet("S6 Sohbet gecmisi", "OK" if var else "BASARISIZ",
                   f"{len(data.get('conversations', []))} konusma listelendi")

        # --- S7: eski konusmayi acip devam etme
        r = await c.get(f"/api/conversations/{konusma}") if konusma else None
        if r is None or r.status_code == 404:
            kaydet("S7 Konusmaya devam", "YOK", "endpoint yok")
        else:
            once = len(r.json()["messages"])
            d = await sor(cookie, "kac gun onceden talep edilmeli?", conversation_id=konusma)
            sonra = len((await c.get(f"/api/conversations/{konusma}")).json()["messages"])
            kaydet("S7 Konusmaya devam", "OK" if sonra == once + 2 else "BASARISIZ",
                   f"{once} -> {sonra} mesaj (dogru konusmaya yazildi mi)", d["toplam"])

        # --- S8: oylama
        if y["mesaj_id"]:
            r = await c.post(f"/api/messages/{y['mesaj_id']}/vote", json={"vote": -1})
            if r.status_code == 404 and "mesaj" not in r.text:
                kaydet("S8 Oylama", "YOK", "endpoint yok")
            else:
                ok = r.status_code == 200
                fb = await c.get("/api/admin/feedback")
                listede = ok and fb.status_code == 200 and any(
                    x["id"] == y["mesaj_id"] for x in fb.json().get("down", []))
                kaydet("S8 Oylama", "OK" if listede else "BASARISIZ",
                       f"oy={r.status_code}, geri bildirim listesinde={listede}")
        else:
            kaydet("S8 Oylama", "YOK", "mesaj kimligi donmuyor")

        # --- S9: belge yukleme
        r = await c.post("/api/admin/docs", files={
            "file": ("senaryo_testi.md", io.BytesIO("Kırmızı panda bambu yer.".encode()), "text/markdown")})
        if r.status_code == 404:
            kaydet("S9 Belge yukleme", "YOK", "endpoint yok (HTTP 404)")
            yuklendi = False
        else:
            yuklendi = r.status_code == 201
            kaydet("S9 Belge yukleme", "OK" if yuklendi else "BASARISIZ", f"HTTP {r.status_code}")

        # --- S10: yeniden indeksleme + yeni belgeye soru
        if yuklendi:
            t0 = time.perf_counter()
            r = await c.post("/api/admin/reindex")
            reindex_sure = time.perf_counter() - t0
            kaydet("S10 Yeniden indeksleme", "OK" if r.status_code == 200 else "BASARISIZ",
                   f"HTTP {r.status_code}", reindex_sure)
            yeni = await sor(cookie, "Kirmizi pandalar ne yer?")
            bulundu = "bambu" in yeni["metin"].lower()
            kaydet("S10b Yeni belgeye soru", "OK" if bulundu else "BASARISIZ",
                   f"cevap={yeni['metin'][:70]!r}", yeni["toplam"])
        else:
            kaydet("S10 Yeniden indeksleme", "YOK", "belge yuklenemedi")
            kaydet("S10b Yeni belgeye soru", "YOK", "")

        # --- S11: Turkce dosya adi
        r = await c.post("/api/admin/docs", files={
            "file": ("Çalışan Rehberi.md", io.BytesIO("Test.".encode()), "text/markdown")})
        if r.status_code == 404:
            kaydet("S11 Turkce dosya adi", "YOK", "yukleme ucu yok")
        elif r.status_code == 201:
            ad = r.json().get("name")
            kaydet("S11 Turkce dosya adi", "OK" if ad == "Çalışan Rehberi.md" else "BOZULDU",
                   f"-> {ad!r}")
            await c.delete(f"/api/admin/docs/{ad}")
        else:
            kaydet("S11 Turkce dosya adi", "BASARISIZ", f"HTTP {r.status_code}")

        # --- S12: rol bazli yetki
        r = await c.put("/api/admin/docs/masraf_yonetmeligi.md/roles", json={"roles": ["finans"]})
        if r.status_code == 404:
            kaydet("S12 Rol bazli yetki", "YOK", "endpoint yok")
        else:
            await c.delete("/api/admin/users/senaryo@test.local")
            await c.post("/api/admin/users", json={
                "email": "senaryo@test.local", "password": "gucluParola1",
                "is_admin": False, "role": "ik"})
            async with httpx.AsyncClient(base_url=BASE, timeout=600) as u:
                await u.post("/api/login", json={"email": "senaryo@test.local", "password": "gucluParola1"})
                uck = "; ".join(f"{k}={v}" for k, v in u.cookies.items())
                g = await sor(uck, "Masraf limiti nedir?")
                sizdi = "masraf_yonetmeligi.md" in [s["source"] for s in (g["kaynaklar"] or [])]
                kaydet("S12 Rol bazli yetki", "OK" if not sizdi else "SIZINTI",
                       f"yetkisiz kullaniciya kaynak={[s['source'] for s in (g['kaynaklar'] or [])] or 'YOK'}")
                if g.get("konusma"):
                    await u.delete(f"/api/conversations/{g['konusma']}")
            await c.put("/api/admin/docs/masraf_yonetmeligi.md/roles", json={"roles": []})
            await c.delete("/api/admin/users/senaryo@test.local")

        # --- S13: sifre degistirme (ADMIN'de DEGIL, atilacak bir kullanicida:
        #     yeni sifre >=8 karakter dayatildigi icin admin'in kisa sifresine
        #     geri donulemiyor ve hesap kilitli kaliyordu)
        r = await c.post("/api/admin/users", json={
            "email": "sifre@test.local", "password": "IlkParola123", "is_admin": False})
        if r.status_code == 404:
            kaydet("S13 Sifre degistirme", "YOK", "kullanici ucu yok")
        else:
            async with httpx.AsyncClient(base_url=BASE, timeout=120) as u:
                lg = await u.post("/api/login", json={"email": "sifre@test.local",
                                                      "password": "IlkParola123"})
                if lg.status_code != 200:
                    kaydet("S13 Sifre degistirme", "YOK", "endpoint yok")
                else:
                    pr = await u.post("/api/password", json={
                        "current_password": "IlkParola123", "new_password": "YeniParola456"})
                    if pr.status_code == 404:
                        kaydet("S13 Sifre degistirme", "YOK", "endpoint yok")
                    else:
                        yeni = await u.post("/api/login", json={
                            "email": "sifre@test.local", "password": "YeniParola456"})
                        kaydet("S13 Sifre degistirme",
                               "OK" if pr.status_code == 200 and yeni.status_code == 200 else "BASARISIZ",
                               f"degistirme={pr.status_code}, yeni sifreyle giris={yeni.status_code}")
            await c.delete("/api/admin/users/sifre@test.local")

        # --- S14: kaba kuvvet korumasi
        async with httpx.AsyncClient(base_url=BASE, timeout=60) as anon:
            kodlar = []
            for _ in range(10):
                rr = await anon.post("/api/login", json={"email": "yok@test.local", "password": "x"})
                kodlar.append(rr.status_code)
        kaydet("S14 Kaba kuvvet korumasi", "OK" if 429 in kodlar else "YOK",
               f"kodlar={sorted(set(kodlar))} (429 = kilitlendi)")

        # --- S15: RAG saglik gorunurlugu
        h = (await c.get("/api/health")).json()
        rag = h.get("rag")
        kaydet("S15 RAG durum gorunurlugu", "OK" if rag else "YOK",
               f"rag={rag}" if rag else "health yalnizca model listesi donuyor")

        # --- S16: eszamanli 3 kullanici
        t0 = time.perf_counter()
        sonuc = await asyncio.gather(*[
            sor(cookie, q) for q in ("Yillik izin kac gun?", "Masraf limiti nedir?",
                                     "Parola kurallari nedir?")], return_exceptions=True)
        es_sure = time.perf_counter() - t0
        hata = [s for s in sonuc if isinstance(s, Exception)]
        kaydet("S16 Eszamanli 3 soru", "OK" if not hata else "HATA",
               f"hata={len(hata)}", es_sure)

        # --- temizlik
        if yuklendi:
            await c.delete("/api/admin/docs/senaryo_testi.md")
            await c.post("/api/admin/reindex")
        r = await c.get("/api/conversations")
        if r.status_code == 200:
            for x in r.json().get("conversations", []):
                await c.delete(f"/api/conversations/{x['id']}")

    print(f"\n--- {ETIKET} OZET ---")
    for d in ("OK", "YOK", "BASARISIZ", "YANLIS", "SIZINTI", "HALUSINASYON", "HATA", "COKTU"):
        n = sum(1 for s in sonuclar if s["durum"] == d)
        if n:
            print(f"  {d:14} {n}")
    Path(f"sonuc_{ETIKET}.json").write_text(
        json.dumps(sonuclar, ensure_ascii=False, indent=2), encoding="utf-8")


asyncio.run(main())
