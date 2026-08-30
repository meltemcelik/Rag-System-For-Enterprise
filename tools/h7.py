"""H7: rol kisitlamasi, kisitlama ANINDA konulunca acik/yeni oturumda uygulaniyor mu?"""

# Depo koku dosyanin kendi konumundan turetilir; sabit yol YAZILMAZ
# (bu betikler once depo disinda yazildi, oradan tasindi).
from pathlib import Path as _Path
_REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import asyncio, json, os, sys
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
USER = ("roltest@yok.local", "gucluParola1")


async def ask(cookie, msg):
    out, parts = {"sources": [], "conv": None}, []
    async with websockets.connect(WS, additional_headers={"Cookie": cookie}) as ws:
        await ws.send(json.dumps({"message": msg}))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=300))
            if m["type"] == "conversation":
                out["conv"] = m["id"]
            elif m["type"] == "token":
                parts.append(m["content"])
            elif m["type"] == "done":
                out["sources"] = m.get("sources") or []
                break
            elif m["type"] == "error":
                out["error"] = m["content"]
                break
    out["text"] = "".join(parts)
    return out


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=600) as adm:
        await adm.post("/api/login", json={"email": ADMIN[0], "password": ADMIN[1]})
        await adm.put("/api/admin/docs/masraf_yonetmeligi.md/roles", json={"roles": []})
        await adm.delete(f"/api/admin/users/{USER[0]}")
        await adm.post("/api/admin/users", json={
            "email": USER[0], "password": USER[1], "is_admin": False, "role": "ik"})

        async with httpx.AsyncClient(base_url=BASE, timeout=600) as u:
            await u.post("/api/login", json={"email": USER[0], "password": USER[1]})
            ck = "; ".join(f"{k}={v}" for k, v in u.cookies.items())

            before = await ask(ck, "Masraf limiti nedir?")
            s_before = [s["source"] for s in before["sources"]]
            print(f"kisitlama YOKken kaynaklar : {s_before}")

            await adm.put("/api/admin/docs/masraf_yonetmeligi.md/roles",
                          json={"roles": ["finans"]})

            after = await ask(ck, "Masraf limiti nedir?")
            s_after = [s["source"] for s in after["sources"]]
            print(f"kisitlama VARken kaynaklar : {s_after}")
            print(f"cevap metni: {after['text'][:100]!r}")

            leaked = "masraf_yonetmeligi.md" in s_after
            print(f"\n{'HATA: kisitlama uygulanmadi' if leaked else 'ok: kisitlama aninda uygulandi'}")

            for cid in (before["conv"], after["conv"]):
                if cid:
                    await u.delete(f"/api/conversations/{cid}")

        await adm.put("/api/admin/docs/masraf_yonetmeligi.md/roles", json={"roles": []})
        await adm.delete(f"/api/admin/users/{USER[0]}")


asyncio.run(main())
