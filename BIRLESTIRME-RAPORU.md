# Birleştirme Raporu — İki Paralel Dalın Tek Sürüme İndirilmesi

**Tarih:** 9 Ağustos 2026 · **Taban:** `ded6cc4` · **Getirilen:** `b481b11` (Berk Ötün)
**Ortam:** tek makine, yerel Ollama, `qwen3:8b` sohbet + `bge-m3` embedding + `qwen3:4b`
yargıç, 163 indekslenmiş parça, hibrit mod, `RAG_STRICT=true`.

Proje kapanışında iki iş kolu birleştirildi. Bu rapor **ne alındığını, neyin
alınmadığını ve her kararın hangi ölçüme dayandığını** kaydeder. Ölçülmemiş hiçbir
şey "iyileştirme" olarak sunulmamıştır; hipotezi çürüten sonuçlar da buradadır.

---

## 1. Durum: iki dal neden ayrıştı

Her iki taraf da `cee4398`'den (24 Temmuz) dallandı ve birbirinden habersiz ilerledi.
Berk'in çalışması takım deposuna hiç girmemişti; kendi hesabında ayrı bir depoda
duruyordu. Bu birleştirmeyle `b481b11` kendi yazarlığıyla takım deposunun geçmişine
alındı.

| | Bu depo (8 commit) | Berk'in dalı (1 commit) |
|---|---|---|
| Odak | Uygulama katmanı | Ölçüm + dağıtım katmanı |
| İçerik | oturum iptali, doküman kütüphanesi + rol ACL, sohbet geçmişi, oylama, kaba kuvvet koruması, takip sorusu dönüşümü, 227 test | 5 eval betiği, Docker düzeltmeleri, 20 yeni altın set sorusu, `ws://` düzeltmesi, sorgu önbelleği |
| Çakışma | 6 dosya | — |

Çakışan altı dosyanın çözüm kuralları **merge'den önce** yazıldı; her satır o
kurallara göre çözüldü.

---

## 2. Yöntem

Bir değişikliğin etkisini görmek için aynı anda tek değişken değiştirildi. Merge
sırasında hem kod hem ölçüm seti değiştiği için ikisi ayrı ayrı izole edildi:

| Kod | Set | Skor | Kaynak isabeti | Red doğruluğu | Yanlış red | Kaçan red |
|---|---|---:|---:|---:|---:|---:|
| `ded6cc4` (önce) | 82 | 0,9676 | 0,9839 | 0,9512 | 1 | 3 |
| birleştirilmiş | 82 | **0,9676** | 0,9839 | 0,9512 | 1 | 3 |
| birleştirilmiş | 102 | 0,9493 | **0,9868** | 0,9118 | 1 | 8 |

**Birinci ve ikinci satır birebir aynı** → birleştirme kod regresyonu yaratmadı.
Üçüncü satırdaki düşüş tamamen setin zorlaşmasından kaynaklanıyor; kaynak isabeti
ise hafifçe *yükseldi*.

### Yeni tuzakların geçerlilik denetimi

Skor düşüşünün haksız olup olmadığını anlamak için Berk'in eklediği 6 "belgede yok"
tuzağı korpusta tek tek arandı:

| Tuzak | Korpusta karşılığı | Geçerli mi |
|---|---|---|
| Performans değerlendirmesi nasıl yapılıyor? | hiç geçmiyor | evet |
| Şirket telefonu veriliyor mu? | yalnızca "telefonla bildirim" (izin bildirimi) | evet |
| Ofis giriş/çıkış saatleri? | hiç geçmiyor | evet |
| Onaylanan masraf hangi gün yatar? | hiç geçmiyor | evet |
| Parolamı unuttum, nasıl sıfırlarım? | parola **kuralları** var (12 karakter, 90 gün), **sıfırlama prosedürü** yok | evet — en zoru |
| Yıllık izni saatlik kullanabilir miyim? | hiç geçmiyor | evet |

Altısı da meşru. 0,9493 haksız bir düşüş değil, küçük setin gizlediği gerçek zorluk.
Kaynak atıfı ölçümünün 75/75 doğru çıkması da Berk'in 14 cevaplanabilir sorusunun
etiketlerinin korpusla tutarlı olduğunu gösteriyor.

---

## 3. Alınanlar — her biri gerekçeli

### 3.1 `ws://` düzeltmesi (gerçek hata)

İstemci WebSocket protokolünü sabit `ws://` olarak kuruyordu. HTTPS arkasında
tarayıcı karışık içerik kuralı gereği bu bağlantıyı **bloklar** — sohbet hiç açılmaz.
Protokol artık sayfadan türetiliyor. Üç satır; dağıtımı doğrudan etkiliyor.

### 3.2 Sorgu embedding önbelleği (ölçülen kazanç)

Retrieval süresinin neredeyse tamamı sorgu embedding'i (Ollama çağrısı); kendi
skorlamamız (BM25 + kosinüs + RRF) birkaç milisaniye. 102 sorgu, iki geçiş:

| | Ortalama | Medyan | p95 |
|---|---:|---:|---:|
| Soğuk (ilk sorgu) | 726,3 ms | 726,0 ms | 740,8 ms |
| Sıcak (tekrar sorgu) | 17,4 ms | 17,4 ms | 18,3 ms |

**41,7x hızlanma**, sorgu başına ~709 ms tasarruf. Sık sorulan sorularda kullanıcı
bu süreyi artık beklemiyor. Önbellek 512 girişle sınırlı (LRU benzeri).

### 3.3 Docker (imaj çalışmıyordu)

Üç ayrı kusur vardı, üçü de dağıtımı bozuyordu:

- **İmajda hiç belge yoktu** — `COPY data ./data` eksikti.
- **Embedding modeli indirilmiyordu** — `model-pull` yalnızca sohbet modelini
  çekiyordu. Bu, ürettiği belirti nedeniyle en sinsi hataydı: sistem ayağa kalkar,
  hata vermez, ama hibrit arama sessizce yalnız BM25'e düşer ve çoğu soruya "bilgi
  bulamadım" denir. Artık `bge-m3` de çekiliyor.
- **Kalıcılık yanlıştı** — `users.db` ve embedding önbelleği için ayrı volume yoktu.
  `DB_PATH` ile ayrıştırıldı.

Buna ek olarak `.dockerignore` embedding önbelleğini dışlıyordu. Compose o yola boş
bir named volume bağlıyor ve Docker boş volume'u imajdaki içerikle tohumluyor —
önbellek imajda olmayınca konteyner ilk açılışta 163 parçayı baştan embed etmek
zorunda kalıyordu. Önbelleği commit'leme kararımız Docker tarafında işlevsizdi;
dışlama kaldırıldı.

`docker-compose.host.yml` eklendi: host'taki Ollama'yı kullanarak modelleri yeniden
indirmeden hızlı doğrulama.

### 3.4 Beş yeni ölçüm betiği

Bizim ölçümümüz yalnızca retrieval'a bakıyordu — "doğru belge geldi mi?". Bu betikler
bir adım öteye geçiyor ve **hiç ölçmediğimiz** boyutları ölçüyor: üretilen cevabın
doğruluğu, halüsinasyon, gösterilen kaynağın doğruluğu, gecikme, bozuk girdi
dayanıklılığı. Sonuçlar §5'te.

### 3.5 Prompt tek kaynağa toplandı (fikri alındı, kodu düzeltildi)

Berk'in `build_rag_messages` fikri doğruydu: üretim ve ölçüm **aynı** prompt'u
kullanmazsa ölçüm yalan söyler. Ama uygulaması iki şeyi düşürüyordu:

1. `cfg.system_prompt` yerine sabit metin gömüyordu → admin panelindeki system
   prompt alanı sessizce işlevsizleşirdi.
2. Geçmiş kırpmasını (`MAX_HISTORY_TURNS`) atıyordu → uzun sohbette bağlam şişerdi.

Fonksiyon her iki özelliği koruyacak şekilde yeniden yazıldı, `app/rag.py`'ye taşındı;
`app/main.py` ve `eval/*` artık gerçekten aynı fonksiyonu çağırıyor. İki regresyon
için birer test eklendi (biri system_prompt'un kullanıldığını, biri `main.py`'de
kopya bir prompt kurucusu kalmadığını doğrular).

---

## 4. Alınmayanlar — gerekçeleriyle

| Ne | Neden alınmadı |
|---|---|
| `sources_of` ile kaynağın cevap metnine gömülmesi | Bizim `parse_sources` yapısal `{source, page, snippet}` döndürüyor, ayrı `sources` alanında gidiyor ve veritabanına da öyle yazılıyor — geçmiş yeniden yüklendiğinde korunuyor. Metne gömmek veriyi mesajla karıştırır. Berk'in fonksiyonları ölçüm betikleri kullandığı için duruyor, ama artık **aynı düzenli ifadeyi** paylaşıyorlar (iki ayrı regex tekilleştirildi). |
| `.gitignore`'un `data/.rag_cache/` dizinini yok sayması | Önbelleği bilinçli commit'liyoruz: yeni kurulumda embedding modeli olmadan da indeks yüklensin diye. Gerekçe dosyada yazılı. |
| `.env`'in takipten çıkarılması | Ekip kararı; §6'da ayrıca ele alındı. |
| `tools/sifre_geri_al.py` | Doğrudan veritabanına parola hash'i yazıp zayıf parolayı geri koyuyordu. Geliştirme sırasında işe yaradı, depoda durması yanlış örnek olurdu. |

---

## 5. Ölçüm sonuçları (birleştirilmiş kod, 102 soruluk set)

| Ölçüm | Sonuç | Ne ölçer |
|---|---:|---|
| Regresyon kapısı (birleşik skor) | **0,9493** | `eval/run.py` — CI'ı kıran tek sayı |
| Kaynak isabeti | **%98,7** | doğru belge ilk 4 sonuçta |
| Red doğruluğu | **%91,2** | "belgede yok" tuzaklarını reddetme |
| Kaynak atıfı — gösterilen doğru | **%100** (75/75) | kullanıcı doğru belgeye yönlendiriliyor |
| Kaynak atıfı — ilk kaynak doğru | **%98,7** (74/75) | en üstte doğruyu görüyor |
| Uçtan uca cevap doğruluğu | **%75,0** (57/76) | beklenen tüm bilgiler cevapta |
| Ortalama anahtar kelime kapsamı | **%78,9** | — |
| Sadakat (halüsinasyon yok) | **%84,0** (63/75) | cevap yalnızca bağlamdan besleniyor |
| Dayanıklılık (kenar durum) | **11/13** (%84,6) | boş/bozuk/enjeksiyon girdiler |
| Çeşitlendirilmiş retrieval | **24/34** (%70,6) | 11 kategori, gerçek soru biçimleri |
| Takip sorusu isabeti | **0,77** (13 vaka) | ham sorguyla 0,31 |
| Kaynak kesinliği | %85,7 | gösterilen kaynakların kaçı ilgili |
| Retrieval gecikmesi | **726 ms → 17 ms** | sorgu önbelleği, 41,7x |
| Otomatik test | **158 + 81 = 239** | 0 başarısız |

> **Sadakat notu:** Berk %90,2 raporlamıştı, biz %84,0 ölçtük. Fark setten
> kaynaklanıyor: onun ölçümü 82 soruluk sürümde (61 cevaplanabilir), bizimki
> 102 soruluk sürümde (75 cevaplanabilir). Eklenen 14 cevaplanabilir sorunun
> tamamı yoğun istatistik içeren PDF raporundan ve 12 uydurmanın görünen
> kısmı da o sorularda yoğunlaşıyor. Aynı sette karşılaştırılmadıkça iki sayı
> yan yana konmamalıdır.

### Ölçüm betiklerinde düzeltilen kusurlar

Betikler Berk'in koduna göre yazılmıştı; bizim koda uyarlanırken üç gerçek kusur çıktı:

1. **`answer_eval.py` `think: false` göndermiyordu.** Üretim (`app/ollama.py`)
   gönderiyor — bu bizim daha önce ölçtüğümüz %54'lük hızlanmanın sebebiydi. Betik
   düzeltilmeden **üretimden farklı bir konfigürasyonu** ölçüyordu; ayrıca `qwen3`'ün
   `<think>` bloğu anahtar kelime eşleşmesine karışabiliyor ve çalışma 20 dakikayı
   aşıyordu. Yukarıdaki %75,0 düzeltmeden **sonra**, üretimle aynı kurulumda alındı.
2. **`citation_eval.py` Windows konsolunda çöküyordu** (cp1254'te emoji
   `UnicodeEncodeError`) ve çıktısındaki not bizde olmayan bir davranışı anlatıyordu.
3. **`eval/README.md` regresyon kapımızı hiç anmıyordu** — en önemli ölçüm aracımız
   belgede yoktu.

Düzeltmelerden sonra sayılar Berk'in raporladıklarıyla büyük ölçüde örtüştü
(cevap doğruluğu %73,7 → %75,0; kaynak atıfı %98,4 → %98,7; dayanıklılık 11/13 aynı).
Bu örtüşme betiklerin sağlam olduğunu, yalnızca uyarlanmaları gerektiğini gösteriyor.

---

## 6. Çeşitlendirilmiş retrieval testi ve bulunan kusurlar

Altın set tek bir birleşik skor verir; nerede ve neden yanlış olduğunu göstermez.
Bunun için 11 kategoride 34 sorgu (`tools/cesitli_test.py`) çalıştırıldı ve her
sorgu için **eşik uygulanmadan** ham BM25 ve ham kosinüs kaydedildi — böylece bir
red "hiç bulunamadı" mı yoksa "eşiğin biraz altında kaldı" mı ayrılabiliyor.

> **Ölçüm aletinin kendisi de denetlendi.** İlk turda `ranked()` eşiği içeride
> uyguladığı için geçen sorgulara bile 0,0000 kosinüs raporlanıyordu; ayrıca
> beklenen kaynak anahtarı `"guvenlik"` yazılmıştı ama dosya `bilgi_guvenligi.txt`
> — dört sonuç haksız yere "yanlış" işaretlenmişti. İkisi de düzeltildi.

### Kök neden dağılımı (12 başarısızlık)

| Kök neden | Adet | Kanıt |
|---|---:|---|
| Diakritik körlüğü (BM25) | 2 | `dogrulama` ≠ `doğrulama` → BM25 3,99 |
| Türkçe ek/gövde farkı | 3 | `parola`/`parolalar`, `masraf`/`masrafları` → **ortak token sıfır** |
| Semantik boşluk (eşanlam, tek kelime) | 3 | "Tatil hakkım" kosinüs 0,5089 |
| Eşiğin kıl payı üstünde sızıntı | 4 | 0,5391 – 0,5764 |

Yanlış redlerin en yüksek kosinüsü 0,5342, sızıntıların en düşüğü 0,5391 — yani
**mevcut eşik (0,535) tam aradaki boşlukta**. Eşik yanlış yerde değil; başarısızlıkların
çoğu eşik meselesi değil.

### Uygulanan düzeltme: diakritik katlaması

BM25 tokenizasyonu Türkçe diakritikleri katlıyor (`ı→i`, `ş→s`, `ğ→g`, `ü→u`,
`ö→o`, `ç→c`). Yalnızca arama indeksini etkiler; parça metni, dosya adı ve cevap
tam Türkçe kalır. Sorgu ve belge tarafında **aynı** normalleştirmenin uygulandığı
regresyon testiyle korunuyor.

| | Altın set (102) | Çeşitli test (34) |
|---|---:|---:|
| Önce | 0,9493 | 22/34 (%64,7) |
| Sonra | **0,9493** (nötr) | **24/34 (%70,6)** |

İzole etki: **2 sorgu düzeldi, 0 sorgu bozuldu.** BM25 3,99 → 11,73 ve 6,62 → 14,07.
Gerçek kullanımda da görüldü: "İş yemeği için günlük limit ne kadar?" önce
reddediliyordu, şimdi doğru cevabı kaynağıyla veriyor.

## 7. Gerçek hayat denemeleri

`tools/gercek_kullanim.py` — bir çalışanın gün içindeki akışı, 10 deneme.
Hepsi güncel kod üzerinde çalıştırıldı.

| Deneme | Sonuç |
|---|---|
| Takip soruları ("Peki devri ne olacak?", "Talebi kime iletiyorum?") | ✅ doğru cevap + doğru kaynak |
| Masraf soruları + takip ("Fatura olmadan olur mu?") | ✅ |
| Parola karakter sayısı | ✅ |
| **"Ne sıklıkla değiştirmem gerekiyor?"** | ❌ reddediyor — belgede cevap var (§8) |
| Belgede olmayan 3 soru | ✅ üçü de reddedildi |
| Bozuk yazım / diakritiksiz / büyük harf (3 soru) | ✅ üçü de doğru |
| Sohbet geçmişi — kaynaklar kalıcı mı | ✅ `{source, page, snippet}` korunuyor |
| Belge yükle → yeniden indeksle (2,4 sn) → hemen sor | ✅ |
| Rol bazlı yetki — yetkisiz kullanıcı | ✅ sızıntı yok |
| Temizlik (belge + kullanıcı silme) | ✅ |

## 8. Ölçülüp REDDEDİLEN üç değişiklik

Bu bölüm raporun objektifliği açısından en önemlisi: üç makul fikir denendi,
ölçüldü ve **veriye dayanarak geri alındı**.

**1. Türkçe gövdeleme (ön-ek kırpması).** Morfoloji kusurunu hedefliyordu.

| Kol | Altın set | Çeşitli test |
|---|---:|---:|
| yalnızca katlama | **0,9493** | 24/34 |
| + kırpma 6 | 0,9329 | 26/34 |
| + kırpma 5 | 0,9231 | — |

Serbest sorularda kazandırıyor ama regresyon kapısını düşürüyor: gevşek eşleşme
tuzakları içeri alıyor (kaçan red 8 → 10) ve fazladan aday RRF'te doğru parçayı
`top_k` dışına itiyor (kaynak isabeti 0,9868 → 0,9737). `RAG_STEM_LEN` ayarı kodda
duruyor, **varsayılan 0 (kapalı)**; farklı bir korpusta deneyecek olan yeniden ölçmeli.

**2. Takip sorusu dönüşümünde soru formu.** Few-shot örnekleri isim öbeği
öğretiyordu; tek bir vakada ölçülmüştü ki soru formu eşiği geçiyor
(`"parola degistirme suresi"` 0,4583 → red · `"Parola ne siklikla degistirilir?"`
0,5507 → geçti). Örnekler soru formuna çevrildi ve 13 vakalık takip setinde ölçüldü:

| Örnek biçimi | İsabet | Bozduğu vaka |
|---|---:|---:|
| isim öbeği (korundu) | **0,7692** | 0 |
| tam soru | 0,6923 | 1 |

Hedeflenen vakayı düzeltirken başka iki vakayı bozuyor. Geri alındı; gerekçe ve
ölçüm `app/query.py` içinde yazılı. Tek vakadan genelleme yapmanın neden yanlış
olduğunun somut örneği.

**3. Seçici answerability kapısı (`RAG_GROUNDCHECK_MIN_CONF`).** Karar kuralı
deneyden **önce** yazıldı: "C > A ise aç, değilse kapalı bırak."

| Kol | Ayar | Skor | Yanlış red | Kaçan red |
|---|---|---:|---:|---:|
| A (kontrol) | kapalı | **0,9493** | 1 | 8 |
| C | seçici, eşik 0,60 | 0,8426 | 12 | 4 |
| B | her sorguda sor | 0,8114 | 16 | 1 |

Seçicilik B'ye göre iyileştiriyor (0,8114 → 0,8426) ama A'nın çok altında kalıyor.
Kaçan redleri 8'den 4'e indirirken yanlış redleri 1'den 12'ye çıkarıyor. Kapalı
kalıyor. Bu sonuç 30 Temmuz'daki bağımsız ölçümle (0,968 → 0,838) tutarlı.

## 9. Bilinen sınırlar ve devredilen riskler

- **Red doğruluğu %91,2.** Kalan 8 kaçan reddin tamamı "konuyla ilgili ama belgede
  yok" tipinde. En zoru parola sıfırlama: belgede parola kuralları var, sıfırlama
  prosedürü yok — sözlük olarak çok yakın, anlamca yok. Bunları ucuz bir sinyalle
  (eşik, rerank, LLM kapısı) ayırmak mümkün olmadı; §8'deki üç deney de bunu doğruladı.
- **Türkçe morfoloji açık kalan tek kusur.** Çeşitli testin morfoloji kategorisi
  1/4. Somut örnek: "Parolam kaç karakter olmalı?" doğru cevaplanıyor ama takibi
  olan "Ne sıklıkla değiştirmem gerekiyor?" reddediliyor — oysa belgede "Parolalar
  90 günde bir değiştirilir" yazıyor. Sebebi ölçüldü: `parola`/`parolalar` ve
  `degistirme`/`değiştirilir` çiftlerinde **ortak token sıfır**, embedding tarafında
  da kosinüs 0,4362 (eşik 0,535). Çözümü gerçek bir Türkçe gövdeleyici (Zemberek
  vb.) ya da daha küçük parça boyutu; ikisi de eşiklerin yeniden kalibrasyonunu
  gerektirir, bu yüzden kapanış commit'ine alınmadı.
- **Kaynak kesinliği %85,7.** Gösterilen 21 kaynağın 3'ü alakasız (duyarlılık
  %100 — doğru kaynak her zaman listede). Sebep: `retrieve()` RRF ile daima
  `top_k=4` parça döndürüyor ve `parse_sources` hepsini kaynak sayıyor; eşiği kıl
  payı geçen parça da "kullanılmış" gibi görünüyor. Cevap üretilemediği hâlde
  kaynak gösterildiği bir durum da gözlendi (rol kısıtlı belge denemesi).
  Düzeltmek gösterilen kaynak sayısını kısmayı gerektirir; bu da %100'lük
  duyarlılığı riske atar — ölçülmeden yapılmamalı.
- **`.env` depoda commitli.** Private depo ve ortak yapılandırma gerekçesiyle bilinçli
  bir karardı. Ancak proje boyunca bunun somut riski görüldü: depoyu kopyalayan bir
  ekip üyesi sırları da kopyalar. Depo public yapılırsa dosyayı silmek yetmez —
  `SECRET_KEY` ve `ADMIN_PASSWORD` git geçmişinde kalır, ikisi de döndürülmelidir.
- **Rol süzmesi retrieval sonrası yapılıyor**; yetkisiz parçalar sonuç sayısından yer
  yiyebilir. Mevcut eşiklerle oluşmadığı ölçüldü, eşikler gevşetilirse görülebilir.
- **Giriş deneme sınırı süreç içi bellektedir**; çok işçili dağıtımda paylaşımlı bir
  sayaca taşınmalıdır.
- **Ölçümler tek makinede**, CPU üzerinde çalışan yerel modele bağlıdır; mutlak
  değerler donanıma göre değişir, oranlar anlamlıdır.
