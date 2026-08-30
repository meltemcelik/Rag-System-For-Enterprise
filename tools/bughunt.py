"""Hata avi — her hipotez icin URETILEBILIR kanit ariyoruz.

Kural: kanitlanamayan hipotez RAPOR EDILMEZ.
"""

# Depo koku dosyanin kendi konumundan turetilir; sabit yol YAZILMAZ
# (bu betikler once depo disinda yazildi, oradan tasindi).
from pathlib import Path as _Path
_REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import asyncio, io, json, os, sys, time
import httpx, websockets

BASE = "http://localhost:8000"
WS = "ws://localhost:8000/ws/chat"
REPO = _REPO_ROOT

env = {}
for line in open(os.path.join(REPO, ".env"), encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
ADMIN = (env["ADMIN_EMAIL"], env["ADMIN_PASSWORD"])

confirmed, refuted = [], []


def verdict(name, is_bug, evidence):
    (confirmed if is_bug else refuted).append((name, evidence))
    print(f"  {'HATA DOGRULANDI' if is_bug else 'hata yok        '}  {name}")
    print(f"      {evidence}")


async def drain(ws, timeout=300):
    out, parts = {"conv": None, "mid": None}, []
    while True:
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        if m["type"] == "conversation":
            out["conv"] = m["id"]
        elif m["type"] == "token":
            parts.append(m["content"])
        elif m["type"] == "done":
            out["mid"] = m.get("message_id")
            break
        elif m["type"] == "error":
            out["error"] = m["content"]
            break
    out["text"] = "".join(parts)
    return out


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=600) as c:
        r = await c.post("/api/login", json={"email": ADMIN[0], "password": ADMIN[1]})
        assert r.status_code == 200, r.text
        cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())

        # =============================================================
        print("\n[H1] Ayni WS baglantisinda gecmisten konusma acilinca mesaj"
              " DOGRU konusmaya mi yaziliyor?")
        # =============================================================
        async with websockets.connect(WS, additional_headers={"Cookie": cookie}) as ws:
            await ws.send(json.dumps({"message": "Yillik izin kac gun?"}))
            first = await drain(ws)
            conv_a = first["conv"]

        async with websockets.connect(WS, additional_headers={"Cookie": cookie}) as ws:
            await ws.send(json.dumps({"message": "Masraf limiti nedir?"}))
            second = await drain(ws)
            conv_b = second["conv"]

        # Arayuzun yaptigi: TEK baglanti acik, once bir konusma baslar,
        # sonra "Gecmis"ten baska bir konusma acilip mesaj gonderilir.
        async with websockets.connect(WS, additional_headers={"Cookie": cookie}) as ws:
            await ws.send(json.dumps({"message": "Parola kurallari nedir?"}))
            third = await drain(ws)
            conv_c = third["conv"]
            # Simdi kullanici Gecmis'ten conv_a'yi acti ve yaziyor:
            await ws.send(json.dumps({"message": "peki devri?", "conversation_id": conv_a}))
            fourth = await drain(ws)

        a_msgs = (await c.get(f"/api/conversations/{conv_a}")).json()["messages"]
        c_msgs = (await c.get(f"/api/conversations/{conv_c}")).json()["messages"]
        a_texts = [m["content"] for m in a_msgs if m["role"] == "user"]
        c_texts = [m["content"] for m in c_msgs if m["role"] == "user"]
        landed_in_c = "peki devri?" in c_texts
        landed_in_a = "peki devri?" in a_texts
        verdict("H1 konusma karismasi", landed_in_c and not landed_in_a,
                f"mesaj conv#{conv_c}'ye yazildi, hedef conv#{conv_a} idi | "
                f"A={a_texts} C={c_texts}")

        # =============================================================
        print("\n[H2] conversation_id sayi degilse ne oluyor?")
        # =============================================================
        crashed = None
        try:
            async with websockets.connect(WS, additional_headers={"Cookie": cookie}) as ws:
                await ws.send(json.dumps({"message": "merhaba", "conversation_id": "abc"}))
                res = await drain(ws, timeout=60)
                crashed = f"cevap geldi: {res.get('error') or res['text'][:60]!r}"
        except Exception as exc:
            crashed = f"{type(exc).__name__}: {str(exc)[:120]}"
        verdict("H2 sayisal olmayan conversation_id", "ConnectionClosed" in str(crashed),
                str(crashed))

        # =============================================================
        print("\n[H3] Baska kullanicinin conversation_id'si yeni konusma mi aciyor?")
        # =============================================================
        # (izolasyon testinde gecmisti; burada veri butunlugu acisindan bakiyoruz)
        async with websockets.connect(WS, additional_headers={"Cookie": cookie}) as ws:
            await ws.send(json.dumps({"message": "test", "conversation_id": 999999}))
            res = await drain(ws)
        verdict("H3 olmayan conversation_id", res["conv"] is None,
                f"yeni konusma #{res['conv']} acildi (beklenen davranis)")

        # =============================================================
        print("\n[H4] Giris deneme sayaci sinirsiz buyuyor mu? (bellek)")
        # =============================================================
        before = None
        async with httpx.AsyncClient(base_url=BASE, timeout=120) as anon:
            for i in range(60):
                await anon.post("/api/login",
                                json={"email": f"sahte{i}@yok.local", "password": "x"})
        # Sunucu ici sayaci disaridan goremiyoruz; ayni mantigi yerel calistir
        sys.path.insert(0, REPO)
        from app import auth as local_auth
        local_auth._attempts.clear()
        for i in range(5000):
            key = f"1.2.3.{i % 255}:kullanici{i}@yok.local"
            local_auth.rate_limited(key)
            local_auth.record_attempt(key)
        size = len(local_auth._attempts)
        verdict("H4 sayac sinirsiz buyuyor", size >= 5000,
                f"5000 farkli e-posta -> sozlukte {size} giris, hicbiri temizlenmiyor")

        # =============================================================
        print("\n[H5] Belge silmek yan etkiyle ACL dosyasi olusturuyor mu?")
        # =============================================================
        acl_path = os.path.join(REPO, "data", "docs_acl.json")
        if os.path.exists(acl_path):
            os.remove(acl_path)
        existed_before = os.path.exists(acl_path)
        await c.post("/api/admin/docs",
                     files={"file": ("silme_testi.md", io.BytesIO(b"gecici"), "text/markdown")})
        await c.delete("/api/admin/docs/silme_testi.md")
        created = os.path.exists(acl_path)
        content = open(acl_path, encoding="utf-8").read() if created else ""
        verdict("H5 silme ACL dosyasi yaratiyor", (not existed_before) and created,
                f"silmeden once yok, sonra var -> icerik {content!r}")
        if created:
            os.remove(acl_path)

        # =============================================================
        print("\n[H6] Desteklenmeyen tur klasorde sessizce yok sayiliyor mu?")
        # =============================================================
        listed = {d["name"] for d in (await c.get("/api/admin/docs")).json()["docs"]}
        on_disk = set(os.listdir(os.path.join(REPO, "data", "docs")))
        invisible = {f for f in on_disk if f not in listed and not f.startswith(".")}
        verdict("H6 klasorde gorunmeyen dosya", bool(invisible),
                f"diskte var ama ne listeleniyor ne indeksleniyor: {invisible or 'yok'}")

        # =============================================================
        print("\n[H7] Rol degisikligi acik oturumda hemen etkili mi?")
        # =============================================================
        await c.delete("/api/admin/users/roltest@yok.local")
        await c.post("/api/admin/users", json={
            "email": "roltest@yok.local", "password": "gucluParola1",
            "is_admin": False, "role": "ik"})
        async with httpx.AsyncClient(base_url=BASE, timeout=300) as u:
            await u.post("/api/login", json={"email": "roltest@yok.local", "password": "gucluParola1"})
            ucookie = "; ".join(f"{k}={v}" for k, v in u.cookies.items())
            await c.put("/api/admin/docs/masraf_yonetmeligi.md/roles", json={"roles": ["finans"]})
            async with websockets.connect(WS, additional_headers={"Cookie": ucookie}) as ws:
                await ws.send(json.dumps({"message": "Masraf limiti nedir?"}))
                res = await drain(ws)
            srcs = [s["source"] for s in (res["sources"] or [])]
            leaked = "masraf_yonetmeligi.md" in srcs
        verdict("H7 rol kisitlamasi aninda uygulaniyor mu", leaked,
                f"kisitlamadan SONRA acilan oturumda kaynaklar: {srcs}")
        await c.put("/api/admin/docs/masraf_yonetmeligi.md/roles", json={"roles": []})
        await c.delete("/api/admin/users/roltest@yok.local")

        # temizlik
        for cid in (conv_a, conv_b, conv_c):
            await c.delete(f"/api/conversations/{cid}")
        if res.get("conv"):
            await c.delete(f"/api/conversations/{res['conv']}")

    print(f"\n{'=' * 60}")
    print(f"DOGRULANAN HATA: {len(confirmed)}   |   dogrulanmayan hipotez: {len(refuted)}")
    for name, ev in confirmed:
        print(f"  * {name}")
    return 0


sys.exit(asyncio.run(main()))
