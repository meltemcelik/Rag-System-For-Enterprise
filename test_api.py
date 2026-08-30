"""Uctan uca API/WebSocket testleri — CALISAN bir sunucuya karsi kosar.

test_features.py saf mantigi test eder (sunucusuz). Bu dosya gercek HTTP ve
WebSocket uzerinden gider: yetkilendirme, kullanicilar arasi izolasyon, dosya
yukleme sinirlari, oturum sahteciligi ve sohbet akisi.

Once sunucuyu baslatin:
    py run.py

Sonra:
    py test_api.py                # LLM gerektiren sohbet testleri dahil
    py test_api.py --hizli        # sohbeti atla (saniyeler surer)

Test kullanicilari ve belgeleri sonunda temizlenir.
"""
import argparse
import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import sys
import time
from pathlib import Path

try:
    import httpx
    import websockets
except ImportError:
    print("Eksik bagimlilik: py -m pip install httpx websockets")
    sys.exit(2)

BASE = "http://localhost:8000"
WS = "ws://localhost:8000/ws/chat"
ROOT = Path(__file__).resolve().parent

VICTIM = ("kurban@test.local", "gucluParola1")
ATTACKER = ("saldirgan@test.local", "gucluParola1")
TEST_DOC = "api_test_belgesi.md"

_passed = 0
_failed: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  ok      {name}")
    else:
        _failed.append((name, detail))
        print(f"  BASARISIZ {name}  {detail}")


def read_env() -> dict:
    out = {}
    path = ROOT / ".env"
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


ENV = read_env()
ADMIN = (ENV.get("ADMIN_EMAIL", "admin@example.com"), ENV.get("ADMIN_PASSWORD", "admin"))


def cookie_header(client: httpx.AsyncClient) -> str:
    return "; ".join(f"{k}={v}" for k, v in client.cookies.items())


async def ask(cookie: str, message: str, conversation_id=None,
              timeout: int = 300) -> dict:
    """Tek soru sor, cevabin tamamini bekle."""
    payload = {"message": message}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    result = {"text": "", "sources": None, "message_id": None, "conversation": None}
    parts = []
    async with websockets.connect(WS, additional_headers={"Cookie": cookie}) as ws:
        await ws.send(json.dumps(payload))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            if m["type"] == "conversation":
                result["conversation"] = m["id"]
            elif m["type"] == "token":
                parts.append(m["content"])
            elif m["type"] == "done":
                result["sources"] = m.get("sources")
                result["message_id"] = m.get("message_id")
                break
            elif m["type"] == "error":
                result["error"] = m["content"]
                break
    result["text"] = "".join(parts)
    return result


# --- 1. yetkilendirme ------------------------------------------------------
async def test_authorization():
    print("\n[1] Yetkilendirme — normal kullanici admin uclarina erisememeli")
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        await c.post("/api/login", json={"email": ATTACKER[0], "password": ATTACKER[1]})
        endpoints = [
            ("GET", "/api/admin/users", {}),
            ("GET", "/api/admin/docs", {}),
            ("GET", "/api/admin/config", {}),
            ("GET", "/api/admin/models", {}),
            ("GET", "/api/admin/feedback", {}),
            ("POST", "/api/admin/reindex", {}),
            ("PUT", "/api/admin/config", {"json": {"temperature": 0.1}}),
            ("POST", "/api/admin/users", {"json": {"email": "x@y.z", "password": "p", "is_admin": True}}),
            ("DELETE", f"/api/admin/users/{VICTIM[0]}", {}),
            ("PUT", f"/api/admin/users/{VICTIM[0]}/role", {"json": {"role": "admin"}}),
            ("PUT", f"/api/admin/docs/{TEST_DOC}/roles", {"json": {"roles": []}}),
            ("DELETE", f"/api/admin/docs/{TEST_DOC}", {}),
        ]
        for method, path, kwargs in endpoints:
            r = await c.request(method, path, **kwargs)
            check(f"403 {method} {path}", r.status_code == 403, f"-> {r.status_code}")


async def test_anonymous():
    print("\n[2] Kimlik dogrulamasiz erisim")
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        for path in ("/api/me", "/api/conversations", "/api/admin/users", "/api/admin/docs"):
            r = await c.get(path)
            check(f"401 {path}", r.status_code == 401, f"-> {r.status_code}")
        r = await c.post("/api/password", json={"current_password": "a", "new_password": "bbbbbbbb"})
        check("401 POST /api/password", r.status_code == 401, f"-> {r.status_code}")
    try:
        async with websockets.connect(WS) as ws:
            await ws.send(json.dumps({"message": "merhaba"}))
            await asyncio.wait_for(ws.recv(), timeout=10)
        check("WebSocket cerezsiz reddedildi", False, "baglanti kabul edildi")
    except Exception:
        check("WebSocket cerezsiz reddedildi", True)


async def test_forged_tokens():
    print("\n[3] Sahte oturum cerezleri")
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        bad = ["", "abc", "a.b", "....", "eyJzdWIiOiJhZG1pbkBleGFtcGxlLmNvbSJ9.xxxx",
               base64.urlsafe_b64encode(b'{"sub":"admin@example.com","exp":9999999999}').decode() + ".sig"]
        for token in bad:
            r = await c.get("/api/me", headers={"Cookie": f"session={token}"})
            check(f"reddedildi: {token[:18] or '(bos)'}", r.status_code == 401, f"-> {r.status_code}")

        # Varsayilan SECRET_KEY ile imzalanmis token kabul edilmemeli.
        payload = json.dumps({"sub": ADMIN[0], "exp": int(time.time()) + 3600},
                             separators=(",", ":")).encode()
        b64 = lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip("=")
        sig = hmac.new(b"change-me-in-production", payload, hashlib.sha256).digest()
        forged = b64(payload) + "." + b64(sig)
        r = await c.get("/api/me", headers={"Cookie": f"session={forged}"})
        check("varsayilan anahtarla imzali token reddedildi", r.status_code == 401,
              f"-> {r.status_code} (SECRET_KEY varsayilan degerde olabilir!)")

        # Suresi gecmis token
        old = json.dumps({"sub": ADMIN[0], "exp": int(time.time()) - 10}, separators=(",", ":")).encode()
        r = await c.get("/api/me", headers={
            "Cookie": f"session={b64(old)}.{b64(hmac.new(b'x', old, hashlib.sha256).digest())}"})
        check("suresi gecmis token reddedildi", r.status_code == 401, f"-> {r.status_code}")


# --- 4. kullanicilar arasi izolasyon --------------------------------------
async def test_isolation(skip_chat: bool):
    print("\n[4] Kullanicilar arasi izolasyon")
    if skip_chat:
        print("  (sohbet gerektirir, --hizli ile atlandi)")
        return
    async with httpx.AsyncClient(base_url=BASE, timeout=300) as vic:
        await vic.post("/api/login", json={"email": VICTIM[0], "password": VICTIM[1]})
        res = await ask(cookie_header(vic), "Yillik izin hakki kac gun?")
        conv, msg = res["conversation"], res["message_id"]
        check("kurban cevap aldi", bool(res["text"]), f"-> {res.get('error')}")

    async with httpx.AsyncClient(base_url=BASE, timeout=300) as atk:
        await atk.post("/api/login", json={"email": ATTACKER[0], "password": ATTACKER[1]})
        r = await atk.get(f"/api/conversations/{conv}")
        check("baskasinin konusmasi okunamaz", r.status_code == 404, f"-> {r.status_code}")
        r = await atk.delete(f"/api/conversations/{conv}")
        check("baskasinin konusmasi silinemez", r.status_code == 404, f"-> {r.status_code}")
        r = await atk.post(f"/api/messages/{msg}/vote", json={"vote": 1})
        check("baskasinin mesajina oy verilemez", r.status_code == 404, f"-> {r.status_code}")
        listed = (await atk.get("/api/conversations")).json()["conversations"]
        check("listede baskasinin konusmasi yok", all(x["id"] != conv for x in listed))
        hijack = await ask(cookie_header(atk), "devami ne?", conversation_id=conv)
        check("baskasinin konusmasina yazilamaz", hijack["conversation"] != conv,
              f"-> {hijack['conversation']}")

    async with httpx.AsyncClient(base_url=BASE, timeout=60) as vic:
        await vic.post("/api/login", json={"email": VICTIM[0], "password": VICTIM[1]})
        msgs = (await vic.get(f"/api/conversations/{conv}")).json()["messages"]
        check("kurbanin konusmasi bozulmadi", len(msgs) == 2, f"-> {len(msgs)}")


# --- 5. dosya yukleme ------------------------------------------------------
async def test_uploads():
    print("\n[5] Dosya yukleme sinirlari")
    async with httpx.AsyncClient(base_url=BASE, timeout=300) as adm:
        await adm.post("/api/login", json={"email": ADMIN[0], "password": ADMIN[1]})
        rejected = [
            ("../../../../evil.md", b"x", "dizin gezinmesi"),
            ("..\\..\\evil.md", b"x", "windows dizin gezinmesi"),
            ("evil.exe", b"MZ", "calistirilabilir"),
            ("evil.md.exe", b"MZ", "cift uzanti"),
            (".gitignore", b"x", "nokta ile baslayan"),
            ("CON.md", b"x", "windows ayrilmis ad"),
            ("bos.md", b"", "bos icerik"),
            ("buyuk.md", b"x" * (26 * 1024 * 1024), "boyut asimi"),
        ]
        for name, data, why in rejected:
            r = await adm.post("/api/admin/docs",
                               files={"file": (name, io.BytesIO(data), "application/octet-stream")})
            check(f"reddedildi: {why}", r.status_code == 400, f"-> {r.status_code} {r.text[:70]}")

        # Turkce ad korunmali
        r = await adm.post("/api/admin/docs", files={
            "file": ("Çalışan Rehberi.md", io.BytesIO("Kırmızı panda bambu yer.".encode()), "text/markdown")})
        check("Turkce ad kabul edildi", r.status_code == 201, f"-> {r.status_code} {r.text[:70]}")
        if r.status_code == 201:
            check("Turkce ad bozulmadi", r.json()["name"] == "Çalışan Rehberi.md", f"-> {r.json()}")
            await adm.delete("/api/admin/docs/Çalışan Rehberi.md")

        # Depo disina hicbir sey yazilmadi
        for stray in (ROOT.parent / "evil.md", ROOT / "evil.md"):
            check(f"depo disina yazilmadi: {stray.name}", not stray.exists(), f"-> {stray}")
        r = await adm.delete("/api/admin/docs/../../../.env")
        check("path traversal ile silinemez", r.status_code == 404, f"-> {r.status_code}")
        check(".env yerinde", (ROOT / ".env").exists())


# --- 6. sinir degerleri ----------------------------------------------------
async def test_boundaries():
    print("\n[6] Sinir degerleri ve gecersiz girdiler")
    async with httpx.AsyncClient(base_url=BASE, timeout=120) as adm:
        await adm.post("/api/login", json={"email": ADMIN[0], "password": ADMIN[1]})
        cases = [
            ("POST", "/api/messages/999999/vote", {"json": {"vote": 1}}, 404, "olmayan mesaja oy"),
            ("POST", "/api/messages/1/vote", {"json": {"vote": 5}}, 422, "gecersiz oy degeri"),
            ("POST", "/api/messages/1/vote", {"json": {"vote": -5}}, 422, "gecersiz negatif oy"),
            ("GET", "/api/conversations/999999", {}, 404, "olmayan konusma"),
            ("DELETE", "/api/conversations/999999", {}, 404, "olmayan konusma silme"),
            ("PUT", "/api/admin/config", {"json": {"temperature": 9}}, 422, "temperature ust sinir"),
            ("PUT", "/api/admin/config", {"json": {"temperature": -1}}, 422, "temperature alt sinir"),
            ("PUT", "/api/admin/docs/yok.md/roles", {"json": {"roles": ["x"]}}, 404, "olmayan belgeye rol"),
            ("PUT", "/api/admin/users/yok@x.com/role", {"json": {"role": "x"}}, 404, "olmayan kullaniciya rol"),
            ("POST", "/api/password", {"json": {"current_password": "yanlis", "new_password": "yeniParola1"}}, 403, "yanlis mevcut sifre"),
            ("POST", "/api/password", {"json": {"current_password": ADMIN[1], "new_password": "kisa"}}, 422, "kisa yeni sifre"),
        ]
        for method, path, kwargs, expected, why in cases:
            r = await adm.request(method, path, **kwargs)
            check(f"{expected} {why}", r.status_code == expected, f"-> {r.status_code}")

        r = await adm.delete(f"/api/admin/users/{ADMIN[0]}")
        check("admin kendini silemez", r.status_code == 400, f"-> {r.status_code}")


# --- 7. sohbet akisi -------------------------------------------------------
async def test_chat(skip_chat: bool):
    print("\n[7] Sohbet akisi — atif, oylama, gecmis, takip sorusu")
    if skip_chat:
        print("  (LLM gerektirir, --hizli ile atlandi)")
        return
    async with httpx.AsyncClient(base_url=BASE, timeout=600) as c:
        await c.post("/api/login", json={"email": ADMIN[0], "password": ADMIN[1]})
        cookie = cookie_header(c)

        first = await ask(cookie, "Yillik izin hakki kac gun?")
        conv = first["conversation"]
        check("konusma kimligi geldi", conv is not None)
        check("mesaj kimligi geldi", first["message_id"] is not None)
        check("kaynak atifi dondu", bool(first["sources"]), f"-> {first['sources']}")
        if first["sources"]:
            src = first["sources"][0]
            check("atifta belge adi var", bool(src.get("source")), f"-> {src}")
            check("atifta alinti var", bool(src.get("snippet")), f"-> {src}")

        follow = await ask(cookie, "peki devri?", conversation_id=conv)
        check("takip sorusu baglam buldu", bool(follow["sources"]), f"-> {follow['text'][:120]}")

        r = await c.post(f"/api/messages/{first['message_id']}/vote", json={"vote": -1})
        check("oy verildi", r.status_code == 200, f"-> {r.status_code}")
        fb = (await c.get("/api/admin/feedback")).json()
        check("geri bildirim listesinde", any(x["id"] == first["message_id"] for x in fb["down"]))

        msgs = (await c.get(f"/api/conversations/{conv}")).json()["messages"]
        check("gecmis 4 mesaj", len(msgs) == 4, f"-> {len(msgs)}")
        check("kaynaklar kalici", bool(msgs[1]["sources"]), f"-> {msgs[1]}")
        check("oy kalici", msgs[1]["vote"] == -1, f"-> {msgs[1]['vote']}")

        await c.post(f"/api/messages/{first['message_id']}/vote", json={"vote": 0})
        msgs = (await c.get(f"/api/conversations/{conv}")).json()["messages"]
        check("oy geri alindi", msgs[1]["vote"] is None, f"-> {msgs[1]['vote']}")
        await c.delete(f"/api/conversations/{conv}")


# --- 7b. daha once uretilmis hatalar --------------------------------------
async def test_regressions(skip_chat: bool):
    print("\n[7b] Regresyon — daha once uretilen hatalar")
    if skip_chat:
        print("  (sohbet gerektirir, --hizli ile atlandi)")
        return
    async with httpx.AsyncClient(base_url=BASE, timeout=600) as c:
        await c.post("/api/login", json={"email": ADMIN[0], "password": ADMIN[1]})
        cookie = cookie_header(c)

        # HATA: sayisal olmayan conversation_id WebSocket'i cokertiyordu.
        try:
            res = await ask(cookie, "merhaba", conversation_id="abc", timeout=300)
            check("bozuk conversation_id baglantiyi dusurmedi", bool(res["text"]),
                  f"-> {res.get('error')}")
            check("bozuk kimlikte yeni konusma acildi", res["conversation"] is not None)
            if res["conversation"]:
                await c.delete(f"/api/conversations/{res['conversation']}")
        except Exception as exc:  # noqa: BLE001
            check("bozuk conversation_id baglantiyi dusurmedi", False,
                  f"{type(exc).__name__}: {str(exc)[:90]}")

        # HATA: ayni baglantida baska konusmaya gecilince mesaj ESKI konusmaya yaziliyordu.
        first = await ask(cookie, "Yillik izin kac gun?")
        conv_a = first["conversation"]
        second = await ask(cookie, "Masraf limiti nedir?")
        conv_b = second["conversation"]

        marker = "REGRESYON ISARETI"
        async with websockets.connect(WS, additional_headers={"Cookie": cookie}) as ws:
            await ws.send(json.dumps({"message": "Parola kurallari nedir?"}))
            conv_c = None
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=300))
                if m["type"] == "conversation":
                    conv_c = m["id"]
                elif m["type"] in ("done", "error"):
                    break
            # Kullanici "Gecmis"ten conv_a'yi acti ve ayni baglantidan yaziyor
            await ws.send(json.dumps({"message": marker, "conversation_id": conv_a}))
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=300))
                if m["type"] in ("done", "error"):
                    break

        a = (await c.get(f"/api/conversations/{conv_a}")).json()["messages"]
        cc = (await c.get(f"/api/conversations/{conv_c}")).json()["messages"]
        in_a = any(m["content"] == marker for m in a)
        in_c = any(m["content"] == marker for m in cc)
        check("mesaj DOGRU konusmaya yazildi", in_a and not in_c,
              f"-> hedef#{conv_a} icinde={in_a}, onceki#{conv_c} icinde={in_c}")

        for cid in (conv_a, conv_b, conv_c):
            if cid:
                await c.delete(f"/api/conversations/{cid}")


# --- 8. saglik -------------------------------------------------------------
async def test_health():
    print("\n[8] Saglik ve RAG durumu")
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        h = (await c.get("/api/health")).json()
        check("rag blogu var", "rag" in h, f"-> {list(h)}")
        rag = h.get("rag", {})
        check("mod bildirildi", rag.get("mode") in ("hybrid", "keyword", "embed", "rerank", "kapali"),
              f"-> {rag.get('mode')}")
        check("degraded bayragi var", "degraded" in rag, f"-> {rag}")
        check("parca sayisi bildirildi", isinstance(rag.get("chunks"), int), f"-> {rag.get('chunks')}")
        if rag.get("degraded"):
            check("UYARI: embedding modeli eksik, arama bozulmus durumda", False,
                  f"ollama pull {rag.get('embed_model')}")


# --- 9. giris deneme siniri ------------------------------------------------
async def test_rate_limit():
    print("\n[9] Giris deneme siniri")
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        codes = []
        for _ in range(10):
            r = await c.post("/api/login", json={"email": "yok@test.local", "password": "yanlis"})
            codes.append(r.status_code)
        check("basarisiz denemeler 401 dondu", 401 in codes, f"-> {set(codes)}")
        check("sinir asilinca 429", 429 in codes, f"-> {set(codes)}")
        # Sinir hesabi kilitlememeli: gecerli kullanici baska bir anahtardan girebilmeli
        r = await c.post("/api/login", json={"email": ADMIN[0], "password": ADMIN[1]})
        check("gecerli giris etkilenmedi", r.status_code == 200, f"-> {r.status_code}")


# --- kurulum / temizlik ----------------------------------------------------
async def setup():
    async with httpx.AsyncClient(base_url=BASE, timeout=120) as adm:
        r = await adm.post("/api/login", json={"email": ADMIN[0], "password": ADMIN[1]})
        if r.status_code != 200:
            print(f"Admin girisi basarisiz ({r.status_code}). .env dogru mu, sunucu acik mi?")
            return False
        for email, password in (VICTIM, ATTACKER):
            await adm.delete(f"/api/admin/users/{email}")
            await adm.post("/api/admin/users", json={
                "email": email, "password": password, "is_admin": False, "role": "ik"})
        await adm.post("/api/admin/docs", files={
            "file": (TEST_DOC, io.BytesIO(b"API testi icin gecici belge."), "text/markdown")})
        return True


async def cleanup():
    async with httpx.AsyncClient(base_url=BASE, timeout=120) as adm:
        await adm.post("/api/login", json={"email": ADMIN[0], "password": ADMIN[1]})
        for email, _ in (VICTIM, ATTACKER):
            await adm.delete(f"/api/admin/users/{email}")
        await adm.delete(f"/api/admin/docs/{TEST_DOC}")
        await adm.post("/api/admin/reindex")


async def main(skip_chat: bool) -> int:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.get(f"{BASE}/api/health")
    except Exception:
        print(f"Sunucuya erisilemiyor: {BASE}\nOnce 'py run.py' ile baslatin.")
        return 2

    if not await setup():
        return 2
    try:
        await test_authorization()
        await test_anonymous()
        await test_forged_tokens()
        await test_isolation(skip_chat)
        await test_uploads()
        await test_boundaries()
        await test_chat(skip_chat)
        await test_regressions(skip_chat)
        await test_health()
        await test_rate_limit()
    finally:
        await cleanup()

    print(f"\n{_passed} gecti, {len(_failed)} basarisiz")
    for name, detail in _failed:
        print(f"  - {name}: {detail}")
    return 1 if _failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Uctan uca API/WebSocket testleri")
    ap.add_argument("--hizli", action="store_true", help="LLM gerektiren sohbet testlerini atla")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.hizli)))
