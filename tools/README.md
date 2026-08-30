# tools/ — ölçüm ve hata avı betikleri

`DEGISIKLIK-RAPORU.md` ve `BIRLESTIRME-RAPORU.md` içindeki her sayı bu betiklerden
çıktı. Raporlar depodaydı, onları üreten kod değildi — bu dizin o boşluğu kapatır:
rapordaki bir iddiadan şüphelenen biri ilgili betiği çalıştırıp kendi sayısını üretebilir.

**`eval/` ile farkı:** `eval/` sistemin *kalitesini* sürekli ölçer (altın set, regresyon
kapısı, CI). `tools/` ise belirli bir değişiklik setini *doğrulamak* için yazılmış,
çoğu tek seferlik betiklerdir. Yeni özellik ölçmek istiyorsanız `eval/`'e bakın.

## Çalıştırma

Çoğu betik çalışan bir sunucu ister (`python run.py`), bir kısmı sunucusuz çalışır.
Proje kökünden çağırın:

```bash
python tools/senaryolar.py http://localhost:8000 SONRA
python tools/bughunt.py
```

Bazı betikler admin şifresini/ayarları değiştirir. Ölçümden önce `data/users.db`
yedeği alın — bu betikler geliştirme ortamı içindir, **üretimde çalıştırmayın**.

## Betikler

### Uçtan uca senaryo ve entegrasyon
| Betik | Ne yapar | Raporda nerede |
|---|---|---|
| `gercek_kullanim.py` | **10 gerçek hayat denemesi**: takip soruları, masraf/güvenlik soruları, belgede olmayanlar, bozuk yazım, geçmiş, belge yükleme + anında sorgu, rol yetkisi, önbellek. Kullanıcının gördüğü cevabı ve kaynakları yazdırır. `python tools/gercek_kullanim.py http://localhost:8000` | BIRLESTIRME-RAPORU §7 |
| `cesitli_test.py` | **34 sorgu / 11 kategori** retrieval testi. Her sorgu için eşik UYGULANMADAN ham BM25 ve ham kosinüs yazar — bir reddin "hiç bulunamadı" mı "eşiğin biraz altında" mı olduğunu ayırır. Sunucu gerekmez. | BIRLESTIRME-RAPORU §6 |
| `senaryolar.py` | 17 gerçek kullanıcı yolculuğu; aynı betik iki sürüme de uygulanır, desteklenmeyen özelliğe "YOK" yazar | DEGISIKLIK-RAPORU §4 önce/sonra tablosu |
| `integration.py` | Canlı sunucuya karşı API + WebSocket + yeni özellikler | §2 |
| `drive_rag.py` | Uygulamayı gerçek kullanıcı gibi sürer (login → /api/me → admin/models → WS) | — |

### Hata avı
| Betik | Ne yapar | Raporda nerede |
|---|---|---|
| `bughunt.py` | Her hipotez için **üretilebilir kanıt** arar; kanıtlanamayan hata sayılmaz | §6 (15 hata) |
| `bughunt2.py` | İkinci tur; sunucuya dokunmaz (paralel ölçümü bozmasın) | §6 |
| `acik_arayan.py` | Daha önce dokunulmamış alanlarda köşe durum taraması | §2 (10 kontrol) |
| `adversarial.py` | Yetki aşımı, izolasyon, enjeksiyon, sınır değerleri | §7 |
| `forge_demo.py` | `SECRET_KEY` açığının ispatı: commitli anahtarla şifre bilmeden admin oturumu uydurma | §6 kritik satır |

### Ölçüm
| Betik | Ne yapar | Raporda nerede |
|---|---|---|
| `measure_claims.py` | Kendi iddialarımızı ölçer | §3 |
| `neutral_scorer.py` | **Tarafsız ölçer**: hem eski hem yeni `rag.py` ile çalışır — önce/sonra kıyası aynı kodla yapılsın diye | §3 (sıfır regresyon iddiasının temeli) |
| `gecikme.py` | Tekrarlı gecikme ölçümü (tek seferlik sonuçlar çok gürültülüydü) | §5 |
| `health_teshis.py` | `/api/health` neden yavaşladı — bileşenlerini tek tek ölçer | §6 sağlık ucu satırı |
| `sonuc_ONCE.json` / `sonuc_SONRA.json` | Önce/sonra ham ölçüm verisi | §4, §5 |

### Teşhis / hipotez sınama
| Betik | Ne yapar |
|---|---|
| `probe.py` | Bir soru neden reddedildi — BM25 ve embed skorlarını eşiklerle karşılaştırır |
| `probe_condense.py` | Takip sorusu dönüşümü ne üretiyor |
| `probe_turkish.py` | Türkçe karakter davranışı |
| `acl_crowding.py` | ACL süzmesi izinli parçaları `top_k`'dan dışarı itiyor mu (§8 bilinen sınır) |
| `h7.py` | Rol kısıtlaması anında mı uygulanıyor (§7 çürütülen hipotez) |
| `verify_degraded.py` | `status()` eksik embedding modelini yakalıyor mu |
| `verify_upload.py` | Alakasız bir sorgunun ("kedi besleme politikası") hangi kaynakları döndürdüğü — guardrail'in sızdırıp sızdırmadığı. *Adı içeriğiyle uyuşmuyor; yükleme testi değildir.* |

## Depoya alınmayan

`sifre_geri_al.py` bilinçli olarak alınmadı: senaryo testlerinin değiştirdiği admin
şifresini eski (zayıf) hâline döndürmek için parola hash'ini doğrudan veritabanına
yazıyordu. Geliştirme sırasında işe yaradı, ama depoda durması yanlış bir örnek olur.
