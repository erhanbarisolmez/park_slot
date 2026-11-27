# CURRENT_STATE.md — Edge-Park Doluluk Pipeline (Gerçek Kod Davranışı)

> Bu doküman kod okunarak üretildi; görüş/tahmin değil, gözlemlenen davranıştır. Kanıtlanamayan noktalar ayrı bölümde `UNKNOWN / NEEDS VERIFICATION` olarak işaretlendi. İki repo kapsandı: edge (`/home/erhanbarisolmez/projects/park_slot`) ve merkezi backend (`/home/erhanbarisolmez/projects/selfpark-backend-v0-main`, aşağıda kısaca **backend**).

## 1. Repository Bileşenleri

### Edge (`park_slot`)
| Katman | Dizin | Sorumluluk |
|---|---|---|
| Capture | `src/park_slot/capture/` | Kamera karesi alma (RTSP, dosya, YouTube) |
| Inference | `src/park_slot/inference/` | CNN slot sınıflandırması + Faster R-CNN araç dedektörü + pipeline orkestrasyonu |
| Annotation | `src/park_slot/annotation/` | Slot/ROI tanım şeması, geometri, perspektif dönüşüm, GUI kalibrasyon aracı |
| Services | `src/park_slot/services/` | `StateStore` (onaylı durum motoru), `MqttPublisher`, `PredictionLog`, `SlotManager`, `OverrideReview` |
| API | `src/park_slot/api/` | Yerel REST + WebSocket (operatör paneli, minimap, mobil özet) |
| Tools | `tools/` | Kalibrasyon/etiketleme/layout-kaydetme yardımcı script'leri (CLI, elle çalıştırılır) |

### Backend (`selfpark-backend-v0-main`)
| Katman | Dizin | Sorumluluk |
|---|---|---|
| MQTT altyapısı (domain-bağımsız) | `src/main/java/com/selftech/mqtt/` | Broker bağlantısı, topic dispatch |
| Kamera domain | `src/main/java/com/selftech/selfparkbackendv001/mqtt/`, `.../controllers/CameraController.java`, `.../service/concretes/ParkOccupancyManager.java` | Kamera MQTT handler'ı, slot layout REST'i, doluluk iş mantığı |
| Kafka altyapısı | `src/main/java/com/selftech/kafka/` | Topic registry, Avro converter'lar, `CentralEventCoordinator`, DLQ, Kafka Streams config |
| anomaly_detection | `src/main/java/com/selftech/anomaly_detection/` | Gerçek-zamanlı/batch anomali analizi (kamera doluluğuyla **bağlantısız**, bkz. §6) |

## 2. Gerçek Event Akışı (uçtan uca)

```
Kamera görüntüsü
   └─▶ capture/{rtsp,file,youtube}.py            (kare al)
        └─▶ inference/pipeline.py:_process_frame  (CNN + hibrit araç-dedektör düzeltmesi)
             └─▶ services/state_store.py:update_batch  (hysteresis + occlusion + override → ONAYLI state)
                  ├─▶ _notify → services/mqtt_publisher.py   (TAM snapshot, best-effort MQTT publish)
                  ├─▶ _notify → api/ws.py                    (yerel WebSocket, bağımsız abone)
                  └─▶ _notify → services/prediction_log.py   (proxy kalite metrikleri)

MQTT broker (selfpark/camera/{code}/detection)
   └─▶ backend: mqtt/CameraDetectionMqttHandler.handle()
        ├─▶ ADIM 1 (senkron): ParkOccupancyManager.processDetection → DB yazımı + SimpMessagingTemplate WebSocket broadcast
        └─▶ ADIM 2 (best-effort async): CameraDetectionEventPublisherService → Kafka (Avro, selfpark.mqtt.cameraDetection.v1)
             └─▶ Kafka topic'te kalıcı duruyor — HİÇBİR consumer okumuyor (bkz. §5)
```

**Önemli:** İstemcilere giden gerçek zamanlı güncelleme (merkezi WebSocket), Kafka adımını **beklemiyor**; ADIM 1 içinde, Kafka publish'inden önce ve ondan bağımsız olarak gerçekleşiyor.
Kanıt: `CameraDetectionMqttHandler.java` (ADIM 1 → ADIM 2 sırası), `ParkOccupancyManager.java:109` (`messagingTemplate.convertAndSend(...)`, Kafka çağrısından önce, aynı senkron metod içinde).

## 3. PyTorch Inference → Slot State Engine

| Soru | Bulgu | Kanıt |
|---|---|---|
| Ham tahmin / onaylı state ayrı mı? | Evet. `engine.predict_batch` (ham) → `pipeline._process_frame` (hibrit düzeltme) → `state_store.update_batch` (hysteresis → onaylı). `SlotState.raw_status` ile `SlotState.status` ayrı alanlar. | `inference/engine.py:140-164`, `inference/pipeline.py:290-338`, `services/state_store.py:298-317` |
| Confidence threshold var mı? | Evet, **0.7** (config'den). Eşik altında doğrudan `UNKNOWN`. | `inference/engine.py:33,43,155-156`, `config/default.yaml:12`, `config/cam1.yaml:12` |
| Zamansal doğrulama / hysteresis? | Evet. `confirm_seconds=4.0` (varsayılan); bir önceki onaylı durum `UNKNOWN` ise çıkışta 2× (8s) uygulanıyor. | `services/state_store.py:155-184` |
| Inference sıklığı ≠ event sıklığı mı? | **Kısmen.** CNN inference, frame_interval ile birebir aynı hızda çalışıyor (kendi ayrı throttle'ı yok). Araç dedektörü ayrı bir interval'a sahip (varsayılan 3 karede bir). MQTT/event üretimi ise yalnızca onaylı DEĞİŞİKLİKTE tetikleniyor — bu ayrım StateStore seviyesinde var, inference seviyesinde yok. | `inference/pipeline.py:258-274,309-326` |
| "Araç tespit edilemedi" → otomatik EMPTY mi? | Hayır, kavram hatalı — CNN her slotu ikili (empty/occupied) sınıflandırıyor, "tespit yoksa" diye bir dal yok. Ayrı Faster R-CNN dedektörü ise sadece POZİTİF yönde (araç varsa "occupied" teyidi) kullanılıyor; "araç yok" demesi CNN'in "empty" kararını zorlamıyor — bu kasıtlı (negatif/suppression dalı denenip kaldırılmış, yanlış boşaltmalara yol açtığı için). | `inference/vehicle_detector.py:39-47` (docstring), `inference/pipeline.py:330-336` |
| UNKNOWN destekleniyor mu? | Evet, iki bağımsız kaynaktan: (1) model confidence eşiği altında, (2) StateStore'da toplu-engellenme (occlusion) şüphesi. | `inference/engine.py:155-156`, `services/state_store.py:241-265,298` |
| Kamera kararması/occlusion/düşük kalite? | Occlusion'a özel mantık var: 3+ slot aynı anda aynı yöne geçerse (ve fiziksel olarak yakınsa) `UNKNOWN`'a çekiliyor, en fazla 25s. Genel görüntü kalitesi/karanlık için ayrı bir kontrol **yok** — düşük kalite sadece confidence'ı düşürerek dolaylı UNKNOWN'a yol açabilir. | `services/state_store.py:113-153,241-265` |
| Gereksiz tekrarlanan event önleniyor mu? | Evet, iki seviyede: StateStore sadece `changed=True` olanı bildiriyor; `MqttPublisher` ayrıca tam payload bir öncekiyle aynıysa publish'i atlıyor. | `services/state_store.py:206-207,319-325`, `services/mqtt_publisher.py:100-103` |
| Kamera bağlantısı koparsa? | **Reconnect/retry yok** (`capture/rtsp.py`, `capture/file.py`). `frame is None` olursa `_process_frame` erken `return` eder, `update_batch` hiç çağrılmaz → state **donuk kalır**, otomatik `UNKNOWN`'a düşmez, downstream'e "stale" sinyali gitmez. `is_open()` tanımlı ama hiç çağrılmıyor. | `inference/pipeline.py:279-282`, `capture/rtsp.py` |
| Model/pipeline exception'ı? | Dış `try/except` tüm kareyi atlıyor — `update_batch` çağrılmıyor, yanlış bir EMPTY/OCCUPIED event üretilmiyor, ama sadece loglanıyor (sessiz). | `inference/pipeline.py:258-266` |
| Restart sonrası state? | `StateStore` her zaman boş başlıyor (kalıcı kaynak yok). İlk gözlem hiç hysteresis beklemeden **anında** onaylanıyor ("ilk gözlem: hemen kabul et"). Restart ile ilk kare arasında API/MQTT `total_slots=0` döndürüyor (eski/yanlış veri göstermiyor, sadece boş). | `services/state_store.py:99,161-164`, `__main__.py` |

## 4. SQLite / Outbox Durumu — Kullanıcı Hipotezinin Doğrulanması

**Kullanıcının başlangıç hipotezi:** "Edge tarafında SQLite kullanılıyor, slot durumları ve Outbox kayıtları SQLite'ta tutuluyor, Kafka erişilemediğinde yerel veri korunuyor."

**Doğrulanan gerçek:** Bu hipotez **yanlış**. `park_slot` reposunda `sqlite` veya `outbox` geçen **hiçbir** Python dosyası yok (repo geneli grep ile doğrulandı). `StateStore` tamamen bellek içi bir `dict`. `MqttPublisher.publish()` best-effort'tur: publish başarısız olursa sadece loglanır, hiçbir kalıcı kuyruğa yazılmaz, retry mekanizması yoktur.

**Aktif bir hata da var:** `mqtt_publisher.py:100-103` içinde `_last_payload` dedup değişkeni, gerçek `client.publish()` çağrısından **önce** güncelleniyor:
```python
with self._lock:
    if payload_slots == self._last_payload:
        return
    self._last_payload = payload_slots   # ← publish denenmeden önce "gönderildi" varsayılıyor
...
try:
    self._client.publish(topic, body, qos=self._settings.qos)
except Exception as e:
    logger.error(...)
```
paho-mqtt'nin `publish()` çağrısı QoS>0'da bile ağ hatasında genelde exception fırlatmaz (başarısız bir dönüş kodu üretir), yani bu `except` bloğu çoğu gerçek ağ hatasını yakalamaz. Sonuç: bir publish gerçekten gitmese bile `_last_payload` zaten güncellendiği için **o slot durumu bir daha hiç yeniden gönderilmez** — sadece sonraki farklı bir değişiklik olursa fark edilir, aksi halde sessizce kaybolur. Ve backend DB'si **yalnızca** bu MQTT mesajıyla güncellendiği için (bkz. §5), bu kayıp sadece Kafka'yı değil, gerçek doluluk durumunun tamamını (DB + WebSocket + Kafka) etkiliyor.

> **Güncelleme (2026-11-24 doğrulama turu):** `config/default.yaml`'daki `mqtt.enabled` bayrağı `174d007` commit'iyle `false → true` çevrilmiş, `56f57ce` commit'iyle `broker_host` gerçek bir IP'ye (`192.168.1.88`) bağlanmış. `mqtt_publisher.py:67-70`'teki (artık kaldırılan) "şu an tüm kamera config'lerinde mqtt.enabled=false olduğu için bu kod yolu tetiklenmiyor" yorumu bu commit'lerden sonra **yanlış** hale gelmişti — yukarıdaki dedup bug'ı artık teorik bir risk değil, üretimde aktif. Bu tur içinde `mqtt_publisher.py`'de düzeltildi: `_last_payload` artık yalnızca `client.publish()` exception fırlatmadan tamamlandıktan **sonra** güncelleniyor (bkz. `IMPLEMENTATION_ROADMAP.md` Aşama 0).

## 5. Kafka Producer/Consumer Altyapısı

| Konu | Bulgu | Kanıt |
|---|---|---|
| Avro + Schema Registry | Gerçekten runtime'da aktif — ayrı bir `confluentinc/cp-schema-registry:7.4.0` container'ı var, `KafkaAvroSerializer`/`KafkaAvroDeserializer` + `specific.avro.reader=true` kullanılıyor (sadece build-time codegen değil). | `docker-compose.yaml:65-75`, `KafkaConfig.java` |
| Producer config çelişkisi | `application.yml`: `acks=1` ("tek broker" yorumuyla) — ama **gerçekte kullanılan** bean `KafkaConfig.producerFactory()`: `acks=all, retries=5, enable.idempotence=true, max.in.flight.requests.per.connection=1`. yml ayarı etkisiz/yanıltıcı. Ayrıca `KafkaProducerConfig.java` içinde hiç inject edilmeyen, birebir duplike bir `producerConfigs()` bean'i daha var (ölü kod). | `application.yml`, `KafkaConfig.java`, `KafkaProducerConfig.java` |
| Topic versiyonlama | `selfpark.mqtt.cameraDetection.v0` → `v1` — `slots` alanının `map<boolean>`'dan `map<string>`'e (EMPTY/OCCUPIED/UNKNOWN) geçişi Avro-uyumsuz bir değişiklik olduğu için yeni topic açılmış. Doğru refleks, ama geçiş/eski-tüketici planı yok. | `git diff` (TopicKey.java, application.yml, CameraDetectionEvent.avsc) |
| Partition key | `cameraCode` (parkInfoId değil). Bugün zararsız çünkü hiç tüketen yok (aşağıya bkz.), ama park-bazlı sıralı okuma isteyecek gelecekteki bir consumer için kısıtlayıcı. | `CameraDetectionEventPublisherService.java`, `CameraDetectionMqttHandler.java` |
| **Topic tüketiliyor mu?** | **HAYIR.** `KafkaTopicRegistry.getConsumableTopicNames()` `STREAM` kategorisini bilerek dışlıyor (yorum: "processed by Kafka Streams" — ama bu kamera topic'i için hiçbir Streams topolojisi de yok). `CentralEventConsumer`'ın `@KafkaListener`'ı bu listeyi dinliyor. `selfpark.mqtt.cameraDetection.v1`/`SELFPARK_CAMERA_DETECTION` adı kod genelinde sadece 3 dosyada geçiyor — hepsi publish tarafında (`TopicKey.java`, `CameraDetectionMqttHandler.java`, `CameraDetectionEventPublisherService.java`). **Kafka'ya giden her başarılı kamera event'i kalıcı duruyor ama hiçbir iş mantığı/stream/consumer tarafından okunmuyor — şu an "write-only arşiv".** | `KafkaTopicRegistry.java:169-178`, repo geneli grep |
| DLQ | **Gerçek, çalışan mekanizma.** `DLQEvent` (JPA/Postgres) + durum makinesi (RECEIVED→...→REPROCESSED/ARCHIVED). `DLQReprocessingService` 30 saniyede bir (`@Scheduled`) FIXED/IGNORED durumundaki event'leri otomatik yeniden dener (max 100/batch, max 5 deneme), manuel REST reprocess de var. `DLQConsumer` (`@KafkaListener` tüm `dlq.*` topic'ler üzerinde, STREAM kategorisi dahil) kamera DLQ'sunu da gerçekten tüketiyor — yani **DLQ yolu validasyon hataları için uçtan uca çalışır durumda**, normal akış için değil. | `DLQEventRepository`, `DLQReprocessingService`, `DLQConsumer` |
| 3 bağımsız DLQ üretim yolu | (1) `CameraDetectionMqttHandler.routeToDlq()` — manuel, MQTT validasyon hatası; (2) `CentralEventConsumer.routeToDLQ()` — manuel, consumer-side hata; (3) Spring Kafka'nın framework-seviyesi `DefaultErrorHandler`+`DeadLetterPublishingRecoverer` (exponential backoff 100ms→1s, max 5s) — yakalanmamış `@KafkaListener` exception'ları için. Üçü de aynı `dlq.{topic}` adlandırmasına yazıyor, birbirinden bağımsız/duplike kod yolları. | `KafkaConfig.java:116-142`, handler kodları |
| Kafka broker | Tek node KRaft (`cp-kafka:7.4.0`), aynı node hem broker hem controller, quorum=1 oy, `offsets.topic.replication.factor=1` — replication factor her yerde zorunlu olarak 1. Gerçek SPOF. `acks=all` burada ek replica olmadığı için pratikte `acks=1` ile aynı dayanıklılığı sağlıyor. | `docker-compose.yaml` |

## 6. anomaly_detection Modülü ile İlişki

Kamera doluluğuyla **bağlantısız**. Tek park-ilişkili sınıf `ParkingAnomalyDetector` (`streams/real_time/`), girdisi `selfpark.event.parkEntry.v0`/`parkExit.v0` (bariyer/turnike event'leri), kamera topic'i değil. `grep -rn "cameraDetection" src/main/java/com/selftech/anomaly_detection/` → **0 sonuç**. `AnomalyDetectionStream.java` boş bir stub (`// Kafka Streams ile anomali tespiti mantığı` yorumundan ibaret). Modülün geri kalanı tamamen smartlock/sensör/ödeme odaklı.

## 7. MQTT Altyapısı (Domain-Bağımsız Katman)

| Konu | Bulgu | Kanıt |
|---|---|---|
| Dispatch | `MqttMessageDispatcher`, `List<MqttTopicHandler>` üzerinde lineer tarama, ilk `canHandle()==true` olanı çağırıp durur. Handler exception'ı sadece loglanır. | `MqttMessageDispatcher.java:28-46` |
| Broker bağlantısı | `DefaultMqttPahoClientFactory`, `cleanSession=true`, **`setAutomaticReconnect` çağrılmamış** (Paho default `false` — otomatik reconnect konfigüre değil). `adapter.setQos(1)`, `mqttInputChannel` senkron `DirectChannel` (buffer/executor yok — yük altında backpressure/thread-starvation riski). | `MqttConnectionConfig.java:42-66` |
| Topic | `selfpark/#` — tek wildcard adapter, sadece kamera değil tüm `selfpark/` alt topic'lerini dinliyor. | `application.yml:323-328` |
| `kafkaFailureChannel` | Tanımlı ve bir `@ServiceActivator`'a bağlı (sadece log) ama **hiçbir handler kullanmıyor** — ölü kod. `CameraDetectionMqttHandler` kendi try/catch'inde yutup logluyor. | `MqttConnectionConfig.java:68-77`, repo geneli grep |

## 8. ROI / Slot Identity Mapping

| Soru | Bulgu | Kanıt |
|---|---|---|
| ROI index = kalıcı slot id mi? | Evet, **tek ve aynı kimlik**. `SlotDefinition.id: int` — ayrı bir "roiId" kavramı yok. id'ler ardışık değil (`config/slots/cam2.json`: `[1,2,3,4,5,11,...]`, silinenlerden boşluk kalıyor) — bu da id'nin kalıcı bir kimlik olarak tasarlandığını doğruluyor. | `annotation/schema.py:22`, `config/slots/cam2.json` |
| ROI koordinatları nerede? | 3 ayrı yerde, 3 farklı rolde: (1) `config/slots/cam*.json` — tam poligon, sadece edge'de, backend'e hiç gitmiyor; (2) backend `SlotStateDTO.x/y` — sadece normalize kuşbakışı minimap koordinatı; (3) `perspective_transform` — sadece edge'de. | `register_slot_layout.py`, `SlotStateDTO.java`, `annotation/schema.py:59` |
| Senkron var mı? | **Manuel, tek yönlü (edge→backend).** `tools/register_slot_layout.py` admin JWT ile edge'in minimap x/y'sini PUT eder. Ters yön (backend→edge) hiç yok. Otomatik tetikleyici (dosya izleme, CI hook) yok. | `register_slot_layout.py` docstring |
| GUI değişikliği nasıl yayılıyor? | GUI → dosya: elle 's' tuşuyla kaydetme. Dosya → edge process: hot-reload yok, restart gerekiyor (`SlotManager` config'i sadece process başlangıcında okuyor). Dosya → backend: tamamen ayrı, elle çalıştırılan `register_slot_layout.py` adımı. | `annotation/gui.py`, `__main__.py` |
| **Kamera değişince slot kimliği korunuyor mu?** | **Hayır.** Backend key'i `{cameraCode}-{slotId}`. CAM-001→CAM-004 değişiminde aynı fiziksel slot `CAM-001-1` yerine `CAM-004-1` olur; backend'de (`ParkSlotOccupancy.slots` Map) bu tamamen yeni bir anahtar, eski `CAM-001-1` kaydı güncellenmeyi durdurur ve **sonsuza dek orphan kalır** (temizleme kodu yok). | `mqtt_publisher.py:15`, `ParkSlotOccupancy.java` |
| ROI yeniden çizilirse mevcut state etkilenir mi? | Process restart edilmeden dosya değişikliği hiçbir etki yapmaz. Restart edilince `StateStore` sıfırdan başlar (bellek içi) — geçmiş kaybolur, sadece gelecekteki tahminler etkilenir. Backend'deki kayıt geçmişe dönük değişmez; id silinip yeniden numaralandırılırsa eski backend key'i orphan kalır. | `__main__.py:114`, `state_store.py:99` |
| Birden fazla kamera aynı slotu izleyebilir mi? | Teknik olarak evet (`ParkingCamera.parkInfo` `@ManyToOne`, kısıtlama yok) — bu yüzden prefix şeması var. Ama kod, iki kameranın gerçekten aynı fiziksel alanı gösterip göstermediğini tespit/önleyen hiçbir mekanizma içermiyor; tamamen operasyonel/insan kararı. | `ParkingCamera.java:51-55` |
| Versiyon alanı var mı? | **Yok** — `config/slots/cam*.json` şemasında (`camera_id, source_url, image_width, image_height, slots, perspective_transform`) versiyon/zaman damgası alanı yok. | `config/slots/cam1.json` |

> **Güncelleme (2026-11-24 doğrulama turu):** Backend'de `24ab3ad` commit'i (`feat(camera,kafka): kamera bazlı park doluluk özelliği`) `ParkSlotOccupancy`'yi `parkInfoId` bazlı tek satıra taşımış ve `lastCameraId`/`lastCameraCode` alanları eklemiş; entity yorumu artık slot map key'inin "bare slot index ('1'..'capacity')" olması **niyetini** belirtiyor (`ParkSlotOccupancy.java:45-51`). **Ancak** `ParkOccupancyManager.processDetection:68`teki `slotIndex = entry.getKey()` satırı, gelen MQTT payload'ındaki anahtarı hiçbir normalizasyon/prefix-stripping yapmadan doğrudan kullanıyor; edge tarafı (`mqtt_publisher.py:96`, `tools/register_slot_layout.py:53`) hâlâ `{camera_code}-{slot_id}` formatlı prefixli key gönderiyor. Yani **tasarım niyeti değişmiş, gerçek çalışma zamanı davranışı değişmemiş** — kamera kodu değişince eski key hâlâ orphan kalıyor (bkz. `ARCHITECTURE_GAP_ANALYSIS.md` madde 4, `ADR_DOCUMENTS.md` ADR-002 status update).
>
> **Güncelleme (2026-11-27): Bu bölüm artık tarihsel — o dönemki (Aşama 3
> öncesi) gerçeği kaydediyor, güncel davranışı YANSITMIYOR.** `ParkSlotOccupancy`
> ve ham `{cameraCode}-{slotId}` anahtarı tamamen kaldırıldı (Aşama 7, PR c —
> `park_slot_occupancy` tablosu `DROP` edildi). Slot kimliği artık kalıcı
> `parkSlotId` (`camera_slot_identity` + `SlotIdentityResolver`, Aşama 3,
> ADR-002) — kamera değişince (operatör `explicitParkSlotId` carryover'ını
> kullandığı sürece) kimlik korunur. Carryover kullanılmazsa oluşan orphan
> satırlar artık günlük `OrphanCameraSlotCleanupScheduler` tarafından
> otomatik temizleniyor (selfpark-backend PR #7, `2698ac0`). Detay:
> `ADR_DOCUMENTS.md` ADR-002 status update (2026-11-27),
> `ARCHITECTURE_GAP_ANALYSIS.md` madde 4.

## 9. Edge Local WebSocket (`api/ws.py`)

`/ws/live` endpoint'i var; `StateStore.on_change` callback zincirine **`MqttPublisher`'dan bağımsız, paralel** olarak abone (ikisi birbirinden habersiz). `full_status` (bağlantıda) ve `slot_update` (değişimde) event'leri yayınlıyor. Tüketiciler: `/` (minimap), `/live` (operatör override paneli), `/mobile` (salt-okunur kamera özeti) — hepsi sahadaki tarayıcı istemcileri, Kafka/backend'le ilgisi yok. Manuel override, WS üzerinden değil REST (`POST`/`DELETE /api/v1/slots/{id}/override`) üzerinden tetikleniyor.
Kanıt: `api/ws.py:76-120`, `api/app.py:605-625`, `api/routes.py:194-223`.

## 10. FREE→OCCUPIED Geçişinin Temsili (kullanıcının özellikle sorduğu nokta)

Event'in kendisinde **temsil edilmiyor** — Avro şeması sadece "şu an hangi slot hangi durumda" taşıyor, "hangi slot neden/nasıl değişti" bilgisini taşımıyor. Geçiş tespiti yalnızca backend'de, Kafka'dan tamamen bağımsız, `ParkOccupancyManager.processDetection` içinde önceki DB kaydıyla diff alınarak yapılıyor:
```java
if (previousStatus == OCCUPIED && newStatus == EMPTY) becameAvailable = true;
```
Bunun sonucu asimetrik: **OCCUPIED→EMPTY** (`becameAvailable`) bir hedefe gidiyor ama o hedef (`notifyInterestedUsers`) şu an boş bir TODO stub. **EMPTY→OCCUPIED** (FREE→OCCUPIED, kullanıcının sorduğu yön) için **hiçbir sinyal/bildirim üretilmiyor** — ne event'e işleniyor, ne log dışında bir yere gidiyor.
Kanıt: `ParkOccupancyManager.java:76-78,168-171`.

## 11. Veri Sahipliği (Mevcut Durum — "Olması Gereken" Değil)

- **Edge `StateStore`**: tek onaylı-state hesaplayıcısı, ama kalıcı değil (restart = sıfırlanır).
- **Backend `ParkSlotOccupancy`**: edge'den gelen son MQTT mesajının DB'deki izdüşümü; kendi başına hiçbir doğrulama/hysteresis yapmıyor, sadece gelen değeri yazıyor.
- **Kafka**: şu an sadece bir kayıt (audit) kanalı — hiçbir okuyucusu olmadığı için fiilen "sahiplik" taşımıyor.
- **WebSocket (merkezi)**: Kafka'dan değil, doğrudan MQTT handler'ın DB-yazım adımından besleniyor — tek backend instance'ına bağımlı.

## UNKNOWN / NEEDS VERIFICATION

- RTSP/OpenCV'nin gerçek ağ kopmasında `cv2.VideoCapture` davranışı (runtime/saha testi gerekir).
- Paho/Spring Integration adapter'ının broker koptuğunda gerçek reconnect davranışı (kod `automaticReconnect=false` gösteriyor, adapter'ın iç recovery'si doğrulanmadı).
- `KafkaStreamsConfig`'teki `Serdes.ByteArray()` ile `application.yml`'deki `JsonSerde` arasında hangisinin runtime'da öncelikli olduğu.
- `YouTubeSource`'un kalıcı kopmada pipeline'ı tamamen durdurup durdurmadığı (bu tur RTSP/File ile sınırlı tutuldu).
- Toplu-engellenme (occlusion) tespitinin production'da gerçek false-positive oranı (log/saha verisi gerekir).
- Diğer MQTT handler'ların (örn. LockSensorMqttHandler) `kafkaFailureChannel`'ı kullanıp kullanmadığı (bu tur kapsam dışı bırakıldı).
- Çoklu kamera-aynı-slot senaryosunda backend'in herhangi bir çakışma/uyarı validasyonu üretip üretmediği (muhtemelen yok, ama `ParkOccupancyManager`/`CameraService`'in tam taranması gerekir).
