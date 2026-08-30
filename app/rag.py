"""RAG retrieval hook — the single seam the RAG team owns (v2).

v2: sayfa-farkinda PDF okuma + kaynak/sayfa atifi; PDF metni once PyMuPDF
(fitz) -> yoksa pypdf -> yoksa OCR; kaliteli chunk'lama; otomatik klasor;
kalici embedding cache; guardrail. main.py degismez (guard_reply kullanir).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class Retriever(Protocol):
    async def retrieve(self, query: str, doc_filter: str | None = None) -> list[str]: ...


class NullRetriever:
    cfg = None

    async def retrieve(self, query: str, doc_filter: str | None = None) -> list[str]:
        return []


def _load_dotenv() -> None:
    """rag.py kendi RAG_* ayarlarini os.getenv ile okur; .env'i de gorsun diye
    yukler (config.py yalnizca kendi Settings alanlarini yukler). Gercek ortam
    degiskenleri onceliklidir (setdefault) — bagimlilik yok, elle parse."""
    for base in (Path.cwd(), Path(__file__).resolve().parent.parent):
        env_path = base / ".env"
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key:
                    os.environ.setdefault(key, val)
        except Exception:
            pass
        break


_load_dotenv()


def _env(name: str, default: str) -> str:
    val = os.getenv(name)
    return val if val not in (None, "") else default


def _flag(name: str, default: str) -> bool:
    return _env(name, default).lower() in ("1", "true", "yes", "on")


@dataclass
class RagConfig:
    docs_dir: Path = field(default_factory=lambda: Path(_env("RAG_DOCS_DIR", "data/docs")))
    top_k: int = field(default_factory=lambda: int(_env("RAG_TOP_K", "4")))
    mode: str = field(default_factory=lambda: _env("RAG_MODE", "auto").lower())
    min_score: float = field(default_factory=lambda: float(_env("RAG_MIN_SCORE", "0.0")))
    # Moda ozel alt sinirlar (BM25 skoru 0-~20, kosinus 0-1 oldugu icin ayri).
    # RAG_MIN_SCORE > 0 verilirse ikisini de ezer (elle override).
    min_score_keyword: float = field(default_factory=lambda: float(_env("RAG_MIN_SCORE_KEYWORD", "4.0")))
    min_score_embed: float = field(default_factory=lambda: float(_env("RAG_MIN_SCORE_EMBED", "0.5")))

    chunk_size: int = field(default_factory=lambda: int(_env("RAG_CHUNK_SIZE", "900")))
    chunk_overlap: int = field(default_factory=lambda: int(_env("RAG_CHUNK_OVERLAP", "150")))
    embed_model: str = field(default_factory=lambda: _env("RAG_EMBED_MODEL", "nomic-embed-text"))
    ollama_base_url: str = field(default_factory=lambda: _env("OLLAMA_BASE_URL", "http://localhost:11434"))
    ocr: bool = field(default_factory=lambda: _flag("RAG_OCR", "true"))
    cache_dir: Path = field(default_factory=lambda: Path(_env("RAG_CACHE_DIR", "data/.rag_cache")))
    strict: bool = field(default_factory=lambda: _flag("RAG_STRICT", "true"))
    no_context_reply: str = field(
        default_factory=lambda: _env("RAG_NO_CONTEXT_REPLY", "Bu konuda belgelerimde bilgi bulamadim.")
    )
    # Reranking (asama 3): cross-encoder ile aday yeniden siralama.
    rerank_model: str = field(default_factory=lambda: _env("RAG_RERANK_MODEL", "BAAI/bge-reranker-v2-m3"))
    rerank_pool: int = field(default_factory=lambda: int(_env("RAG_RERANK_POOL", "20")))
    rerank_min_score: float = field(default_factory=lambda: float(_env("RAG_RERANK_MIN_SCORE", "0.5")))
    # Answerability (groundedness) kapisi: retrieval'dan sonra LLM'e "bu baglam
    # soruyu cevapliyor mu?" diye sorar. Skor esiginin ayiramadigi "konuyla ilgili
    # ama cevabi yok" durumlarini yakalar. RAG_GROUNDCHECK=true ile acilir.
    groundcheck: bool = field(default_factory=lambda: _flag("RAG_GROUNDCHECK", "false"))
    groundcheck_model: str = field(default_factory=lambda: _env("RAG_GROUNDCHECK_MODEL", "llama3.2:3b"))
    # Secici groundcheck: top kosinus bu degerin USTUNDEyse LLM'e sorma (0=kapali=her zaman sor)
    groundcheck_min_conf: float = field(default_factory=lambda: float(_env("RAG_GROUNDCHECK_MIN_CONF", "0.0")))

    # --- Esik yardimcilari (tum alanlar tanimlandiktan sonra) ---
    # RAG_MIN_SCORE > 0 verilirse moda ozel esikleri ezer (elle override).
    def keyword_threshold(self) -> float:
        return self.min_score if self.min_score > 0 else self.min_score_keyword

    def embed_threshold(self) -> float:
        return self.min_score if self.min_score > 0 else self.min_score_embed

    def rerank_threshold(self) -> float:
        return self.rerank_min_score


@dataclass
class Chunk:
    text: str
    source: str
    page: int | None = None
    tokens: list[str] = field(default_factory=list)


_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Turkce'ye ozel kucuk harf: Python'un lower()'i "İ"->"i̇" (combining dot) ve
# "I"->"i" yapar; bu Turkce kelimeleri eslesmez kilar. Once dogru eslemeyi yap.
_TR_LOWER_MAP = str.maketrans({"İ": "i", "I": "ı", "Ş": "ş", "Ğ": "ğ", "Ü": "ü", "Ö": "ö", "Ç": "ç"})


def _tr_lower(text: str) -> str:
    return text.translate(_TR_LOWER_MAP).lower()


# Retrieval'i bozan ve alakasiz sorularda skoru sisiren cok sik gecen kelimeler.
# Sadece islevsel/soru kelimeleri; "zorunlu", "izin" gibi anlam tasiyanlar YOK.
_STOPWORDS = frozenset(
    """
ve veya ile de da ki mi mı mu mü midir mudir mudur ne neden nasıl niçin niye için gibi
kadar daha çok az en bir bu şu o ben sen biz siz onlar bana sana ona bize size hangi
her hep bazı acaba ama fakat ancak yani çünkü eğer ise olan olarak var yok nedir
nerede nereye kaç mıdır
the a an of to in on at is are and or for with what how why when where who
""".split()
)


# --- BM25 icin Turkce normallestirme -----------------------------------------
# Iki olculmus kusuru kapatir (yalnizca BM25'i etkiler; embedding ham metni kullanir):
#
# 1) DIAKRITIK: kullanicilar "yillik izin" yazar, belgede "yıllık izin" gecer.
#    Tam kelime eslesmesine dayanan BM25 icin bunlar farkli kelimelerdi.
# 2) EK/GOVDE: Turkce eklemeli bir dil; "parola"/"parolalar", "masraf"/"masrafları"
#    eslesmiyor (bu ciftlerde ortak token SIFIR). Sabit uzunlukta on-ek kirpmasi
#    denendi ve OLCULDU -> VARSAYILAN KAPALI, cunku kapiyi dusuruyor:
#
#      kol                     altin set (102)   cesitli test (34)
#      yalnizca katlama            0.9493            24/34
#      katlama + kirpma 6          0.9329            26/34
#      katlama + kirpma 5          0.9231              -
#
#    Kirpma serbest sorularda kazandiriyor ama gevsek eslesme yuzunden "ilgili ama
#    belgede yok" tuzaklarini iceri aliyor (kacan red 8 -> 10) ve fazladan aday
#    RRF'te dogru parcayi ilk top_k'dan disari itiyor (kaynak isabeti 0.9868 ->
#    0.9737). Farkli bir korpusta denemek isteyen RAG_STEM_LEN=6 verip
#    eval/run.py ile YENIDEN OLCMELIDIR.
_TR_FOLD_MAP = str.maketrans({"ı": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c",
                              "â": "a", "î": "i", "û": "u"})


def _fold(text: str) -> str:
    """Turkce kucuk harf + diakritik katlamasi (BM25 eslesmesi icin)."""
    return _tr_lower(text).translate(_TR_FOLD_MAP)


_STEM_LEN = int(_env("RAG_STEM_LEN", "0"))
# Stopword'ler de ayni normallestirmeden gecmeli, yoksa "nasıl"/"nasil" ayrisir.
_STOPWORDS_FOLDED = frozenset(_fold(w) for w in _STOPWORDS)


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    for w in _WORD_RE.findall(_fold(text)):
        if len(w) <= 1 or w in _STOPWORDS_FOLDED:
            continue
        out.append(w[:_STEM_LEN] if _STEM_LEN and len(w) > _STEM_LEN else w)
    return out


def _read_pages(path: Path, cfg: RagConfig) -> list[tuple[str, int | None]]:
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md", ".markdown"):
        return [(path.read_text(encoding="utf-8", errors="ignore"), None)]
    if suffix == ".docx":
        try:
            import docx
            text = "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
            return [(text, None)]
        except Exception:
            return []
    if suffix == ".pdf":
        return _read_pdf(path, cfg)
    return []


def _read_pdf(path: Path, cfg: RagConfig) -> list[tuple[str, int | None]]:
    """Once PyMuPDF (en temiz metin), yoksa pypdf; bos sayfada OCR."""
    out: list[tuple[str, int | None]] = []
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(path))
        for i in range(doc.page_count):
            try:
                text = doc.load_page(i).get_text("text") or ""
            except Exception:
                text = ""
            if not text.strip() and cfg.ocr:
                text = _ocr_pdf_page(path, i)
            if text.strip():
                out.append((text, i + 1))
        doc.close()
        if out:
            return out
    except Exception:
        pass
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
    except Exception:
        return out
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if not text.strip() and cfg.ocr:
            text = _ocr_pdf_page(path, i - 1)
        if text.strip():
            out.append((text, i))
    return out


def _ocr_pdf_page(path: Path, page_index: int) -> str:
    try:
        import pytesseract
        from pdf2image import convert_from_path
    except Exception:
        return ""
    try:
        images = convert_from_path(str(path), first_page=page_index + 1, last_page=page_index + 1)
        if not images:
            return ""
        try:
            return pytesseract.image_to_string(images[0], lang="tur+eng")
        except Exception:
            return pytesseract.image_to_string(images[0])
    except Exception:
        return ""


_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def _split_text(text: str, size: int, overlap: int) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    units: list[str] = []
    for para in paragraphs:
        if len(para) <= size:
            units.append(para)
            continue
        sentence = ""
        for s in _SENT_SPLIT.split(para):
            s = s.strip()
            if not s:
                continue
            if len(sentence) + len(s) + 1 <= size:
                sentence = f"{sentence} {s}" if sentence else s
            else:
                if sentence:
                    units.append(sentence)
                while len(s) > size:
                    units.append(s[:size].strip())
                    s = s[size - overlap:]
                sentence = s
        if sentence:
            units.append(sentence)
    chunks: list[str] = []
    current = ""
    for u in units:
        if len(current) + len(u) + 2 <= size:
            current = f"{current}\n\n{u}" if current else u
        else:
            if current:
                chunks.append(current.strip())
            tail = current[-overlap:] if overlap and current else ""
            current = f"{tail}\n\n{u}".strip() if tail else u
    if current.strip():
        chunks.append(current.strip())
    return chunks


def _load_chunks(cfg: RagConfig) -> list[Chunk]:
    docs_dir = cfg.docs_dir
    if not docs_dir.exists() or not docs_dir.is_dir():
        return []
    exts = {".txt", ".md", ".markdown", ".pdf", ".docx"}
    chunks: list[Chunk] = []
    for path in sorted(docs_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in exts:
            continue
        # Her zaman ileri slash: Windows'ta "alt\belge.md" uretilirse HTTP yolundan
        # gelen "alt/belge.md" ile eslesmez ve rol/ACL kontrolleri sessizce kacar.
        source = path.relative_to(docs_dir).as_posix()
        for text, page in _read_pages(path, cfg):
            if not text.strip():
                continue
            for piece in _split_text(text, cfg.chunk_size, cfg.chunk_overlap):
                chunks.append(Chunk(text=piece, source=source, page=page, tokens=_tokenize(piece)))
    return chunks


def _format(chunk: Chunk) -> str:
    if chunk.page is not None:
        head = f"[kaynak: {chunk.source}, sayfa {chunk.page}]"
    else:
        head = f"[kaynak: {chunk.source}]"
    return f"{head}\n{chunk.text}"


# _format'in tersi: baglam parcalarindan yapisal atif bilgisi cikarir. Boylece
# retriever arayuzu degismeden API/arayuz kaynak gosterebilir.
_SOURCE_RE = re.compile(r"^\[kaynak:\s*(?P<source>.+?)(?:,\s*sayfa\s*(?P<page>\d+))?\]\s*$")


def chunk_source(formatted: str) -> str | None:
    m = _SOURCE_RE.match(formatted.split("\n", 1)[0].strip())
    return m.group("source") if m else None


def parse_sources(context: list[str]) -> list[dict]:
    """Baglamdaki benzersiz (belge, sayfa) atiflari, ilk gorulme sirasiyla."""
    out: list[dict] = []
    seen: set[tuple[str, int | None]] = set()
    for piece in context:
        head, _, body = piece.partition("\n")
        m = _SOURCE_RE.match(head.strip())
        if not m:
            continue
        page = int(m.group("page")) if m.group("page") else None
        key = (m.group("source"), page)
        if key in seen:
            continue
        seen.add(key)
        out.append({"source": m.group("source"), "page": page, "snippet": _snippet(body)})
    return out


def _snippet(text: str, limit: int = 220) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"


class BM25Retriever:
    def __init__(self, chunks: list[Chunk], cfg: RagConfig, k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.cfg = cfg
        self.k1 = k1
        self.b = b
        self.doc_len = [len(c.tokens) for c in chunks]
        self.avgdl = (sum(self.doc_len) / len(chunks)) if chunks else 0.0
        df: dict[str, int] = {}
        for c in chunks:
            for term in set(c.tokens):
                df[term] = df.get(term, 0) + 1
        n = len(chunks)
        self.idf = {t: max(0.0, math.log(1 + (n - f + 0.5) / (f + 0.5))) for t, f in df.items()}
        self.tf: list[dict[str, int]] = []
        for c in chunks:
            counts: dict[str, int] = {}
            for term in c.tokens:
                counts[term] = counts.get(term, 0) + 1
            self.tf.append(counts)

    def _score(self, q_terms: list[str], idx: int) -> float:
        tf = self.tf[idx]
        dl = self.doc_len[idx] or 1
        score = 0.0
        for term in q_terms:
            f = tf.get(term)
            if not f:
                continue
            idf = self.idf.get(term, 0.0)
            denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
            score += idf * (f * (self.k1 + 1)) / denom
        return score

    def ranked(self, query: str, limit: int, doc_filter: str | None = None) -> list[tuple[int, float]]:
        """Hybrid icin: esigi gecen (chunk_idx, skor) listesi, skora gore sirali."""
        q_terms = _tokenize(query)
        if not q_terms:
            return []
        thr = self.cfg.keyword_threshold()
        scored = [
            (i, self._score(q_terms, i)) for i in range(len(self.chunks))
            if not doc_filter or self.chunks[i].source == doc_filter
        ]
        scored = [(i, sc) for i, sc in scored if sc > 0 and sc >= thr]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    async def retrieve(self, query: str, doc_filter: str | None = None) -> list[str]:
        if not self.chunks:
            return []
        return await asyncio.to_thread(self._retrieve_sync, query, doc_filter)

    def _retrieve_sync(self, query: str, doc_filter: str | None = None) -> list[str]:
        q_terms = _tokenize(query)
        if not q_terms:
            return []
        scored = [
            (self._score(q_terms, i), i) for i in range(len(self.chunks))
            if not doc_filter or self.chunks[i].source == doc_filter
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        threshold = self.cfg.keyword_threshold()
        out: list[str] = []
        for score, idx in scored[: self.cfg.top_k]:
            if score <= 0 or score < threshold:
                continue
            out.append(_format(self.chunks[idx]))
        return out


class EmbeddingRetriever:
    _QCACHE_MAX = 512  # sorgu embedding onbellegi ust siniri (LRU-benzeri)

    def __init__(self, chunks: list[Chunk], cfg: RagConfig):
        self.chunks = chunks
        self.cfg = cfg
        self._doc_vecs: list[list[float]] | None = None
        self._lock = asyncio.Lock()
        self._qcache: dict[str, list[float]] = {}  # sorgu -> vektor (tekrar sorularda embed atlanir)

    async def _embed_query(self, query: str) -> list[float]:
        """Sorgu embedding'i — onbellekli. Ayni sorgu tekrar gelirse Ollama'ya gitmez."""
        key = query.strip()
        hit = self._qcache.get(key)
        if hit is not None:
            return hit
        vec = (await self._embed([key]))[0]
        self._qcache[key] = vec
        if len(self._qcache) > self._QCACHE_MAX:
            self._qcache.pop(next(iter(self._qcache)))
        return vec

    def _cache_file(self) -> Path:
        # Modele gore TEK cache dosyasi; icerik {parca_hash: vektor} sozlugu.
        # (Eski surum korpusun tamaminin hash'ini kullaniyordu -> tek belge
        #  degisince her sey yeniden embed'leniyordu. Artik parca-bazli/incremental.)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", self.cfg.embed_model)
        return self.cfg.cache_dir / f"emb_{safe}.json"

    def _chunk_key(self, text: str) -> str:
        return hashlib.sha1((self.cfg.embed_model + "::" + text).encode("utf-8")).hexdigest()

    def _load_cache(self) -> dict[str, list[float]]:
        try:
            f = self._cache_file()
            if f.exists():
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save_cache(self, cache: dict[str, list[float]]) -> None:
        try:
            self.cfg.cache_dir.mkdir(parents=True, exist_ok=True)
            path = self._cache_file()
            tmp = path.with_name(path.name + ".tmp")  # atomik yaz: once tmp, sonra replace
            tmp.write_text(json.dumps(cache), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            pass

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        """Ollama embedding cagrisi.

        Yeni Ollama: POST /api/embed  {"model", "input": [..]} -> {"embeddings": [[..]]}
        Eski Ollama: POST /api/embeddings {"model", "prompt": "tek metin"} -> {"embedding": [..]}
        Once yenisini dener; 404 alirsa eskisine duser (her metin icin ayri istek).
        """
        import httpx

        base = self.cfg.ollama_base_url.rstrip("/")
        async with httpx.AsyncClient(timeout=300) as client:
            # 1) Yeni toplu uc
            try:
                resp = await client.post(
                    f"{base}/api/embed",
                    json={"model": self.cfg.embed_model, "input": texts},
                )
                if resp.status_code != 404:
                    resp.raise_for_status()
                    data = resp.json()
                    embeddings = data.get("embeddings")
                    if embeddings is None and "embedding" in data:
                        embeddings = [data["embedding"]]
                    if embeddings:
                        return embeddings
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise

            # 2) Eski tekil uc (yedek)
            out: list[list[float]] = []
            for t in texts:
                resp = await client.post(
                    f"{base}/api/embeddings",
                    json={"model": self.cfg.embed_model, "prompt": t},
                )
                resp.raise_for_status()
                vec = resp.json().get("embedding")
                if not vec:
                    return []
                out.append(vec)
            return out

    async def _ensure_index(self) -> None:
        if self._doc_vecs is not None:
            return
        async with self._lock:
            if self._doc_vecs is not None:
                return

            cache = self._load_cache()
            keys = [self._chunk_key(c.text) for c in self.chunks]

            # Cache'te olmayan (yeni/degismis) benzersiz parcalari topla.
            # Ayni metin birden fazla kez geciyorsa yalnizca bir kez embed'lenir.
            missing: dict[str, str] = {}
            for key, chunk in zip(keys, self.chunks):
                if key not in cache and key not in missing:
                    missing[key] = chunk.text

            if missing:
                miss_keys = list(missing)
                miss_texts = [missing[k] for k in miss_keys]
                total = len(miss_texts)
                batch = max(1, int(_env("RAG_EMBED_BATCH", "64")))
                done = 0
                for i in range(0, total, batch):
                    part_keys = miss_keys[i:i + batch]
                    part_texts = miss_texts[i:i + batch]
                    vecs = await self._embed(part_texts)
                    if len(vecs) != len(part_texts):
                        # Embed basarisiz -> guvenli cikis; guardrail devreye girer.
                        self._doc_vecs = []
                        return
                    for k, v in zip(part_keys, vecs):
                        cache[k] = v
                    done += len(part_texts)
                    print(f"[rag] embedding {done}/{total} yeni parca islendi...", flush=True)
            else:
                print(f"[rag] embedding: {len(self.chunks)} parca tamamen cache'ten yuklendi (yeni embed yok).", flush=True)

            # Cache'i korpus boyutuyla sinirli tut: yalnizca mevcut parcalari sakla
            # (silinen belgelerin vektorleri temizlenir). Yeni embed varsa ya da
            # gereksiz eski kayit varsa diske yaz.
            current = set(keys)
            if missing or any(k not in current for k in cache):
                self._save_cache({k: cache[k] for k in keys})

            self._doc_vecs = [cache[k] for k in keys]

    async def retrieve(self, query: str, doc_filter: str | None = None) -> list[str]:
        if not self.chunks or not query.strip():
            return []
        await self._ensure_index()
        if not self._doc_vecs:
            return []
        q_vec = await self._embed_query(query)
        scored = sorted(
            ((_cosine(q_vec, dv), i) for i, dv in enumerate(self._doc_vecs)
             if not doc_filter or self.chunks[i].source == doc_filter),
            key=lambda x: x[0],
            reverse=True,
        )
        threshold = self.cfg.embed_threshold()
        out: list[str] = []
        for score, idx in scored[: self.cfg.top_k]:
            if score < threshold:
                continue
            out.append(_format(self.chunks[idx]))
        return out


    async def ranked(self, query: str, limit: int, doc_filter: str | None = None) -> list[tuple[int, float]]:
        """Hybrid icin: esigi gecen (chunk_idx, kosinus) listesi, skora gore sirali."""
        if not self.chunks or not query.strip():
            return []
        await self._ensure_index()
        if not self._doc_vecs:
            return []
        q_vec = await self._embed_query(query)
        thr = self.cfg.embed_threshold()
        scored = [
            (i, _cosine(q_vec, dv)) for i, dv in enumerate(self._doc_vecs)
            if not doc_filter or self.chunks[i].source == doc_filter
        ]
        scored = [(i, sc) for i, sc in scored if sc >= thr]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class HybridRetriever:
    """BM25 + embedding birlesimi (rapordaki 'kurumsal standart').

    Her iki motor kendi kalibre esigiyle aday uretir; adaylar Reciprocal Rank
    Fusion (RRF) ile birlestirilir. Kelime eslesmesi olmayan ama anlamca dogru
    parcalari embedding, tam kelime eslesmelerini BM25 yakalar.

    Guardrail: yalnizca IKISI DE bos donerse baglam bos kalir -> reddedilir.
    """

    RRF_K = 60  # RRF sabiti (literaturde standart)

    def __init__(self, chunks: list[Chunk], cfg: RagConfig):
        self.chunks = chunks
        self.cfg = cfg
        self.bm25 = BM25Retriever(chunks, cfg)
        self.embed = EmbeddingRetriever(chunks, cfg)

    async def retrieve(self, query: str, doc_filter: str | None = None) -> list[str]:
        if not self.chunks or not query.strip():
            return []

        pool = max(self.cfg.top_k * 4, 12)  # her motordan genis aday havuzu
        bm_hits = await asyncio.to_thread(self.bm25.ranked, query, pool, doc_filter)
        try:
            em_hits = await self.embed.ranked(query, pool, doc_filter)
        except Exception as exc:  # embed cokerse sistem BM25 ile calismaya devam etsin
            print(f"[rag] embedding hatasi, yalnizca BM25 kullaniliyor: {exc}")
            em_hits = []

        if not bm_hits and not em_hits:
            return []  # -> guard_reply reddi tetikler

        fused: dict[int, float] = {}
        for rank, (idx, _) in enumerate(bm_hits, start=1):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (self.RRF_K + rank)
        for rank, (idx, _) in enumerate(em_hits, start=1):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (self.RRF_K + rank)

        order = sorted(fused.items(), key=lambda x: x[1], reverse=True)
        return [_format(self.chunks[i]) for i, _ in order[: self.cfg.top_k]]


class RerankRetriever:
    """Asama 3: hybrid aday havuzu -> cross-encoder ile yeniden siralama.

    ONEMLI: adaylar BM25/embed ESIKLERINE TAKILMADAN (ham top-N) toplanir; cunku
    dogru cevap bazen embed esiginin (0.52) altinda kaliyordu (yanlis red). Reranker
    her adayi soruyla BIRLIKTE okuyup gercek alaka puani verir; sahte eslesmeye dusuk,
    gercek cevaba yuksek puan vererek ic ice gecen skorlari ayirir.

    Guardrail artik rerank skoruna gore: en iyi aday esigin altindaysa (ya da hic
    aday yoksa) baglam bos doner -> guard_reply reddi tetiklenir.
    """

    def __init__(self, chunks: list[Chunk], cfg: RagConfig):
        self.chunks = chunks
        self.cfg = cfg
        self.bm25 = BM25Retriever(chunks, cfg)
        self.embed = EmbeddingRetriever(chunks, cfg)
        self._model = None
        self._model_lock = asyncio.Lock()

    async def _get_model(self):
        if self._model is not None:
            return self._model
        async with self._model_lock:
            if self._model is None:
                def _load():
                    from sentence_transformers import CrossEncoder
                    return CrossEncoder(self.cfg.rerank_model, max_length=512)
                self._model = await asyncio.to_thread(_load)
        return self._model

    def _bm25_raw(self, query: str, n: int) -> list[int]:
        qt = _tokenize(query)
        if not qt:
            return []
        scored = [(i, self.bm25._score(qt, i)) for i in range(len(self.chunks))]
        scored = [(i, s) for i, s in scored if s > 0]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [i for i, _ in scored[:n]]

    async def _embed_raw(self, query: str, n: int) -> list[int]:
        await self.embed._ensure_index()
        if not self.embed._doc_vecs:
            return []
        qv = (await self.embed._embed([query]))[0]
        scored = [(i, _cosine(qv, dv)) for i, dv in enumerate(self.embed._doc_vecs)]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [i for i, _ in scored[:n]]

    async def rerank_scored(self, query: str) -> list[tuple[int, float]]:
        """(chunk_idx, rerank_skoru 0-1) — esiksiz, yuksekten dusuge. Kalibrasyon da bunu kullanir."""
        if not self.chunks or not query.strip():
            return []
        pool = max(self.cfg.rerank_pool, self.cfg.top_k)
        bm_idx = await asyncio.to_thread(self._bm25_raw, query, pool)
        try:
            em_idx = await self._embed_raw(query, pool)
        except Exception as exc:
            print(f"[rag] embedding hatasi (rerank aday havuzu), yalnizca BM25: {exc}")
            em_idx = []
        cand = list(dict.fromkeys(bm_idx + em_idx))  # birlesim, sirali-benzersiz
        if not cand:
            return []
        model = await self._get_model()
        pairs = [[query, self.chunks[i].text] for i in cand]

        def _predict():
            # sentence-transformers CrossEncoder.predict() bge-reranker icin ZATEN
            # 0-1 olasilik dondurur; ikinci kez sigmoid UYGULAMA (skorlari [0.5,0.73]'e
            # sikistirip gercek cevap ile sahte eslesmeyi ayirt edilemez kilar).
            return [float(x) for x in model.predict(pairs)]

        scores = await asyncio.to_thread(_predict)
        return sorted(zip(cand, scores), key=lambda x: x[1], reverse=True)

    async def retrieve(self, query: str, doc_filter: str | None = None) -> list[str]:
        ranked = await self.rerank_scored(query)
        if not ranked:
            return []
        thr = self.cfg.rerank_threshold()
        kept = [
            (i, s) for i, s in ranked
            if s >= thr and (not doc_filter or self.chunks[i].source == doc_filter)
        ][: self.cfg.top_k]
        if not kept:
            return []  # -> guard_reply reddi tetiklenir
        return [_format(self.chunks[i]) for i, _ in kept]


def _parse_grounded(content: str) -> bool:
    """LLM ciktisindan EVET/HAYIR karari. qwen3 gibi modeller <think>...</think>
    uretir; SADECE son cevaba bak (dusunme izinde iki kelime de gecebilir).
    Yalnizca net HAYIR'da reddet; EVET/belirsiz -> koru (fail-open)."""
    if "</think>" in content:
        content = content.rsplit("</think>", 1)[-1]
    a = content.upper()
    if "HAYIR" in a and "EVET" not in a:
        return False
    return True


class GroundedRetriever:
    """Answerability (groundedness) kapisi. Ic retriever'i sarar; retrieval'dan
    gelen baglami cevap uretilmeden once yerel LLM'e sorar:
    'Bu baglam soruyu cevaplamak icin gereken bilgiyi iceriyor mu? EVET/HAYIR'.

    Skor-tabanli guardrail'in ayiramadigi 'konuyla ilgili ama cevabi yok'
    durumlarini yakalar ( or. 'iznimi paraya cevirebilir miyim?' -> izin parcasina
    benziyor ama cevabi yok). HAYIR -> baglam bosaltilir -> guard_reply reddi.

    main.py degismez. RAG_GROUNDCHECK=true ile acilir. Hata/belirsizlikte baglam
    KORUNUR (fail-open): LLM hatasi yuzunden gecerli cevaplar reddedilmesin.
    """

    def __init__(self, inner, cfg: RagConfig):
        self.inner = inner
        self.cfg = cfg  # guard_reply cfg'yi buradan okur (strict/no_context_reply)

    async def retrieve(self, query: str) -> list[str]:
        context = await self.inner.retrieve(query)
        if not context:
            return []  # retrieval zaten reddetti
        # Secici kapi: yeterince guclu (yuksek kosinus) eslesmelerde LLM'e HIC sorma
        # -> gercek cevaplari asiri temkinli LLM'den korur; yalnizca kararsiz bandda sor.
        min_conf = getattr(self.cfg, "groundcheck_min_conf", 0.0) or 0.0
        if min_conf > 0:
            top = await self._top_cosine(query)
            if top is not None and top >= min_conf:
                return context  # yeterince guvenli -> LLM'siz kabul
        try:
            grounded = await self._is_grounded(query, context)
        except Exception as exc:
            print(f"[rag] groundedness kontrolu basarisiz, baglam korunuyor: {exc}")
            return context
        return context if grounded else []

    async def _top_cosine(self, query: str) -> float | None:
        """Ic retriever'in embedding motorundan en yuksek (esik ustu) kosinusu al."""
        embr = getattr(self.inner, "embed", None)
        if embr is None:
            return None
        try:
            hits = await embr.ranked(query, 1)
        except Exception:
            return None
        return hits[0][1] if hits else None

    def _messages(self, query: str, context: list[str]) -> list[dict]:
        """Few-shot answerability prompt'u. Kucuk modeller ornek gormeden bu meta-
        gorevi beceremiyor; 2 ornek (EVET/HAYIR) modeli net karar vermeye yonlendirir."""
        block = "\n\n".join(context)
        return [
            {"role": "system", "content": (
                "Gorevin: verilen BAGLAM'in, SORU'yu cevaplayacak bilgiyi ICERIP "
                "icermedigine karar vermek. Baglamda sorunun cevabi (sayi, kural, "
                "sure, tarih vb.) acikca varsa 'EVET'. Baglam yalnizca ayni konuyla "
                "ilgili ama o spesifik cevabi icermiyorsa 'HAYIR'. SADECE tek kelime "
                "yaz: EVET veya HAYIR. Baska hicbir sey yazma. /no_think"
            )},
            {"role": "user", "content": "BAGLAM:\nTam zamanli calisanlar yilda 20 is gunu ucretli yillik izne hak kazanir.\n\nSORU: Yillik izin kac gundur?"},
            {"role": "assistant", "content": "EVET"},
            {"role": "user", "content": "BAGLAM:\nKullanilmayan yillik iznin en fazla 10 gunu bir sonraki yila devredilebilir.\n\nSORU: Yillik iznimi paraya cevirebilir miyim?"},
            {"role": "assistant", "content": "HAYIR"},
            {"role": "user", "content": f"BAGLAM:\n{block}\n\nSORU: {query}"},
        ]

    async def _is_grounded(self, query: str, context: list[str]) -> bool:
        import httpx

        url = f"{self.cfg.ollama_base_url.rstrip('/')}/api/chat"
        payload = {
            "model": self.cfg.groundcheck_model,
            "messages": self._messages(query, context),
            "stream": False,
            "think": False,  # qwen3 gibi dusunen modellerde dusunmeyi kapat (hiz + temiz cikti)
            "options": {"temperature": 0},
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        content = (data.get("message") or {}).get("content") or ""
        return _parse_grounded(content)


def guard_reply(retriever, context: list[str]) -> str | None:
    cfg = getattr(retriever, "cfg", None)
    if cfg is not None and getattr(cfg, "strict", False) and not context:
        return cfg.no_context_reply
    return None


def _embed_model_available(cfg: RagConfig) -> bool:
    import httpx
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(f"{cfg.ollama_base_url.rstrip('/')}/api/tags")
            resp.raise_for_status()
            names = [m.get("name", "") for m in resp.json().get("models", [])]
    except Exception:
        return False
    base = cfg.embed_model.split(":")[0]
    return any(n == cfg.embed_model or n.split(":")[0] == base for n in names)


_MODE_BY_CLASS = {
    "BM25Retriever": "keyword",
    "EmbeddingRetriever": "embed",
    "HybridRetriever": "hybrid",
    "RerankRetriever": "rerank",
    "NullRetriever": "kapali",
}


def model_in_list(model: str, names: list[str]) -> bool:
    """Etiketli/etiketsiz ad eslesmesi: 'bge-m3' ile 'bge-m3:latest' aynidir."""
    base = model.split(":")[0]
    return any(n == model or n.split(":")[0] == base for n in names)


async def status(retriever, models: list[str] | None = None) -> dict:
    """Health/admin icin retriever durumu.

    `degraded=True` = embedding modeli Ollama'da yok. Hybrid bu durumda sessizce
    yalnizca BM25'e duser: sistem hatasiz gorunur ama cogu soruya bos baglam
    uretir. Bu bayrak o sessiz kalite dususunu gorunur kilar.

    `models` verilirse Ollama'ya YENIDEN sorulmaz. /api/health zaten model
    listesini cekiyor; ikinci bir /api/tags cagrisi ucun suresini ikiye
    katliyordu (bu makinede tek cagri ~2.2 sn).
    """
    inner = getattr(retriever, "inner", retriever)
    cfg = getattr(inner, "cfg", None)
    mode = _MODE_BY_CLASS.get(type(inner).__name__, "bilinmiyor")
    info = {
        "mode": mode,
        "chunks": len(getattr(inner, "chunks", None) or []),
        "groundcheck": isinstance(retriever, GroundedRetriever),
        "degraded": False,
    }
    if cfg is None:
        return info
    info["embed_model"] = cfg.embed_model
    if mode in ("hybrid", "embed", "rerank"):
        if models is None:
            available = await asyncio.to_thread(_embed_model_available, cfg)
        else:
            available = model_in_list(cfg.embed_model, models)
        info["embed_model_available"] = available
        info["degraded"] = not available
    return info


def _apply_groundcheck(base, cfg: RagConfig):
    """RAG_GROUNDCHECK=true ise retriever'i answerability kapisiyla sarar."""
    if cfg.groundcheck:
        print(f"[rag] answerability kapisi ACIK (groundedness, model={cfg.groundcheck_model})")
        return GroundedRetriever(base, cfg)
    return base


def get_retriever() -> Retriever:
    cfg = RagConfig()
    try:
        chunks = _load_chunks(cfg)
    except Exception as exc:
        print(f"[rag] indexleme basarisiz, duz sohbete dusuluyor: {exc}")
        return NullRetriever()
    if not chunks:
        print(f"[rag] '{cfg.docs_dir}' icinde belge yok -> RAG kapali (duz sohbet).")
        return NullRetriever()
    n_pdf = sum(1 for c in chunks if c.page is not None)
    mode = cfg.mode
    if mode == "auto":
        # Embedding modeli erisilebilirse hybrid (kurumsal standart), degilse keyword.
        mode = "hybrid" if _embed_model_available(cfg) else "keyword"

    if mode == "rerank":
        try:
            import sentence_transformers  # noqa: F401
        except Exception:
            print("[rag] sentence-transformers kurulu degil -> rerank yerine hybrid'e dusuluyor.")
            print("      (pip install sentence-transformers  ile kurup RAG_MODE=rerank yapin)")
            mode = "hybrid" if _embed_model_available(cfg) else "keyword"
        else:
            print(f"[rag] {len(chunks)} parca ({n_pdf} PDF-sayfa) - mod=rerank ({cfg.rerank_model} <- BM25+{cfg.embed_model}) - top_k={cfg.top_k}")
            return _apply_groundcheck(RerankRetriever(chunks, cfg), cfg)

    if mode == "hybrid":
        print(f"[rag] {len(chunks)} parca ({n_pdf} PDF-sayfa) - mod=hybrid (BM25 + {cfg.embed_model}) - top_k={cfg.top_k}")
        return _apply_groundcheck(HybridRetriever(chunks, cfg), cfg)
    if mode == "embed":
        print(f"[rag] {len(chunks)} parca ({n_pdf} PDF-sayfa) - mod=embed ({cfg.embed_model}) - top_k={cfg.top_k}")
        return _apply_groundcheck(EmbeddingRetriever(chunks, cfg), cfg)
    print(f"[rag] {len(chunks)} parca ({n_pdf} PDF-sayfa) - mod=keyword (BM25) - top_k={cfg.top_k}")
    return _apply_groundcheck(BM25Retriever(chunks, cfg), cfg)


# ==========================================================================
# Cevap uretimi prompt'u — TEK KAYNAK: hem app/main.py (uretim) hem eval/*
# (olcum) bu fonksiyonu cagirir, boylece OLCTUGUMUZ prompt ile kullanicinin
# gordugu prompt ayni olur. Prompt iki yerde ayri durursa olcum yalan soyler.
# ==========================================================================
DEFAULT_ANSWER_SYSTEM = "You are a helpful assistant. Answer concisely."

# Modele gonderilen gecmis bu kadar turla sinirli (baglam tasmasini onler).
MAX_HISTORY_TURNS = 10


def build_rag_messages(
    question: str,
    context: list[str],
    history: list[dict] | None = None,
    system_prompt: str | None = None,
) -> list[dict]:
    """Cevap uretimi icin Ollama /api/chat mesajlarini kurar.

    system_prompt admin panelinden degistirilebildigi icin PARAMETREDIR; sabit
    gomulurse /api/admin/config'teki alan sessizce islevsizlesir. Gecmis de
    MAX_HISTORY_TURNS ile kirpilir; kirpilmazsa uzun sohbette baglam sisip
    modelin cevabini bozar. Ikisi de regresyon testiyle korunuyor.
    """
    sp = system_prompt if system_prompt is not None else DEFAULT_ANSWER_SYSTEM
    turns = (history or [])[-MAX_HISTORY_TURNS * 2:]
    messages: list[dict] = [{"role": "system", "content": sp}, *turns]
    if context:
        block = "\n\n".join(context)
        content = (
            "Use the following context to answer the question. "
            "If it is not relevant, answer normally.\n\n"
            f"<context>\n{block}\n</context>\n\nQuestion: {question}"
        )
    else:
        # Baglam yoksa modele duz soru gider. RAG_STRICT=true iken buraya zaten
        # gelinmez: guard_reply retrieval seviyesinde reddeder.
        content = question
    messages.append({"role": "user", "content": content})
    return messages


# ==========================================================================
# Kaynak atifi — parse_sources yapisal ({source, page, snippet}) sonuc dondurur
# ve uretimde kullanilir; asagidaki iki yardimci ise duz metin etiketi uretir
# ve olcum betikleri icindir. Ucu de AYNI duzenli ifadeyi (_SOURCE_RE) kullanir.
# ==========================================================================
_PAGE_SUFFIX_RE = re.compile(r",\s*sayfa\s*\d+\s*$")


def source_label(chunk_text: str) -> str:
    """'[kaynak: dosya, sayfa N] ...' parcasindan 'dosya, sayfa N' etiketini cikarir."""
    m = _SOURCE_RE.match(chunk_text.strip().split("\n", 1)[0].strip())
    if not m:
        return ""
    page = m.group("page")
    return f"{m.group('source')}, sayfa {page}" if page else m.group("source")


def source_file(chunk_text_or_label: str) -> str:
    """Sadece dosya adi (', sayfa N' ekini atar) — karsilastirma/eslesme icin."""
    label = source_label(chunk_text_or_label) or chunk_text_or_label
    return _PAGE_SUFFIX_RE.sub("", label).strip()


def sources_of(context: list[str], limit: int | None = None) -> list[str]:
    """Baglamdaki parcalarin kaynak etiketlerini SIRAYLA, tekrarsiz dondurur (gosterim icin)."""
    out: list[str] = []
    for c in (context[:limit] if limit else context):
        label = source_label(c)
        if label and label not in out:
            out.append(label)
    return out
