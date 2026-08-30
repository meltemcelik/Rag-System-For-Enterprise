# Değişiklik Raporu — Test Sonuçlarına Dayalı İnceleme

**Dal:** `feature/rag-enterprise-features` · **Taban:** `cee4398` (24 Temmuz) · **Son:** `45e9210`
**Ölçüm ortamı:** tek makine, yerel Ollama, `qwen3:8b` sohbet modeli, `bge-m3` embedding
modeli, 163 indekslenmiş parça, hibrit arama modu.

Bu rapor, ekibin son commit'i ile mevcut dalın son hali arasındaki farkı ölçüm
sonuçlarıyla belgeler. Her iddia çalıştırılmış bir teste dayanır; doğrulanamayan
hiçbir şey "başarılı" sayılmamıştır.

| | |
|---|---|
| Değişen dosya | 29 |
| Eklenen satır | 2 896 |
| Silinen satır | 93 |
| Otomatik test | 227 |
| Başarısız | 0 |
| Düzeltilen hata | 15 |

---

## 1. Değişikliğin dağılımı

Eklenen satırların yaklaşık yarısı üretim kodu değil, test ve ölçüm altyapısıdır.
İncelemede gözden kaçmaması gereken bir orandır.

| Kategori | Dosyalar | Değişen satır | Pay |
|---|---|---:|---:|
| Test ve ölçüm | `test_features.py` · `test_api.py` · `eval/run.py` · `eval/multiturn_set.jsonl` | 1 296 | %43 |
| Uygulama kodu | `auth` · `main` · `docs` · `store` · `query` · `rag` · `db` · `ollama` · `schemas` | 996 | %33 |
| Arayüz | `static/index.html` · `static/admin.html` | 495 | %17 |
| Dokümantasyon | `DOCS.md` · `README.md` | 179 | %6 |
| Yapılandırma | `.env` · `.env.example` · `.gitignore` · `requirements.txt` | 23 | %1 |

**Yeni modüller:** `app/store.py` (sohbet geçmişi), `app/docs.py` (doküman kütüphanesi
ve erişim kuralları), `app/query.py` (takip sorusu dönüşümü), `app/db.py` (paylaşılan
bağlantı).

**Depodan çıkarılanlar:** commitli `__pycache__/*.pyc` dosyaları ve `data/users.db`
(çalışma zamanı verisi).

---

## 2. Test sonuçları

| Paket | Kapsam | Kontrol | Başarısız | Depoda |
|---|---|---:|---:|---|
| `test_features.py` | Atıf ayrıştırma, rol yetkisi, Türkçe/Unicode, 400 turluk rastgele girdi, eşzamanlı yazma, veritabanı sürüm geçişi, oturum iptali | 146 | 0 | evet |
| `test_api.py` | Yetkilendirme (12 admin ucu), sahte/süresi geçmiş çerezler, kullanıcı izolasyonu, yükleme sınırları, sohbet akışı, regresyonlar | 81 | 0 | evet |
| Köşe durumu taraması | Çıkış sonrası çerez, silinen konuşmaya yazma, 50 KB mesaj, yalnız durak kelime, eşzamanlı iki oturum, 12 turluk sohbet | 10 | 0 | **hayır** |
| Kullanım senaryoları | Uçtan uca kullanıcı yolculukları (giriş → soru → geçmiş → belge → rol) | 17 | 0 | **hayır** |

> **Sınır:** Köşe durumu ve senaryo betikleri çalıştırıldı ve sonuçları bu rapordadır,
> ancak depoya eklenmedi. Sürekli entegrasyonda yalnızca **227** otomatik test koşar.
> Bu iki betiğin de depoya alınması önerilir.

Çalıştırma:

```bash
python test_features.py            # sunucu ve Ollama gerekmez
python test_api.py                 # çalışan sunucu ister
python eval/run.py --multiturn --verbose
```

---

## 3. Arama kalitesi — değişmedi

Ekibin retrieval motoruna (BM25 + embedding hibrit, eşikler, RRF birleştirme) kasıtlı
olarak dokunulmadı. Aynı ölçer kodu iki sürümde de çalıştırıldı.

| Ölçüt (82 soruluk altın set) | Önce | Sonra | Durum |
|---|---:|---:|---|
| Kaynak isabeti | 0,9839 | 0,9839 | değişmedi |
| Red doğruluğu | 0,9512 | 0,9512 | değişmedi |
| Yanlış red | 1 | 1 | değişmedi |
| Kaçan red | 3 | 3 | değişmedi |
| Birleşik skor | 0,9676 | 0,9676 | değişmedi |

Bu tablo bir başarısızlık değil, **hedeflenen sonuçtur**: 2 896 satırlık değişiklik
ekibin en kritik varlığında sıfır regresyon bıraktı.

### Takip sorularında ölçülen kazanç

Retrieval seviyesinde ölçüm bu iyileşmeyi göremez, çünkü dönüşüm sohbet katmanında
yapılır. 10 vakalık ayrı bir set bunun için eklendi (`eval/multiturn_set.jsonl`).

| Ölçüm | Değer |
|---|---:|
| Ham takip sorusuyla kaynak isabeti | 0,2 |
| Dönüştürülmüş sorguyla isabet | **0,8** |
| Dönüşümün bozduğu vaka | 0 |

---

## 4. Kullanım senaryoları — önce / sonra

Her iki sürüm aynı makinede, aynı model ve aynı belgelerle ayağa kaldırıldı; birebir
aynı betik uygulandı.

| Senaryo | Önce (`cee4398`) | Sonra |
|---|---|---|
| Giriş | çalışıyor | çalışıyor |
| Temel soru | doğru — *kaynak yok* | doğru + kaynak atfı |
| **Takip sorusu** | **başarısız** — "bilgi bulamadım" | doğru cevap + kaynak |
| Alakasız soru reddi | çalışıyor | çalışıyor |
| Türkçe karakterli soru | çalışıyor | çalışıyor |
| Sohbet geçmişi | yok (404) | çalışıyor |
| Konuşmaya devam etme | yok | çalışıyor |
| Cevabı oylama | yok | çalışıyor |
| Belge yükleme | yok (404) | çalışıyor |
| Yeniden indeksleme | yok | 2,6 sn |
| Yeni belgeye soru sorma | yok | çalışıyor |
| Türkçe dosya adı | yok | bozulmadan korunuyor |
| Rol bazlı belge yetkisi | yok | sızıntı yok |
| Şifre değiştirme | yok | çalışıyor |
| **Kaba kuvvet koruması** | **yok** | 429 ile kilitliyor |
| RAG durum görünürlüğü | yok | çalışıyor |
| Eşzamanlı 3 soru | çalışıyor | çalışıyor |

**Önce:** 5 çalışıyor · 11 özellik yok · 1 başarısız — **Sonra:** 17 / 17

---

## 5. Performans

Tek makine, CPU üzerinde, her ölçüm 5 tekrar — medyan değerler. Isınma turu ölçüme
dahil edilmemiştir.

| Ölçüm | Önce | Sonra | Fark |
|---|---:|---:|---:|
| Cevaplanan soru — ilk token | 8,76 sn | 4,01 sn | **−%54** |
| Cevaplanan soru — toplam | 9,22 sn | 4,42 sn | **−%52** |
| `/api/health` | 536 ms | 483 ms | ≈ eşit |
| `/api/me` | 4,4 ms | 5,1 ms | ≈ eşit |
| Reddedilen soru (saf retrieval) | 2,89 sn | 3,02 sn | +%4 |
| Takip sorusu — toplam | 2,92 sn | 6,68 sn | +%129 |

**%54'lük hızlanmanın sebebi bir satırlık düzeltmedir.** qwen3 düşünen bir modeldir;
istekte `think: false` gönderilmediği için cevap öncesi görünmeyen bir düşünme bloğu
üretiyor, kullanıcı o süre boyunca boş ekran görüyordu.

**%129 artış elma-armut kıyasıdır.** Önceki sürüm takip sorusuna 2,92 saniyede
*"bilgi bulamadım"* diyor; yeni sürüm 6,68 saniyede *doğru cevabı üretiyor*. Yani bu,
"başarısız olma süresi" ile "başarma süresi" karşılaştırmasıdır. Dönüşümün kendi
maliyeti ayrıca ölçüldü: **medyan 0,8 saniye**.

---

## 6. Düzeltilen hatalar

Kasıtlı bir hata avı yapıldı: her hipotez için üretilebilir kanıt arandı, kanıtlanamayan
hiçbir şey hata olarak raporlanmadı. **Köken** sütunu, hatanın bu çalışmada mı eklendiğini
yoksa önceden mi var olduğunu gösterir.

| Hata | Ciddiyet | Köken | Kanıt |
|---|---|---|---|
| Oturum imzalama anahtarı public varsayılan değerde — şifre bilmeden admin oturumu üretilebiliyordu | kritik | önceden | Sahte token üretilip eski doğrulama mantığınca kabul edildiği gösterildi |
| Mesaj yanlış konuşmaya kaydediliyor — ekranda doğru yerde görünüp yenilemede kayboluyor | yüksek | bu çalışma | Tarayıcıda üretildi: ekranda #18, veritabanında #22 |
| Çıkış yapmak oturumu sonlandırmıyor — kopyalanmış çerez 24 saat geçerli kalıyor | yüksek | önceden | Çıkıştan sonra eski çerezle `/api/me` → HTTP 200 |
| Embedding modeli eksikken sistem sessizce yalnız anahtar kelimeye düşüyor | orta | önceden | Üretimde yaşandı: her soruya "bilgi bulamadım", hata yok |
| Türkçe dosya adları bozuluyor | orta | bu çalışma | `Çalışan Rehberi.pdf` → `_al__an Rehberi.pdf` |
| Alt klasördeki belgeye rol atanamıyor (Windows yol ayracı) | orta | bu çalışma | HTTP'den `alt/belge.md`, listede `alt\belge.md` → 404 |
| Silinen belgenin erişim kuralı geride kalıyor | orta | bu çalışma | Silme sonrası ACL kaydı hâlâ mevcut |
| Düşünen modelin ham düşünme metni cevaba sızıyor | orta | önceden | qwen3 varsayılan model yapıldığında ortaya çıktı |
| Aynı adla yükleme mevcut belgeyi uyarısız siliyor | orta | bu çalışma | İkinci yükleme sonrası içerik değişti, uyarı yok |
| Sağlık ucu iki katına yavaşladı — Ollama'ya gereksiz ikinci çağrı | orta | bu çalışma | 2,8 sn → 5,0 sn; düzeltmeden sonra 434 ms |
| Sayısal olmayan konuşma kimliği WebSocket'i düşürüyor | orta | bu çalışma | Sunucu günlüğünde `ValueError` yığın izi |
| Giriş deneme sayacı sınırsız bellek büyütüyor | orta | bu çalışma | 5 000 farklı e-posta → 5 000 kalıcı kayıt |
| 50'den eski konuşmalar arayüzden ulaşılamıyor | düşük | bu çalışma | 55 konuşma oluşturuldu, en eskisi listede yok |
| Belge silmek gereksiz boş kural dosyası yaratıyor | düşük | bu çalışma | Silme öncesi dosya yok, sonrası var |
| Desteklenmeyen türdeki belge sessizce yok sayılıyor | düşük | önceden | Klasördeki `.html` raporu ne indeksleniyor ne listeleniyor |

Her hata için regresyon testi eklendi. Bir düzeltme (bellek sayacı) ilk denemede yetersiz
kaldı ve kendi regresyon testi tarafından yakalandı; ikinci denemede sert üst sınır uygulandı.

---

## 7. Çürütülen hipotezler

Raporun objektifliği açısından bunlar da kayda değer: incelendi, kanıt bulunamadı,
**hiçbir değişiklik yapılmadı**.

- Belge içeriğindeki `<script>` etiketi arayüzde çalışmıyor — DOM'a hiç girmiyor
- Yükleme sırasında depo dışına dosya yazılamıyor
- Rol kısıtlaması anında uygulanıyor, gecikme yok
- Parça metni içindeki sahte `[kaynak:]` başlığı ayrıştırmayı yanıltmıyor
- Silinmiş konuşmaya yazma bağlantıyı düşürmüyor
- Aynı kullanıcıdan eşzamanlı iki oturum birbirine karışmıyor
- 50 KB'lık mesaj, yalnız durak kelimeden oluşan sorgu ve 12 turluk sohbet sorunsuz
- Büyük harfli rol adları doğru eşleşiyor

---

## 8. Bilinen sınırlar

- **Rol süzmesi retrieval sonrası yapılıyor.** Yetkisiz parçalar sonuç sayısından yer
  yiyebilir. Mevcut eşiklerle bu durumun oluşmadığı ölçüldü (5 sorgunun hiçbirinde kayıp
  yok), ancak eşikler gevşetilirse ortaya çıkabilir.
- **Giriş deneme sınırı süreç içi bellektedir.** Çok işçili dağıtımda paylaşımlı bir
  sayaca taşınmalıdır.
- **Takip sorusu dönüşümü kaba bir eşiğe dayanır** (8 kelimeden kısa). Zaten bağımsız olan
  kısa sorularda gereksiz bir LLM turu oluşur; ölçülen net etkisi sıfır, maliyeti ~0,8 saniye.
- **Performans ölçümleri tek makinededir** ve CPU üzerinde çalışan yerel modele bağlıdır;
  mutlak değerler donanıma göre değişir, oranlar anlamlıdır.
- **`.env` depoya dahil edilmiştir.** Depo private olduğu ve yalnızca geliştirici ekip
  eriştiği için bilinçli tercih edilmiştir. Depo ileride public yapılırsa dosyayı silmek
  yetmez — `SECRET_KEY` ve `ADMIN_PASSWORD` git geçmişinde kalacağı için ikisi de
  döndürülmelidir.

---

## 9. İnceleme için öneri

Değişikliği inceleyecek ekip arkadaşları için öncelik sırası:

1. `app/main.py` — WebSocket akışındaki konuşma seçimi mantığı (en ciddi hatanın çıktığı yer)
2. `app/auth.py` — oturum iptali: tekil (çıkış) ve toplu (şifre değişimi) ayrımı
3. `app/docs.py` — dosya adı temizleme kuralları ve erişim kurallarının uygulanma noktası
4. `app/query.py` — takip sorusu dönüşümünün yalnızca aramayı etkilediği, modele giden
   mesajı değiştirmediği
5. `eval/run.py` — `--fail-under` eşiğinin sürekli entegrasyona bağlanması

> **Dokunulmayan alan:** `app/rag.py` içindeki retrieval mantığı (BM25, embedding, eşikler,
> RRF) değiştirilmedi. Bu dosyaya yalnızca kaynak ayrıştırma ve durum bildirme fonksiyonları
> eklendi; ölçüm bunu doğruluyor.
