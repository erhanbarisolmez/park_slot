# ParkSlot Kullanım Kılavuzu

YouTube canlı yayınından park slotu doluluk tespiti için veri toplama, etiketleme ve model eğitimi adımları.

## Hangi Adımlar Tek Seferlik, Hangileri Tekrarlanır?

| Adım | Tekrar durumu |
|---|---|
| 1. Ortam kurulumu | **Tek seferlik** (bilgisayar/proje başına bir kez) |
| 2. Kamera slotu işaretleme (`annotate.py`) | **Tek seferlik**, kamera başına bir kez — slotlar değişmediği sürece (kamera açısı/konumu değişirse yeniden işaretlenmeli) |
| 3. Veri toplama (`collect_data.py`) | **Tekrarlanır** — farklı `--minutes-ago` değerleriyle (farklı saat/gün/hava durumu/doluluk) defalarca çalıştırılmalı, yeterli ve çeşitli veri birikene kadar |
| 4. Model eğitimi (`train.py`) | **Tekrarlanır** — her yeni veri turundan sonra (veya belirli aralıklarla) modeli yeniden eğitip performansı kontrol et |
| 5. ONNX export | Sadece dağıtım/entegrasyon öncesi, eğitilmiş yeni bir model olduğunda tekrarlanır |

## 1. Ortam Kurulumu

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

Kurulumu doğrula:

```bash
python -c "import park_slot, torch, cv2; print('OK', torch.__version__, cv2.__version__)"
```

## 2. Kamera Slotlarını İşaretleme

Her kamera (`cam1`, `cam2`, ...) için park yeri sınırlarını bir kez işaretlemen gerekiyor. URL ilk seferde verilir ve kaydedilir — **bir daha yazmana gerek kalmaz.**

```bash
python tools/annotate.py --source "https://www.youtube.com/watch?v=..." --camera cam1
```

Kontroller:
- Sol tık: nokta ekle
- Sağ tık: son noktayı geri al
- Enter: aktif polygon'u slot olarak kaydet, sıradaki slota geç
- `u`: son slotu geri al
- `s`: tüm slotları `config/slots/cam1.json` dosyasına kaydet (URL de bu dosyaya yazılır) **ve pencereyi otomatik kapat**
- `q` / ESC: kaydetmeden çık (**işaretlediğin slotlar ve URL kaybolur**)

### Zaten kayıtlı bir kamerayı yeniden işaretlemek

URL bir kere kaydedildikten sonra, aynı kamera için tekrar `--source` yazmana gerek yok:

```bash
python tools/annotate.py --camera cam1
```

Başka bir kamera eklemek için aynı adımları farklı bir `--camera` adıyla (örn. `cam2`) ve yeni bir URL ile tekrarla.

### Hassas düzeltme: kuşbakışı (bird's-eye) görünümde işaretleme

Açılı kameralarda, kameraya göre dar açılı veya uzak slotlar ham karede çok ince dilimlere dönüşebilir — bitişik iki slot arasına (örn. iki araç arasındaki üçüncü bir boşluğa) hassas bir sınır çizmek neredeyse imkansız hale gelir. `tools/annotate_birdseye.py`, kareyi perspektif çarpıklığı olmayan, yüksek çözünürlüklü bir kuşbakışı tuvale çevirip işaretlemeyi orada yapmanı sağlar — gerçek dünyadaki oranlar korunduğu için sınırları çok daha kolay ve hassas çizersin.

**Önkoşul:** bu kamera için `python tools/mark_perspective.py --camera <id>` zaten çalıştırılmış olmalı (kuşbakışı dönüşümü için gereken referans noktaları orada ayarlanır).

```bash
python tools/annotate_birdseye.py --camera cam2
# tuval boyutunu değiştirmek istersen:
python tools/annotate_birdseye.py --camera cam2 --width 1600 --height 1000
```

Açıldığında mevcut tüm slotlar kuşbakışı tuvalde önceden yüklü (yeşil) gösterilir — id numaralandırması kaldığı yerden devam eder. Kontroller `annotate.py` ile aynıdır (yukarıya bak), tek fark: `u` ile **sadece en son eklenen slottan geriye doğru** geri alabilirsin — ortadaki bir slotu düzeltmek istiyorsan ondan sonraki tüm slotları da geri alıp sırayla yeniden çizmen gerekir (araç bunu açılışta hangi id'lere kadar geri almak gerektiğini yazdırır). `s` ile kaydettiğinde, çizdiğin her şey otomatik olarak ham kamera piksel uzayına geri çevrilip `config/slots/cam2.json`'a yazılır — `perspective_transform` (kuşbakışı minimap kalibrasyonu) değişmeden korunur.

## 3. Eğitim Verisi Toplama

`tools/collect_data.py`, slotları kırpıp `e` (empty/boş) veya `o` (occupied/dolu) tuşlarıyla etiketlemeni sağlar. `--source` verilmezse `--slots` dosyasındaki kayıtlı URL kullanılır.

Kontroller (her slot için tek tek sorulur):
- `e`: boş
- `o`: dolu
- `s`: bu slotu atla
- `q` / ESC: aracı bitir

### a) Canlı mod (sürekli, gerçek zamanlı)

```bash
python tools/collect_data.py --slots config/slots/cam1.json --interval 5
```

Her `--interval` saniyede bir yeni kare alır. Araçların gelip gitmesini gerçek zamanda beklemen gerekir.

### b) Geçmişten tek kare (zaman sıçraması)

Gerçek zamanda beklemeden, canlı uçtan X dakika önceki bir anı çekip etiketle:

```bash
python tools/collect_data.py --slots config/slots/cam1.json --minutes-ago 60
```

### c) Geçmişten zaman penceresi taraması (önerilen — en hızlı veri toplama yöntemi)

Belirli bir geçmiş aralığı, sabit adımlarla tara — her adımda farklı bir an gelir, gerçek zamanlı bekleme yok:

```bash
python tools/collect_data.py --slots config/slots/cam1.json \
    --minutes-ago 180 --window-minutes 120 --interval 1800
```

Bu örnek: 3 saat önceden başlar, 1 saat öncesine kadar, 30 dakikada (`1800` saniye) bir örnek alır → farklı doluluk durumlarından hızlıca çeşitli veri toplamanı sağlar.

| Parametre | Anlamı |
|---|---|
| `--minutes-ago` | Başlangıç noktası: şu andan kaç dakika önce |
| `--window-minutes` | O noktadan itibaren ileriye doğru kaç dakikalık pencere taransın |
| `--interval` | Pencere taramasında ardışık örnekler arası *stream zamanı* aralığı (saniye) |

### d) Zamansal çeşitlilik için örnek tarama planı

> **Önemli — `--minutes-ago` her zaman komutu çalıştırdığın "şu an"a görecelidir.** Sabit bir takvim noktası değil: `--minutes-ago 1440` dediğinde her zaman "bu komutu çalıştırdığın andan 1 gün önce" anlamına gelir — dün aynı komutu çalıştırmış olsan bile, bugün çalıştırdığında yine *bugünden* 1 gün geri gider. Belirli bir takvim gününe (örn. "3 Kasım'a") ulaşmak istiyorsan, o günün ne kadar gerisinde olduğunu komutu çalıştırdığın ana göre yeniden hesaplaman gerekir (aşağıdaki tablo da bu yüzden sabit değil, referans alındığı ana göredir).
>
> **Fragman süresi otomatik tespit edilir.** Her YouTube canlı yayının DASH fragman süresi farklıdır (bazılarında 2.0s, bazılarında 5.0s) — araç bunu her çalıştırmada ilgili akışın manifestinden okur, sabit varsaymaz. Yanlış/sabit bir değer kullanmak `--minutes-ago`'nun gerçekte kaç dakika geriye gittiğini saptırırdı (daha önce bir akışta bu yüzden istenenin ~2.5 katı kadar geri gidiliyordu — artık düzeltildi).

`--minutes-ago` ampirik olarak en az ~6000 dakikaya (≈4 gün) kadar geriye gidebiliyor. Aynı saatten tekrar tekrar veri toplamak yerine, **gündüz/gece** ve **hafta içi/hafta sonu** dönüşümlü 10 farklı noktadan örnek almak doluluk çeşitliliğini artırır (araçların varlık/yokluk döngüsü saat ve güne göre değişir). Aşağıdaki tablo `2026-11-05 13:33` referans alınarak hesaplanmıştır — kendi çalıştırma anına göre `date -d "-<dakika> minutes"` ile güncelleyebilirsin:

| # | `--minutes-ago` | Yaklaşık zaman | Gün | Beklenen doluluk |
|---|---|---|---|---|
| 1 | 0 | bugün 13:33 | Perşembe (gündüz) | yoğun olabilir |
| 2 | 360 | bugün 07:33 | Perşembe (sabah erken) | düşük-orta |
| 3 | 720 | dün gece 01:33 | Perşembe (gece) | büyük olasılıkla boş |
| 4 | 1440 | 1 gün önce 13:33 | Çarşamba (gündüz) | yoğun olabilir |
| 5 | 2160 | 1 gün önce 01:33 | Çarşamba (gece) | büyük olasılıkla boş |
| 6 | 2880 | 2 gün önce 13:33 | Salı (gündüz) | yoğun olabilir |
| 7 | 3600 | 2 gün önce 01:33 | Salı (gece) | büyük olasılıkla boş |
| 8 | 4320 | 3 gün önce 13:33 | Pazartesi (gündüz) | yoğun olabilir |
| 9 | 5040 | 3 gün önce 01:33 | Pazartesi (gece) | büyük olasılıkla boş |
| 10 | 5760 | 4 gün önce 13:33 | Pazar (gündüz, hafta sonu) | farklı desen (hafta sonu) |

Her nokta için küçük bir pencere de taratarak (`--window-minutes 60 --interval 1800`) o andaki ani değişimleri de yakalayabilirsin:

```bash
for m in 0 360 720 1440 2160 2880 3600 4320 5040 5760; do
    python tools/collect_data.py --slots config/slots/cam1.json \
        --minutes-ago "$m" --window-minutes 60 --interval 1800
done
```

> Gece saatlerinde görüntü karanlık olacağından modelin hem gündüz hem gece koşullarını öğrenmesi için bu çeşitlilik önemli — sadece gündüz verisiyle eğitilen model gece performansında zayıf kalır.

Çıktılar `data/labeled_<camera>/empty/` ve `data/labeled_<camera>/occupied/` altına kaydedilir (her kamera kendi klasörüne - bkz. "Birden Fazla Kamera" bölümü). `--output` verilmezse `collect_sweep.sh` kamera adından bu klasörü otomatik türetir; `collect_data.py`'yi elle çağırıyorsan `--output data/labeled_<camera>` ile belirt.

Tekrarlayan, farklı zaman noktalarından toplu tarama için hazır script:

```bash
bash tools/collect_sweep.sh config/slots/cam1.json
```

Gündüz/gece, 5 farklı gün dönüşümlü 10 noktayı otomatik tarar (`q` basarsan tüm sweep durur, bkz. script içi yorum). Hedefe ulaşmak için bu script'i **birkaç kez** (farklı günlerde, ör. 4-5 kez) tekrar çalıştırman gerekir - tek seferde hedef sayılara ulaşmaz.

> **Not — ne kadar veri gerekli?** Her kare, kameradaki slot sayısı kadar örnek üretir (örn. `cam1` için 14 slot = 1 kare → 14 kırpılmış görüntü). Küçük CNN modeli için pratik hedefler:
> - **Minimum:** sınıf başına (empty/occupied) ~200-300 görüntü — ilk eğitim denemesi için yeterli.
> - **Önerilen:** sınıf başına ~500-1000+ görüntü, mümkün olduğunca **dengeli** (empty/occupied birbirine yakın sayıda) ve **çeşitli** (farklı saat, gündüz/gece, hava durumu, farklı araç konumları) olacak şekilde.
> - `--window-minutes` taraması ile tek çalıştırmada onlarca kare (= yüzlerce slot görüntüsü) birikir; birkaç farklı `--minutes-ago` turu (farklı gün/saat) ile hedefe ulaşmak en hızlı yol.
> - İki sınıf arasında aşırı dengesizlik (örn. %90 empty) olursa model occupied'ı iyi öğrenemez — bilinçli olarak dolu olduğu bilinen saatlerden (`--minutes-ago`) de örnek topla.

## 4. Model Eğitimi

Yeterince veri topladıktan sonra (her iki sınıftan da örnek olacak şekilde), **her kamera için ayrı eğitim** (ayrı veri klasörü = ayrı model):

```bash
python tools/train.py --data data/labeled_cam1 --output data/models_cam1 --epochs 50
```

En iyi checkpoint `data/models_cam1/best.pth` olarak kaydedilir.

## 5. ONNX'e Dönüştürme (opsiyonel, dağıtım için)

```bash
python tools/export_onnx.py --checkpoint data/models_cam1/best.pth --output data/exports/model_cam1.onnx
```

## Önerilen İş Akışı Özeti

```bash
# 1) Kamerayı bir kez işaretle (URL kaydedilir)
python tools/annotate.py --source "https://youtube.com/watch?v=..." --camera cam1

# 2) Farklı zaman dilimlerinden veri topla (birkaç kez, farklı günlerde tekrarla)
bash tools/collect_sweep.sh config/slots/cam1.json

# 3) Modeli eğit (bu kameraya özel veri/checkpoint klasörüyle)
python tools/train.py --data data/labeled_cam1 --output data/models_cam1

# 4) (Opsiyonel) ONNX'e aktar
python tools/export_onnx.py --checkpoint data/models_cam1/best.pth --output data/exports/model_cam1.onnx
```

## Birden Fazla Kamera (cam2, cam3, cam4, ...)

Her kamera **tamamen bağımsız** işlenir: kendi `config/slots/<camera>.json`'u, kendi `data/labeled_<camera>/` veri klasörü, kendi `data/models_<camera>/best.pth` modeli. Farklı kameraların verisi/modeli birbirine karışmaz (farklı açı/sahne olabileceğinden kamera başına ayrı model daha güvenilir sonuç verir).

Bir kamerayı eklemek için aynı 4 adımı tekrarla:

```bash
# 1) İşaretle
python tools/annotate.py --source "https://youtube.com/watch?v=..." --camera cam2

# 2) Veri topla (birkaç kez tekrarla - 4-5 tur sonrası sınıf başına 500+ görüntüye ulaşılır)
bash tools/collect_sweep.sh config/slots/cam2.json

# 3) Eğit
python tools/train.py --data data/labeled_cam2 --output data/models_cam2

# 4) (Opsiyonel) ONNX
python tools/export_onnx.py --checkpoint data/models_cam2/best.pth --output data/exports/model_cam2.onnx
```

cam3, cam4 için `cam2`'yi ilgili kamera adıyla değiştir. `tools/audit_slots.py` (argümansız) tüm kameraların slot config'lerini tek seferde tarar.
