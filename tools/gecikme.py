"""Tekrarli gecikme olcumu — tek seferlik sonuclar cok gurultuluydu.

Her olcum 5 tekrar; medyan + en kotu. Iki surum ayni sorularla.
"""
import asyncio, json, statistics, sys, time
import httpx, websockets

HEDEFLER = [("ONCE", "http://localhost:8010"), ("SONRA", "http://localhost:8000")]
TEKRAR = 5
ADMIN = ("admin@example.com", "admin")

# Cevaplanan soru (LLM calisir) / reddedilen soru (LLM HIC calismaz -> saf retrieval)
CEVAPLANAN = "Yillik izin hakki kac gun?"
REDDEDILEN = "Kirmizi pandalar ne yer?"
TAKIP_GECMIS = "Yillik izin kac gun?"
TAKIP = "peki devri?"


async def olc(base, cookie, mesaj, conversation_id=None):
    ws_url = base.replace("http://", "ws://") + "/ws/chat"
    payload = {"message": mesaj}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    ilk, konusma = None, None
    t0 = time.perf_counter()
    async with websockets.connect(ws_url, additional_headers={"Cookie": cookie}) as ws:
        await ws.send(json.dumps(payload))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=300))
            if m["type"] == "conversation":
                konusma = m["id"]
            elif m["type"] == "token" and ilk is None:
                ilk = time.perf_counter() - t0
            elif m["type"] in ("done", "error"):
                break
    return {"ilk": ilk, "toplam": time.perf_counter() - t0, "konusma": konusma}


def ozet(ad, degerler):
    temiz = [d for d in degerler if d is not None]
    if not temiz:
        return f"  {ad:34} -"
    return (f"  {ad:34} medyan {statistics.median(temiz):6.2f}s   "
            f"en kotu {max(temiz):6.2f}s   n={len(temiz)}")


async def surum(etiket, base):
    print(f"\n### {etiket}  ({base})")
    async with httpx.AsyncClient(base_url=base, timeout=600) as c:
        await c.post("/api/login", json={"email": ADMIN[0], "password": ADMIN[1]})
        cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())

        # HTTP uc gecikmeleri
        for yol in ("/api/health", "/api/me"):
            sureler = []
            for _ in range(TEKRAR):
                t0 = time.perf_counter()
                await c.get(yol)
                sureler.append((time.perf_counter() - t0) * 1000)
            print(f"  {yol:34} medyan {statistics.median(sureler):6.1f}ms  "
                  f"en kotu {max(sureler):6.1f}ms")

        # Isinma (ilk cagri sogugu olcume katmasin)
        await olc(base, cookie, CEVAPLANAN)

        cevap_ilk, cevap_top, red_top, takip_top = [], [], [], []
        konusmalar = []
        for _ in range(TEKRAR):
            r = await olc(base, cookie, CEVAPLANAN)
            cevap_ilk.append(r["ilk"]); cevap_top.append(r["toplam"])
            konusmalar.append(r["konusma"])
            r = await olc(base, cookie, REDDEDILEN)
            red_top.append(r["toplam"]); konusmalar.append(r["konusma"])
            # takip sorusu: once baglam kur, sonra kisa takip
            g = await olc(base, cookie, TAKIP_GECMIS)
            konusmalar.append(g["konusma"])
            t = await olc(base, cookie, TAKIP, conversation_id=g["konusma"])
            takip_top.append(t["toplam"]); konusmalar.append(t["konusma"])

        print(ozet("cevaplanan: ilk token", cevap_ilk))
        print(ozet("cevaplanan: toplam", cevap_top))
        print(ozet("reddedilen (LLM yok = retrieval)", red_top))
        print(ozet("takip sorusu: toplam", takip_top))

        # temizlik
        r = await c.get("/api/conversations")
        if r.status_code == 200:
            for x in r.json().get("conversations", []):
                await c.delete(f"/api/conversations/{x['id']}")
        return {"cevap_ilk": cevap_ilk, "cevap_top": cevap_top,
                "red_top": red_top, "takip_top": takip_top}


async def main():
    veriler = {}
    for etiket, base in HEDEFLER:
        veriler[etiket] = await surum(etiket, base)

    print(f"\n{'=' * 72}\nKARSILASTIRMA (medyan)\n{'=' * 72}")
    print(f"  {'olcum':34} {'ONCE':>10} {'SONRA':>10} {'fark':>12}")
    for anahtar, ad in [("cevap_ilk", "cevaplanan: ilk token"),
                        ("cevap_top", "cevaplanan: toplam"),
                        ("red_top", "reddedilen (saf retrieval)"),
                        ("takip_top", "takip sorusu: toplam")]:
        o = [v for v in veriler["ONCE"][anahtar] if v is not None]
        s = [v for v in veriler["SONRA"][anahtar] if v is not None]
        if not o or not s:
            print(f"  {ad:34} {'-':>10} {'-':>10}")
            continue
        mo, ms = statistics.median(o), statistics.median(s)
        yuzde = (ms - mo) / mo * 100
        print(f"  {ad:34} {mo:9.2f}s {ms:9.2f}s {yuzde:+11.1f}%")


asyncio.run(main())
