"""Acik arayan testler — daha once dokunulmamis alanlar.

Amac gecmek degil, KOSE DURUMLARINDA kirilmayi bulmak.
Her bulgu icin uretilebilir kanit; kanitlanamayan "supheli" olarak isaretlenir.
"""

# Depo koku dosyanin kendi konumundan turetilir; sabit yol YAZILMAZ
# (bu betikler once depo disinda yazildi, oradan tasindi).
from pathlib import Path as _Path
_REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import asyncio, io, json, os, sys, time
import httpx, websockets

BASE, WS = "http://localhost:8000", "ws://localhost:8000/ws/chat"
REPO = _REPO_ROOT

env = {}
for line in open(os.path.join(REPO, ".env"), encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
ADMIN = (env["ADMIN_EMAIL"], env["ADMIN_PASSWORD"])

bulgular, temiz = [], []


def kaydet(ad, sorun_var, kanit):
    (bulgular if sorun_var else temiz).append((ad, kanit))
    print(f"  {'BULGU' if sorun_var else 'ok   '}  {ad}")
    print(f"         {kanit}")


async def sor(cookie, mesaj, conversation_id=None, timeout=300):
    payload = {"message": mesaj}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    out, parts = {"konusma": None, "mesaj_id": None, "kaynaklar": None}, []
    async with websockets.connect(WS, additional_headers={"Cookie": cookie}) as ws:
        await ws.send(json.dumps(payload))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            if m["type"] == "conversation":
                out["konusma"] = m["id"]
            elif m["type"] == "token":
                parts.append(m["content"])
            elif m["type"] == "done":
                out["mesaj_id"] = m.get("message_id")
                out["kaynaklar"] = m.get("sources")
                break
            elif m["type"] == "error":
                out["hata"] = m["content"]
                break
    out["metin"] = "".join(parts)
    return out


def ck(c):
    return "; ".join(f"{k}={v}" for k, v in c.cookies.items())


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=600) as c:
        await c.post("/api/login", json={"email": ADMIN[0], "password": ADMIN[1]})
        cookie = ck(c)

        # ---------------------------------------------------------------
        print("\n[A1] Cikis yapilinca eski cerez gecersiz oluyor mu?")
        async with httpx.AsyncClient(base_url=BASE, timeout=60) as u:
            await u.post("/api/login", json={"email": ADMIN[0], "password": ADMIN[1]})
            eski = ck(u)
            await u.post("/api/logout")
            r = await httpx.AsyncClient(base_url=BASE, timeout=60).get(
                "/api/me", headers={"Cookie": eski})
        kaydet("A1 cikis sonrasi token iptali", r.status_code == 200,
               f"cikistan SONRA eski cerezle /api/me -> HTTP {r.status_code} "
               f"({'hala gecerli, sunucu tarafi iptal YOK' if r.status_code == 200 else 'reddedildi'})")

        # ---------------------------------------------------------------
        print("\n[A2] E-posta buyuk/kucuk harf duyarli mi?")
        r = await httpx.AsyncClient(base_url=BASE, timeout=60).post(
            "/api/login", json={"email": ADMIN[0].upper(), "password": ADMIN[1]})
        kaydet("A2 e-posta buyuk harfle giris", r.status_code != 200,
               f"{ADMIN[0].upper()} -> HTTP {r.status_code} "
               f"({'kabul (dogru)' if r.status_code == 200 else 'RED (kullanici sasirir)'})")

        # ---------------------------------------------------------------
        print("\n[A3] Aktif konusma silinirken mesaj gonderilirse?")
        async with websockets.connect(WS, additional_headers={"Cookie": cookie}) as ws:
            await ws.send(json.dumps({"message": "Yillik izin kac gun?"}))
            konusma = None
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=300))
                if m["type"] == "conversation":
                    konusma = m["id"]
                elif m["type"] in ("done", "error"):
                    break
            await c.delete(f"/api/conversations/{konusma}")   # baglanti acikken sil
            hata = None
            try:
                await ws.send(json.dumps({"message": "devami?"}))
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=300))
                    if m["type"] in ("done", "error"):
                        break
            except Exception as exc:
                hata = f"{type(exc).__name__}: {str(exc)[:60]}"
        kalan = await c.get(f"/api/conversations/{konusma}")
        kaydet("A3 silinmis konusmaya yazma", hata is not None,
               f"WS hatasi={hata}; silinmis konusma sorgusu -> HTTP {kalan.status_code} "
               f"(mesaj sahipsiz kayda yazildiysa gorunmez olur)")

        # ---------------------------------------------------------------
        print("\n[A4] Ayni ada sahip belge tekrar yuklenirse?")
        await c.post("/api/admin/docs", files={
            "file": ("cakisma.md", io.BytesIO(b"BIRINCI icerik"), "text/markdown")})
        r2 = await c.post("/api/admin/docs", files={
            "file": ("cakisma.md", io.BytesIO(b"IKINCI icerik"), "text/markdown")})
        yol = os.path.join(REPO, "data", "docs", "cakisma.md")
        icerik = open(yol, encoding="utf-8").read() if os.path.exists(yol) else ""
        bildirdi = r2.status_code == 201 and r2.json().get("replaced") is True
        kaydet("A4 ayni ad ikinci yukleme", not bildirdi,
               f"HTTP {r2.status_code}, icerik={icerik!r}, replaced={r2.json().get('replaced')} "
               f"({'uzerine yazma BILDIRILIYOR' if bildirdi else 'SESSIZCE eziyor'})")
        await c.delete("/api/admin/docs/cakisma.md")

        # ---------------------------------------------------------------
        print("\n[A5] Yalnizca durak kelimelerden olusan soru?")
        r = await sor(cookie, "ve veya ile de da ki mi")
        kaydet("A5 durak kelime sorgusu", "hata" in r,
               f"cevap={r['metin'][:60]!r}, hata={r.get('hata')}")

        # ---------------------------------------------------------------
        print("\n[A6] Cok uzun mesaj (50 KB)?")
        uzun = "Yillik izin hakki kac gun? " + ("dolgu " * 8000)
        try:
            r = await sor(cookie, uzun, timeout=400)
            sonuc = f"cevap uzunlugu={len(r['metin'])}, hata={r.get('hata')}"
            sorun = "hata" in r
        except Exception as exc:
            sonuc, sorun = f"{type(exc).__name__}: {str(exc)[:70]}", True
        kaydet("A6 50 KB mesaj", sorun, sonuc)

        # ---------------------------------------------------------------
        print("\n[A7] Admin ayarlari sohbete gercekten yansiyor mu?")
        onceki = (await c.get("/api/admin/config")).json()
        await c.put("/api/admin/config", json={
            "system_prompt": "Her cevabina mutlaka 'KIRMIZIBALIK' kelimesiyle basla."})
        r = await sor(cookie, "Yillik izin kac gun?")
        yansidi = "KIRMIZIBALIK" in r["metin"].upper()
        await c.put("/api/admin/config", json={"system_prompt": onceki["system_prompt"]})
        kaydet("A7 system_prompt etkisi", not yansidi,
               f"cevap={r['metin'][:80]!r} (isaret kelime {'VAR' if yansidi else 'YOK'})")

        # ---------------------------------------------------------------
        print("\n[A8] Ayni kullanicidan es zamanli 2 WebSocket?")
        try:
            a, b = await asyncio.gather(
                sor(cookie, "Yillik izin kac gun?"),
                sor(cookie, "Masraf limiti nedir?"))
            ayri = a["konusma"] != b["konusma"]
            kaydet("A8 iki es zamanli oturum", not ayri,
                   f"konusmalar #{a['konusma']} ve #{b['konusma']} "
                   f"({'ayri (dogru)' if ayri else 'AYNI (karisma)'})")
            for k in (a["konusma"], b["konusma"]):
                if k:
                    await c.delete(f"/api/conversations/{k}")
        except Exception as exc:
            kaydet("A8 iki es zamanli oturum", True, f"{type(exc).__name__}: {exc}")

        # ---------------------------------------------------------------
        print("\n[A9] Buyuk/kucuk harf farkiyla rol atama?")
        await c.post("/api/admin/docs", files={
            "file": ("rol_testi.md", io.BytesIO(b"gizli veri"), "text/markdown")})
        await c.put("/api/admin/docs/rol_testi.md/roles", json={"roles": ["FINANS", "İK"]})
        acl = (await c.get("/api/admin/docs")).json()["docs"]
        kural = next((d["roles"] for d in acl if d["name"] == "rol_testi.md"), None)
        await c.delete("/api/admin/users/rolkullanici@test.local")
        await c.post("/api/admin/users", json={
            "email": "rolkullanici@test.local", "password": "gucluParola1",
            "is_admin": False, "role": "FINANS"})
        u2 = httpx.AsyncClient(base_url=BASE, timeout=300)
        await u2.post("/api/login", json={"email": "rolkullanici@test.local",
                                          "password": "gucluParola1"})
        me = (await u2.get("/api/me")).json()
        eslesme = me.get("role") in (kural or [])
        kaydet("A9 buyuk harfli rol eslesmesi", not eslesme,
               f"belge rolleri={kural}, kullanici rolu={me.get('role')!r} "
               f"({'eslesiyor' if eslesme else 'ESLESMIYOR -> yetki calismaz'})")
        await u2.aclose()
        await c.delete("/api/admin/users/rolkullanici@test.local")
        await c.delete("/api/admin/docs/rol_testi.md")

        # ---------------------------------------------------------------
        print("\n[A10] Gecmis siniri asilinca sohbet bozuluyor mu?")
        konu = None
        try:
            for i in range(12):
                r = await sor(cookie, f"Yillik izin kac gun? ({i+1}. kez)",
                              conversation_id=konu, timeout=300)
                konu = konu or r["konusma"]
                if "hata" in r:
                    raise RuntimeError(r["hata"])
            msgs = (await c.get(f"/api/conversations/{konu}")).json()["messages"]
            kaydet("A10 12 turluk sohbet", len(msgs) != 24,
                   f"{len(msgs)} mesaj kayitli (beklenen 24), son cevap saglikli")
        except Exception as exc:
            kaydet("A10 12 turluk sohbet", True, f"{type(exc).__name__}: {str(exc)[:80]}")
        if konu:
            await c.delete(f"/api/conversations/{konu}")

        # temizlik
        r = await c.get("/api/conversations")
        if r.status_code == 200:
            for x in r.json().get("conversations", []):
                await c.delete(f"/api/conversations/{x['id']}")
        await c.post("/api/admin/reindex")

    print(f"\n{'=' * 64}")
    print(f"BULGU: {len(bulgular)}   temiz: {len(temiz)}")
    for ad, kanit in bulgular:
        print(f"  * {ad}")


asyncio.run(main())
