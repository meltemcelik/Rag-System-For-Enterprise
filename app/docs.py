"""Dokuman kutuphanesi: listeleme, yukleme, silme ve rol bazli erisim.

Belgeler `RAG_DOCS_DIR` altinda durur. Erisim kurallari ayri bir JSON dosyasinda
(`data/docs_acl.json`) tutulur: {"gizli.pdf": ["finans"]}. Listede olmayan belge
herkese aciktir; admin her belgeyi gorur.
"""
import json
import re
from pathlib import Path

from .rag import RagConfig, chunk_source

ALLOWED_SUFFIXES = {".txt", ".md", ".markdown", ".pdf", ".docx"}
MAX_BYTES = 25 * 1024 * 1024
# Turkce harfler korunur (\w Unicode'dur): "Calisan Rehberi.pdf" bozulmadan gecer.
# Disarida kalanlar: yol ayraclari, Windows'ta yasak karakterler, kontrol karakterleri.
_UNSAFE_CHARS = re.compile(r"[^\w.()\- ]", re.UNICODE)
_PATH_SEPARATORS = ("/", "\\")
# Windows'ta ayrilmis aygit adlari — uzantiyla bile dosya olarak kullanilamaz.
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}


def _cfg() -> RagConfig:
    return RagConfig()


def docs_dir() -> Path:
    return _cfg().docs_dir


def _acl_path() -> Path:
    return docs_dir().parent / "docs_acl.json"


def load_acl() -> dict[str, list[str]]:
    path = _acl_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: list(v) for k, v in data.items() if isinstance(v, list)}


def normalize(name: str) -> str:
    """Belge adini tek bicime indir (Windows ters slash -> ileri slash)."""
    return name.replace("\\", "/").strip("/")


def set_roles(name: str, roles: list[str]) -> None:
    name = normalize(name)
    acl = load_acl()
    if roles:
        acl[name] = sorted({r.strip().lower() for r in roles if r.strip()})
    else:
        acl.pop(name, None)
    path = _acl_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(acl, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_name(filename: str) -> str:
    """Yuklenen dosya adini guvenli hale getir.

    Tarayicilar yol bilgisi gondermez; ayrac iceren bir ad kasitli mudahaledir,
    bu yuzden sessizce duzeltmek yerine REDDEDILIR.
    """
    if any(sep in filename for sep in _PATH_SEPARATORS):
        raise ValueError("dosya adi yol ayraci iceremez")
    name = _UNSAFE_CHARS.sub("_", Path(filename).name).strip()
    if not name or name.startswith(".") or name.strip("._ ") == "":
        raise ValueError("gecersiz dosya adi")
    if Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError(f"desteklenmeyen tur (izinli: {', '.join(sorted(ALLOWED_SUFFIXES))})")
    if Path(name).stem.lower() in _RESERVED:
        raise ValueError("ayrilmis dosya adi")
    return name


def list_docs() -> list[dict]:
    """Klasordeki TUM belgeler.

    Desteklenmeyen turler de `indexed=False` ile listelenir: aksi halde klasore
    elle konan (or. .html) bir dosya ne indeksleniyor ne de goruluyordu —
    yonetici belgenin aramada cikmamasinin sebebini anlayamiyordu.
    """
    root = docs_dir()
    acl = load_acl()
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        rel = path.relative_to(root).as_posix()  # HTTP yoluyla ayni bicim
        stat = path.stat()
        out.append(
            {
                "name": rel,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "roles": acl.get(rel, []),
                "indexed": path.suffix.lower() in ALLOWED_SUFFIXES,
            }
        )
    return out


def indexed_names() -> set[str]:
    return {d["name"] for d in list_docs() if d["indexed"]}


def save_doc(filename: str, data: bytes) -> tuple[str, bool]:
    """(kaydedilen_ad, uzerine_yazildi_mi).

    Ayni adla yukleme mevcut belgeyi sessizce siliyordu; cagiran taraf bunu
    kullaniciya bildirebilsin diye ayrica donduruluyor.
    """
    if not data:
        raise ValueError("bos dosya")
    if len(data) > MAX_BYTES:
        raise ValueError(f"dosya {MAX_BYTES // (1024 * 1024)} MB sinirini asiyor")
    name = safe_name(filename)
    root = docs_dir()
    root.mkdir(parents=True, exist_ok=True)
    hedef = root / name
    replaced = hedef.exists()
    hedef.write_bytes(data)
    return name, replaced


def delete_doc(name: str) -> bool:
    name = normalize(name)
    root = docs_dir().resolve()
    target = (root / name).resolve()
    if root not in target.parents or not target.is_file():
        return False
    target.unlink()
    if name in load_acl():  # kural yoksa ACL dosyasina hic dokunma
        set_roles(name, [])
    return True


def allowed_sources(user: dict) -> set[str] | None:
    """Kullanicinin gorebilecegi belgeler; None = kisitlama yok (admin)."""
    if user.get("is_admin"):
        return None
    acl = load_acl()
    if not acl:
        return None
    role = (user.get("role") or "user").lower()
    blocked = {name for name, roles in acl.items() if role not in roles}
    if not blocked:
        return None
    return {name for name in indexed_names() if name not in blocked}


def filter_context(context: list[str], user: dict) -> list[str]:
    """Rol yetkisi olmayan belgelerden gelen parcalari baglamdan cikarir.

    Retrieval'dan SONRA suzer; boylece 5 retriever sinifinin arayuzu degismez.
    Bedeli: yetkisiz parcalar top_k'dan yer yiyebilir (kullanici daha az baglam
    gorur, asla fazlasini gormez) — guvenlik tarafinda hata yapmayan yon.
    """
    allowed = allowed_sources(user)
    if allowed is None:
        return context
    return [c for c in context if chunk_source(c) in allowed]


def visible_docs(user: dict) -> list[dict]:
    """Kullanicinin erisebilecegi indekslenmis belgeler."""
    all_d = list_docs()
    allowed = allowed_sources(user)
    if allowed is None:
        return [d for d in all_d if d.get("indexed") is not False]
    return [d for d in all_d if d["name"] in allowed and d.get("indexed") is not False]

