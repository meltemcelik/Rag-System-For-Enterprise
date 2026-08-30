"""Hata avi — 2. tur. Sunucuya DOKUNMAZ (agent'larin olcumunu bozmasin).

Hipotezler:
  H8  Alt klasordeki belgeye rol atanabiliyor mu? (Windows \ vs / uyusmazligi)
  H9  Alt klasordeki belge silinince ACL kaydi kaliyor mu?
  H10 Konusma listesi 50 ile sinirli; daha eskiler erisilemez hale mi geliyor?
  H11 /api/health kimlik dogrulamasiz belge sayisi/model adi sizdiriyor mu?
  H12 parse_sources, parca metni icinde [kaynak: ...] gecerse yaniliyor mu?
  H13 Cok uzun gecmis condense prompt'unu sisiriyor mu?
"""

# Depo koku dosyanin kendi konumundan turetilir; sabit yol YAZILMAZ
# (bu betikler once depo disinda yazildi, oradan tasindi).
from pathlib import Path as _Path
_REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import os, sys, tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="hunt2_"))
_DOCS = _TMP / "docs"
(_DOCS / "gizli").mkdir(parents=True)
(_DOCS / "acik.md").write_text("Herkese acik belge.", encoding="utf-8")
(_DOCS / "gizli" / "maas.md").write_text("Maas bilgileri gizlidir.", encoding="utf-8")
os.environ["RAG_DOCS_DIR"] = str(_DOCS)
os.environ["RAG_CACHE_DIR"] = str(_TMP / "cache")
os.environ["RAG_MODE"] = "keyword"

sys.path.insert(0, _REPO_ROOT)
from app import docs, query, store  # noqa: E402
from app.config import settings  # noqa: E402
from app.rag import parse_sources  # noqa: E402

settings.db_path = str(_TMP / "t.db")

confirmed, clean = [], []


def verdict(name, is_bug, evidence):
    (confirmed if is_bug else clean).append((name, evidence))
    print(f"  {'HATA' if is_bug else 'ok  '}  {name}")
    print(f"        {evidence}")


# --- H8/H9: alt klasor + yol ayraci -----------------------------------------
print("\n[H8] Alt klasordeki belgeye rol atanabiliyor mu?")
listed = {d["name"] for d in docs.list_docs()}
sub_name = next((n for n in listed if "maas" in n), None)
print(f"        list_docs adi: {sub_name!r}")
# API yolu {name:path} oldugu icin istemciden HER ZAMAN ileri slash gelir:
api_name = "gizli/maas.md"
matches = api_name in listed
verdict("H8 alt klasor rol atama", not matches,
        f"API'den gelen {api_name!r} listedeki {sub_name!r} ile eslesmiyor -> 404")

print("\n[H9] Alt klasordeki belge silinince ACL kaydi kaliyor mu?")
docs.set_roles(sub_name, ["finans"])          # dogru ad ile kural yaz
had = docs.load_acl().get(sub_name)
docs.delete_doc(api_name)                      # API'nin kullanacagi ad ile sil
leftover = docs.load_acl().get(sub_name)
gone = not (_DOCS / "gizli" / "maas.md").exists()
verdict("H9 silmede ACL kaydi kaliyor", gone and leftover is not None,
        f"dosya silindi={gone}, ACL kaydi hala={leftover!r} (kural yazilmisti={had!r})")

# --- H10: konusma listesi siniri -------------------------------------------
print("\n[H10] Konusma listesi 50 ile sinirli mi, eskiler erisilemez mi?")
store.init_db()
ids = [store.create_conversation("cok@x.com", f"konusma {i}") for i in range(55)]
oldest = ids[0]
# Asil sorun ULASILAMAZLIK: sayfalama ile en eskiye varilabiliyor mu?
reachable, offset = False, 0
while True:
    page = store.list_conversations("cok@x.com", limit=50, offset=offset)
    if not page:
        break
    if any(c["id"] == oldest for c in page):
        reachable = True
        break
    offset += len(page)
total = store.count_conversations("cok@x.com")
verdict("H10 eski konusmalar erisilemez", not reachable,
        f"55 konusma, toplam={total}; en eski (#{oldest}) sayfalamayla erisilebiliyor={reachable}")

# --- H12: metin icinde sahte kaynak basligi --------------------------------
print("\n[H12] Parca metninde [kaynak: ...] gecerse ayristirma yaniliyor mu?")
tricky = "[kaynak: gercek.md]\nBelgede sunlar yaziyor:\n[kaynak: sahte.md]\nikinci satir"
got = parse_sources([tricky])
verdict("H12 sahte kaynak basligi", len(got) != 1 or got[0]["source"] != "gercek.md",
        f"-> {got}")

# --- H13: cok uzun gecmis --------------------------------------------------
print("\n[H13] Cok uzun gecmis condense prompt'unu sisiriyor mu?")
long_hist = []
for i in range(40):
    long_hist.append({"role": "user", "content": "soru " + "x" * 2000})
    long_hist.append({"role": "assistant", "content": "cevap " + "y" * 4000})
turns = long_hist[-query.MAX_HISTORY_TURNS * 2:]
prompt_chars = sum(len(m["content"]) for m in turns)
verdict("H13 condense prompt siniri", prompt_chars > 100_000,
        f"condense'e giden gecmis {prompt_chars} karakter "
        f"(son {query.MAX_HISTORY_TURNS} tur), tur sayisi sinirli ama TUR UZUNLUGU sinirsiz")

print(f"\n{'=' * 62}")
print(f"DOGRULANAN: {len(confirmed)}   temiz: {len(clean)}")
for n, e in confirmed:
    print(f"  * {n}")
