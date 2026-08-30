# Kurumsal RAG Asistanı

Şirket belgeleri üzerinde çalışan, **kaynak-atıflı** ve halüsinasyona karşı
**guardrail'li** kurumsal bilgi asistanı. Ollama ile yerel LLM çalıştırır,
WebSocket ile token-token streaming yapar.

**Retrieval:** Hibrit — BM25 (kelime) + bge-m3 (anlam) embedding, Reciprocal Rank
Fusion ile birleştirilir. Bağlamda cevap yoksa model **hiç çağrılmadan** reddedilir;
her cevabın altında yararlanılan kaynak belge gösterilir.

**Öne çıkanlar:** kaynak atıfları · sohbet geçmişi · 👍/👎 geri bildirim ·
arayüzden doküman yükleme · rol bazlı belge yetkisi · takip sorusu anlama ·
oturum iptali · kaba kuvvet koruması · altın set regresyon kapısı.

> **Tam dokümantasyon:** [DOCS.md](DOCS.md) — mimari, istek akışı, API referansı,
> WebSocket protokolü ve RAG'ın nasıl özelleştirileceği burada.
>
> **Değişiklik setinin ölçüm raporu:** [DEGISIKLIK-RAPORU.md](DEGISIKLIK-RAPORU.md)
>
> **Birleştirme ve final ölçümleri:** [BIRLESTIRME-RAPORU.md](BIRLESTIRME-RAPORU.md)

## Ölçüm sonuçları

102 soruluk altın set (76 cevaplanabilir + 26 "belgede yok" tuzağı) üzerinde,
üretim konfigürasyonuyla ölçüldü: hibrit mod, chunk 400/80, eşikler ≥ 6,80 (BM25)
ve ≥ 0,535 (kosinüs), guardrail açık, `qwen3:8b` + `bge-m3`.

| Metrik | Sonuç | Ne ölçer |
|---|---|---|
| Regresyon kapısı (birleşik skor) | **0,9493** | `eval/run.py` — CI'ı kıran tek sayı |
| Kaynak isabeti | **%98,7** | doğru belge ilk 4 sonuçta |
| Red doğruluğu | **%91,2** | "belgede yok" tuzaklarını reddetme |
| Kaynak atıfı (doğru gösterildi / en üstte) | **%100 / %98,7** | kullanıcı doğru belgeye yönlendiriliyor |
| Uçtan uca cevap doğruluğu | **%75,0** | beklenen tüm bilgiler cevapta |
| Sadakat (halüsinasyon yok) | **%84,0** | cevap yalnızca bağlamdan besleniyor |
| Dayanıklılık (kenar durum) | **11/13** | bozuk / adversarial girdiler |
| Retrieval gecikmesi | **726 ms → 17 ms** | sorgu önbelleği, 41,7x |
| Otomatik test | **239** | 0 başarısız |

Ölçüm altyapısı `eval/`, raporlardaki iddiaları üreten betikler `tools/` altında.
**Her değişiklik önce/sonra ölçülerek yapıldı**; ölçüp geri aldığımız üç değişiklik
de gerekçeleriyle [BIRLESTIRME-RAPORU.md](BIRLESTIRME-RAPORU.md) §8'de.

> Red doğruluğu, set 82'den 102'ye büyütüldüğünde %95'ten %91'e "düştü" — bu bir
> gerileme değil, küçük setin gizlediği gerçek zorluğun görünmesidir. Kalan
> tuzaklar, hiçbir ucuz sinyalin (eşik, rerank, LLM kapısı — üçü de ölçüldü)
> gerçek sınırdaki sorulardan temiz ayıramadığı, bilinçli kabul edilmiş bir sınırdır.

## Çalıştırma

### A) Docker (en kolay — Ollama dahil her şey)

```bash
docker compose up --build
```
Ollama'yı ayağa kaldırır, modeli indirir, backend'i başlatır → http://localhost:8000
Farklı model: `MODEL=qwen3:4b docker compose up --build`

### B) Yerel (tek komut, otomatik hazırlık)

```bash
python run.py
```
`run.py` sırasıyla: eksik bağımlılıkları kurar → Ollama'yı başlatır → modeli
indirir → sunucuyu açar. Tekrar çalıştırmak güvenli, tamamlanmış adımları atlar.
(Ollama'nın kurulu olması gerekir: https://ollama.com/download)

> Not: canlı reload isteyen geliştiriciler `uvicorn app.main:app --reload` kullanabilir.

## Giriş / Kullanıcılar

Chat ve admin paneli **giriş** gerektirir (e-posta + şifre). İlk çalıştırmada,
kullanıcı DB'si boşsa bir admin otomatik oluşturulur:

```
e-posta: admin@example.com
şifre  : admin
```

Bunu `ADMIN_EMAIL` / `ADMIN_PASSWORD` (veya `.env`) ile değiştir; production'da
`SECRET_KEY`'i de mutlaka değiştir — oturum çerezleri onunla imzalanır, varsayılan
değerde kalırsa herkes admin oturumu üretebilir. **`.env` commitlenmez.** Yeni kullanıcılar admin panelindeki
**Kullanıcılar** bölümünden eklenir/silinir. Kullanıcılar `data/users.db`
(SQLite) içinde saklanır — Docker'da `userdata` volume ile kalıcıdır.

## Sayfalar

- `/`        — chat (kaynak atıfları, geçmiş, doküman kütüphanesi sekmeleri)
- `/admin`   — model / prompt / temperature + kullanıcı & rol yönetimi (admin-only)

## Test ve ölçüm

```bash
python test_features.py            # 103 birim testi — sunucu/Ollama gerekmez
python test_api.py                 # 78 uçtan uca API + WebSocket testi (sunucu açıkken)
python test_api.py --hizli         # aynısı, LLM gerektiren sohbet testleri hariç
python test_rag.py                 # retriever'ı elle dene

python eval/run.py --verbose           # altın set: kaynak isabeti + red doğruluğu
python eval/run.py --multiturn         # takip sorularını da ölç (sohbet hattı)
python eval/run.py --fail-under 0.90   # CI kapısı (skor düşerse çıkış kodu 1)
```

`test_features.py` saf mantığı test eder: kaynak ayrıştırma, rol yetkisi, Türkçe
karakterler, rastgele girdiler (fuzz), eş zamanlı yazma, veritabanı sürüm geçişi.
`test_api.py` gerçek HTTP/WebSocket üzerinden gider: yetkilendirme, kullanıcı
izolasyonu, sahte oturum çerezleri, yükleme sınırları.

## API

| Yöntem | Yol                  | Yetki  | Açıklama                          |
|--------|----------------------|--------|-----------------------------------|
| POST   | `/api/login`         | —      | `{email, password}` → oturum çerezi |
| POST   | `/api/logout`        | —      | Çerezi siler                      |
| GET    | `/api/me`            | giriş  | Aktif kullanıcı (e-posta, admin, rol) |
| POST   | `/api/password`      | giriş  | Kendi şifreni değiştir            |
| WS     | `/ws/chat`           | giriş  | `{"message": "..."}`; `token` akışı, sonra `done` (+ `message_id`, `sources`) |
| GET    | `/api/health`        | —      | Ollama + RAG durumu (`rag.degraded` uyarısı) |
| GET    | `/api/conversations` | giriş  | Sohbet geçmişi                    |
| GET/DELETE | `/api/conversations/{id}` | giriş | Konuşmayı aç / sil        |
| POST   | `/api/messages/{id}/vote` | giriş | Cevabı oyla (`1` / `-1` / `0`) |
| GET    | `/api/admin/config`  | admin  | Aktif üretim ayarları             |
| PUT    | `/api/admin/config`  | admin  | Ayarları güncelle (kısmi)         |
| GET    | `/api/admin/models`  | admin  | Ollama'daki modeller              |
| GET    | `/api/admin/users`   | admin  | Kullanıcı listesi                 |
| POST   | `/api/admin/users`   | admin  | `{email, password, is_admin, role}` ekle |
| PUT    | `/api/admin/users/{email}/role` | admin | Rol ata                |
| DELETE | `/api/admin/users/{email}` | admin | Kullanıcı sil                |
| GET/POST | `/api/admin/docs`  | admin  | Belge listesi / yükleme           |
| DELETE | `/api/admin/docs/{name}` | admin | Belge sil                     |
| PUT    | `/api/admin/docs/{name}/roles` | admin | Belgeye rol kısıtı ata  |
| POST   | `/api/admin/reindex` | admin  | Belgeleri yeniden indeksle        |
| GET    | `/api/admin/feedback` | admin | Oy almış soru/cevap çiftleri      |

## Kendi belgelerinizle kullanmak

`data/docs/` içine kendi dosyalarınızı koyun (`.md`, `.txt`, `.pdf`, `.html`) ve
sunucuyu yeniden başlatın — indeksleme otomatik. İlk açılışta embedding'ler
hesaplanıp `data/.rag_cache/` altına yazılır; sonraki açılışlar hızlıdır.

**Önemli:** eşikler (`RAG_MIN_SCORE_KEYWORD` / `RAG_MIN_SCORE_EMBED`) bu
korpusa göre kalibre edildi. Kendi belgelerinizle `eval/` altındaki golden set'i
kendi sorularınızla değiştirip yeniden ölçün.

## Retriever'ı değiştirmek

Retrieval mantığının tamamı tek dosyada: [`app/rag.py`](app/rag.py). Hazır
modlar `RAG_MODE` ile seçilir (`keyword` / `embed` / `hybrid` / `rerank`).
Kendi retriever'ınızı yazmak isterseniz `retrieve(query) -> list[str]` metodu
olan bir sınıf yazıp `get_retriever()`'dan döndürmeniz yeterli; dönen parçalar
prompt'a otomatik enjekte edilir.

```python
class MyRetriever:
    async def retrieve(self, query: str) -> list[str]:
        # Her parçanin BASINDA kaynak etiketi olmali:
        #   "[kaynak: dosya.md] ...metin..."
        # Guardrail icin: ilgili parca yoksa BOS liste don, uydurma metin donme.
        return ["[kaynak: dosya.md] ilgili metin parçası"]

def get_retriever():
    return MyRetriever()
```

Değiştirmeden önce ve sonra `python eval/evaluate.py` çalıştırıp skoru
karşılaştırın — ayrıntılar için [DOCS.md](DOCS.md) §13.
