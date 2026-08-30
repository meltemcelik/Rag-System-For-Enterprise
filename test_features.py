"""Yeni ozelliklerin testleri — sunucu ve Ollama gerekmez.

Kapsam: kaynak atiflari, rol bazli belge yetkisi, sorgu sikistirma, sohbet
gecmisi, oylama, giris deneme siniri, Turkce/Unicode davranisi, rastgele
girdiler (fuzz), es zamanli yazma ve veritabani surum gecisi.

Gecici bir veritabani/belge klasoru kullanir; gercek verilere dokunmaz.

Kullanim:
    py test_features.py
"""
import asyncio
import os
import random
import sqlite3
import string
import sys
import tempfile
import threading
import time
from pathlib import Path

# Gercek .env/veritabani yerine gecici ortam — app import edilmeden AYARLANMALI.
_TMP = Path(tempfile.mkdtemp(prefix="ragtest_"))
_DOCS = _TMP / "docs"
_DOCS.mkdir()
(_DOCS / "izin.md").write_text("Yillik izin 20 is gunudur.", encoding="utf-8")
(_DOCS / "maas.md").write_text("Maas bordrolari her ayin 15'inde yayinlanir.", encoding="utf-8")
os.environ["RAG_DOCS_DIR"] = str(_DOCS)
os.environ["RAG_CACHE_DIR"] = str(_TMP / "cache")
os.environ["RAG_MODE"] = "keyword"  # Ollama gerekmesin

from app import auth, docs, query, security, store  # noqa: E402
from app.config import settings  # noqa: E402
from app.rag import chunk_source, parse_sources  # noqa: E402

settings.db_path = str(_TMP / "test.db")

_passed = 0
_failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failed.append(name)
        print(f"  FAIL {name}  {detail}")


# --- kaynak atiflari -------------------------------------------------------
def test_sources() -> None:
    print("\n[kaynak atiflari]")
    context = [
        "[kaynak: izin.md]\nYillik izin 20 is gunudur.",
        "[kaynak: rapor.pdf, sayfa 12]\nGelir tablosu ayrintilari.",
        "[kaynak: izin.md]\nAyni belge tekrar.",
        "basliksiz metin",
    ]
    got = parse_sources(context)
    check("benzersiz atif sayisi", len(got) == 2, f"-> {len(got)}")
    check("sayfasiz belge", got[0] == {"source": "izin.md", "page": None, "snippet": "Yillik izin 20 is gunudur."}, f"-> {got[0]}")
    check("sayfa numarasi ayristi", got[1]["page"] == 12, f"-> {got[1]}")
    check("chunk_source", chunk_source(context[1]) == "rapor.pdf")
    check("basliksiz parca None", chunk_source("basliksiz metin") is None)

    long_body = "x" * 400
    snippet = parse_sources([f"[kaynak: a.md]\n{long_body}"])[0]["snippet"]
    check("uzun metin kisaltildi", len(snippet) <= 221 and snippet.endswith("…"), f"-> {len(snippet)}")


# --- belge yetkisi ---------------------------------------------------------
def test_docs_and_acl() -> None:
    print("\n[belge yetkisi]")
    names = {d["name"] for d in docs.list_docs()}
    check("belgeler listelendi", names == {"izin.md", "maas.md"}, f"-> {names}")

    # Yol ayraci iceren ad sessizce duzeltilmez, REDDEDILIR (tarayici yol gondermez;
    # ayrac varsa kasitli mudahaledir).
    for bad, why in [
        ("../../etc/passwd", "dizin gezinmesi"),
        ("../../../../evil.md", "gecerli uzantili dizin gezinmesi"),
        ("..\\..\\evil.md", "windows dizin gezinmesi"),
        ("a/b/rapor.pdf", "alt klasor"),
        ("virus.exe", "izinsiz tur"),
        ("rapor.md.exe", "cift uzanti"),
        ("", "bos ad"),
        (".gizli.md", "nokta ile baslayan"),
        ("...", "yalnizca nokta"),
        ("CON.md", "windows ayrilmis ad"),
        ("lpt1.pdf", "windows ayrilmis ad (lpt1)"),
    ]:
        try:
            got = docs.safe_name(bad)
            check(f"reddedildi: {why}", False, f"{bad!r} -> {got!r} kabul edildi")
        except ValueError:
            check(f"reddedildi: {why}", True)

    docs.set_roles("maas.md", ["finans"])
    context = [
        "[kaynak: izin.md]\nYillik izin 20 is gunudur.",
        "[kaynak: maas.md]\nMaas bordrolari her ayin 15'inde yayinlanir.",
    ]
    admin = {"email": "a@x.com", "is_admin": 1, "role": "user"}
    finans = {"email": "f@x.com", "is_admin": 0, "role": "finans"}
    normal = {"email": "n@x.com", "is_admin": 0, "role": "user"}

    check("admin her belgeyi gorur", len(docs.filter_context(context, admin)) == 2)
    check("finans rolu gorur", len(docs.filter_context(context, finans)) == 2)
    filtered = docs.filter_context(context, normal)
    check("yetkisiz kullanici suzuldu", len(filtered) == 1, f"-> {len(filtered)}")
    check("kalan dogru belge", chunk_source(filtered[0]) == "izin.md")

    docs.set_roles("maas.md", [])
    check("kural kaldirilinca herkese acik", len(docs.filter_context(context, normal)) == 2)

    saved, _ = docs.save_doc("yeni not.md", b"icerik")
    check("yukleme", (_DOCS / saved).exists(), f"-> {saved}")
    check("silme", docs.delete_doc(saved) and not (_DOCS / saved).exists())
    check("olmayan belge silinmez", docs.delete_doc("yok.md") is False)
    try:
        docs.save_doc("buyuk.md", b"x" * (docs.MAX_BYTES + 1))
        check("boyut siniri", False, "asiri buyuk dosya kabul edildi")
    except ValueError:
        check("boyut siniri", True)


# --- sorgu sikistirma ------------------------------------------------------
def test_condense() -> None:
    print("\n[sorgu sikistirma]")
    history = [{"role": "user", "content": "Yillik izin kac gun?"},
               {"role": "assistant", "content": "20 is gunu."}]
    check("gecmis yoksa sikistirma yok", query.needs_condensing([], "peki ya bu?") is False)
    check("kisa takip sorusu sikistirilir", query.needs_condensing(history, "peki yurt disi icin?") is True)
    check("uzun bagimsiz soru sikistirilmaz",
          query.needs_condensing(history, "Sirket masraf yonetmeliginde gunluk yemek limiti kac lira olarak belirlenmis?") is False)

    check("dusunme izi temizlendi", query._clean("<think>hmm</think>Yurt disi izin hakki", "f") == "Yurt disi izin hakki")
    check("tirnak temizlendi", query._clean('"Yurt disi izin"', "f") == "Yurt disi izin")
    check("cok uzun cikti reddedildi", query._clean("kelime " * 50, "orijinal") == "orijinal")
    check("bos cikti reddedildi", query._clean("   ", "orijinal") == "orijinal")

    # Few-shot ornekleri modele NE URETECEGINI ogretir; bicimleri bozulursa
    # donusum sessizce kotulesir. Bicim kontrolu (icerik karari eval/run.py
    # --multiturn ile olculur; query.py'deki nota bakin).
    for _, _, beklenen_cikti in query._EXAMPLES:
        check(f"ornek cikti tek satir: {beklenen_cikti[:30]!r}",
              "\n" not in beklenen_cikti and beklenen_cikti.strip() == beklenen_cikti)
        check(f"ornek cikti makul uzunlukta: {beklenen_cikti[:30]!r}",
              0 < len(beklenen_cikti.split()) <= 12, f"-> {len(beklenen_cikti.split())} kelime")

    class FailingOllama:
        async def complete(self, *a, **k):
            from app.ollama import OllamaError
            raise OllamaError("baglanti yok")

    result = asyncio.run(query.condense(FailingOllama(), "m", history, "peki ya bu?"))
    check("LLM hatasinda orijinale doner", result == "peki ya bu?", f"-> {result}")


# --- gecmis ve oylama ------------------------------------------------------
def test_store() -> None:
    print("\n[gecmis ve oylama]")
    auth.init_db()
    store.init_db()

    conv = store.create_conversation("a@x.com", "Yillik izin kac gun?")
    store.add_message(conv, "user", "Yillik izin kac gun?")
    answer_id = store.add_message(conv, "assistant", "20 is gunu.", [{"source": "izin.md", "page": None}])

    convs = store.list_conversations("a@x.com")
    check("konusma listelendi", len(convs) == 1 and convs[0]["message_count"] == 2, f"-> {convs}")
    check("baslik ilk mesajdan", convs[0]["title"] == "Yillik izin kac gun?")
    check("baska kullanici gormez", store.list_conversations("b@x.com") == [])

    messages = store.get_messages(conv, "a@x.com")
    check("mesajlar sirali", [m["role"] for m in messages] == ["user", "assistant"])
    check("kaynaklar geri okundu", messages[1]["sources"][0]["source"] == "izin.md")
    check("sahibi olmayan erisemez", store.get_messages(conv, "b@x.com") is None)

    check("oy verildi", store.set_vote(answer_id, "a@x.com", -1))
    check("oy okundu", store.get_messages(conv, "a@x.com")[1]["vote"] == -1)
    check("baskasi oy veremez", store.set_vote(answer_id, "b@x.com", 1) is False)
    check("kullanici mesajina oy verilemez", store.set_vote(messages[0]["id"], "a@x.com", 1) is False)

    down = store.voted_messages(-1)
    check("geri bildirim toplandi", len(down) == 1 and down[0]["question"] == "Yillik izin kac gun?", f"-> {down}")
    check("oy geri alindi", store.set_vote(answer_id, "a@x.com", 0) and store.voted_messages(-1) == [])

    check("baskasi silemez", store.delete_conversation(conv, "b@x.com") is False)
    check("sahibi siler", store.delete_conversation(conv, "a@x.com"))
    check("mesajlar da silindi", store.get_messages(conv, "a@x.com") is None)


# --- kimlik dogrulama -----------------------------------------------------
def test_auth() -> None:
    print("\n[kimlik dogrulama]")
    auth.create_user("rol@x.com", "gucluParola1", is_admin=False, role="finans")
    user = auth.get_user("rol@x.com")
    check("rol kaydedildi", user["role"] == "finans", f"-> {user['role']}")
    auth.set_role("rol@x.com", "IK")
    check("rol guncellendi (kucuk harf)", auth.get_user("rol@x.com")["role"] == "ik")

    check("dogru sifre gecer", auth.authenticate("rol@x.com", "gucluParola1") is not None)
    auth.set_password("rol@x.com", "yeniParola99")
    check("eski sifre gecmez", auth.authenticate("rol@x.com", "gucluParola1") is None)
    check("yeni sifre gecer", auth.authenticate("rol@x.com", "yeniParola99") is not None)
    try:
        auth.set_password("rol@x.com", "kisa")
        check("kisa sifre reddedildi", False, "kabul edildi")
    except ValueError:
        check("kisa sifre reddedildi", True)

    key = "1.2.3.4:rol@x.com"
    check("baslangicta sinir yok", auth.rate_limited(key) is False)
    for _ in range(8):
        auth.record_attempt(key)
    check("8 denemeden sonra sinirli", auth.rate_limited(key) is True)
    auth.clear_attempts(key)
    check("basarili giristen sonra sifirlandi", auth.rate_limited(key) is False)


# --- prompt kurulumu -------------------------------------------------------
def test_build_messages() -> None:
    """Prompt TEK kaynaktan (app/rag.py) kurulur: uretim ve eval ayni fonksiyonu
    cagirir. Birlestirme sirasinda gelen alternatif surum system_prompt'u ve
    gecmis kirpmasini dusuruyordu; asagidaki iki kontrol o iki regresyonun
    geri gelmesini engeller."""
    print("\n[prompt kurulumu]")
    from app.rag import MAX_HISTORY_TURNS, build_rag_messages

    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(60)]
    messages = build_rag_messages("yeni soru", [], history, system_prompt="sistem")
    check("gecmis sinirlandi", len(messages) == MAX_HISTORY_TURNS * 2 + 2, f"-> {len(messages)}")
    check("en yeni turlar korundu", messages[-2]["content"] == "m59", f"-> {messages[-2]}")
    check("sistem prompt basta", messages[0] == {"role": "system", "content": "sistem"})

    with_ctx = build_rag_messages("izin kac gun", ["[kaynak: izin.md]\n20 is gunu"], [], system_prompt="sistem")
    check("baglam prompt'a girdi", "<context>" in with_ctx[-1]["content"])
    check("baglam yoksa duz soru", build_rag_messages("merhaba", [], [], system_prompt="s")[-1]["content"] == "merhaba")

    # REGRESYON KORUMASI 1: admin panelinden gelen system_prompt kullanilmali,
    # sabit gomulmemeli (aksi halde /api/admin/config'teki alan islevsizlesir).
    ozel = build_rag_messages("soru", [], [], system_prompt="OZEL TALIMAT")
    check("system_prompt parametresi kullaniliyor", ozel[0]["content"] == "OZEL TALIMAT", f"-> {ozel[0]}")

    # REGRESYON KORUMASI 2: uretim yolu (main.py) gercekten bu fonksiyonu cagirmali.
    import app.main as m
    check("main.py tek kaynagi kullaniyor", getattr(m, "build_rag_messages", None) is build_rag_messages)
    check("main.py'de kopya build_messages kalmadi", not hasattr(m, "build_messages"))


# --- Turkce / Unicode ------------------------------------------------------
def test_turkish() -> None:
    """Urun Turkce belgeler icin; Turkce karakterler hicbir katmanda bozulmamali."""
    print("\n[turkce / unicode]")
    from app.rag import _tokenize, _tr_lower

    check("buyuk I -> i", _tr_lower("İZİN") == "izin", f"-> {_tr_lower('İZİN')}")
    check("buyuk I (noktasiz) -> ı", _tr_lower("IRMAK") == "ırmak", f"-> {_tr_lower('IRMAK')}")
    check("s/g/u/o/c", _tr_lower("ŞĞÜÖÇ") == "şğüöç", f"-> {_tr_lower('ŞĞÜÖÇ')}")

    # BM25 token'lari diakritigi KATLAR (aramanin dogru calismasi icin). Kullanici
    # "calisan" yazip belgedeki "Çalışan"i bulabilsin diye. Bu yalnizca arama
    # indeksini etkiler; parca metni, dosya adi ve cevap tam Turkce kalir.
    check("tokenize diakritigi katliyor", "calisan" in _tokenize("Çalışan hakları"),
          f"-> {_tokenize('Çalışan hakları')}")

    # ASIL KURAL: katlama SORGU ve BELGE tarafinda AYNI olmali. Yalnizca birine
    # uygulanirsa arama sessizce bozulur — bu kontrol o hatayi yakalar.
    for diakritikli, duz in [("Çalışan hakları", "calisan haklari"),
                             ("Yıllık İzin", "yillik izin"),
                             ("Güvenliği", "guvenligi"),
                             ("şirket", "sirket")]:
        check(f"katlama tutarli: {diakritikli!r} == {duz!r}",
              _tokenize(diakritikli) == _tokenize(duz),
              f"-> {_tokenize(diakritikli)} vs {_tokenize(duz)}")

    # Parca METNI katlanmamali; kullaniciya gosterilen ve modele giden metin bu.
    from app.rag import Chunk, _format
    parca = Chunk(text="Yıllık ücretli izin 20 iş günüdür.", source="izin.md",
                  page=None, tokens=_tokenize("Yıllık ücretli izin"))
    check("parca metni Turkce kaliyor", "Yıllık ücretli izin" in _format(parca),
          f"-> {_format(parca)[:60]}")

    # Dosya adlari: Turkce harfler KORUNMALI (ASCII'ye indirgenmemeli)
    for original in ["şirket_izin_politikası.md", "Çalışan Rehberi.pdf", "İnsan Kaynakları.docx"]:
        got = docs.safe_name(original)
        check(f"dosya adi korundu: {original}", got == original, f"-> {got}")

    check("bosluk ve parantez korunur", docs.safe_name("rapor (güncel).pdf") == "rapor (güncel).pdf")

    # Tehlikeli karakterler yine temizlenmeli
    for bad, why in [("rapor<>.md", "acili parantez"), ('rapor".md', "tirnak"),
                     ("rapor|x.md", "boru"), ("rapor*.md", "yildiz"), ("rapor?.md", "soru")]:
        got = docs.safe_name(bad)
        check(f"temizlendi: {why}", all(ch not in got for ch in '<>"|*?'), f"-> {got}")

    # Turkce baslikli konusma
    conv = store.create_conversation("tr@x.com", "Yıllık iznimi ne zaman kullanmalıyım?")
    title = store.list_conversations("tr@x.com")[0]["title"]
    check("konusma basligi Turkce korur", title == "Yıllık iznimi ne zaman kullanmalıyım?", f"-> {title}")
    body = "Şirket politikası: yılda 20 iş günü ücretli izin."
    store.add_message(conv, "assistant", body, [{"source": "şirket_izin.md", "page": None}])
    saved = store.get_messages(conv, "tr@x.com")[0]
    check("mesaj icerigi Turkce korur", saved["content"] == body, f"-> {saved['content']}")
    check("kaynak adi Turkce korur", saved["sources"][0]["source"] == "şirket_izin.md",
          f"-> {saved['sources']}")
    store.delete_conversation(conv, "tr@x.com")

    # Emoji / dort baytli karakterler cokme yaratmamali
    conv = store.create_conversation("tr@x.com", "Rapor 📊 hakkında")
    check("emoji kabul edildi", store.list_conversations("tr@x.com")[0]["title"] == "Rapor 📊 hakkında")
    store.delete_conversation(conv, "tr@x.com")


# --- rastgele / kotu girdiler ---------------------------------------------
def test_fuzz() -> None:
    """Saf fonksiyonlar hicbir girdide beklenmedik istisna atmamali."""
    print("\n[fuzz — rastgele girdiler]")
    from app.auth import verify_token
    from app.rag import chunk_source, parse_sources

    rnd = random.Random(1337)
    alphabet = string.printable + "şğüöçİIı你好🙂\x00\x1b"

    def blob(n):
        return "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, n)))

    crashes = []
    for _ in range(400):
        text = blob(120)
        for fn, label in ((chunk_source, "chunk_source"), (lambda t: parse_sources([t]), "parse_sources"),
                          (verify_token, "verify_token")):
            try:
                fn(text)
            except Exception as exc:  # noqa: BLE001 — cokme ariyoruz
                crashes.append(f"{label}({text!r:.40}) -> {type(exc).__name__}: {exc}")
    check("saf fonksiyonlar cokmedi (400 tur)", not crashes, f"-> {crashes[:3]}")

    # verify_token hicbir rastgele girdide kimlik dogrulamamali
    forged = [verify_token(blob(80)) for _ in range(300)]
    check("rastgele token kimlik dogrulamadi", all(f is None for f in forged),
          f"-> {[f for f in forged if f][:3]}")

    # safe_name: ya gecerli ad ya ValueError; asla yol disina cikmamali
    bad_names, escapes = [], []
    for _ in range(300):
        raw = blob(40)
        try:
            name = docs.safe_name(raw)
        except ValueError:
            continue
        except Exception as exc:  # noqa: BLE001
            bad_names.append(f"{raw!r:.30} -> {type(exc).__name__}: {exc}")
            continue
        if "/" in name or "\\" in name or name.startswith(".") or ".." in name:
            escapes.append(f"{raw!r:.30} -> {name!r}")
    check("safe_name beklenmedik istisna atmadi", not bad_names, f"-> {bad_names[:3]}")
    check("safe_name asla yol uretmedi", not escapes, f"-> {escapes[:3]}")

    # parse_sources bozuk basliklarda sessizce atlamali
    malformed = ["[kaynak:]", "[kaynak: ]", "[kaynak: a.md, sayfa]", "[kaynak: a.md, sayfa abc]",
                 "[KAYNAK: a.md]", "kaynak: a.md", "[kaynak: a.md", "[[kaynak: a.md]]"]
    try:
        got = parse_sources(malformed)
        check("bozuk basliklar atlandi", isinstance(got, list), f"-> {got}")
    except Exception as exc:  # noqa: BLE001
        check("bozuk basliklar atlandi", False, f"{type(exc).__name__}: {exc}")

    # query._clean her turlu LLM ciktisinda string dondurmeli
    weird = ["", "   ", "\n\n", "<think>x</think>", '""', "a" * 5000, "sat1\nsat2\nsat3", "🙂"]
    outs = [query._clean(w, "yedek") for w in weird]
    check("_clean her zaman string dondu", all(isinstance(o, str) and o for o in outs), f"-> {outs}")


# --- es zamanlilik ---------------------------------------------------------
def test_concurrency() -> None:
    """SQLite deposu es zamanli yazma altinda veri kaybetmemeli/kilitlenmemeli."""
    print("\n[es zamanlilik]")
    conv = store.create_conversation("yogun@x.com", "yuk testi")
    errors, ids = [], []
    lock = threading.Lock()

    def writer(i):
        try:
            mid = store.add_message(conv, "assistant" if i % 2 else "user", f"mesaj {i}")
            with lock:
                ids.append(mid)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check("es zamanli yazmada hata yok", not errors, f"-> {errors[:3]}")
    check("40 mesajin hepsi yazildi", len(ids) == 40, f"-> {len(ids)}")
    check("mesaj kimlikleri benzersiz", len(set(ids)) == len(ids), f"-> {len(set(ids))}")
    stored = store.get_messages(conv, "yogun@x.com")
    check("hepsi geri okundu", len(stored) == 40, f"-> {len(stored)}")

    # Ayni mesaja es zamanli oy: son yazan kazanmali, cokme olmamali
    target = [m["id"] for m in stored if m["role"] == "assistant"][0]
    vote_errors = []

    def voter(v):
        try:
            store.set_vote(target, "yogun@x.com", v)
        except Exception as exc:  # noqa: BLE001
            with lock:
                vote_errors.append(str(exc))

    vt = [threading.Thread(target=voter, args=(1 if i % 2 else -1,)) for i in range(20)]
    for t in vt:
        t.start()
    for t in vt:
        t.join()
    check("es zamanli oylamada hata yok", not vote_errors, f"-> {vote_errors[:3]}")
    final = [m["vote"] for m in store.get_messages(conv, "yogun@x.com") if m["id"] == target][0]
    check("oy tutarli bir degerde", final in (1, -1), f"-> {final}")

    # Es zamanli konusma olusturma
    created = []
    def maker(i):
        with lock:
            created.append(store.create_conversation("yogun@x.com", f"konusma {i}"))
    ct = [threading.Thread(target=maker, args=(i,)) for i in range(15)]
    for t in ct:
        t.start()
    for t in ct:
        t.join()
    check("konusma kimlikleri benzersiz", len(set(created)) == 15, f"-> {len(set(created))}")

    for cid in created:
        store.delete_conversation(cid, "yogun@x.com")
    store.delete_conversation(conv, "yogun@x.com")


# --- veritabani surum gecisi ----------------------------------------------
def test_migration() -> None:
    """role kolonu sonradan eklendi; eski veritabani veri kaybetmeden acilmali."""
    print("\n[veritabani surum gecisi]")
    old_db = _TMP / "eski.db"
    conn = sqlite3.connect(old_db)
    conn.execute(
        """CREATE TABLE users (
            email TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL)"""
    )
    conn.execute("INSERT INTO users VALUES (?, ?, ?, ?)",
                 ("eski@x.com", "salt:hash", 1, time.time()))
    conn.commit()
    conn.close()

    original = settings.db_path
    settings.db_path = str(old_db)
    try:
        auth.init_db()
        store.init_db()
        user = auth.get_user("eski@x.com")
        check("eski kullanici korundu", user is not None and bool(user["is_admin"]), f"-> {user}")
        check("role kolonu eklendi", user.get("role") == auth.DEFAULT_ROLE, f"-> {user.get('role')}")
        check("liste role dondurur", all("role" in u for u in auth.list_users()))
        check("sohbet tablolari olustu", store.list_conversations("eski@x.com") == [])
        auth.init_db()  # tekrar calistirmak guvenli olmali
        check("init_db tekrar calistirilabilir", auth.get_user("eski@x.com") is not None)
    finally:
        settings.db_path = original


# --- kalicilik -------------------------------------------------------------
def test_persistence() -> None:
    """Veriler surec yeniden baglandiginda diskten geri gelmeli."""
    print("\n[kalicilik]")
    conv = store.create_conversation("kalici@x.com", "kalicilik testi")
    mid = store.add_message(conv, "assistant", "cevap", [{"source": "a.md", "page": 3}])
    store.set_vote(mid, "kalici@x.com", 1)
    docs.set_roles("izin.md", ["finans"])

    # Modulleri yeniden yukleyerek "yeniden baslatma" benzet
    import importlib
    importlib.reload(sys.modules["app.store"])
    importlib.reload(sys.modules["app.docs"])
    from app import docs as docs2, store as store2

    msgs = store2.get_messages(conv, "kalici@x.com")
    check("konusma diskten geldi", msgs is not None and len(msgs) == 1, f"-> {msgs}")
    check("kaynak atifi kalici", msgs[0]["sources"][0]["page"] == 3, f"-> {msgs[0]['sources']}")
    check("oy kalici", msgs[0]["vote"] == 1, f"-> {msgs[0]['vote']}")
    check("belge rolu kalici", docs2.load_acl().get("izin.md") == ["finans"], f"-> {docs2.load_acl()}")

    docs2.set_roles("izin.md", [])
    store2.delete_conversation(conv, "kalici@x.com")


# --- daha once uretilmis hatalarin regresyon testleri ----------------------
def test_regressions() -> None:
    """Hata avinda URETILEN hatalar. Bu testler o hatalarin geri donmesini engeller."""
    print("\n[regresyon — daha once uretilen hatalar]")
    from app.main import _as_int

    # HATA: sayisal olmayan conversation_id ws_chat'te int() ile cokuyordu.
    for junk in ("abc", "", None, "1.5", [], {}, "12abc"):
        check(f"_as_int cokmedi: {junk!r}", _as_int(junk) is None, f"-> {_as_int(junk)}")
    check("_as_int gecerli sayiyi cevirir", _as_int("42") == 42 and _as_int(7) == 7)

    # HATA: giris deneme sayaci sinirsiz buyuyordu (her yeni e-posta kalici giris).
    auth._attempts.clear()
    for i in range(auth._MAX_TRACKED_KEYS + 50):
        key = f"10.0.0.1:kullanici{i}@x.com"
        auth.rate_limited(key)
        auth.record_attempt(key)
    check("sayac ust sinirin altinda kaldi", len(auth._attempts) <= auth._MAX_TRACKED_KEYS,
          f"-> {len(auth._attempts)} giris")

    # Basarisiz olmayan sorgular sozlukte bos liste birakmamali
    auth._attempts.clear()
    auth.rate_limited("bos:anahtar")
    check("sorgulama bos giris birakmadi", "bos:anahtar" not in auth._attempts,
          f"-> {auth._attempts}")

    # Sinirin hala calistigini dogrula (duzeltme islevi bozmadi)
    auth._attempts.clear()
    key = "10.0.0.2:kurban@x.com"
    for _ in range(auth._MAX_ATTEMPTS):
        auth.record_attempt(key)
    check("sinir hala devrede", auth.rate_limited(key) is True)

    # HATA: belge silmek, hicbir kural yokken bos ACL dosyasi yaratiyordu.
    acl_file = docs.docs_dir().parent / "docs_acl.json"
    if acl_file.exists():
        acl_file.unlink()
    name, _ = docs.save_doc("silme_yan_etkisi.md", b"gecici")
    check("belge silindi", docs.delete_doc(name))
    check("bos ACL dosyasi olusmadi", not acl_file.exists(), f"-> {acl_file}")

    # Kural VARSA silme onu temizlemeli
    name, _ = docs.save_doc("kurallı_belge.md", b"gecici")
    docs.set_roles(name, ["finans"])
    check("kural yazildi", docs.load_acl().get(name) == ["finans"])
    docs.delete_doc(name)
    check("silince kural da gitti", name not in docs.load_acl(), f"-> {docs.load_acl()}")
    if acl_file.exists():
        acl_file.unlink()

    # HATA: Windows'ta alt klasordeki belgenin adi "alt\belge.md" uretiliyordu;
    # HTTP yolundan "alt/belge.md" geldigi icin rol atama 404 veriyor, silmede de
    # ACL kaydi geride kaliyordu.
    sub = docs.docs_dir() / "altklasor"
    sub.mkdir(exist_ok=True)
    (sub / "gizli.md").write_text("gizli icerik", encoding="utf-8")
    try:
        names = {d["name"] for d in docs.list_docs()}
        check("alt klasor adi ileri slash", "altklasor/gizli.md" in names, f"-> {names}")
        check("ters slash uretilmiyor", not any("\\" in n for n in names), f"-> {names}")

        docs.set_roles("altklasor/gizli.md", ["finans"])
        check("alt klasore rol atandi", docs.load_acl().get("altklasor/gizli.md") == ["finans"],
              f"-> {docs.load_acl()}")
        check("ters slash ile de ayni kayda ulasilir",
              docs.load_acl().get(docs.normalize("altklasor\\gizli.md")) == ["finans"])

        yetkisiz = {"email": "n@x.com", "is_admin": 0, "role": "ik"}
        ctx = ["[kaynak: altklasor/gizli.md]\ngizli icerik", "[kaynak: izin.md]\nizin"]
        kalan = docs.filter_context(ctx, yetkisiz)
        check("alt klasor kisitlamasi uygulandi", len(kalan) == 1, f"-> {kalan}")

        docs.delete_doc("altklasor/gizli.md")
        check("alt klasor belgesi silindi", not (sub / "gizli.md").exists())
        check("silince ACL kaydi da gitti", "altklasor/gizli.md" not in docs.load_acl(),
              f"-> {docs.load_acl()}")
    finally:
        for leftover in sub.glob("*"):
            leftover.unlink()
        sub.rmdir()
        acl2 = docs.docs_dir().parent / "docs_acl.json"
        if acl2.exists():
            acl2.unlink()

    # HATA: konusma listesi 50 ile sinirliydi; daha eskiler arayuzden ulasilamiyordu.
    many = [store.create_conversation("sayfa@x.com", f"k{i}") for i in range(55)]
    try:
        page1 = store.list_conversations("sayfa@x.com", limit=50, offset=0)
        page2 = store.list_conversations("sayfa@x.com", limit=50, offset=50)
        check("ilk sayfa 50", len(page1) == 50, f"-> {len(page1)}")
        check("ikinci sayfa kalanlari verdi", len(page2) == 5, f"-> {len(page2)}")
        check("toplam sayisi dogru", store.count_conversations("sayfa@x.com") == 55,
              f"-> {store.count_conversations('sayfa@x.com')}")
        ids = {c["id"] for c in page1} | {c["id"] for c in page2}
        check("en eski konusma sayfalamayla erisilebilir", many[0] in ids)
        check("sayfalar cakismiyor", len(ids) == 55, f"-> {len(ids)}")
    finally:
        for cid in many:
            store.delete_conversation(cid, "sayfa@x.com")

    # HATA: cikis yapmak oturumu sunucu tarafinda sonlandirmiyordu; kopyalanmis
    # bir cerez suresi dolana kadar (24 saat) calismaya devam ediyordu.
    auth.create_user("oturum@x.com", "gucluParola1")
    masaustu = auth.create_token("oturum@x.com")
    telefon = auth.create_token("oturum@x.com")
    check("iki cihaz da gecerli",
          auth.user_for_token(masaustu) and auth.user_for_token(telefon))
    check("token'lar farkli (jti)", masaustu != telefon)

    # Cikis: AYNI SANIYE icinde bile o oturum dusmeli...
    auth.revoke_token(telefon)
    check("cikis yapilan oturum gecersiz", auth.user_for_token(telefon) is None)
    # ...ama diger cihaz etkilenmemeli.
    check("diger cihaz etkilenmedi", auth.user_for_token(masaustu) is not None)
    check("verify_token imzayi hala dogrular", auth.verify_token(telefon) == "oturum@x.com")
    check("decode_token cop girdide None", auth.decode_token("abc") is None)
    check("iptalden sonra yeni giris calisir",
          auth.user_for_token(auth.create_token("oturum@x.com")) is not None)

    # Sifre degisikligi TUM oturumlari dusurmeli
    auth.set_password("oturum@x.com", "BaskaParola9")
    auth.revoke_sessions("oturum@x.com")
    check("sifre degisince tum oturumlar dustu", auth.user_for_token(masaustu) is None)
    check("sifre degisiminden sonraki token gecerli",
          auth.user_for_token(auth.create_token("oturum@x.com")) is not None)

    # HATA: ayni adla yukleme mevcut belgeyi sessizce eziyordu.
    ad1, yazildi1 = docs.save_doc("ayni_ad.md", b"birinci")
    ad2, yazildi2 = docs.save_doc("ayni_ad.md", b"ikinci")
    check("ilk yuklemede uzerine yazma yok", yazildi1 is False)
    check("ikinci yuklemede uzerine yazma bildirildi", yazildi2 is True)
    check("icerik guncellendi",
          (docs.docs_dir() / ad2).read_bytes() == b"ikinci")
    docs.delete_doc(ad2)

    # HATA: desteklenmeyen turler ne listeleniyor ne uyariliyordu (sessizce yok).
    (docs.docs_dir() / "rapor.html").write_text("<h1>rapor</h1>", encoding="utf-8")
    try:
        listed = {d["name"]: d for d in docs.list_docs()}
        check("desteklenmeyen dosya listelendi", "rapor.html" in listed, f"-> {list(listed)}")
        check("indexed=False isaretlendi", listed.get("rapor.html", {}).get("indexed") is False,
              f"-> {listed.get('rapor.html')}")
        check("desteklenen dosya indexed=True", listed["izin.md"]["indexed"] is True)
        check("indexed_names yalnizca desteklenenler", "rapor.html" not in docs.indexed_names(),
              f"-> {docs.indexed_names()}")
    finally:
        (docs.docs_dir() / "rapor.html").unlink()


def test_security() -> None:
    print("\n[guvenlik ve PII maskeleme]")

    # 1. TC Kimlik No
    t1, r1 = security.sanitize_pii("Benim TC numaram 12345678901 olarak kayitli.")
    check("TC Kimlik No tespit edildi", len(r1) == 1 and r1[0]["type"] == "TC_KİMLİK_NO")
    check("TC maskelendi", "[TC_NO: *******01]" in t1 and "12345678901" not in t1)

    # 2. TR IBAN
    t2, r2 = security.sanitize_pii("Maasimi TR33 0006 1005 1978 6423 8812 34 nolu hesaba yatir.")
    check("IBAN tespit edildi", len(r2) == 1 and r2[0]["type"] == "IBAN")
    check("IBAN maskelendi", "[IBAN: TR********************34]" in t2 and "1978" not in t2)

    # 3. Kredi Karti
    t3, r3 = security.sanitize_pii("Harcama karti: 5528-7900-1234-5678.")
    check("Kredi Karti tespit edildi", len(r3) == 1 and r3[0]["type"] == "KREDİ_KARTI")
    check("Kart maskelendi", "[KREDİ_KARTI: ****-****-****-5678]" in t3 and "7900" not in t3)

    # 4. Telefon Numarasi
    t4, r4 = security.sanitize_pii("Bana 0532 123 45 67 numarasindan ulasabilirsiniz.")
    check("Telefon tespit edildi", len(r4) == 1 and r4[0]["type"] == "TELEFON")
    check("Telefon maskelendi", "[TELEFON: 05**-***-**67]" in t4 and "123" not in t4)

    # 5. E-posta
    t5, r5 = security.sanitize_pii("Destek icin ahmet.yilmaz@sirket.com adresine yazin.")
    check("E-posta tespit edildi", len(r5) == 1 and r5[0]["type"] == "E_POSTA")
    check("E-posta maskelendi", "[E_POSTA: a***@sirket.com]" in t5 and "yilmaz" not in t5)

    # 6. Temiz Metin
    t6, r6 = security.sanitize_pii("Sirket izin politikasi hakkinda bilgi alabilir miyim?")
    check("Temiz metinde PII yok", len(r6) == 0 and t6 == "Sirket izin politikasi hakkinda bilgi alabilir miyim?")

    # 7. Audit Log
    aid = store.add_audit_log(
        email="audit@test.com",
        conv_id=1,
        query_text=t1,
        pii_types=["TC_KİMLİK_NO"],
        sources=["izin.md"],
    )
    check("Audit log eklendi", aid > 0)
    logs = store.list_audit_logs(limit=10)
    check("Audit log listelendi", any(l["id"] == aid for l in logs))
    check("Audit log PII tipi korundu", any("TC_KİMLİK_NO" in l["pii_types"] for l in logs if l["id"] == aid))
    stats = store.count_audit_logs()
    check("Audit istatistikleri hesaplandi", stats["total_events"] > 0 and stats["pii_events"] > 0)


def main() -> int:
    print(f"gecici ortam: {_TMP}")
    for test in (test_sources, test_docs_and_acl, test_condense, test_store, test_auth,
                 test_build_messages, test_turkish, test_fuzz, test_concurrency,
                 test_migration, test_persistence, test_regressions, test_security):
        test()
    print(f"\n{_passed} gecti, {len(_failed)} basarisiz")
    if _failed:
        print("basarisiz:", ", ".join(_failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

