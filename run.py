"""Tek komutla başlat.

Sırasıyla: Python bağımlılıklarını kurar, Ollama'yı çalışır hale getirir,
hedef modeli indirir, sonra API sunucusunu açar. Tekrar çalıştırmak güvenli —
zaten sağlanmış her adım atlanır.

    python run.py
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REQUIREMENTS = os.path.join(HERE, "requirements.txt")


def log(msg: str) -> None:
    print(f"[run] {msg}", flush=True)


def ensure_dependencies() -> None:
    try:
        import fastapi, httpx, pydantic_settings, uvicorn  # noqa: F401
    except ImportError:
        log("Bağımlılıklar kuruluyor...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", "-r", REQUIREMENTS]
        )


def _tags(base_url: str, timeout: int = 3):
    """Return list of model names, or None if Ollama is unreachable."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=timeout) as r:
            data = json.loads(r.read())
        return [m["name"] for m in data.get("models", [])]
    except (urllib.error.URLError, OSError, ValueError):
        return None


def ensure_ollama(base_url: str) -> None:
    if _tags(base_url) is not None:
        return
    exe = shutil.which("ollama")
    if not exe:
        log("HATA: Ollama kurulu değil -> https://ollama.com/download")
        sys.exit(1)
    log("Ollama başlatılıyor (ollama serve)...")
    flags = 0x00000008 if os.name == "nt" else 0  # DETACHED_PROCESS (Windows)
    subprocess.Popen(
        [exe, "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    for _ in range(30):
        time.sleep(1)
        if _tags(base_url) is not None:
            log("Ollama hazır.")
            return
    log("HATA: Ollama başlatılamadı.")
    sys.exit(1)


def ensure_model(base_url: str, model: str) -> None:
    names = _tags(base_url) or []
    if model in names:
        return
    exe = shutil.which("ollama")
    if not exe:
        log(f"UYARI: '{model}' yok ve ollama CLI bulunamadı, indirilemiyor.")
        return
    log(f"Model indiriliyor: {model} (ilk sefer uzun sürebilir)...")
    subprocess.check_call([exe, "pull", model])


def main() -> None:
    ensure_dependencies()
    from app.config import settings  # deps garanti olduktan sonra import et

    ensure_ollama(settings.ollama_base_url)
    ensure_model(settings.ollama_base_url, settings.default_model)

    import uvicorn

    log(f"Sunucu: http://localhost:{settings.port}   (admin: /admin)")
    uvicorn.run("app.main:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
