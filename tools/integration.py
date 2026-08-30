"""Canli sunucuya karsi entegrasyon testi: API + WebSocket + yeni ozellikler."""

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

ok, fail = 0, []


def check(name, cond, detail=""):
    global ok
    if cond:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail.append(name)
        print(f"  FAIL {name}  {detail}")


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=300) as c:
        # --- health: RAG durumu
        h = (await c.get("/api/health")).json()
        check("health rag blogu var", "rag" in h, f"-> {h.keys()}")
        check("mod hybrid", h["rag"]["mode"] == "hybrid", f"-> {h['rag']}")
        check("embed modeli mevcut", h["rag"]["embed_model_available"] is True)
        check("degraded false", h["rag"]["degraded"] is False)
        check("parca sayisi", h["rag"]["chunks"] > 100, f"-> {h['rag']['chunks']}")

        # --- giris deneme siniri
        for _ in range(9):
            r = await c.post("/api/login", json={"email": "yok@x.com", "password": "yanlis"})
        check("rate limit 429", r.status_code == 429, f"-> {r.status_code}")

        # --- gercek giris
        r = await c.post("/api/login", json={"email": env["ADMIN_EMAIL"], "password": env["ADMIN_PASSWORD"]})
        check("admin girisi", r.status_code == 200, f"-> {r.status_code} {r.text[:120]}")
        me = (await c.get("/api/me")).json()
        check("/api/me rol dondu", me.get("role") == "user", f"-> {me}")

        # --- dokuman kutuphanesi
        d = (await c.get("/api/admin/docs")).json()["docs"]
        check("belgeler listelendi", len(d) >= 3, f"-> {len(d)}")

        files = {"file": ("test_yukleme.md", io.BytesIO(b"Test belgesi: kirmizi panda bambu yer."), "text/markdown")}
        r = await c.post("/api/admin/docs", files=files)
        check("belge yuklendi", r.status_code == 201, f"-> {r.status_code} {r.text[:150]}")

        files = {"file": ("kotu.exe", io.BytesIO(b"MZ"), "application/octet-stream")}
        r = await c.post("/api/admin/docs", files=files)
        check("izinsiz tur reddedildi", r.status_code == 400, f"-> {r.status_code}")

        r = await c.put("/api/admin/docs/test_yukleme.md/roles", json={"roles": ["finans"]})
        check("rol atandi", r.status_code == 200, f"-> {r.status_code} {r.text[:120]}")
        d = {x["name"]: x for x in (await c.get("/api/admin/docs")).json()["docs"]}
        check("rol geri okundu", d["test_yukleme.md"]["roles"] == ["finans"], f"-> {d.get('test_yukleme.md')}")

        # --- yeniden indeksleme
        r = await c.post("/api/admin/reindex")
        check("reindex", r.status_code == 200 and r.json()["rag"]["chunks"] > 100, f"-> {r.text[:200]}")

        # --- rol bazli yetki: normal kullanici olustur
        await c.delete("/api/admin/users/calisan@x.com")
        r = await c.post("/api/admin/users", json={
            "email": "calisan@x.com", "password": "gucluParola1", "is_admin": False, "role": "ik"})
        check("kullanici olusturuldu", r.status_code in (201, 400), f"-> {r.status_code} {r.text[:120]}")
        cookies_admin = dict(c.cookies)

    # --- WebSocket: atif + oylama + gecmis
    async with httpx.AsyncClient(base_url=BASE, timeout=300) as c:
        await c.post("/api/login", json={"email": env["ADMIN_EMAIL"], "password": env["ADMIN_PASSWORD"]})
        cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())

        conv_id, msg_id, sources, answer = None, None, None, ""
        async with websockets.connect("ws://localhost:8000/ws/chat",
                                      additional_headers={"Cookie": cookie}) as ws:
            await ws.send(json.dumps({"message": "Yillik izin hakki kac gun?"}))
            parts = []
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=300))
                if m["type"] == "conversation":
                    conv_id = m["id"]
                elif m["type"] == "token":
                    parts.append(m["content"])
                elif m["type"] == "done":
                    msg_id, sources = m.get("message_id"), m.get("sources")
                    break
                elif m["type"] == "error":
                    check("ws hata yok", False, m["content"]); return
            answer = "".join(parts)
            check("konusma id geldi", conv_id is not None)
            check("cevap uretildi", "20" in answer, f"-> {answer[:120]}")
            check("mesaj id geldi", msg_id is not None)
            check("kaynak atifi geldi", sources and sources[0]["source"] == "sirket_izin_politikasi.md", f"-> {sources}")
            check("atifta snippet var", sources and sources[0].get("snippet"), f"-> {sources[0] if sources else None}")

            # takip sorusu: sikistirma olmadan bulunamayacak bir soru
            await ws.send(json.dumps({"message": "peki devri ne zamana kadar?", "conversation_id": conv_id}))
            parts = []
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=300))
                if m["type"] == "token":
                    parts.append(m["content"])
                elif m["type"] == "done":
                    followup_sources = m.get("sources")
                    break
            followup = "".join(parts)
            check("takip sorusu baglam buldu", bool(followup_sources), f"-> {followup[:150]}")
            check("takip cevabi mantikli", "mart" in followup.lower() or "31" in followup, f"-> {followup[:200]}")

        # --- oylama
        r = await c.post(f"/api/messages/{msg_id}/vote", json={"vote": -1})
        check("oy verildi", r.status_code == 200, f"-> {r.status_code} {r.text[:120]}")
        fb = (await c.get("/api/admin/feedback")).json()
        check("geri bildirim listesinde", any(x["id"] == msg_id for x in fb["down"]), f"-> {len(fb['down'])}")

        # --- gecmis
        convs = (await c.get("/api/conversations")).json()["conversations"]
        check("gecmiste konusma var", any(x["id"] == conv_id for x in convs), f"-> {convs[:2]}")
        this = [x for x in convs if x["id"] == conv_id][0]
        check("mesaj sayisi 4", this["message_count"] == 4, f"-> {this}")
        msgs = (await c.get(f"/api/conversations/{conv_id}")).json()["messages"]
        check("mesajlar kalici", len(msgs) == 4 and msgs[1]["sources"], f"-> {len(msgs)}")
        check("oy kalici", msgs[1]["vote"] == -1, f"-> {msgs[1]['vote']}")

    # --- rol bazli yetki: ik rolundeki kullanici finans belgesini gormemeli
    async with httpx.AsyncClient(base_url=BASE, timeout=300) as c2:
        r = await c2.post("/api/login", json={"email": "calisan@x.com", "password": "gucluParola1"})
        if r.status_code != 200:
            check("normal kullanici girisi", False, f"-> {r.status_code} {r.text[:120]}")
        else:
            check("normal kullanici girisi", True)
            check("admin ucu yasak", (await c2.get("/api/admin/docs")).status_code == 403)
            cookie2 = "; ".join(f"{k}={v}" for k, v in c2.cookies.items())
            async with websockets.connect("ws://localhost:8000/ws/chat",
                                          additional_headers={"Cookie": cookie2}) as ws:
                await ws.send(json.dumps({"message": "Kirmizi panda ne yer?"}))
                got = None
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=300))
                    if m["type"] == "done":
                        got = m.get("sources"); break
                # test_yukleme.md yalnizca "finans" rolune acik; bu kullanici "ik".
                check("yetkisiz belge suzuldu",
                      not any(s["source"] == "test_yukleme.md" for s in (got or [])), f"-> {got}")

    # temizlik
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        await c.post("/api/login", json={"email": env["ADMIN_EMAIL"], "password": env["ADMIN_PASSWORD"]})
        await c.delete("/api/admin/docs/test_yukleme.md")
        await c.delete("/api/admin/users/calisan@x.com")
        await c.post("/api/admin/reindex")

    print(f"\n{ok} gecti, {len(fail)} basarisiz")
    if fail:
        print("basarisiz:", ", ".join(fail))
    return 1 if fail else 0


sys.exit(asyncio.run(main()))
