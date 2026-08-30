# Kurumsal RAG Asistanı — Dokümantasyon

Şirket belgeleri üzerinde çalışan, **kaynak atıflı** ve halüsinasyona karşı
**guardrail'li** bir soru-cevap sistemi. Ollama üzerinden yerel bir LLM çalıştırır,
WebSocket ile **token-token streaming** yapar, **e-posta + şifre girişi** ve bir
**admin paneli** vardır.

Tüm retrieval mantığı tek dosyadadır: `app/rag.py`. Backend'in geri kalanı
(`main.py`, `auth.py`, `ollama.py`) retrieval'dan habersizdir; bu sayede
retriever'ı değiştirmek tek dosyaya dokunmak demektir.

İçindekiler:

1. [Ne yapar / ne yapmaz](#1-ne-yapar--ne-yapmaz)
2. [Mimari ve istek akışı](#2-mimari-ve-istek-akışı)
3. [Dosya yapısı](#3-dosya-yapısı)
4. [Kurulum ve çalıştırma](#4-kurulum-ve-çalıştırma)
5. [Yapılandırma (ortam değişkenleri)](#5-yapılandırma-ortam-değişkenleri)
6. [RAG hattı nasıl çalışıyor](#6-rag-hattı-nasıl-çalışıyor)
7. [Guardrail ve kaynak atıfı](#7-guardrail-ve-kaynak-atıfı)
8. [Ölçüm (eval)](#8-ölçüm-eval)
9. [Kimlik doğrulama nasıl çalışır](#9-kimlik-doğrulama-nasıl-çalışır)
10. [API referansı](#10-api-referansı)
11. [WebSocket protokolü](#11-websocket-protokolü)
12. [Frontend](#12-frontend)
13. [Retriever'ı değiştirmek / genişletmek](#13-retrieverı-değiştirmek--genişletmek)
14. [Güvenlik / production kontrol listesi](#14-güvenlik--production-kontrol-listesi)
15. [Sık karşılaşılan sorunlar](#15-sık-karşılaşılan-sorunlar)

---

## 1. Ne yapar / ne yapmaz

**Yapar**

- `data/docs` altındaki belgeleri (`.md`, `.txt`, `.html`, `.pdf`) okur, parçalar
  ve indeksler. PDF'lerde sayfa numarasını korur.
- Hibrit retrieval: BM25 (kelime) + bge-m3 (anlam) embedding, Reciprocal Rank
  Fusion ile birleştirilir.
- Bağlamda cevap yoksa **modeli hiç çağırmadan reddeder** (guardrail).
- Her cevabın altında hangi belgeden yararlanıldığını gösterir (kaynak atıfı).
- Ollama'daki modeli çalıştırır, cevabı token token akıtır (WebSocket).
- E-posta + şifre ile giriş; oturum httponly çerezle taşınır.
- Admin paneli: model / system prompt / temperature ayarı + kullanıcı ekle/sil.
- Konuşma geçmişini bağlantı süresince (bellekte) tutar → çok turlu sohbet.
- Embedding'leri diskte önbelleğe alır; yeniden başlatmada belge yeniden
  embed edilmez, yalnızca değişen/yeni parçalar hesaplanır.

**Yapmaz (bilinçli)**

- Harici vektör veritabanı yok. İndeks bellekte tutulur, embedding'ler JSON
  önbellekte saklanır. Bu korpus boyutu (birkaç bin parça) için yeterlidir.
- Konuşma kalıcılığı yok (bağlantı kapanınca geçmiş silinir).
- Kullanıcı başına sohbet kaydı yok.
- Belge yükleme arayüzü yok; belgeler `data/docs` klasörüne elle konur.

---

## 2. Mimari ve istek akışı

Üç parça: **Ollama** (LLM + embedding sunucusu), **FastAPI backend**, **basit HTML frontend**.

```
Tarayıcı (index.html / admin.html)
        │  HTTP (login, admin API)  +  WebSocket (chat)
        ▼
┌─────────────────────── FastAPI (app/main.py) ───────────────────────┐
│  auth.py     → giriş, çerez, yetki kontrolü (SQLite: data/users.db) │
│  /ws/chat    → mesaj al → retrieve → guardrail → prompt → stream    │
│  rag.py      → indeksleme, BM25 + embedding + RRF, guard_reply      │
│  ollama.py   → Ollama'ya async streaming istek                      │
└──────────────────────────────┬──────────────────────────────────────┘
                               │  HTTP stream (NDJSON) + /api/embeddings
                               ▼
                        Ollama (localhost:11434)
```

**Bir sohbet mesajının yolculuğu** (`/ws/chat`):

1. Tarayıcı `{"message": "..."}` gönderir.
2. Backend çerezdeki oturumu doğrular (geçersizse bağlantı zaten açılmaz).
3. `retriever.retrieve(mesaj)` çağrılır → eşiği geçen bağlam parçaları döner.
4. `guard_reply(retriever, parcalar)` → parça yoksa ve `RAG_STRICT=true` ise
   **model hiç çağrılmadan** "Bu konuda belgelerimde bilgi bulamadim." yanıtı
   döner. Akış burada biter.
5. Parça varsa `build_rag_messages(...)` system prompt + geçmiş + bağlam +
   soruyu birleştirir.
6. `ollama.stream_chat(...)` modelden token'ları akıtır; her token anında
   `{"type":"token","content":"..."}` olarak tarayıcıya gider.
7. Cevap bitince kaynak satırı eklenir: `📄 Kaynak: dosya1 · dosya2`.
8. `{"type":"done"}` gönderilir; mesaj ve cevap geçmişe eklenir.

**Açılışta ne oluyor** (`lifespan`): `get_retriever()` bir kez çağrılır. Belgeler
okunur, parçalanır, BM25 indeksi kurulur ve embedding'ler `data/.rag_cache`
içinden yüklenir. Yalnızca önbellekte olmayan parçalar Ollama'ya gönderilir.

---

## 3. Dosya yapısı

```
Rag_System_For_Enterprise/
├── app/
│   ├── config.py     Ortam değişkenleri (Ollama URL, model, auth ayarları)
│   ├── schemas.py    Pydantic modelleri (config, login, yeni kullanıcı)
│   ├── db.py         Paylaşılan SQLite bağlantısı (auth + store aynı dosya)
│   ├── auth.py       Kullanıcı DB'si + şifre hash + oturum token + rol + yetki
│   ├── store.py      Sohbet geçmişi: konuşmalar, mesajlar, kaynaklar, oylar
│   ├── docs.py       Doküman kütüphanesi + rol bazlı erişim (ACL)
│   ├── query.py      Takip sorusunu bağımsız arama sorgusuna çevirme
│   ├── ollama.py     Ollama'ya async streaming istemci
│   ├── rag.py        ★ Tüm RAG hattı (okuma, parçalama, retrieval, guardrail)
│   └── main.py       FastAPI: sayfalar, auth, admin API, /ws/chat
├── static/
│   ├── index.html    Chat arayüzü (sohbet + geçmiş + dokümanlar sekmeleri)
│   └── admin.html    Admin paneli (ayarlar + kullanıcı/rol yönetimi)
├── data/
│   ├── docs/         ★ Korpus: buraya konan belgeler indekslenir
│   ├── .rag_cache/   Embedding önbelleği (bilinçli olarak commitli)
│   └── users.db      SQLite kullanıcı DB'si (git'e girmez)
├── eval/             Ölçüm altyapısı — bkz. §8
│   ├── golden_set.jsonl        Altın set (soru + beklenen kaynak/anahtar kelime)
│   ├── golden_set_kilavuz.md   Sete soru eklerken uyulacak kurallar
│   ├── multiturn_set.jsonl     Takip sorusu seti (sohbet hattını ölçer)
│   ├── run.py                  ★ Regresyon kapısı (tek skor, CI'da kırar)
│   ├── evaluate.py             hit@k, MRR, guardrail
│   ├── answer_eval.py          uçtan uca cevap doğruluğu
│   ├── faithfulness_eval.py    halüsinasyon (yargıç model)
│   ├── citation_eval.py        kaynak atıfı doğruluğu
│   ├── latency_eval.py         gecikme + önbellek kazancı
│   ├── edge_cases.py           dayanıklılık / kenar durumlar
│   ├── calibrate.py, calibrate_hybrid.py, calibrate_rerank.py, sweep_embed.py
│   │                           eşik / chunk taraması (kalibrasyon)
│   └── diagnose.py, diagnose_refuse.py, debug_ground.py
│                               tek tek soru teşhisi
├── tools/            Rapor ölçümlerini üreten betikler — bkz. tools/README.md
├── test_features.py  Birim: atıf/ACL/Türkçe/fuzz/eşzamanlılık/migrasyon (sunucusuz)
├── test_api.py       Uçtan uca API + WebSocket (çalışan sunucu ister)
├── run.py            Tek komutla başlatıcı (deps + Ollama + model + sunucu)
├── test_rag.py       Sunucusuz hızlı retriever denemesi
├── Dockerfile
├── docker-compose.yml        Ollama dahil her şey
├── docker-compose.host.yml   Yalnızca backend (Ollama host'ta)
├── requirements.txt
├── .env.example
├── README.md         Hızlı başlangıç + ölçüm sonuçları
└── DOCS.md           Bu dosya
```

---

## 4. Kurulum ve çalıştırma

### A) Docker — en kolay (Ollama dahil her şey)

```bash
docker compose up --build
```

Ollama'yı ayağa kaldırır, `model-pull` servisi üretim ve embedding modellerini
indirir, sonra backend başlar → http://localhost:8000
Farklı model: `MODEL=qwen3:4b docker compose up --build`

Kalıcı named volume'ler: `ollama` (indirilen modeller), `userdata` (kullanıcı
DB'si), `ragcache` (embedding önbelleği). Bunları silmedikçe ikinci açılış hızlıdır.

**Ollama zaten host'ta kurulu ise** ikinci compose dosyasını kullanın; yalnızca
backend'i başlatır, `host.docker.internal:11434` üzerinden mevcut Ollama'ya bağlanır:

```bash
docker compose -f docker-compose.host.yml up --build
```

### B) Yerel — otomatik hazırlık

```bash
python run.py
```

`run.py` sırasıyla: eksik Python paketlerini kurar → Ollama'yı başlatır → modeli
indirir → sunucuyu açar. Tekrar çalıştırmak güvenli; tamamlanmış adımları atlar.
(Ollama'nın kurulu olması gerekir: https://ollama.com/download)

> Canlı reload isteyen geliştirici: `uvicorn app.main:app --reload`

İlk açılış, korpusun tamamı embed edileceği için birkaç dakika sürebilir.
Sonraki açılışlar `data/.rag_cache` sayesinde saniyeler içinde tamamlanır.

### C) Sunucusuz hızlı deneme

Retriever'ı sunucu açmadan denemek için:

```bash
python test_rag.py                        # varsayılan sorular
python test_rag.py "kendi sorun" --full   # parçaların tam metni
RAG_MODE=keyword python test_rag.py       # BM25, Ollama gerekmez
```

### Erişim linkleri

Sunucu çalışırken (varsayılan port 8000). Farklı makineden erişim için
`localhost` yerine sunucunun IP'sini yazın.

**Tarayıcıda açılan sayfalar**

- Chat (frontend): http://localhost:8000/
- Admin paneli: http://localhost:8000/admin
- API dokümanı (FastAPI otomatik, denenebilir): http://localhost:8000/docs

**API uçları**

- Sağlık / Ollama durumu: http://localhost:8000/api/health
- Aktif kullanıcı: http://localhost:8000/api/me
- Aktif ayarlar (admin): http://localhost:8000/api/admin/config
- Kurulu modeller (admin): http://localhost:8000/api/admin/models
- Kullanıcılar (admin): http://localhost:8000/api/admin/users

**WebSocket**

- Chat akışı: `ws://localhost:8000/ws/chat` (HTTPS arkasında `wss://`)

> Not: Sayfalar giriş ister, `/api/admin/*` uçları admin oturumu ister — çıplak
> açıldığında `401/403` dönerler; tarayıcıda giriş yaptıktan sonra çalışırlar.
> Tam liste ve gövde örnekleri için [§10 API referansı](#10-api-referansı).

---

## 5. Yapılandırma (ortam değişkenleri)

`.env.example` dosyasını `.env` olarak kopyalayıp düzenleyin (veya ortam değişkeni
olarak verin). Tümü opsiyoneldir; aşağıda varsayılanlarıyla listelenir.
`.env` dosyası `.gitignore`'dadır, **commit edilmez**.

### Sunucu ve model

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama sunucusunun adresi |
| `DEFAULT_MODEL` | `llama3.2:3b` | Cevap üreten model (admin panelden değişir) |
| `SYSTEM_PROMPT` | `You are a helpful assistant...` | Varsayılan sistem talimatı |
| `TEMPERATURE` | `0.7` | Üretim rastgeleliği (0–2) |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Sunucu adresi |

### Kimlik doğrulama

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `SECRET_KEY` | `change-me-in-production` | Oturum token'ı imzalama anahtarı — **production'da değiştir** |
| `SESSION_TTL_HOURS` | `24` | Oturum süresi (saat) |
| `DB_PATH` | `data/users.db` | Kullanıcı veritabanı dosyası |
| `ADMIN_EMAIL` | `admin@example.com` | İlk admin e-postası (yalnızca DB boşsa) |
| `ADMIN_PASSWORD` | `admin` | İlk admin şifresi (yalnızca DB boşsa) |

### RAG — belgeler ve parçalama

| Değişken | Varsayılan | Üretimde | Açıklama |
|---|---|---|---|
| `RAG_DOCS_DIR` | `data/docs` | aynı | Korpus klasörü |
| `RAG_TOP_K` | `4` | aynı | Kaç parça döndürülsün |
| `RAG_MODE` | `auto` | `hybrid` | `keyword` \| `embed` \| `hybrid` \| `rerank` \| `auto` |
| `RAG_EMBED_MODEL` | `nomic-embed-text` | `bge-m3` | Ollama embedding modeli |
| `RAG_CHUNK_SIZE` | `900` | `400` | Parça boyutu (kelime) |
| `RAG_CHUNK_OVERLAP` | `150` | `80` | Komşu parçalar arası örtüşme |
| `RAG_OCR` | `true` | aynı | Metin katmanı boş PDF sayfalarında OCR dene |
| `RAG_CACHE_DIR` | `data/.rag_cache` | aynı | Embedding önbelleği |
| `RAG_EMBED_BATCH` | `64` | aynı | İndekslemede tek seferde embed edilen parça |

### RAG — eşikler ve guardrail

| Değişken | Varsayılan | Üretimde | Açıklama |
|---|---|---|---|
| `RAG_MIN_SCORE_KEYWORD` | `4.0` | `6.80` | BM25 alt sınırı (skor 0–~20) |
| `RAG_MIN_SCORE_EMBED` | `0.5` | `0.535` | Kosinüs alt sınırı (0–1) |
| `RAG_MIN_SCORE` | `0.0` | — | `>0` verilirse üstteki ikisini birden ezer |
| `RAG_STRICT` | `true` | aynı | Bağlam yoksa modeli çağırmadan reddet |
| `RAG_NO_CONTEXT_REPLY` | `Bu konuda belgelerimde bilgi bulamadim.` | aynı | Red yanıtının metni |

Eşikler 102 soruluk golden set üzerinde `eval/calibrate_hybrid.py` ile kalibre
edildi (iki eşiği birlikte tarar; tek tek optimize etmek hybrid'de yanıltıcıdır).
**Kendi belgelerinizle mutlaka yeniden ölçün** — bu değerler bu korpusa özeldir.

### RAG — cevaplanabilirlik kapısı (opsiyonel)

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `RAG_GROUNDCHECK` | `false` | Eşiği geçen bağlam için ikinci bir LLM'e "bu bağlam soruyu cevaplıyor mu?" diye sor |
| `RAG_GROUNDCHECK_MODEL` | `llama3.2:3b` | Kapı modeli (üretimde `qwen3:4b`) |
| `RAG_GROUNDCHECK_MIN_CONF` | `0.0` | Yalnızca güven skoru bu değerin **altındaki** sorgularda çalıştır (seçici tetikleme) |

Varsayılan kapalıdır: her sorguya fazladan bir LLM çağrısı ekler ve ölçümlerde
guardrail kazancı, eklediği gecikme ve yanlış red riskini karşılamadı.

### RAG — rerank modu (yalnızca `RAG_MODE=rerank`)

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `RAG_RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | Cross-encoder modeli |
| `RAG_RERANK_POOL` | `20` | Yeniden sıralanacak aday havuzu |
| `RAG_RERANK_MIN_SCORE` | `0.5` | Rerank sonrası alt sınır |

`sentence-transformers` kurulu değilse (varsayılan olarak `requirements.txt`'te
yoktur) sistem uyarı basar ve çalışmaya devam eder — çökmez.

---

## 6. RAG hattı nasıl çalışıyor

### 6.1 Belge okuma

`data/docs` altındaki dosyalar uzantıya göre okunur:

- `.md`, `.txt` → düz metin.
- `.html` → etiketler ayıklanır, metin çıkarılır.
- `.pdf` → önce **PyMuPDF (fitz)**, olmazsa **pypdf**. Metin katmanı boş kalan
  sayfalar için `RAG_OCR=true` ise **pytesseract + pdf2image** ile OCR denenir.
  PDF'lerde sayfa numarası korunur.

### 6.2 Parçalama (chunking)

Metin `RAG_CHUNK_SIZE` kelimelik parçalara bölünür, komşu parçalar
`RAG_CHUNK_OVERLAP` kadar örtüşür (cümlenin parça sınırında kesilip anlamını
kaybetmemesi için). Her parçanın başına kaynak etiketi eklenir:

```
[kaynak: sirket_izin_politikasi.md]
[kaynak: Enterprise RAG ... .pdf, sayfa 12]
```

Bu etiket hem modele hangi belgeden okuduğunu söyler, hem de kullanıcıya
gösterilecek kaynağın çıkarıldığı yerdir.

### 6.3 Skorlama

**BM25 (`RAG_MODE=keyword`)** — saf Python, `k1=1.5`, `b=0.75`. Ollama
gerektirmez. Tokenizasyon Türkçeye duyarlıdır: `İ→i`, `I→ı`, `Ş→ş`, `Ğ→ğ`,
`Ü→ü`, `Ö→ö`, `Ç→ç` dönüşümü `.lower()`'dan **önce** yapılır (Python'un
varsayılan `lower()`'ı Türkçe `I`'yı yanlış çevirir). Ayrıca bir durak kelime
listesi uygulanır.

**Embedding (`RAG_MODE=embed`)** — Ollama `/api/embeddings` ile `bge-m3`,
kosinüs benzerliği. İki katmanlı önbellek:

- *Parça önbelleği*: `data/.rag_cache/emb_<model>.json`, anahtar
  `sha1(model + "::" + metin)`. Atomik yazılır (geçici dosya + `replace`),
  silinen parçaların vektörleri temizlenir. Belge değişmediyse yeniden embed yok.
- *Sorgu önbelleği*: bellekte, son 512 sorgu. Aynı soru ikinci kez sorulduğunda
  embedding çağrısı hiç yapılmaz — gecikme ~2.3s'den ~62ms'ye düşer (~37x).

**Hibrit (`RAG_MODE=hybrid`, üretim ayarı)** — BM25 ve embedding ayrı ayrı
sıralanır, sonra **Reciprocal Rank Fusion** ile birleştirilir:

```
RRF(d) = Σ  1 / (k + sıra_i(d))        k = 60
```

RRF sıralamayı kullanır, ham skoru değil; bu yüzden ölçekleri farklı iki
sinyali (BM25 0–20, kosinüs 0–1) normalize etmeye gerek kalmaz. Eşik kontrolü
her sinyal için kendi ölçeğinde ayrı yapılır.

**Rerank (`RAG_MODE=rerank`)** — hibrit sonuçtan `RAG_RERANK_POOL` adaylık bir
havuz alınır, cross-encoder ile yeniden sıralanır. Ölçümlerde hibrite belirgin
üstünlük sağlamadığı ve ağır bir bağımlılık getirdiği için varsayılan değildir.

**`auto`** — embedding erişilebilirse `hybrid`, değilse `keyword`.

---

## 7. Guardrail ve kaynak atıfı

### Guardrail

Halüsinasyona karşı asıl savunma **eşiktir**: `retrieve()` yalnızca eşiği geçen
parçaları döndürür. Hiç parça kalmazsa `guard_reply()` devreye girer:

```python
refusal = guard_reply(retriever, chunks)
if refusal:
    # model HİÇ çağrılmaz, doğrudan bu metin döner
    return refusal
```

`RAG_STRICT=true` iken bu, "belgede yoksa uydurma" garantisinin en ucuz ve en
kesin biçimidir — çünkü uydurabilecek bileşen (LLM) hiç çalıştırılmaz.
`RAG_STRICT=false` yapılırsa sistem bağlamsız da olsa modele sorar (düz sohbet
davranışı).

İkinci kademe olarak `RAG_GROUNDCHECK=true` ile bir "cevaplanabilirlik kapısı"
açılabilir: bağlam geldiği hâlde soruyu cevaplamıyorsa yakalar. Few-shot
EVET/HAYIR prompt'u kullanır, `<think>...</think>` bloklarını ayıklar ve
**fail-open** çalışır (kapı modeli hata verirse cevap üretmeye devam edilir,
sistem kilitlenmez). Varsayılan kapalıdır.

### Kaynak atıfı

Cevap tamamlandıktan sonra, kullanılan parçaların `[kaynak: ...]` etiketlerinden
belge adları çıkarılır ve cevabın altına eklenir:

```
📄 Kaynak: sirket_izin_politikasi.md · masraf_yonetmeligi.md
```

İlgili yardımcılar `rag.py` içindedir: `source_label`, `source_file`, `sources_of`.

---

## 8. Ölçüm (eval)

Her değişiklik **önce ve sonra** ölçülerek yapıldı. Ölçüm dosyası
`eval/golden_set.jsonl` — 102 soru, **76 cevaplanabilir + 26 "belgede yok" tuzağı**.

```bash
# REGRESYON KAPISI — değişiklikten sonra önce bunu çalıştırın.
# Tek skor üretir; eşiğin altına düşerse sıfırdan farklı çıkış kodu döner (CI).
python eval/run.py
python eval/run.py --fail-under 0.94
python eval/run.py --multiturn --verbose    # takip sorusu setini de ölç

# retrieval + guardrail (LLM çağırmaz, hızlı)
python eval/evaluate.py
python eval/evaluate.py --k 3
RAG_MODE=keyword python eval/evaluate.py   # BM25 ile karşılaştır

# kaynak atıfı (LLM çağırmaz, hızlı)
python eval/citation_eval.py

# dayanıklılık / kenar durumlar (LLM çağırmaz)
python eval/edge_cases.py

# gecikme + önbellek kazancı (soğuk/sıcak geçiş)
python eval/latency_eval.py

# uçtan uca cevap doğruluğu (Ollama açık olmalı, yavaş)
python eval/answer_eval.py --limit 20

# sadakat / halüsinasyon — yargıç model kullanır (yavaş)
python eval/faithfulness_eval.py --limit 15
```

Üretim konfigürasyonundaki sonuçlar için [README](README.md#ölçüm-sonuçları)'e bakın.

Sete yeni soru eklerken `eval/golden_set_kilavuz.md` içindeki kurallara uyun —
özellikle "ilgili ama belgede yok" tuzaklarının gerçekten hiçbir belgede
bulunmadığını gözle doğrulayın.

**Kalibrasyon yardımcıları** (eşik/parça ayarını veriyle seçer, tahminle değil):
`calibrate.py` (tek eşik), `calibrate_hybrid.py` (BM25 + embed eşiğini **birlikte**
tarar — üretim değerleri bununla bulundu), `calibrate_rerank.py` (cross-encoder
eşiği), `sweep_embed.py` (yanlış red / yanlış kabul takasını tek çalıştırmada
gösterir).

**Teşhis yardımcıları** (tek tek soru inceleme): `diagnose.py` (yanlış reddedilen
soruların gerçek BM25/kosinüs skorları), `diagnose_refuse.py` ("belgede yok"
sorusu hangi kapıdan sızdı), `debug_ground.py` (groundcheck LLM'inin ham cevabı).

Ayrıca `python test_rag.py "soru metni" --full` ile tek bir sorunun döndürdüğü
parçaları sunucu açmadan görebilirsiniz.

**`tools/` ile farkı:** `eval/` sistemin kalitesini sürekli ölçer (regresyon kapısı,
CI). `tools/` ise raporlardaki iddiaları üreten, çoğu tek seferlik doğrulama ve hata
avı betiklerini barındırır — bkz. [tools/README.md](tools/README.md).

---

## 9. Kimlik doğrulama nasıl çalışır

- **Kullanıcılar** SQLite'ta (`data/users.db`) saklanır: e-posta, şifre hash'i,
  admin bayrağı.
- **Şifreler** düz metin tutulmaz — PBKDF2-HMAC-SHA256, kullanıcıya özel salt,
  200.000 iterasyon.
- **Giriş** (`POST /api/login`) başarılıysa, sunucu HMAC ile imzalanmış bir
  oturum token'ı üretip **httponly çerez** olarak yollar. Çerez httponly olduğu
  için JavaScript okuyamaz (XSS'e karşı token çalınmasını zorlaştırır).
- **Her istekte** çerezdeki token doğrulanır: imza geçerli mi + süresi dolmuş mu.
  Token sunucuda saklanmaz (stateless), `SECRET_KEY` değişirse tüm oturumlar düşer.
- **İlk admin**: sunucu ilk açıldığında kullanıcı tablosu boşsa, `ADMIN_EMAIL` /
  `ADMIN_PASSWORD` ile bir admin oluşturulur ve konsola basılır.
- **Roller**: `is_admin` bayrağı. Admin uçları (`/api/admin/*`) yalnızca admin'e
  açık; normal kullanıcılar yalnızca giriş yapıp sohbet edebilir.

**Yetki seviyeleri:**

- **— (herkes)**: `/`, `/api/login`, `/api/logout`, `/api/health`
- **giriş gerekli**: `/api/me`, `/ws/chat`
- **admin gerekli**: tüm `/api/admin/*`

---

## 10. API referansı

Temel URL: `http://localhost:8000`. Gövdeler JSON'dur.

### Kimlik doğrulama

**`POST /api/login`** — giriş yap

```json
// istek
{ "email": "admin@example.com", "password": "admin" }
// yanıt 200 (+ Set-Cookie: session=...)
{ "email": "admin@example.com", "is_admin": true }
```

**`POST /api/logout`** — çıkış (çerezi siler) → `{ "ok": true }`

**`GET /api/me`** — aktif kullanıcı (giriş gerekli) → `{ "email": "...", "is_admin": false, "role": "user" }`
Giriş yoksa `401`. Frontend, açılışta bunu çağırıp login mi chat mi göstereceğine karar verir.

**`POST /api/password`** — kendi şifreni değiştir (giriş gerekli)
```json
{ "current_password": "...", "new_password": "en az 8 karakter" }
// 200 { "ok": true }  |  403 mevcut şifre hatalı  |  400 çok kısa
```

> Giriş denemeleri sınırlıdır: aynı IP + e-posta için 5 dakikada 8 başarısız
> denemeden sonra `429`. Başarılı girişte sayaç sıfırlanır.

### Durum

**`GET /api/health`** — Ollama ve RAG durumu

```json
{
  "ok": true,
  "models": ["qwen3:8b", "bge-m3:latest", ...],
  "rag": {
    "mode": "hybrid", "chunks": 163, "groundcheck": false,
    "embed_model": "bge-m3", "embed_model_available": true,
    "degraded": false
  }
}
{ "ok": false, "error": "..." }   // Ollama kapalıysa
```
**`rag.degraded = true`** ⇒ embedding modeli Ollama'da yok. Hybrid bu durumda
*sessizce* yalnızca BM25'e düşer: sistem hatasız görünür ama çoğu soruya boş
bağlam üretip "bilgi bulamadım" der. Arayüz bu bayrağı görünce uyarı bandı
gösterir. Çözüm: `ollama pull <embed_model>`.

### Admin — üretim ayarları (admin gerekli)

**`GET /api/admin/config`** → `{ "model": "...", "system_prompt": "...", "temperature": 0.7 }`

**`PUT /api/admin/config`** — kısmi güncelleme (verilmeyen alan değişmez)

```json
{ "temperature": 0.2 }                 // sadece temperature'ı değiştirir
{ "model": "qwen3:4b", "system_prompt": "..." }
```

`temperature` 0–2 dışıysa `422`.

**`GET /api/admin/models`** → `{ "models": [...] }` (Ollama'daki kurulu modeller)

### Admin — kullanıcılar (admin gerekli)

**`GET /api/admin/users`** → `{ "users": [{ "email": "...", "is_admin": 1, "role": "user", "created_at": ... }] }`

**`POST /api/admin/users`** — yeni kullanıcı

```json
{ "email": "ekip@ornek.com", "password": "1234", "is_admin": false, "role": "finans" }
// 201 { "ok": true }  |  400 { "detail": "bu e-posta zaten kayıtlı" }
```

**`PUT /api/admin/users/{email}/role`** — rol ata → `{ "ok": true }`
```json
{ "role": "finans" }
```

**`DELETE /api/admin/users/{email}`** — kullanıcı sil → `{ "ok": true }`
Kendini silemezsin → `400`.

### Admin — doküman kütüphanesi (admin gerekli)

**`GET /api/admin/docs`** → `{ "docs": [{ "name": "...", "size": 1234, "modified": ..., "roles": [] }] }`

**`POST /api/admin/docs`** — belge yükle (`multipart/form-data`, alan adı `file`)
İzinli türler: `.txt .md .markdown .pdf .docx`, en fazla 25 MB.
Dosya adı kuralları: Türkçe harfler **korunur** (`Çalışan Rehberi.pdf` bozulmaz);
yol ayracı içeren ad **reddedilir** (tarayıcı yol göndermez, ayraç varsa kasıt
vardır); Windows'ta ayrılmış adlar (`CON`, `LPT1`…) ve nokta ile başlayanlar
reddedilir; `< > " | * ?` gibi karakterler `_` ile değiştirilir.
→ `201 { "ok": true, "name": "...", "reindex_required": true }`

**`DELETE /api/admin/docs/{name}`** — belge sil → `{ "ok": true, "reindex_required": true }`

**`PUT /api/admin/docs/{name}/roles`** — erişim kuralı
```json
{ "roles": ["finans", "ik"] }   // boş liste = herkese açık
```
Kurallar `data/docs_acl.json` dosyasında tutulur. Adı listede olmayan belge
herkese açıktır; **admin her belgeyi görür.** Yetkisiz belgelerden gelen
parçalar retrieval'dan *sonra* bağlamdan çıkarılır.

**`POST /api/admin/reindex`** — belgeleri yeniden indeksle → `{ "ok": true, "rag": {...} }`
Embedding cache parça bazlı olduğu için yalnızca yeni/değişmiş parçalar
yeniden embed'lenir. Yükleme/silme sonrası çağırın.

### Sohbet geçmişi ve geri bildirim (giriş gerekli)

**`GET /api/conversations`** → `{ "conversations": [{ "id": 1, "title": "...", "created_at": ..., "message_count": 4 }] }`

**`GET /api/conversations/{id}`** → `{ "id": 1, "messages": [{ "id": 2, "role": "assistant", "content": "...", "sources": [...], "vote": -1 }] }`
Başkasının konuşması → `404`.

**`DELETE /api/conversations/{id}`** → `{ "ok": true }`

**`POST /api/messages/{id}/vote`** — cevabı oyla
```json
{ "vote": 1 }    // 1 beğeni, -1 beğenmeme, 0 geri al
```
Yalnızca kendi konuşmandaki *asistan* mesajına oy verilebilir.

**`GET /api/admin/feedback`** → `{ "down": [...], "up": [...] }` (admin gerekli)
Oy almış soru/cevap çiftleri — altın sete aday toplamak için.

---

## 11. WebSocket protokolü

**Adres:** `ws://<host>:8000/ws/chat` (HTTPS arkasında `wss://`) — çerez (oturum)
otomatik gönderilir. Giriş yoksa bağlantı `1008` koduyla kapatılır.

**İstemci → sunucu** (JSON):

```json
{ "message": "merhaba" }
{ "message": "peki devri?", "conversation_id": 7 }   // geçmişten devam et
```

**Sunucu → istemci** (JSON, akış):

```json
{ "type": "conversation", "id": 7 }      // ilk mesajda: konuşma kimliği
{ "type": "token", "content": "mer" }    // her parça için tekrar tekrar
{ "type": "token", "content": "haba" }
{ "type": "done", "message_id": 12, "sources": [
    { "source": "izin.md", "page": null, "snippet": "Yıllık izin 20 iş günüdür." }
] }
{ "type": "error", "content": "..." }    // model hatası (bağlantı açık kalır)
```

- Boş/whitespace mesaj yok sayılır.
- Aynı bağlantıda ardışık mesajlar gönderilebilir; geçmiş korunur.
- `error` sonrası bağlantı kapanmaz, yeni mesaj gönderebilirsiniz.
- Her mesaj çifti SQLite'a yazılır; `message_id` oylamada kullanılır.
- **Takip soruları:** geçmiş varsa ve mesaj kısaysa (< 8 kelime) arama sorgusu
  önce LLM ile bağımsız hâle getirilir ("peki devri?" → "yıllık izin devri ne
  zamana kadar kullanılmalıdır"). Modele giden mesaj değişmez, yalnızca
  retrieval bu sorguyla yapılır. Hata olursa sessizce orijinal mesaja döner.
- Modele gönderilen geçmiş son 10 turla sınırlıdır (bağlam taşmasını önler).
- Guardrail devreye girerse cevap tek `token` + `done` olarak gelir; model
  hiç çağrılmadığı için akış anlıktır.
- **Kaynaklar ayrı bir alandır**, cevap metnine karıştırılmaz: `done` mesajının
  `sources` alanında `{source, page, snippet}` nesneleri olarak gelir ve
  veritabanına da bu biçimde yazılır (geçmiş yeniden yüklendiğinde korunur).

**Minimal istemci örneği:**

```js
const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
const ws = new WebSocket(`${proto}//${location.host}/ws/chat`);
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.type === "token") process.stdout.write(m.content);
};
ws.onopen = () => ws.send(JSON.stringify({ message: "selam" }));
```

---

## 12. Frontend

Bilinçli olarak **yüzeysel** — düz HTML/JS, çerçeve yok, `static/` altında iki dosya.

- **`index.html`** — açılışta `/api/me` ile giriş kontrolü yapar; yoksa login
  formu, varsa chat gösterir. Chat WebSocket ile token token yazar. Model
  çıktısı ekrana basılmadan önce `escapeHtml()`'den geçirilir (yalnızca `**kalın**`
  işaretlemesi HTML'e çevrilir), böylece belgeden ya da modelden gelen HTML
  çalıştırılamaz.
- **`admin.html`** — giriş + admin kontrolü; ayar formu ve kullanıcı tablosu.

Frontend ekibi backend'e yalnızca yukarıdaki **API** ve **WebSocket**
sözleşmesiyle konuşur; `static/` içeriğini tamamen değiştirebilirler.

---

## 13. Retriever'ı değiştirmek / genişletmek

Backend retrieval'dan habersizdir: `main.py` yalnızca `get_retriever()` çağırır
ve dönen nesnenin `retrieve(query) -> list[str]` metodunu kullanır. Farklı bir
retrieval yaklaşımı (harici vektör DB, Elasticsearch, hazır bir RAG servisi)
denemek istiyorsanız tek yapmanız gereken `rag.py` içinde yeni bir sınıf yazıp
`get_retriever()`'dan onu döndürmektir — `main.py`'a dokunmanız gerekmez.

```python
class MyRetriever:
    def __init__(self):
        # ağır kaynaklar (model, indeks) BURADA bir kez yüklenir
        self.store = load_my_vector_store()

    async def retrieve(self, query: str) -> list[str]:
        hits = self.store.search(query, k=4)
        # kaynak atıfının çalışması için etiketi koruyun:
        return [f"[kaynak: {h.filename}]\n{h.text}" for h in hits]

def get_retriever():
    return MyRetriever()
```

**Uyulması gerekenler**

- `retrieve` **async** olmalı. Ağır/bloklayan işiniz varsa
  `await asyncio.to_thread(...)` ile ana döngüyü kilitlemeyin.
- Ağır kaynakları `get_retriever()` / `__init__` içinde **bir kez** yükleyin;
  bu fonksiyon açılışta bir kez çağrılır ve sonuç yeniden kullanılır.
- Parçaların başındaki `[kaynak: dosya]` etiketini koruyun — kaynak atıfı ve
  `eval/` scriptlerinin tamamı bu kalıba bağlıdır.
- Guardrail'in çalışması için, alakasız sorularda **boş liste** döndürün.
  `guard_reply()` boş listeyi görüp modeli hiç çağırmadan reddeder.
- Değişiklikten **önce ve sonra** `python eval/evaluate.py` çalıştırıp skorları
  karşılaştırın.

Bağlamın prompt'a nasıl girdiğini değiştirmek isterseniz tek nokta
`rag.py` → `build_rag_messages()`'tır.

---

## 14. Güvenlik / production kontrol listesi

- [ ] `SECRET_KEY`'i güçlü ve gizli bir değere ayarla (ör. `openssl rand -hex 32`).
- [ ] `ADMIN_PASSWORD`'ü değiştir; ilk girişten sonra yeni admin oluşturup
      varsayılanı sil.
- [ ] `.env`, `data/users.db` ve `data/.rag_cache/` **git'e girmesin**
      (`.gitignore`'da tanımlı; daha önce commit edildiyse
      `git rm -r --cached ...` ile takipten çıkar).
- [ ] HTTPS arkasında çalıştır; çereze `Secure` bayrağını ekle (ters proxy veya
      `set_cookie(..., secure=True)`). Frontend `wss://`'e kendiliğinden geçer.
- [ ] `data/users.db`'yi yedekle / kalıcı sakla (Docker'da `userdata` volume ile hazır).
- [ ] Ollama portunu (11434) dışarı açma; yalnızca backend erişsin.
- [ ] **`.env` bu depoda commitli.** Bilinçli bir ekip kararı (private depo, ortak
      yapılandırma) — ama içinde `SECRET_KEY` ve `ADMIN_PASSWORD` var. Depo public
      yapılırsa dosyayı silmek **yetmez**: ikisi de döndürülmeli, çünkü eski
      değerler git geçmişinde kalır. Depoyu kopyalayan herkes bu sırları da alır.
- [ ] Gizli belgeler için rol ata (`PUT /api/admin/docs/{name}/roles`) ve
      kullanıcılara rol ver; aksi hâlde her belge, sisteme erişimi olan herkese açıktır.
- [ ] Giriş deneme sınırı süreç-içi bellektedir; çok işçili (worker) dağıtımda
      Redis gibi paylaşımlı bir sayaca taşıyın.

> Not: Çerez tabanlı oturum kullanıldığından, tarayıcı-dışı state değiştiren
> istekler için CSRF önlemi (SameSite=Lax varsayılan olarak var) yeterli kabul
> edilmiştir. Farklı origin'den erişim gerekiyorsa CORS/CSRF politikasını
> gözden geçirin.

---

## 15. Sık karşılaşılan sorunlar

| Belirti | Sebep / çözüm |
|---|---|
| Chat'te `[hata] Ollama ...` | Ollama kapalı veya model yok. `/api/health`'e bak; `ollama serve` + `ollama pull <model>`. |
| Her soruya "belgelerimde bilgi bulamadim" diyor | Embedding modeli inmemiş olabilir (`ollama pull bge-m3`) ya da eşikler korpusunuz için yüksek. `python test_rag.py` ile parça dönüyor mu bak; dönmüyorsa `eval/calibrate_hybrid.py` ile eşikleri kendi korpusunuza göre yeniden kalibre edin. |
| Açılış çok uzun sürüyor | İlk indeksleme tüm korpusu embed ediyor. Bir sonraki açılış `data/.rag_cache` sayesinde hızlıdır — bu klasörü silmeyin. |
| Belgeyi değiştirdim ama cevap eski | Önbellek parça metnine göre anahtarlanır; değişen parçalar otomatik yeniden embed edilir. Yine de takıldıysa `data/.rag_cache` klasörünü silip yeniden başlatın. |
| `RAG_MODE=rerank` uyarı basıyor | `sentence-transformers` kurulu değil (bilinçli olarak `requirements.txt`'te yok). `pip install sentence-transformers` ya da `hybrid` modunda kalın. |
| `Errno 10048 ... bind on address 8000` | Port dolu (eski sunucu). Eski süreci kapat ya da `PORT` değiştir. |
| Giriş yapılamıyor, şifre doğru | `SECRET_KEY` değişmiş olabilir (eski çerez geçersiz) — tekrar giriş yap. |
| İlk admin bilgisini bilmiyorum | Sunucu ilk açılışta konsola basar; ya da `data/users.db`'yi silip yeniden başlat. |
| Admin panel "yetki gerekli" diyor | Giriş yapan kullanıcı admin değil. Admin bir kullanıcıyla gir. |
| Docker'da model her seferinde iniyor | `ollama` volume'ü kalıcı; ilk sefer haricinde inmez. Volume'ü silmediğinden emin ol. |
| **Her soruya "bilgi bulamadım" diyor** | Embedding modeli eksik → hybrid sessizce BM25'e düşmüştür. `/api/health` → `rag.degraded` bak; `ollama pull bge-m3`. Arayüzde sarı uyarı bandı da çıkar. |
| Yüklediğim belge bulunamıyor | İndeks başlangıçta kurulur. Dokümanlar sekmesinde **Yeniden indeksle**'ye bas. |
| Kullanıcı bir belgeyi göremiyor | `data/docs_acl.json`'da o belgeye rol atanmış ve kullanıcının rolü listede değil. Admin her belgeyi görür. |
| Cevapta `<think>...</think>` görünüyor | Düşünen model (qwen3) + eski istemci. `app/ollama.py` isteklere `think: false` gönderir; sunucuyu yeniden başlatın. |
| Arayüz eski görünüyor | Tarayıcı `index.html`/`admin.html`'i önbelleğe almıştır. Sert yenile (Ctrl+F5). |
| HTTPS'te chat bağlanmıyor | İki olası sebep: (1) ters proxy WebSocket upgrade'ini geçirmiyor (`Upgrade` / `Connection` başlıkları), (2) sayfa `https://` ama istemci `ws://` deniyor — istemci artık protokolü sayfadan türetiyor, eski bir sürüm önbellekteyse Ctrl+F5. |
