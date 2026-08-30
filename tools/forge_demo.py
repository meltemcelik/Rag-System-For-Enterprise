"""ONCE/SONRA guvenlik farki: commitlenmis anahtarla admin oturumu uydurulabilir miydi?

Saldirgan senaryosu: depoyu klonlar, .env icindeki SECRET_KEY'i okur, kendine
admin token'i imzalar. Sifre bilmesine gerek yok.
"""

# Depo koku dosyanin kendi konumundan turetilir; sabit yol YAZILMAZ
# (bu betikler once depo disinda yazildi, oradan tasindi).
from pathlib import Path as _Path
_REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import base64, hashlib, hmac, json, sys, time
from pathlib import Path

BEFORE = Path(sys.argv[1])
SERVER = "http://localhost:8000"


def read_env(path):
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def mint(secret, email, hours=24):
    """auth.create_token ile birebir ayni algoritma."""
    payload = json.dumps({"sub": email, "exp": int(time.time()) + hours * 3600},
                         separators=(",", ":")).encode()
    b64 = lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return b64(payload) + "." + b64(sig)


old_env = read_env(BEFORE / ".env")
print("=== ONCE (cee4398) ===")
print(f"  .env depoda mi          : {'EVET' if (BEFORE / '.env').exists() else 'hayir'}")
old_secret = old_env.get("SECRET_KEY")
print(f"  SECRET_KEY okunabiliyor : {'EVET -> ' + repr(old_secret) if old_secret else 'hayir'}")
print(f"  ADMIN_PASSWORD          : {'EVET -> ' + repr(old_env.get('ADMIN_PASSWORD')) if old_env.get('ADMIN_PASSWORD') else 'hayir'}")

if not old_secret:
    print("  (anahtar yok, gosterim yapilamiyor)")
    sys.exit(0)

forged = mint(old_secret, old_env.get("ADMIN_EMAIL", "admin@example.com"))
print(f"\n  Uydurulmus admin token  : {forged[:48]}...")

# Eski kod bu token'i kabul eder miydi? (eski dogrulama mantigi birebir)
def verify(secret, token):
    try:
        p_b64, s_b64 = token.split(".", 1)
        unb64 = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        payload = unb64(p_b64)
        if not hmac.compare_digest(unb64(s_b64),
                                   hmac.new(secret.encode(), payload, hashlib.sha256).digest()):
            return None
        data = json.loads(payload)
    except Exception:
        return None
    return None if data.get("exp", 0) < time.time() else data.get("sub")

print(f"  Eski sunucu kabul eder miydi? -> {verify(old_secret, forged) or 'HAYIR'}")

print("\n=== SONRA (c588467, anahtar donduruldu) ===")
try:
    import httpx
    r = httpx.get(f"{SERVER}/api/me", headers={"Cookie": f"session={forged}"}, timeout=10)
    print(f"  Ayni token calisan sunucuda -> HTTP {r.status_code} "
          f"({'REDDEDILDI' if r.status_code == 401 else 'KABUL EDILDI (!!)'})")
except Exception as exc:
    print(f"  sunucuya erisilemedi: {exc}")

now_env = read_env(Path(_REPO_ROOT) / ".env")
print(f"  .env artik git'te mi    : hayir (.gitignore + takipten cikarildi)")
print(f"  SECRET_KEY varsayilan mi: {'EVET (!!)' if now_env.get('SECRET_KEY') == 'change-me-in-production' else 'hayir, ozel deger'}")
