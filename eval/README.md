# RAG Değerlendirme (eval)

Sistemin kalitesini **sayısal** ölçen araçlar. Amaç: bir değişiklik yapmadan
önce ve sonra skoru karşılaştırıp "gerçekten iyileşti mi?" sorusuna kanıtla
cevap vermek. Projedeki her iyileştirme bu şekilde önce/sonra ölçülerek yapıldı.

Ölçüm kümesi: `golden_set.jsonl` — **102 soru** (76 cevaplanabilir + 26
"belgede yok" tuzağı). Sete soru eklemek için `golden_set_kilavuz.md`'ye bakın.

## Hangi script neyi ölçer

| Script | Ne ölçer | LLM çağırır mı? | Süre |
|---|---|---|---|
| **`run.py`** | **Regresyon kapısı** — tek birleşik skor üretir, `--fail-under` ile CI'ı kırar | Hayır (embed hariç) | saniyeler |
| `evaluate.py` | Retrieval: hit@k, MRR + guardrail doğruluğu | Hayır (embed hariç) | saniyeler |
| `citation_eval.py` | Gösterilen kaynak doğru mu (ve en üstte mi) | Hayır | saniyeler |
| `latency_eval.py` | Retrieval gecikmesi + sorgu önbelleği kazancı (soğuk/sıcak) | Hayır | ~1 dk |
| `edge_cases.py` | Dayanıklılık: boş/bozuk/enjeksiyon girdiler | Hayır | saniyeler |
| `answer_eval.py` | Uçtan uca: modelin ÜRETTİĞİ cevap doğru mu | Evet | dakikalar |
| `faithfulness_eval.py` | Halüsinasyon: cevap yalnızca bağlamdan mı besleniyor (yargıç model) | Evet | dakikalar |

## Çalıştırma

Hepsi proje kökünden (yani `app/` klasörünü gören yerden) çalıştırılır:

```bash
# Regresyon kapısı — değişiklikten sonra ÖNCE bunu çalıştırın.
# Tek skor verir; skor eşiğin altına düşerse sıfırdan farklı çıkış kodu döner.
python eval/run.py
python eval/run.py --fail-under 0.94        # CI'da kullanılacak biçim
python eval/run.py --multiturn --verbose    # takip sorusu setini de ölç
python eval/run.py --json sonuc.json        # ayrıntılı sonucu dosyaya yaz

# Retrieval + guardrail — ayrıntılı ölçüm
python eval/evaluate.py

# hit@k için k değiştir
python eval/evaluate.py --k 3

# Kaynak atıfı / gecikme / dayanıklılık (hepsi hızlı, LLM yok)
python eval/citation_eval.py
python eval/latency_eval.py
python eval/edge_cases.py

# Uçtan uca cevap ve sadakat — yavaş, --limit ile küçük tut
python eval/answer_eval.py --limit 20
python eval/faithfulness_eval.py --limit 15
```

Modu değiştirerek karşılaştırma yapmak için (Windows'ta `set RAG_MODE=keyword`):

```bash
RAG_MODE=keyword python eval/evaluate.py   # BM25 — Ollama gerekmez, en hızlı
RAG_MODE=embed   python eval/evaluate.py   # bge-m3 — Ollama gerekir
RAG_MODE=hybrid  python eval/evaluate.py   # BM25 + embed, RRF (üretim ayarı)
```

## Metriklerin anlamı

| Metrik | Anlamı | İyi değer |
|---|---|---|
| **hit@k** | Doğru belge, dönen ilk k parça içinde mi? | %100'e yakın |
| **MRR** | Doğru belge kaçıncı sırada geldi (1/sıra ortalaması) | 1.0'a yakın |
| **Guardrail doğruluğu** | "Belgede yok" soruları doğru reddedildi mi? | yüksek |
| **Yanlış kabul** | Belgede olmayan soruya cevap üretti mi (halüsinasyon riski) | 0 |
| **Yanlış red** | Cevap belgede olduğu halde reddetti mi | 0 |
| **Kaynak atıfı** | Gösterilen kaynaklar arasında doğrusu var mı / en üstte mi | %100 |
| **Sadakat** | Cevaptaki her iddia bağlamda destekleniyor mu | yüksek |

Üretim konfigürasyonuyla alınan güncel sonuçlar için
[README](../README.md#ölçüm-sonuçları)'e bakın.

## Ölçerken dikkat

- **Eşikler moda özeldir.** BM25 skoru 0–~20, kosinüs 0–1 aralığında; bu yüzden
  `RAG_MIN_SCORE_KEYWORD` ve `RAG_MIN_SCORE_EMBED` ayrı ayarlanır. Mod
  değiştirip eşiği sabit tutmak yanıltıcı sonuç verir.
- **Tek ölçüme güvenmeyin.** hit@4 yükselirken guardrail düşebilir; ikisine
  birden bakın.
- **Set büyüdükçe skor düşebilir** — bu gerileme değil, küçük setin gizlediği
  gerçek zorluğun görünmesidir.
- **Karşılaştırma yaparken tek değişkeni değiştirin** (ya eşik, ya chunk boyutu,
  ya mod) — yoksa hangi değişikliğin işe yaradığı bilinemez.

## Kalibrasyon ve teşhis scriptleri

Bunlar skor raporlamaz; **ayarı veriyle seçmenizi** sağlar.

| Script | Ne yapar |
|---|---|
| `calibrate.py` | Guardrail eşiğini veriyle önerir (tek eşik) |
| `calibrate_hybrid.py` | BM25 + embed eşiğini **birlikte** tarar — üretim değerleri (6.80 / 0.535) bununla bulundu |
| `calibrate_rerank.py` | Cross-encoder rerank eşiğini tarar |
| `sweep_embed.py` | Embed eşiği taraması: yanlış red ↔ yanlış kabul takasını tek çalıştırmada gösterir |
| `diagnose.py` | Yanlış reddedilen soruların gerçek BM25/kosinüs skorlarını döker |
| `diagnose_refuse.py` | "Belgede yok" sorusu hangi kapıdan (BM25 mı embed mi) sızmış |
| `debug_ground.py` | Groundcheck LLM'inin answerability sorusuna verdiği ham cevap |

> `calibrate_hybrid.py`'nin varlık sebebi: hybrid modda bir soru
> `bm25 ≥ t_keyword` **VEYA** `kosinüs ≥ t_embed` ise cevaplanabilir sayılır.
> Bu yüzden iki eşiği ayrı ayrı optimize etmek yanıltıcıdır; ikili olarak
> taranmaları gerekir.

Tek bir soruyu elle incelemek için: `python test_rag.py "soru metni" --full`.
