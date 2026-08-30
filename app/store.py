"""Sohbet deposu: konusmalar, mesajlar, kaynak atiflari ve geri bildirim oylari.

Onceden gecmis yalnizca WebSocket baglantisi boyunca bellekte yasiyordu; sayfa
yenilenince kayboluyordu. Burada kalici hale gelir ve "Gecmis" sekmesini besler.
Oylar (+1/-1) mesajin yaninda durur; altin sete aday toplamak icin kullanilir.
"""
import json
import time

from .db import connect

TITLE_MAX = 60


def init_db() -> None:
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS conversations (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                email      TEXT NOT NULL,
                title      TEXT NOT NULL,
                created_at REAL NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                conv_id    INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                sources    TEXT,
                vote       INTEGER,
                created_at REAL NOT NULL
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conv_id)")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS audit_logs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                email        TEXT NOT NULL,
                conv_id      INTEGER,
                query_text   TEXT NOT NULL,
                pii_redacted INTEGER NOT NULL DEFAULT 0,
                pii_types    TEXT,
                sources      TEXT,
                created_at   REAL NOT NULL
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC)")


def create_conversation(email: str, first_message: str) -> int:
    title = first_message.strip().replace("\n", " ")[:TITLE_MAX] or "Yeni sohbet"
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (email, title, created_at) VALUES (?, ?, ?)",
            (email.lower(), title, time.time()),
        )
        return int(cur.lastrowid)


def add_message(
    conv_id: int, role: str, content: str, sources: list[dict] | None = None
) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO messages (conv_id, role, content, sources, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (conv_id, role, content, json.dumps(sources) if sources else None, time.time()),
        )
        return int(cur.lastrowid)


def list_conversations(email: str, limit: int = 50, offset: int = 0) -> list[dict]:
    """En yeniden eskiye. Sayfalama olmadan 50'den eski konusmalar arayuzden
    ulasilamaz hale geliyordu (silinmiyorlardi ama listede gorunmuyorlardi)."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT c.id, c.title, c.created_at, COUNT(m.id) AS message_count
                 FROM conversations c LEFT JOIN messages m ON m.conv_id = c.id
                WHERE c.email = ?
                GROUP BY c.id ORDER BY c.created_at DESC LIMIT ? OFFSET ?""",
            (email.lower(), limit, max(0, offset)),
        ).fetchall()
    return [dict(r) for r in rows]


def count_conversations(email: str) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM conversations WHERE email = ?", (email.lower(),)
        ).fetchone()
    return int(row["n"])


def get_messages(conv_id: int, email: str) -> list[dict] | None:
    """Konusmanin mesajlari; konusma bu kullaniciya ait degilse None."""
    with connect() as conn:
        owner = conn.execute(
            "SELECT email FROM conversations WHERE id = ?", (conv_id,)
        ).fetchone()
        if not owner or owner["email"] != email.lower():
            return None
        rows = conn.execute(
            "SELECT id, role, content, sources, vote, created_at FROM messages"
            " WHERE conv_id = ? ORDER BY id",
            (conv_id,),
        ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["sources"] = json.loads(item["sources"]) if item["sources"] else []
        out.append(item)
    return out


def delete_conversation(conv_id: int, email: str) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM conversations WHERE id = ? AND email = ?", (conv_id, email.lower())
        )
        if not cur.rowcount:
            return False
        conn.execute("DELETE FROM messages WHERE conv_id = ?", (conv_id,))
    return True


def set_vote(message_id: int, email: str, vote: int) -> bool:
    """Yalnizca kendi konusmasindaki bir asistan mesajina oy verilebilir."""
    with connect() as conn:
        row = conn.execute(
            """SELECT m.id FROM messages m JOIN conversations c ON c.id = m.conv_id
                WHERE m.id = ? AND c.email = ? AND m.role = 'assistant'""",
            (message_id, email.lower()),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE messages SET vote = ? WHERE id = ?", (vote or None, message_id)
        )
    return True


def voted_messages(vote: int) -> list[dict]:
    """Altin sete aday toplamak icin: verilen oyu almis soru/cevap ciftleri."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT m.id, m.content AS answer, m.sources, m.created_at,
                      (SELECT content FROM messages q WHERE q.conv_id = m.conv_id
                         AND q.id < m.id AND q.role = 'user'
                       ORDER BY q.id DESC LIMIT 1) AS question
                 FROM messages m
                WHERE m.vote = ? AND m.role = 'assistant'
                ORDER BY m.created_at DESC""",
            (vote,),
        ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["sources"] = json.loads(item["sources"]) if item["sources"] else []
        out.append(item)
    return out


_STOPWORDS = {
    "ve", "veya", "ile", "bu", "icin", "için", "bir", "mi", "mı", "mu", "mü",
    "ne", "neler", "nelerdir", "nasil", "nasıl", "nedir", "hakkinda", "hakkında",
    "olan", "olarak", "ise", "da", "de", "miyim", "misin", "misiniz", "musunuz",
    "dokuman", "doküman", "dokumandaki", "dokümandaki", "belge", "belgeler",
    "lutfen", "lütfen", "gun", "gün", "var", "yok", "ben", "sen", "biz", "siz",
    "onlar", "cok", "çok", "daha", "en", "gibi", "kadar", "şu", "o", "bana",
    "beni", "sana", "seni", "bize", "bizi", "size", "sizi", "hangi", "hangisi",
    "kim", "kime", "kimden", "neden", "niçin", "acaba", "öğrenebilir", "öğrenmek",
    "istiyorum", "bilgi", "alabilir", "verir", "eder", "açıklar", "özetler",
    "ilgili", "göre", "tüm", "her", "şey", "sey"
}


def get_analytics_summary() -> dict:
    """Admin LLMOps analitik ozeti: metrikler, trend konular ve dokuman boslugu."""
    import re
    from collections import Counter

    with connect() as conn:
        # 1. Genel sayilar
        total_convs = int(conn.execute("SELECT COUNT(*) AS c FROM conversations").fetchone()["c"])
        total_msgs = int(conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"])
        total_user_q = int(conn.execute("SELECT COUNT(*) AS c FROM messages WHERE role = 'user'").fetchone()["c"])
        total_asst_a = int(conn.execute("SELECT COUNT(*) AS c FROM messages WHERE role = 'assistant'").fetchone()["c"])

        # 2. Oylama ve memnuniyet
        upvotes = int(conn.execute("SELECT COUNT(*) AS c FROM messages WHERE role = 'assistant' AND vote = 1").fetchone()["c"])
        downvotes = int(conn.execute("SELECT COUNT(*) AS c FROM messages WHERE role = 'assistant' AND vote = -1").fetchone()["c"])
        voted_total = upvotes + downvotes
        satisfaction_rate = round((upvotes / (voted_total or 1)) * 100, 1) if voted_total > 0 else 0.0

        # 3. Guardrail reddi / dokuman boslugu tespiti
        # "Bu konuda belgelerimde bilgi bulamadim" iceren yanitlar
        refusal_rows = conn.execute(
            """SELECT m.id, m.created_at,
                      (SELECT content FROM messages q WHERE q.conv_id = m.conv_id
                         AND q.id < m.id AND q.role = 'user'
                       ORDER BY q.id DESC LIMIT 1) AS question
                 FROM messages m
                WHERE m.role = 'assistant' AND m.content LIKE '%bilgi bulamadim%'
                ORDER BY m.created_at DESC LIMIT 50"""
        ).fetchall()

        unanswered_questions = []
        for r in refusal_rows:
            q = (r["question"] or "").strip()
            if q and q not in unanswered_questions:
                unanswered_questions.append(q)

        unanswered_count = len(refusal_rows)
        success_rate = round(((total_asst_a - unanswered_count) / (total_asst_a or 1)) * 100, 1) if total_asst_a > 0 else 100.0

        # 4. En cok sorulan konular / anahtar kelimeler
        user_rows = conn.execute("SELECT content FROM messages WHERE role = 'user' ORDER BY id DESC LIMIT 500").fetchall()
        word_counts = Counter()
        for ur in user_rows:
            text = (ur["content"] or "").lower()
            words = re.findall(r"\b[a-zA-ZçğıöşüÇĞİÖŞÜ]{3,}\b", text)
            for w in words:
                w_norm = w.replace("ı", "i").replace("İ", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
                if w not in _STOPWORDS and w_norm not in _STOPWORDS:
                    word_counts[w] += 1

        top_topics = [{"topic": word, "count": count} for word, count in word_counts.most_common(12)]

        # 5. Son geri bildirimler (voted list)
        recent_feedback_rows = conn.execute(
            """SELECT m.id, m.content AS answer, m.sources, m.vote, m.created_at,
                      (SELECT content FROM messages q WHERE q.conv_id = m.conv_id
                         AND q.id < m.id AND q.role = 'user'
                       ORDER BY q.id DESC LIMIT 1) AS question
                 FROM messages m
                WHERE m.vote IS NOT NULL AND m.role = 'assistant'
                ORDER BY m.created_at DESC LIMIT 20"""
        ).fetchall()

        recent_feedback = []
        for r in recent_feedback_rows:
            item = dict(r)
            item["sources"] = json.loads(item["sources"]) if item["sources"] else []
            recent_feedback.append(item)

        return {
            "metrics": {
                "total_conversations": total_convs,
                "total_messages": total_msgs,
                "total_user_questions": total_user_q,
                "total_assistant_answers": total_asst_a,
                "upvotes": upvotes,
                "downvotes": downvotes,
                "satisfaction_rate": satisfaction_rate,
                "unanswered_count": unanswered_count,
                "success_rate": success_rate,
            },
            "top_topics": top_topics,
            "unanswered_questions": unanswered_questions[:15],
            "recent_feedback": recent_feedback,
        }


def export_analytics_csv() -> str:
    """Analitik verilerini CSV formatinda metin olarak uretir."""
    import csv
    import io
    from datetime import datetime

    summary = get_analytics_summary()
    output = io.StringIO()
    writer = csv.writer(output)

    # 1. Metrikler
    writer.writerow(["=== KURUMSAL RAG LLMOps ANALITIK RAPORU ==="])
    writer.writerow(["Rapor Tarihi", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    writer.writerow([])
    writer.writerow(["METRIK", "DEGER"])
    for k, v in summary["metrics"].items():
        writer.writerow([k, v])

    writer.writerow([])
    # 2. Popüler Konular
    writer.writerow(["=== EN COK SORULAN KONULAR / ANAHTAR KELIMELER ==="])
    writer.writerow(["Konu / Kelime", "Soru Sayisi"])
    for t in summary["top_topics"]:
        writer.writerow([t["topic"], t["count"]])

    writer.writerow([])
    # 3. Doküman Boşluğu (Cevapsız Sorular)
    writer.writerow(["=== DOKUMAN BOSLUGU TESPITI (CEVAPSIZ SORULAR) ==="])
    writer.writerow(["Soru"])
    for q in summary["unanswered_questions"]:
        writer.writerow([q])

    writer.writerow([])
    # 4. Geri Bildirimler
    writer.writerow(["=== KULLANICI GERI BILDIRIMLERI (OYLANAN MESAJLAR) ==="])
    writer.writerow(["ID", "Oy (+1/-1)", "Tarih", "Kullanici Sorusu", "Asistan Cevabi", "Kaynaklar"])
    for f in summary["recent_feedback"]:
        dt = datetime.fromtimestamp(f["created_at"]).strftime("%Y-%m-%d %H:%M:%S") if f.get("created_at") else ""
        srcs = "; ".join([s.get("source", "") for s in f.get("sources", [])])
        writer.writerow([f["id"], f["vote"], dt, f.get("question", ""), f.get("answer", "")[:120], srcs])

    return output.getvalue()


def add_audit_log(
    email: str,
    conv_id: int | None,
    query_text: str,
    pii_types: list[str] | None = None,
    sources: list[str] | None = None,
) -> int:
    """Guvenlik ve erisim denetim kaydi olusturur."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO audit_logs (email, conv_id, query_text, pii_redacted, pii_types, sources, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                email.lower(),
                conv_id,
                query_text[:300],
                1 if pii_types else 0,
                json.dumps(pii_types, ensure_ascii=False) if pii_types else None,
                json.dumps(sources, ensure_ascii=False) if sources else None,
                time.time(),
            ),
        )
        return int(cur.lastrowid)


def list_audit_logs(limit: int = 50, offset: int = 0) -> list[dict]:
    """Denetim kayitlarini en yeniden eskiye listeler."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, email, conv_id, query_text, pii_redacted, pii_types, sources, created_at"
            " FROM audit_logs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (max(1, limit), max(0, offset)),
        ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["pii_types"] = json.loads(item["pii_types"]) if item["pii_types"] else []
        item["sources"] = json.loads(item["sources"]) if item["sources"] else []
        out.append(item)
    return out


def count_audit_logs() -> dict:
    """Denetim istatistiklerini hesaplar."""
    with connect() as conn:
        total = int(conn.execute("SELECT COUNT(*) AS c FROM audit_logs").fetchone()["c"])
        pii_count = int(conn.execute("SELECT COUNT(*) AS c FROM audit_logs WHERE pii_redacted = 1").fetchone()["c"])
    return {"total_events": total, "pii_events": pii_count}


