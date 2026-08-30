"""Kimlik doğrulama: SQLite kullanıcı deposu, PBKDF2 şifre hash'i,
HMAC ile imzalı oturum token'ı. Ek bağımlılık yok — hepsi stdlib.
"""
import base64
import hashlib
import hmac
import json
import os
import sqlite3
import time

from fastapi import Cookie, Depends, HTTPException

from .config import settings
from .db import column_names, connect as _connect

COOKIE_NAME = "session"
_PBKDF2_ITER = 200_000
DEFAULT_ROLE = "user"
_WEAK_PASSWORDS = frozenset({"admin", "12345", "password", "parola", "admin123"})


# --- kullanıcı deposu ------------------------------------------------------
def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                email         TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                is_admin      INTEGER NOT NULL DEFAULT 0,
                created_at    REAL NOT NULL
            )"""
        )
        # Rol bazlı belge yetkisi sonradan eklendi; mevcut veritabanını taşı.
        kolonlar = column_names(conn, "users")
        if "role" not in kolonlar:
            conn.execute(
                f"ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT '{DEFAULT_ROLE}'"
            )
        # Toplu iptal (şifre değişimi): bu andan ÖNCEki token'lar geçersiz.
        if "sessions_valid_after" not in kolonlar:
            conn.execute(
                "ALTER TABLE users ADD COLUMN sessions_valid_after REAL NOT NULL DEFAULT 0"
            )
        # Tekil iptal (çıkış): yalnızca o oturumun token'ı geçersiz kılınır ki
        # bir cihazdan çıkmak diğer cihazları düşürmesin.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS revoked_tokens (
                jti        TEXT PRIMARY KEY,
                expires_at REAL NOT NULL
            )"""
        )
    if not list_users():  # ilk çalıştırmada bootstrap admin
        create_user(settings.admin_email, settings.admin_password, is_admin=True)
        print(
            f"[auth] İlk admin oluşturuldu -> {settings.admin_email} "
            f"/ {settings.admin_password}  (ADMIN_PASSWORD ile değiştir)",
            flush=True,
        )
    if settings.admin_password in _WEAK_PASSWORDS or len(settings.admin_password) < 8:
        print(
            "[auth] UYARI: ADMIN_PASSWORD zayıf. /api/password ile değiştirin ve "
            ".env dosyasını sürüm kontrolünde tutmayın.",
            flush=True,
        )
    if settings.secret_key == "change-me-in-production":
        print("[auth] UYARI: SECRET_KEY varsayılan değerde — oturum imzaları tahmin edilebilir.", flush=True)


def _hash_password(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITER)
    return salt.hex() + ":" + dk.hex()


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt = bytes.fromhex(stored.split(":", 1)[0])
    except (ValueError, IndexError):
        return False
    return hmac.compare_digest(_hash_password(password, salt), stored)


def create_user(
    email: str, password: str, is_admin: bool = False, role: str = DEFAULT_ROLE
) -> None:
    email = email.strip().lower()
    if not email or not password:
        raise ValueError("e-posta ve şifre zorunlu")
    stored = _hash_password(password, os.urandom(16))
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO users (email, password_hash, is_admin, role, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (email, stored, int(is_admin), (role or DEFAULT_ROLE).strip().lower(), time.time()),
            )
    except sqlite3.IntegrityError:
        raise ValueError("bu e-posta zaten kayıtlı")


def set_password(email: str, password: str) -> None:
    if len(password) < 8:
        raise ValueError("şifre en az 8 karakter olmalı")
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE email = ?",
            (_hash_password(password, os.urandom(16)), email.strip().lower()),
        )


def set_role(email: str, role: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET role = ? WHERE email = ?",
            ((role or DEFAULT_ROLE).strip().lower(), email.strip().lower()),
        )


def delete_user(email: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM users WHERE email = ?", (email.strip().lower(),))


def get_user(email: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
        ).fetchone()
    return dict(row) if row else None


def list_users() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT email, is_admin, role, created_at FROM users ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]


def authenticate(email: str, password: str) -> dict | None:
    user = get_user(email)
    if user and _verify_password(password, user["password_hash"]):
        return user
    return None


# --- giriş deneme sınırı ----------------------------------------------------
# Kaba kuvvet denemelerini yavaşlatır. Tek süreçli kurulum için bellekte tutulan
# kayan pencere yeterli; çok işçili dağıtımda paylaşımlı bir sayaca taşınmalı.
_ATTEMPT_WINDOW = 300.0  # saniye
_MAX_ATTEMPTS = 8
_MAX_TRACKED_KEYS = 4096  # bunun üstünde süresi dolmuş anahtarlar temizlenir
_attempts: dict[str, list[float]] = {}


def _sweep(now: float) -> None:
    """Sözlüğü sert bir üst sınırın altında tut.

    Sayaç yalnızca sorgulanan anahtarı buduyordu; farklı e-postalarla yapılan
    denemeler kalıcı olarak birikiyordu (sınırsız bellek büyümesi). Önce süresi
    dolmuşlar atılır; hepsi tazeyse (hızlı akın) en eskiler düşürülür — sınırlama
    en iyi çaba bir savunmadır, sınırsız bellekten iyidir.
    """
    for key in [k for k, hits in _attempts.items()
                if not hits or now - max(hits) >= _ATTEMPT_WINDOW]:
        del _attempts[key]
    if len(_attempts) < _MAX_TRACKED_KEYS:
        return
    oldest = sorted(_attempts, key=lambda k: max(_attempts[k]))
    for key in oldest[: len(_attempts) - _MAX_TRACKED_KEYS // 2]:
        del _attempts[key]


def rate_limited(key: str) -> bool:
    now = time.time()
    hits = [t for t in _attempts.get(key, []) if now - t < _ATTEMPT_WINDOW]
    if hits:
        _attempts[key] = hits
    else:
        _attempts.pop(key, None)  # boş liste bırakma
    return len(hits) >= _MAX_ATTEMPTS


def record_attempt(key: str) -> None:
    now = time.time()
    if len(_attempts) >= _MAX_TRACKED_KEYS:
        _sweep(now)
    _attempts.setdefault(key, []).append(now)


def clear_attempts(key: str) -> None:
    _attempts.pop(key, None)


# --- imzalı oturum token'ı -------------------------------------------------
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: bytes) -> bytes:
    return hmac.new(settings.secret_key.encode(), payload, hashlib.sha256).digest()


def create_token(email: str) -> str:
    # iat kesirli saniye: aynı saniye içinde giriş+çıkış yapılırsa tam sayı
    # çözünürlüğünde iptal çalışmıyordu (iat == sessions_valid_after).
    now = time.time()
    payload = json.dumps(
        {
            "sub": email.lower(),
            "iat": now,
            "exp": int(now) + settings.session_ttl_hours * 3600,
            "jti": base64.urlsafe_b64encode(os.urandom(9)).decode(),
        },
        separators=(",", ":"),
    ).encode()
    return _b64(payload) + "." + _b64(_sign(payload))


def decode_token(token: str | None) -> dict | None:
    """İmza ve süre geçerliyse token içeriği, değilse None."""
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _unb64(payload_b64)
        if not hmac.compare_digest(_unb64(sig_b64), _sign(payload)):
            return None
        data = json.loads(payload)
    except (ValueError, KeyError):
        return None
    if not isinstance(data, dict) or data.get("exp", 0) < time.time():
        return None
    return data


def verify_token(token: str | None) -> str | None:
    data = decode_token(token)
    return data.get("sub") if data else None


def _is_revoked(jti: str | None) -> bool:
    if not jti:
        return False
    with _connect() as conn:
        return conn.execute(
            "SELECT 1 FROM revoked_tokens WHERE jti = ?", (jti,)
        ).fetchone() is not None


def user_for_token(token: str | None) -> dict | None:
    """Token geçerli, iptal edilmemiş ve toplu iptalden sonra üretilmişse kullanıcı.

    Çıkış yapmak yalnızca çerezi siliyordu; kopyalanmış bir çerez süresi dolana
    kadar (24 saat) çalışmaya devam ediyordu.
    """
    data = decode_token(token)
    if not data:
        return None
    user = get_user(data.get("sub") or "")
    if not user or data.get("iat", 0) < (user.get("sessions_valid_after") or 0):
        return None
    if _is_revoked(data.get("jti")):
        return None
    return user


def revoke_token(token: str | None) -> bool:
    """Tek bir oturumu (çıkış yapılan cihazı) geçersiz kıl."""
    data = decode_token(token)
    jti = data.get("jti") if data else None
    if not jti:
        return False
    with _connect() as conn:
        conn.execute("DELETE FROM revoked_tokens WHERE expires_at < ?", (time.time(),))
        conn.execute(
            "INSERT OR REPLACE INTO revoked_tokens (jti, expires_at) VALUES (?, ?)",
            (jti, data.get("exp", time.time())),
        )
    return True


def revoke_sessions(email: str) -> None:
    """Bu kullanıcının TÜM oturumlarını geçersiz kıl (şifre değişiminde)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET sessions_valid_after = ? WHERE email = ?",
            (time.time(), email.strip().lower()),
        )


# --- FastAPI bağımlılıkları -------------------------------------------------
def current_user(session: str | None = Cookie(default=None)) -> dict:
    user = user_for_token(session)
    if not user:
        raise HTTPException(status_code=401, detail="giriş gerekli")
    return user


login_required = current_user


def admin_required(user: dict = Depends(current_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="yetkisiz")
    return user
