"""Gercek hayat kullanim denemesi — bir calisanin gun icinde yasadigi akis.

Amac: golden set skoru degil, URUNUN gercekten ise yarayip yaramadigi. Her adimda
kullanicinin GORDUGU sey yazdirilir: cevap metni, gosterilen kaynak, gecen sure.

  python gercek_kullanim.py http://localhost:8000
"""
import asyncio, json, sys, time
import httpx, websockets

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
WS = BASE.replace("http://", "ws://").replace("https://", "wss://") + "/ws/chat"


def env(key, default=""):
    from pathlib import Path
    p = Path(__file__).resolve()
    for base in (p.parent, *p.parents):
        f = base / ".env"
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith(key + "="):
                    return line.split("=", 1)[1].strip()
    return default


ADMIN_MAIL = "admin@example.com"
ADMIN_PASS = "admin2026"

W = 100
def baslik(s):
    print("\n" + "=" * W); print(s); print("=" * W)

def satir(s=""):
    print(s)


async def giris(client, mail, sifre):
    r = await client.post(f"{BASE}/api/login", json={"email": mail, "password": sifre})
    r.raise_for_status()
    return r.cookies.get("session") or client.cookies.get("session")


async def sor(cookie, soru, conv_id=None, goster=True):
    """Tek soru sorar, kullanicinin gordugu cevabi ve kaynaklari dondurur."""
    t0 = time.perf_counter()
    parcalar, kaynaklar, cid, ilk_token = [], [], conv_id, None
    async with websockets.connect(WS, additional_headers={"Cookie": f"session={cookie}"}) as ws:
        msg = {"message": soru}
        if conv_id:
            msg["conversation_id"] = conv_id
        await ws.send(json.dumps(msg))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=180))
            t = m.get("type")
            if t == "conversation":
                cid = m.get("id")
            elif t == "token":
                if ilk_token is None:
                    ilk_token = time.perf_counter() - t0
                parcalar.append(m.get("content", ""))
            elif t == "done":
                kaynaklar = m.get("sources") or []
                break
            elif t == "error":
                parcalar.append(f"[HATA] {m.get('content')}")
                break
    sure = time.perf_counter() - t0
    cevap = "".join(parcalar).strip()
    if goster:
        satir(f"\n  KULLANICI: {soru}")
        satir(f"  ASISTAN  : {cevap}")
        if kaynaklar:
            for k in kaynaklar:
                sf = k.get("source"); sy = k.get("page")
                satir(f"     kaynak > {sf}" + (f", sayfa {sy}" if sy else ""))
        else:
            satir("     kaynak > (yok)")
        satir(f"     sure   > ilk token {ilk_token:.2f}s | toplam {sure:.2f}s")
    return cevap, kaynaklar, cid, sure


async def main():
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        cookie = await giris(client, ADMIN_MAIL, ADMIN_PASS)
        hdr = {"Cookie": f"session={cookie}"}

        baslik("DENEME 1 — Yeni calisan: izin politikasi ve TAKIP sorulari")
        satir("Amac: asistan 'peki devri?' gibi baglama bagli kisa sorulari anliyor mu?")
        _, _, cid, _ = await sor(cookie, "Yillik iznim kac gun?")
        await sor(cookie, "Peki devri ne olacak?", cid)
        await sor(cookie, "Talebi kime iletiyorum?", cid)

        baslik("DENEME 2 — Masraf: gunluk hayatta sorulan somut sorular")
        _, _, cid2, _ = await sor(cookie, "Is yemegi icin gunluk limit ne kadar?")
        await sor(cookie, "Fatura olmadan olur mu?", cid2)

        baslik("DENEME 3 — Bilgi guvenligi")
        _, _, cid3, _ = await sor(cookie, "Parolam kac karakter olmali?")
        await sor(cookie, "Ne siklikla degistirmem gerekiyor?", cid3)

        baslik("DENEME 4 — Belgede OLMAYAN sorular (guardrail gercek hayatta calisiyor mu?)")
        satir("Amac: uydurmak yerine 'bilmiyorum' diyebiliyor mu?")
        for s in ["Sirket aracı tahsis ediliyor mu?",
                  "Dogum izni kac hafta?",
                  "Kirmizi pandalar ne yer?"]:
            await sor(cookie, s)

        baslik("DENEME 5 — Bozuk yazim / Turkce karakter olmadan (gercek kullanicilar boyle yazar)")
        for s in ["yillik izin kac gun",
                  "PAROLA KURALLARI NELER?",
                  "izn talebi nasil yapilir"]:
            await sor(cookie, s)

        baslik("DENEME 6 — Sohbet gecmisi: kullanici sayfayi yenileyince kaynaklar duruyor mu?")
        r = await client.get(f"{BASE}/api/conversations", headers=hdr)
        veri = r.json()
        konusmalar = veri.get("conversations", veri) if isinstance(veri, dict) else veri
        satir(f"  Kayitli konusma sayisi: {len(konusmalar)}")
        if konusmalar:
            k = konusmalar[0]
            r2 = await client.get(f"{BASE}/api/conversations/{k['id']}", headers=hdr)
            m_veri = r2.json()
            mesajlar = m_veri.get("messages", m_veri) if isinstance(m_veri, dict) else m_veri
            satir(f"  En son konusma  : {k.get('title','(baslik yok)')[:60]}")
            satir(f"  Mesaj sayisi    : {len(mesajlar)}")
            kayitli = [m for m in mesajlar if m.get("sources")]
            satir(f"  Kaynagi saklanan mesaj: {len(kayitli)}")
            if kayitli:
                satir(f"  Ornek kaynak    : {kayitli[0]['sources'][0]}")

        baslik("DENEME 7 — Yeni belge yukle, hemen sor (kurumsal gercek: politika degisti)")
        icerik = (
            "# Uzaktan Calisma Politikasi\n\n"
            "Calisanlar haftada en fazla 3 gun uzaktan calisabilir. Uzaktan calisma "
            "talebi en gec bir onceki Cuma saat 17:00'ye kadar yoneticiye iletilir.\n\n"
            "Uzaktan calisilan gunlerde ogle yemegi karti yuklemesi yapilmaz.\n"
        ).encode("utf-8")
        files = {"file": ("uzaktan_calisma.md", icerik, "text/markdown")}
        r = await client.post(f"{BASE}/api/admin/docs", files=files, headers=hdr)
        satir(f"  Yukleme: HTTP {r.status_code} -> {r.json() if r.status_code < 400 else r.text[:100]}")
        t0 = time.perf_counter()
        r = await client.post(f"{BASE}/api/admin/reindex", headers=hdr)
        satir(f"  Yeniden indeksleme: HTTP {r.status_code}, {time.perf_counter()-t0:.1f}s")
        await sor(cookie, "Haftada kac gun uzaktan calisabilirim?")
        await sor(cookie, "Uzaktan calisma talebini ne zamana kadar iletmeliyim?")

        baslik("DENEME 8 — Rol bazli yetki: gizli belgeyi yetkisiz kullanici goruyor mu?")
        await client.post(f"{BASE}/api/admin/users",
                          json={"email": "stajyer@example.com", "password": "stajyer2026", "role": "stajyer"},
                          headers=hdr)
        r = await client.put(f"{BASE}/api/admin/docs/uzaktan_calisma.md/roles",
                             json={"roles": ["yonetici"]}, headers=hdr)
        satir(f"  Belgeye 'yonetici' rolu atandi: HTTP {r.status_code}")
        try:
            s_cookie = await giris(client, "stajyer@example.com", "stajyer2026")
            cevap, kaynaklar, _, _ = await sor(s_cookie, "Haftada kac gun uzaktan calisabilirim?")
            sizdi = any("uzaktan" in (k.get("source") or "").lower() for k in kaynaklar)
            satir(f"\n  >>> SONUC: {'SIZINTI VAR' if sizdi else 'Sizinti yok — yetkisiz kullanici belgeyi goremedi'}")
        except Exception as e:
            satir(f"  [stajyer girisi basarisiz: {e}]")

        baslik("DENEME 9 — Ayni soruyu tekrar sormak (sorgu onbellegi gercek hayatta)")
        _, _, _, s1 = await sor(cookie, "Yillik iznim kac gun?", goster=False)
        _, _, _, s2 = await sor(cookie, "Yillik iznim kac gun?", goster=False)
        satir(f"  Ilk sorus : {s1:.2f}s")
        satir(f"  Tekrar    : {s2:.2f}s")
        satir(f"  Fark      : {s1-s2:+.2f}s  ({'hizlandi' if s2 < s1 else 'fark yok'})")

        baslik("DENEME 10 — Temizlik: deneme belgesini kaldir")
        r = await client.delete(f"{BASE}/api/admin/docs/uzaktan_calisma.md", headers=hdr)
        satir(f"  Silme: HTTP {r.status_code}")
        r = await client.post(f"{BASE}/api/admin/reindex", headers=hdr)
        satir(f"  Yeniden indeksleme: HTTP {r.status_code}")
        r = await client.delete(f"{BASE}/api/admin/users/stajyer@example.com", headers=hdr)
        satir(f"  Deneme kullanicisi silindi: HTTP {r.status_code}")


asyncio.run(main())
