"""Uygulamayi gercek bir kullanici gibi surer: login -> /api/me -> admin/models -> WS sohbet."""

# Depo koku dosyanin kendi konumundan turetilir; sabit yol YAZILMAZ
# (bu betikler once depo disinda yazildi, oradan tasindi).
from pathlib import Path as _Path
_REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import asyncio, json, os, sys
import httpx
import websockets

BASE = "http://localhost:8000"
REPO = _REPO_ROOT

# .env'den admin bilgilerini oku (parolayi ekrana basma)
env = {}
with open(os.path.join(REPO, ".env"), encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()

EMAIL = env["ADMIN_EMAIL"]
PASSWORD = env["ADMIN_PASSWORD"]


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=120) as c:
        r = await c.get("/api/health")
        print(f"[1] GET /api/health -> {r.status_code} {r.text}")

        r = await c.post("/api/login", json={"email": EMAIL, "password": PASSWORD})
        print(f"[2] POST /api/login ({EMAIL}) -> {r.status_code} {r.text[:200]}")
        if r.status_code != 200:
            return 1
        cookies = c.cookies

        r = await c.get("/api/me")
        print(f"[3] GET /api/me -> {r.status_code} {r.text}")

        # Bu endpoint yerel (commitlenmemis) list_models_info degisikligini test eder
        r = await c.get("/api/admin/models")
        print(f"[4] GET /api/admin/models -> {r.status_code}")
        data = r.json()
        for m in data.get("models", []):
            if isinstance(m, dict):
                size = m.get("size")
                gb = f"{size/1e9:.2f} GB" if size else "?"
                print(f"      {m.get('name'):<28} {gb:>9}  "
                      f"{m.get('parameter_size')}  {m.get('quantization_level')}")
            else:
                print(f"      {m}   <-- DUZ STRING (yerel degisiklik etkin degil)")

    # WebSocket sohbet
    cookie_hdr = "; ".join(f"{k}={v}" for k, v in cookies.items())
    q = "Yillik izin hakki kac gun?"
    print(f"\n[5] WS /ws/chat  soru: {q!r}")
    async with websockets.connect(
        "ws://localhost:8000/ws/chat", additional_headers={"Cookie": cookie_hdr}
    ) as ws:
        await ws.send(json.dumps({"message": q}))
        out = []
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=180))
            if msg["type"] == "token":
                out.append(msg["content"])
            elif msg["type"] == "done":
                break
            elif msg["type"] == "error":
                print(f"      HATA: {msg['content']}")
                return 1
        print("      CEVAP:", "".join(out).strip()[:800])

    print("\nOK: tum adimlar gecti")
    return 0


sys.exit(asyncio.run(main()))
