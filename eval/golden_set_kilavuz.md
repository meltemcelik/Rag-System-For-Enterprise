# Golden Set Kılavuzu

Bu dosya `eval/golden_set.jsonl`'a **yeni soru eklerken** uyulacak kuralları
anlatır. Amaç ölçümü sağlamlaştırmak: set ne kadar büyük ve çeşitliyse skorlar
o kadar güvenilir olur ve bundan sonraki her iyileştirmenin gerçek olup
olmadığını bilebiliriz.

## Setin şu anki durumu

| | |
|---|---|
| Toplam soru | **102** |
| Cevaplanabilir (`should_refuse:false`) | 76 |
| Reddedilmesi gereken (`should_refuse:true`) | 26 (~%25) |
| Zorluk dağılımı | 75 zor / 27 kolay |

Belge başına soru sayısı:

| Belge | Soru |
|---|---|
| `Enterprise RAG and Agentic AI in 2026... .pdf` | 27 |
| `sirket_izin_politikasi.md` | 17 |
| `masraf_yonetmeligi.md` | 17 |
| `bilgi_guvenligi.txt` | 15 |

> **Bilinen boşluk:** `rapor-kurumsal-rag-agentic-ai.html` belgesini hedefleyen
> hiç soru yok. Sete eklenecek ilk sorular buradan gelmeli.

Bu set üzerindeki güncel skorlar için [README](../README.md#ölçüm-sonuçları)'e bakın.

## Format (her satır tek bir JSON)

```json
{"question": "Soru metni?", "expected_sources": ["dosya.md"], "answer_keywords": ["anahtar"], "should_refuse": false, "zorluk": "kolay"}
```

- **question**: kullanıcının soracağı soru.
- **expected_sources**: cevabın bulunduğu belge dosya adı/adları (`data/docs`
  içindeki ad, PDF için tam ad). Birden fazla olabilir. Reddedilecek sorularda
  boş liste: `[]`.
- **answer_keywords**: doğru cevapta geçmesi beklenen kelimeler.
  `evaluate.py` bunu kullanmaz ama `answer_eval.py` uçtan uca cevap
  doğruluğunu bunlarla ölçer — bu yüzden boş bırakılmamalı.
- **should_refuse**: `true` = cevap belgelerde YOK, sistem reddetmeli.
  `false` = cevap var.
- **zorluk**: `"kolay"` (belgedeki kelimelerle) veya `"zor"` (farklı
  kelimelerle / çıkarım gerektiren).

Beş alanın **hepsi** her satırda bulunmalı; eksik alan sessizce yanlış skora
yol açar.

## Nasıl doğru soru yazılır (en önemli kısım)

Her soruyu eklemeden önce ilgili belgeyi aç ve **gözünle doğrula**:

- Cevaplanabilir (`should_refuse:false`) ise → cevap gerçekten o belgede var mı?
  `expected_sources`'u doğru dosyaya yaz.
- Reddedilecek (`should_refuse:true`) ise → o bilgi gerçekten **hiçbir** belgede
  YOK mu? "İlgili ama cevabı olmayan" tuzaklar en değerlisidir (aşağıya bak).

## Eklenmesi gereken soru türleri

1. **Kolay (verbatim):** belgedeki kelimelerin aynısı. Az sayıda yeter, temel kontrol.
2. **Zor (paraphrase / eşanlamlı / günlük dil):** aynı bilgiyi FARKLI kelimelerle sor.
   Bunlar embedding'in gücünü test eder. Örnek: belge "yıllık izin 20 iş günü" →
   soru "senede kaç gün tatilim var?". Şunları karıştır:
   - eşanlamlılar (izin/tatil, parola/şifre, fiş/makbuz, konaklama/otel)
   - günlük/konuşma dili ("makbuzumu kaybettim, param yanar mı?")
   - çıkarım gerektiren ("eşim doğum yapacak, benim iznim ne kadar?")
   - küçük yazım hataları / Türkçe ekler (büyük-küçük harf, "izinmi/izin mi")
3. **Reddedilecek — açıkça alakasız:** "Python'da liste nasıl ters çevrilir?",
   "Bugün dolar kaç TL?", "Bu akşam hangi filmi izlesem?".
4. **Reddedilecek — İLGİLİ ama belgede YOK (en kıymetli tuzaklar):** konu şirket/İK
   ama cevap belgelerde yok. Örnek: "Yıllık iznimi paraya çevirebilir miyim?",
   "Fazla mesai nasıl ücretlendirilir?", "Bayram tatilleri kaç gün?". Bunlar
   guardrail'i gerçekten sınar.

## Denge kuralları

- **Reddedilecek soru oranı ~%20-25** kalsın (şu an %25). Çok azı guardrail'i
  test etmez, çok fazlası hit@4'ü anlamsızlaştırır.
- **Her belgeyi ve her bölümü** kapsa (izin, masraf, güvenlik + PDF'in farklı
  sayfaları + henüz hiç sorusu olmayan HTML raporu).
- Zor/kolay oranını koru: kolay sorular skoru şişirir, gerçek zorluğu göstermez.

## Kullanım

Yeni soruları `golden_set.jsonl`'ın sonuna satır satır ekle, kaydet. Ekledikten
sonra önce dosyanın **bozulmadığını** doğrula (proje kökünden):

```bash
python -c "import json;[json.loads(l) for l in open('eval/golden_set.jsonl',encoding='utf-8') if l.strip()];print('JSON tamam')"
```

Sonra ölç:

```bash
python eval/evaluate.py
```

Set büyüdükçe skorların nasıl değiştiğine bak. Skor biraz düşse bile panik yok —
bu, gerçek zorluğu gördüğümüz anlamına gelir; asıl amaç **güvenilir** ölçüm.
(Nitekim guardrail doğruluğu set 82'den 102'ye çıkınca %85'ten %69'a "düştü";
bu bir gerileme değil, küçük setin gizlediği zorluğun görünür olmasıydı.)

Set büyüdükten sonra **eşikleri yeniden kalibre etmeyi unutmayın**
(`python eval/calibrate_hybrid.py`) — eski eşikler eski sete göre seçilmişti.

Retrieval dışındaki metrikleri de ölçmek için `eval/README.md`'deki komut
listesine bakın.
