# ARCHITECTURE_GAP_ANALYSIS.md — Edge-Park Doluluk Pipeline

> Dayanak: [CURRENT_STATE.md](./CURRENT_STATE.md). Her madde kod kanıtına bağlı; "önerilen iyileştirme" bölümü [TARGET_ARCHITECTURE.md](./TARGET_ARCHITECTURE.md) ve [ADR_DOCUMENTS.md](./ADR_DOCUMENTS.md) ile detaylandırıldı.

## Öncelik Tanımı
- **P0** — Güvenilirlik/veri bütünlüğü açığı, sessiz veri kaybına yol açıyor, üretim öncesi kapatılmalı.
- **P1** — Operasyonel risk, ölçek büyüdükçe veya belirli senaryolarda ciddi etki yaratır.
- **P2** — Bakım/berraklık sorunu, işlevsel kaybı yok ama yanlış varsayımla debug kaybına yol açabilir.
- **P3** — İş değeri olan genişleme fırsatı, şu an zorunlu değil.

## 1. Bulgu Tablosu

| # | Mevcut durum | Sorun/Risk | Etki | Öncelik | Önerilen iyileştirme |
|---|---|---|---|---|---|
| 1 | Edge MQTT publish best-effort; `_last_payload` publish denemesinden **önce** güncelleniyor (`mqtt_publisher.py:100-103`); outbox/retry yok | Başarısız/sessiz bir publish kalıcı olarak "gönderildi" sayılır, o state bir daha hiç yeniden denenmez | Tek giriş noktası (DB+WS+Kafka hepsi bu mesaja bağımlı) olduğu için **sessiz, kalıcı veri kaybı** — sadece analytics değil, gerçek doluluk durumu etkilenir | **P0** | Edge'e SQLite tabanlı outbox + poller/retry ekle (bkz. ADR-004); `_last_payload`'ı yalnızca başarı sonrası güncelle |
| 2 | `selfpark.mqtt.cameraDetection.v1` hiçbir `@KafkaListener`/Streams topolojisi tarafından tüketilmiyor (`KafkaTopicRegistry.getConsumableTopicNames()` STREAM'i dışlıyor) | Kafka bu akışta "gerçek zamanlı omurga" değil, maliyetli bir write-only arşiv | Kullanıcının hedeflediği Kafka→Consumer→WebSocket zinciri şu an **fiilen yok**; Schema Registry/DLQ/broker maliyeti karşılıksız | **P0** | Camera detection topic'i için gerçek bir `@KafkaListener` ekle, merkezi WebSocket yayınını oraya taşı (bkz. ADR-006) |
| 3 | Merkezi WebSocket broadcast, `CameraDetectionMqttHandler` içinde Kafka'yı atlayarak senkron yapılıyor (`SimpMessagingTemplate.convertAndSend`) | Backend yatayda ölçeklenemez — farklı bir instance'a bağlı istemci güncellemeyi hiç görmez | Çoklu-instance deploy edildiğinde fan-out kırık; bugün (tek instance) görünmez bir borç | **P0** | WS yayınını Kafka consumer'a taşı (madde 2 ile aynı çözüm) |
| 4 | Backend key'i `{cameraCode}-{slotId}`; kamera değişince eski key temizlenmeden orphan kalıyor | Zamanla ölü/yanlış veri birikir, kapasite hesapları (`availableCount`) bozulabilir | Uzun vadede veri kalitesi düşüşü, sessiz | **P1** | Slot kimliğini kamera koduna bağımlılıktan ayır: kalıcı `parkSlotId` + ayrı kamera↔slot eşleme kaydı (bkz. ADR-002) |
| 5 | Kamera bağlantısı kopunca (`capture/rtsp.py`) reconnect/retry yok, state donuk kalıyor, otomatik `UNKNOWN`'a düşmüyor | Stale veri süresiz "güncelmiş gibi" sunuluyor | Operatör/istemci, kameranın aslında koptuğunu fark edemez | **P1** | `last_frame_at` watchdog ekle: eşik aşılırsa ilgili kameranın slotlarını `UNKNOWN`'a çek + log/alarm |
| 6 | FREE→OCCUPIED geçişi event'te temsil edilmiyor; EMPTY→OCCUPIED için hiçbir sinyal/bildirim yok (`becameAvailable` sadece ters yönde ve hedefi boş TODO) | İş değeri yüksek senaryolar (rezervasyon ihlali, doluluk uyarısı) üretilemiyor | Orta-yüksek iş etkisi | **P1** | Delta event modeline geçiş (`previousStatus`/`currentStatus` alanlı), her iki yön için de downstream hook (bkz. ADR-001) |
| 7 | Backend MQTT adapter'ında `automaticReconnect` konfigüre değil; `mqttInputChannel` senkron `DirectChannel` (buffer/executor yok) | Broker kısa kesintisinde backend sessizce veri kaybedebilir; yük altında backpressure/thread-starvation riski | Operasyonel kırılganlık | **P1** | `setAutomaticReconnect(true)` + `maxReconnectDelay` ayarla; gerekirse `mqttInputChannel`'ı `ExecutorChannel`'a çevir |
| 8 | Kafka broker tek node (KRaft, RF=1 her yerde) | Gerçek SPOF; `acks=all` burada ek replica olmadığı için kozmetik | Broker düşerse tüm Kafka trafiği (camera detection + DLQ + anomaly streams) durur | **P1** (ölçek büyüdükçe **P0**) | Çok-broker Kafka cluster'a geçiş, RF≥2 — yalnızca gerçek üretim ölçeğinde (bkz. ARCHITECTURE_OPTIONS §Ölçek) |
| 9 | `kafkaFailureChannel` ölü kod; `KafkaProducerConfig.java` duplike/kullanılmayan bean; `application.yml`'deki `acks=1` fiilen devre dışı (gerçek değer `KafkaConfig.java`'dan geliyor: `acks=all`); 3 bağımsız DLQ üretim yolu aynı adlandırmaya yazıyor | Yanlış varsayımla debug kaybı, bakım karmaşıklığı | Düşük-orta, ama birikimli | **P2** | Ölü kod temizliği; tek bir `producerConfigs()` kaynağı; yml'deki yanıltıcı ayarı kaldır/doğru değerle senkronla |
| 10 | ROI/slot config'inde (`config/slots/cam*.json`) versiyon/zaman damgası alanı yok | Mapping değişikliklerinin izlenebilirliği yok | Debug/denetim zorlaşır | **P2** | Config şemasına `version`/`updated_at` alanı ekle (bkz. ADR-002) |
| 11 | `anomaly_detection` modülü kamera doluluk verisiyle bağlantısız (tek park-ilişkili sınıf barrier/turnike event'lerini tüketiyor) | Mevcut sinyal (flip-rate, occlusion — zaten edge'de `PredictionLog` ile kısmen üretiliyor) merkezi analiz göremiyor | Düşük, iş değeri var ama acil değil | **P3** | Kafka tüketimi kurulduktan sonra (madde 2 çözülünce) ayrı bir karar olarak değerlendirilsin |

> **Karar (2026-11-25):** Madde 8 (Kafka RF≥2/çok-broker yükseltmesi) ayrı
> bir faz olarak sonraya bırakıldı — WS/Redis işiyle (ADR-006/ADR-007)
> birleştirilmeyecek. Gerekçe: uygulama doğruluğu RF'den bağımsız, sadece
> broker-down dayanıklılığı etkileniyor; gerçek üretim ölçeğine kadar aciliyeti
> yok. P1 (ölçek büyüdükçe P0) etiketi ve bu bağımsız-faz kararı geçerli kalıyor.

> **Güncelleme (2026-11-24 doğrulama turu):** Madde 1 artık daha acil — edge'de `mqtt.enabled` üretimde `true`'ya çevrildiği için (bkz. CURRENT_STATE §4 güncelleme notu) bu bug teorik değil aktif risk; bu tur içinde `mqtt_publisher.py`'de düzeltildi (bkz. IMPLEMENTATION_ROADMAP Aşama 0). Madde 4 için backend'de kısmi bir refactor yapıldı (`24ab3ad`): `ParkSlotOccupancy` artık `parkInfoId` bazlı, `lastCameraId`/`lastCameraCode` eklendi, entity yorumu bare-slotIndex niyeti belirtiyor. **Ama** gerçek slot-key hâlâ edge'den gelen ham (`{cameraCode}-{slotId}`) string olduğu için davranışsal olarak orphan riski **kapanmadı** — madde 4 hâlâ **P1 açık** (bkz. CURRENT_STATE.md §8 güncelleme notu, ADR-002 status update).

## 2. Production Failure Scenario Matrisi

> Kullanıcının Bölüm 4 tablosundaki 12 senaryo — kod kanıtına dayanarak dolduruldu.

| Senaryo | Beklenen davranış | Mevcut implementasyon | Mevcut davranış | Risk | Önerilen çözüm | Öncelik |
|---|---|---|---|---|---|---|
| PyTorch modeli hata verir | Yanlış EMPTY/OCCUPIED event üretilmemeli | `pipeline.py:258-266` dış try/except | Kare atlanır, `update_batch` çağrılmaz, state donuk kalır, sadece log | Düşük (mantık doğru) ama gözlemlenebilirlik zayıf — sessiz | Hata sayacı/metrik + eşik aşılırsa alarm | P2 |
| Kamera bağlantısı kesilir | State `UNKNOWN` veya stale olarak değerlendirilmeli | `capture/rtsp.py`, `pipeline.py:279-282` | Reconnect yok, `frame=None` → erken `return`, state **donuk** kalır, UNKNOWN'a düşmez | **Yüksek** — stale veri "güncel" gibi sunulur | `last_frame_at` watchdog (bkz. madde 5) | P1 |
| SQLite erişilemez | Veri kaybı sessizce kabul edilmemeli | **N/A — SQLite hiç yok** (bkz. CURRENT_STATE §4) | Bu senaryo şu an tanımsız; en yakın karşılık "MQTT publish başarısız" | **Yüksek** (dolaylı) | Outbox eklenince bu senaryo da gerçek hâle gelir; outbox tasarımı `BEGIN/COMMIT` atomikliğiyle ele alınmalı (bkz. ADR-004) | P0 (outbox ile birlikte) |
| Edge process kapanır | Restart sonrası state ve Outbox kurtarılmalı | `__main__.py`, `state_store.py:99` | **Kalıcı kaynak yok** — state tamamen sıfırlanır, ilk kare hysteresis'siz anında onaylanır | **Yüksek** — kısa süreli "veri yok" sonrası temkinsiz ilk tahmin | Outbox + son-state tablosu eklenince restart sonrası son bilinen durumdan devam edilebilir | P0 (outbox ile birlikte) |
| Kafka erişilemez | Edge yerel çalışmaya devam etmeli | Edge zaten Kafka'yı hiç bilmiyor (sadece MQTT'ye konuşuyor) | Zaten doğru — edge MQTT broker'a konuşuyor, Kafka backend'in iç meselesi | Düşük | — (zaten uygun) | — |
| Kafka ACK kaybolur | Duplicate event güvenli yönetilmeli | `enable.idempotence=true` (`KafkaConfig.java`) | Producer seviyesinde idempotent — tekrarlar producer session içinde dedupe edilir | Düşük (producer-side) ama tüketici yok (madde 2), dedup'ın test edilebileceği bir consumer yok | Consumer eklenince idempotent consumer / `eventId` bazlı dedup da eklenmeli | P1 |
| Backend kapanır | Kafka event'leri kaybolmamalı | Kafka'daki event'ler topic'te kalıcı (retention 7 gün, STREAM kategori) | Backend kapanırken Kafka'ya gönderilememiş olanlar (best-effort async publish) kaybolur; zaten Kafka'ya ulaşanlar güvende | Orta — "gönderilmiş ama ACK beklenirken kapanma" penceresi var | Backend tarafında da outbox pattern (DB yazımıyla aynı transaction'da Kafka outbox) değerlendirilebilir (P2, şu an DB zaten senkron yazılıyor, veri kaybı DB'de yok — sadece Kafka kopyası eksik kalabilir) | P2 |
| WebSocket bağlantısı kesilir | Client reconnect ve snapshot alabilmeli | `ParkOccupancyService.getOccupancy(parkInfoId)` REST endpoint'i var (`ParkOccupancyController`) | Reconnect sonrası client bu REST ile mevcut durumu çekebilir — fakat bu otomatik değil, client'ın kendi sorumluluğu | Orta — client implementasyonuna bağımlı, backend garanti vermiyor | WS reconnect protokolünde "bağlanınca otomatik snapshot gönder" kuralı standartlaştırılsın (bkz. ADR-003/TARGET §6) | P1 |
| Redis kapanır | Belirlenen fallback çalışmalı | **N/A — Redis hiç kullanılmıyor** | Senaryo bu akış için geçersiz | — | — | — |
| Eski event gelir | Yeni state geriye alınmamalı | Backend'de `processDetection` her gelen mesajı sırayla uygular, timestamp/version kontrolü yok | Sıra dışı/geç gelen bir mesaj mevcut (daha yeni) durumun üzerine yazabilir | Orta — MQTT QoS1 + tek partition (cameraCode key) genelde sırayı korur ama garanti değil | Event'e `version`/monotonic sayaç eklenip backend'de "eski version'ı reddet" kuralı (bkz. ADR-001, ADR-005) | P1 |
| ROI mapping değişir | Eski ve yeni mapping tutarlı yönetilmeli | Madde 4 ve 10 | Orphan key birikir, versiyon izlenemiyor | Yüksek (uzun vadeli veri kalitesi) | Madde 4/10 ile aynı çözüm | P1/P2 |
| Edge uzun süre offline kalır | Reconciliation yapılabilmeli | Outbox yok, reconciliation mekanizması yok | Offline süresince hiçbir state değişikliği hiçbir yere kaydedilmiyor (ne yerelde ne merkezi) | **Yüksek** | Outbox + edge tekrar bağlanınca bir snapshot event'i gönderip merkezi projeksiyonu senkronlasın (bkz. TARGET §Reconciliation) | P0/P1 |

## 3. Özet

11 bulgudan **3 tanesi P0**: edge güvenilirlik (outbox), Kafka'nın tüketilmemesi, ve merkezi WS'nin Kafka'yı atlayarak tek-instance'a kilitlenmesi. Üçü de birbirine bağlı — aynı kök nedenin (edge→backend tek güvenilmez kanal, Kafka'nın fiilen devre dışı olması) farklı yüzleri. [TARGET_ARCHITECTURE.md](./TARGET_ARCHITECTURE.md) bu üçünü tek bir tutarlı tasarımla çözüyor.
