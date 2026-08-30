"""Saldirgan test: yetki asimi, izolasyon, enjeksiyon, sinir degerleri.

Amac gecmek degil KIRMAK. Her basarisiz kontrol gercek bir bulgudur.
"""

# Depo koku dosyanin kendi konumundan turetilir; sabit yol YAZILMAZ
# (bu betikler once depo disinda yazildi, oradan tasindi).
from pathlib import Path as _Path
_REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import asyncio, io, json, os, sys
import httpx, websockets

BASE = "http://localhost:8000"
REPO = _REPO_ROOT

env = {}
for line in open(os.path.join(REPO, ".env"), encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

ADMIN = (env["ADMIN_EMAIL"], env["ADMIN_PASSWORD"])
ok, findings = 0, []


def check(name, cond, detail=""):
    """cond=True ise sistem dogru davrandi."""
    global ok
    if cond:
        ok += 1
        print(f"  ok      {name}")
    else:
        findings.append((name, detail))
        print(f"  BULGU   {name}  {detail}")


async def login(c, email, password):
    return await c.post("/api/login", json={"email": email, "password": password})


async def ws_ask(cookie, message, conversation_id=None, timeout=300):
    payload = {"message": message}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    out = {"tokens": [], "sources": None, "message_id": None, "conv": None}
    async with websockets.connect("ws://localhost:8000/ws/chat",
                                  additional_headers={"Cookie": cookie}) as ws:
        await ws.send(json.dumps(payload))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            if m["type"] == "conversation":
                out["conv"] = m["id"]
            elif m["type"] == "token":
                out["tokens"].append(m["content"])
            elif m["type"] == "done":
                out["sources"] = m.get("sources")
                out["message_id"] = m.get("message_id")
                break
            elif m["type"] == "error":
                out["error"] = m["content"]
                break
    out["text"] = "".join(out["tokens"])
    return out


async def main():
    # --- hazirlik: iki normal kullanici -----------------------------------
    async with httpx.AsyncClient(base_url=BASE, timeout=300) as adm:
        await login(adm, *ADMIN)
        for email in ("kurban@x.com", "saldirgan@x.com"):
            await adm.delete(f"/api/admin/users/{email}")
            r = await adm.post("/api/admin/users", json={
                "email": email, "password": "gucluParola1", "is_admin": False, "role": "ik"})
            if r.status_code not in (201, 400):
                print(f"hazirlik basarisiz: {email} -> {r.status_code} {r.text}")
                return 1
        admin_cookie = "; ".join(f"{k}={v}" for k, v in adm.cookies.items())

    print("\n[1] Yetkisiz erisim — normal kullanici admin uclarina")
    async with httpx.AsyncClient(base_url=BASE, timeout=300) as atk:
        await login(atk, "saldirgan@x.com", "gucluParola1")
        for method, path, kw in [
            ("GET", "/api/admin/users", {}),
            ("GET", "/api/admin/docs", {}),
            ("GET", "/api/admin/config", {}),
            ("GET", "/api/admin/models", {}),
            ("GET", "/api/admin/feedback", {}),
            ("POST", "/api/admin/reindex", {}),
            ("PUT", "/api/admin/config", {"json": {"temperature": 0.1}}),
            ("POST", "/api/admin/users", {"json": {"email": "z@x.com", "password": "p", "is_admin": True}}),
            ("DELETE", "/api/admin/users/kurban@x.com", {}),
            ("PUT", "/api/admin/users/kurban@x.com/role", {"json": {"role": "admin"}}),
            ("DELETE", "/api/admin/docs/masraf_yonetmeligi.md", {}),
            ("PUT", "/api/admin/docs/masraf_yonetmeligi.md/roles", {"json": {"roles": []}}),
        ]:
            r = await atk.request(method, path, **kw)
            check(f"403 {method} {path}", r.status_code == 403, f"-> {r.status_code}")

    print("\n[2] Giris yapmadan erisim")
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as anon:
        for path in ("/api/me", "/api/conversations", "/api/admin/users", "/api/admin/docs"):
            r = await anon.get(path)
            check(f"401 {path}", r.status_code == 401, f"-> {r.status_code}")
        try:
            async with websockets.connect("ws://localhost:8000/ws/chat") as ws:
                await ws.send(json.dumps({"message": "merhaba"}))
                await asyncio.wait_for(ws.recv(), timeout=10)
            check("WS cerezsiz reddedildi", False, "baglanti kabul edildi")
        except Exception:
            check("WS cerezsiz reddedildi", True)

    print("\n[3] Sahte / eski oturum cerezi")
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as f:
        # eski SECRET_KEY ile uretilmis gercek bir token (anahtar donduruldu)
        stale = ("eyJzdWIiOiJhZG1pbkBleGFtcGxlLmNvbSIsImV4cCI6MTc4NTQ5MzU2M30."
                 "ND9NucBHs6x1jbcZinQAWuv3ujB4rwBtSv_KP9X8phE")
        r = await f.get("/api/me", headers={"Cookie": f"session={stale}"})
        check("eski anahtarla imzali token reddedildi", r.status_code == 401, f"-> {r.status_code}")
        for bad in ["", "abc", "a.b", "eyJzdWIiOiJhZG1pbkBleGFtcGxlLmNvbSJ9.xxx"]:
            r = await f.get("/api/me", headers={"Cookie": f"session={bad}"})
            check(f"bozuk token reddedildi ({bad[:14] or 'bos'})", r.status_code == 401, f"-> {r.status_code}")

    print("\n[4] Kullanicilar arasi izolasyon")
    async with httpx.AsyncClient(base_url=BASE, timeout=300) as vic:
        await login(vic, "kurban@x.com", "gucluParola1")
        vic_cookie = "; ".join(f"{k}={v}" for k, v in vic.cookies.items())
        res = await ws_ask(vic_cookie, "Yillik izin hakki kac gun?")
        vic_conv, vic_msg = res["conv"], res["message_id"]
        check("kurban cevap aldi", bool(res["text"]), f"-> {res.get('error')}")

    async with httpx.AsyncClient(base_url=BASE, timeout=300) as atk:
        await login(atk, "saldirgan@x.com", "gucluParola1")
        atk_cookie = "; ".join(f"{k}={v}" for k, v in atk.cookies.items())
        r = await atk.get(f"/api/conversations/{vic_conv}")
        check("baskasinin konusmasi okunamaz", r.status_code == 404, f"-> {r.status_code}")
        r = await atk.delete(f"/api/conversations/{vic_conv}")
        check("baskasinin konusmasi silinemez", r.status_code == 404, f"-> {r.status_code}")
        r = await atk.post(f"/api/messages/{vic_msg}/vote", json={"vote": 1})
        check("baskasinin mesajina oy verilemez", r.status_code == 404, f"-> {r.status_code}")
        convs = (await atk.get("/api/conversations")).json()["conversations"]
        check("listede baskasinin konusmasi yok", all(c["id"] != vic_conv for c in convs), f"-> {convs}")
        # WS uzerinden baskasinin konusmasina yazmaya calis
        res = await ws_ask(atk_cookie, "devami ne?", conversation_id=vic_conv)
        check("baskasinin konusmasina devam edilemez", res["conv"] != vic_conv,
              f"-> conv {res['conv']} == kurban {vic_conv}")

    async with httpx.AsyncClient(base_url=BASE, timeout=60) as vic:
        await login(vic, "kurban@x.com", "gucluParola1")
        msgs = (await vic.get(f"/api/conversations/{vic_conv}")).json()["messages"]
        check("kurbanin konusmasi bozulmadi", len(msgs) == 2, f"-> {len(msgs)} mesaj")

    print("\n[5] Dosya yukleme — kotu girdiler")
    async with httpx.AsyncClient(base_url=BASE, timeout=300) as adm:
        await login(adm, *ADMIN)
        cases = [
            ("../../../../evil.md", b"x", "dizin gezinmesi"),
            ("..\\..\\evil.md", b"x", "windows dizin gezinmesi"),
            ("evil.exe", b"MZ", "calistirilabilir"),
            ("evil.md.exe", b"MZ", "cift uzanti"),
            ("", b"x", "bos ad"),
            (".gitignore", b"x", "gizli dosya"),
            ("bos.md", b"", "bos icerik"),
            ("buyuk.md", b"x" * (26 * 1024 * 1024), "boyut asimi"),
        ]
        for name, data, why in cases:
            r = await adm.post("/api/admin/docs",
                               files={"file": (name, io.BytesIO(data), "application/octet-stream")})
            check(f"reddedildi: {why}", r.status_code == 400, f"-> {r.status_code} {r.text[:80]}")

        # depo disina yazilmadi mi?
        import pathlib
        outside = pathlib.Path(REPO).parent / "evil.md"
        check("depo disina dosya yazilmadi", not outside.exists(), f"-> {outside}")

        r = await adm.delete("/api/admin/docs/../../../.env")
        check("path traversal ile silinemedi", r.status_code == 404, f"-> {r.status_code}")
        check(".env hala yerinde", os.path.exists(os.path.join(REPO, ".env")))

    print("\n[6] Enjeksiyon — belge icerigi arayuze sizabiliyor mu")
    async with httpx.AsyncClient(base_url=BASE, timeout=300) as adm:
        await login(adm, *ADMIN)
        payload = b"<script>window.XSS=1</script> Zararli belge testi: kirmizi panda bambu yer."
        r = await adm.post("/api/admin/docs",
                           files={"file": ("xss_test.md", io.BytesIO(payload), "text/markdown")})
        check("test belgesi yuklendi", r.status_code == 201, f"-> {r.text[:100]}")
        await adm.post("/api/admin/reindex")
        adm_cookie = "; ".join(f"{k}={v}" for k, v in adm.cookies.items())
        res = await ws_ask(adm_cookie, "kirmizi panda ne yer")
        src = [s["source"] for s in (res["sources"] or [])]
        check("zararli belge getirildi (beklenen)", "xss_test.md" in src, f"-> {src}")
        snippet = next((s["snippet"] for s in (res["sources"] or []) if s["source"] == "xss_test.md"), "")
        check("snippet ham script iceriyor (arayuz kacislamali)", "<script>" in snippet,
              f"-> {snippet[:60]}")

    print("\n[7] Sinir degerleri")
    async with httpx.AsyncClient(base_url=BASE, timeout=300) as adm:
        await login(adm, *ADMIN)
        r = await adm.post("/api/messages/999999/vote", json={"vote": 1})
        check("olmayan mesaja oy 404", r.status_code == 404, f"-> {r.status_code}")
        r = await adm.post("/api/messages/1/vote", json={"vote": 5})
        check("gecersiz oy degeri 422", r.status_code == 422, f"-> {r.status_code}")
        r = await adm.get("/api/conversations/999999")
        check("olmayan konusma 404", r.status_code == 404, f"-> {r.status_code}")
        r = await adm.put("/api/admin/config", json={"temperature": 9})
        check("gecersiz temperature 422", r.status_code == 422, f"-> {r.status_code}")
        r = await adm.post("/api/password", json={"current_password": "yanlis", "new_password": "yeniParola1"})
        check("yanlis mevcut sifre 403", r.status_code == 403, f"-> {r.status_code}")
        r = await adm.post("/api/password", json={"current_password": ADMIN[1], "new_password": "kisa"})
        check("kisa yeni sifre 422", r.status_code == 422, f"-> {r.status_code}")
        r = await adm.put("/api/admin/docs/olmayan_belge.md/roles", json={"roles": ["x"]})
        check("olmayan belgeye rol 404", r.status_code == 404, f"-> {r.status_code}")
        res = await ws_ask(adm_cookie, "   ")
        check("bos mesaj konusma yaratmadi", res.get("conv") is None, f"-> {res.get('conv')}")

    # --- temizlik
    async with httpx.AsyncClient(base_url=BASE, timeout=300) as adm:
        await login(adm, *ADMIN)
        await adm.delete("/api/admin/docs/xss_test.md")
        await adm.delete("/api/admin/users/kurban@x.com")
        await adm.delete("/api/admin/users/saldirgan@x.com")
        await adm.post("/api/admin/reindex")

    print(f"\n{ok} dogru davranis, {len(findings)} BULGU")
    for name, detail in findings:
        print(f"  - {name}: {detail}")
    return 1 if findings else 0


sys.exit(asyncio.run(main()))
