# Acme A.Ş. — Yazılım Mühendisliği, Git ve Kod İnceleme Standartları

## 1. Git Branching Stratejisi
* Projelerde basitleştirilmiş **GitHub Flow** veya **Trunk-Based Development** yaklaşımı benimsenir.
* Ana geliştirme dalı `main` dalıdır. `main` dalına doğrudan commit (push) atılması koruma altındadır (Protected Branch).
* Branch isimlendirme standartları:
  - Özellik geliştirme: `feat/kullanici-girisi` veya `feature/JIRA-123-rag-stream`
  - Hata düzeltme: `fix/gecikme-sorunu` veya `bugfix/JIRA-456-token-parse`
  - Yeniden yapılandırma / Bakım: `refactor/veritabani-katmani` veya `chore/bagimlilik-guncelleme`

## 2. Pull Request (PR) ve Kod İnceleme (Code Review) Kuralları
* Her Pull Request'in `main` dalına birleştirilebilmesi (merge) için en az **2 kıdemli geliştiriciden (Senior/Lead)** onay (Approve) alması zorunludur.
* PR açıklamalarında yapılan değişikliğin amacı, test adımları ve varsa ekran görüntüsü / test logları yer almalıdır.
* PR boyutu 400 satır değişikliği aşmamalıdır; büyük geliştirmeler küçük ve bağımsız parçalara bölünmelidir.
* Tüm CI/CD otomatik testleri (Birim testleri, Linter, SonarQube güvenlik taraması) yeşil (başarılı) olmadan merge butonu aktifleşmez.

## 3. On-Call Nöbeti ve Olay Yönetimi (Incident Management)
* Mühendislik ekibindeki her geliştirici dönüşümlü olarak haftalık **On-Call (Nöbetçi Mühendis)** listesinde yer alır.
* On-Call nöbeti tutan geliştiriciye haftalık **5.000 TL** nöbet tazminatı ödenir.
* P1 (Kritik) bir olay tetiklendiğinde nöbetçi mühendisin sisteme ilk müdahale süresi en fazla **15 dakikadır**.
* Olay çözüldükten sonra 48 saat içinde **Post-Mortem (Kaza Sonrası İnceleme Raporu)** hazırlanır ve ekip içi toplantıda kök neden analizi paylaşılır.

## 4. Test Kapsamı ve Kod Kalitesi
* Yeni yazılan kodlarda minimum birim test kapsamı (Unit Test Coverage) **%80** olmalıdır.
* Hassas veriler (API key, veritabanı şifresi, JWT secret) kesinlikle kod deposuna commitlenemez; `.env` veya HashiCorp Vault / AWS Secrets Manager üzerinden dinamik okunmalıdır.
