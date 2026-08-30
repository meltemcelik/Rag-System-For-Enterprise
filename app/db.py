"""Paylasilan SQLite baglantisi. auth (kullanicilar) ve store (sohbetler) ayni
dosyayi kullanir; baglanti kurulumu tek yerde dursun diye burada.
"""
import sqlite3
from pathlib import Path

from .config import settings


def connect() -> sqlite3.Connection:
    path = Path(settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    """Basit migrasyon kontrolu icin: tablodaki kolon adlari."""
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
