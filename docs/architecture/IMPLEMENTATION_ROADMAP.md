# IMPLEMENTATION_ROADMAP.md — Olası Geliştirme Sırası

> **Bu konuşmada hiçbir kod değişikliği yapılmadı.** Aşağıdaki aşamalar yalnızca gelecekte uygulanabilecek bir sıralama önerisidir, bağımsız ve doğrulanabilir tutulmuştur. Dayanak: [ARCHITECTURE_GAP_ANALYSIS.md](./ARCHITECTURE_GAP_ANALYSIS.md), [TARGET_ARCHITECTURE.md](./TARGET_ARCHITECTURE.md), [ADR_DOCUMENTS.md](./ADR_DOCUMENTS.md).

## Aşama 0 — Hızlı Düzeltme (bağımlılığı yok, en düşük risk)

> **Aciliyet güncellemesi (2026-11-24):** Edge'de MQTT artık `enabled=true` ve gerçek bir broker'a bağlı (commit `174d007`/`56f57ce`, bkz. CURRENT_STATE.md §4) — bu aşamadaki düzeltme artık teorik değil, üretimdeki aktif bir sessiz-veri-kaybı riskini kapatıyor. **Bu tur içinde uygulandı:** `src/park_slot/services/mqtt_publisher.py`'de `_last_payload` artık yalnızca `client.publish()` exception fırlatmadan tamamlandıktan sonra güncelleniyor; `tests/unit/test_mqtt_publisher.py::test_failed_publish_is_retried_on_next_call` bu davranışı doğruluyor (8/8 test geçiyor).

**Hedef:** Edge'deki aktif sessiz-veri-kaybı hatasını kapatmak.
**Bağımlılıklar:** Yok.
**Değişmesi beklenen modüller:** `src/park_slot/services/mqtt_publisher.py` (sadece `_last_payload` güncelleme sırası).
**Test kriterleri:** `tests/unit/test_mqtt_publisher.py`'ye, publish exception/başarısızlık fırlattığında `_last_payload`'ın güncellenmediğini ve bir sonraki çağrıda aynı payload'ın tekrar denendiğini doğrulayan bir test eklenir.
**Kabul kriterleri:** Başarısız bir publish denemesinden sonra, aynı state bir sonraki `publish()` çağrısında tekrar gönderilmeye çalışılıyor (dedup'a takılmıyor).
**Risk:** Çok düşük — tek metodun iç sırası değişiyor, dış davranış sözleşmesi (topic, payload formatı) aynı kalıyor.

---

## Aşama 1 — Edge SQLite Outbox (ADR-004)

**Hedef:** Edge→backend tek kanalın (MQTT) güvenilirliğini, process restart ve geçici broker kesintilerine dayanıklı hale getirmek.
**Bağımlılıklar:** Aşama 0 (aynı dosyaları etkiliyor, çakışmayı önlemek için sırayla).
**Değişmesi beklenen modüller:** Yeni `src/park_slot/services/outbox.py` (SQLite erişimi + poller thread), `services/state_store.py` (`_notify` çağrısına outbox insert ekleme — DİKKAT: StateStore'un mevcut saf bellek-içi/thread-safe tasarımına SQLite I/O eklemek, `_notify` çağrısının kilit altında mı dışında mı yapılacağına dikkat gerektirir), `services/mqtt_publisher.py` (artık outbox poller tarafından çağrılır, doğrudan `_notify`'dan değil), `__main__.py` (poller thread'in başlatılması).
**Test kriterleri:** Outbox insert + slot_state update'in tek transaction'da atomik olduğunu doğrulayan test (örn. insert ortasında exception simüle edip rollback sonrası her iki tablonun da değişmediğini kontrol etmek); poller'ın `sent_at IS NULL` kayıtları doğru sırada gönderdiğini doğrulayan test; process "restart" simülasyonunda (yeni bir `Outbox` instance'ı aynı dosyayı açarak) gönderilmemiş kayıtların kaybolmadığını doğrulayan test.
**Kabul kriterleri:** MQTT broker'a erişilemezken üretilen state değişiklikleri, broker tekrar erişilebilir olduğunda (yeniden process başlatmadan VEYA restart sonrasında) otomatik olarak gönderiliyor; hiçbir manuel müdahale gerekmiyor.
**Risk:** Orta — `StateStore`'un mevcut thread-safety modeline (zaten bir `threading.Lock` var) yeni bir I/O adımı eklemek, kilit altında disk yazımı yaparsa gecikmeye (lock contention) yol açabilir; poller'ı tamamen ayrı bir thread'de, `_notify`'ı yalnızca hafif bir "yeni outbox satırı var" sinyaliyle tutmak önerilir.

---

## Aşama 2 — Delta/Snapshot Avro Şemaları + Backend Kafka Consumer (ADR-001, ADR-005, ADR-006)

> **Durum (2026-11-25): Tamamlandı ve prod'da doğrulandı.**
> - **2a (ADR-006):** `CameraDetectionMqttHandler` publish-only hale getirildi, yeni `CameraSlotChangeConsumer` (`EventHandlerRegistry`'ye kayıtlı, `@KafkaListener` değil — mevcut `CentralEventConsumer`/`EventHandler<T>` mekanizması kullanıldı) WS broadcast'i üstlendi. selfpark-backend commit `7c2a6bb`.
> - **ADR-001 (delta/snapshot):** Yeni `CameraSlotChangedEvent.avsc` + `selfpark.mqtt.cameraSlotChanged.v1` topic'i — normal akışın tamamı burada (tek slot). `CameraDetectionEvent`/`.../detection` SADECE edge restart/(yeniden) bağlantı snapshot'ı için. Partition key `parkInfoId:slotId` (ADR-005 tam uygulandı). Backend commit `091f84f`, edge (park_slot) commit `d32b196`.
> - **Prod'da bulunup düzeltilen bug (backend commit `7d2776d`):** `.avsc`'deki `"interfaces": [...Event]` alanı no-op (standart Avro syntax'ı değil) — yeni event tipi için `EventAdapterFactory`'de elle adapter eklenmemişti, mesajlar sonsuz retry'a giriyordu (DLQ'ya bile düşmüyordu, veri kaybı yoktu çünkü offset hiç commit edilmemişti). `EventAdapterFactoryTest` eklendi (bu sınıf daha önce hiç test kapsamında değildi).
> - **2026-11-25 ek iyileştirmeler (edge, commit `ae737ae`):** (1) `StateStore.confirm_seconds` (debounce penceresi) artık `config/*.yaml`'dan ayarlanabiliyor, varsayılan 4s→10s; (2) `MqttPublisher.enable_startup_snapshot` ile tam snapshot artık MQTT `on_connect`'e bağlı — eski tek seferlik `publish_raw` çağrısının `connect_async` ile yarışıp snapshot'ı sessizce kaybetme riski giderildi, ayrıca her yeniden bağlanmada (broker restart/ağ kesintisi) otomatik resync sağlanıyor (bu bir periyodik timer DEĞİL, sadece bağlantı olayına tepki — Aşama 4'ün kapsamına girmiyor).
> - **Deploy + doğrulama tamamlandı (2026-11-25):** selfpark-backend `7d2776d` ile, park_slot `ae737ae` ile (CI→ghcr→ArgoCD auto-sync) prod'a dağıtıldı. `selfpark.mqtt.cameraSlotChanged.v1` ve `selfpark.mqtt.cameraDetection.v1` için `kafka-consumer-groups --describe --group selfpark-group` ile **LAG=0** doğrulandı; `cameraDetection.v1`'de ilk kez veri oluştu (3 mesaj, kameralardan birer snapshot). Aşama 2'nin kabul kriteri karşılandı.
> - **Bilerek kapsam dışı bırakıldı:** Periyodik/timer tabanlı reconciliation snapshot'ı (ADR-008) — Aşama 4'e not düşüldü.

**Hedef:** `selfpark.mqtt.cameraDetection.v1`'in "publish-only" durumunu sonlandırmak, Kafka'yı fiilen dağıtım omurgasına dönüştürmek.
**Bağımlılıklar:** Aşama 1 (edge'in yeni delta event'i üretebilmesi için outbox altyapısı hazır olmalı) — ancak backend tarafı (yeni consumer) Aşama 1'den bağımsız olarak, mevcut `CameraDetectionEvent` ile de test edilebilir (önce consumer eklenip mevcut event formatıyla doğrulanabilir, sonra edge yeni formata geçer). Bu nedenle 2a (backend consumer) ve 2b (edge delta formatı) paralel/bağımsız yürütülebilir.
**Değişmesi beklenen modüller:**
- Backend: yeni `CameraSlotChanged.avsc`, `CameraSlotSnapshot.avsc` (veya mevcut `CameraDetectionEvent.avsc`'nin yanına eklenir), yeni Kafka topic tanımları (`application.yml`, `TopicKey.java` — mevcut v0→v1 deseniyle tutarlı), yeni `CameraSlotChangeConsumer` (`@KafkaListener`), `CameraDetectionMqttHandler`'ın WS-broadcast adımının bu consumer'a taşınması, partition key üretiminin `cameraCode`'dan `parkInfoId:slotId`'ye değişmesi.
- Edge: `services/state_store.py` veya `outbox.py`'de delta formatına uygun payload üretimi, periyodik snapshot tetikleyicisi.
**Test kriterleri:** Backend tarafında `CameraSlotChangeConsumer`'ın bir delta event'i aldığında `ParkOccupancyManager`'ın mevcut uygulama mantığını doğru çağırdığını doğrulayan birim test (mevcut `ParkOccupancyManagerTest.java` deseni genişletilir); tek instance'ta WS broadcast'in hâlâ tek bir kez tetiklendiğini doğrulayan test (mevcut `processDetection_occupiedToEmpty_pushesOnce` testine benzer).
**Kabul kriterleri:** Bir kamera detection mesajı MQTT'den geldiğinde, DB güncellemesi ve WS broadcast'i artık Kafka consumer üzerinden gerçekleşiyor (handler artık sadece publish yapıyor); Kafka topic'i tüketiliyor olarak doğrulanabiliyor (`kafka-ui`'de consumer group lag'i ilerliyor).
**Risk:** Orta-yüksek — bu aşama kritik yola Kafka'yı sokuyor (ADR-006 trade-off'u); staging ortamında Kafka gecikmesinin kullanıcı tarafından fark edilebilir olup olmadığı ölçülmeli.

---

## Aşama 3 — Slot Kimliği Normalizasyonu (ADR-002)

> **Durum (2026-11-25): Tamamlandı ve doğrulandı.**
> İnceleme, roadmap'in orijinal varsayımını düzeltti: `{cameraCode}-{slotId}`
> birleşimini backend değil **edge** üretiyormuş (`mqtt_publisher.py`); backend
> bu string'i hiç parçalamadan doğrudan map key'i olarak kullanıyormuş. Bu
> sayede çözüm tamamen backend-only oldu — `park_slot` (edge) reposunda
> hiçbir değişiklik gerekmedi, aşağıdaki "Değişmesi beklenen modüller"
> listesindeki edge kısmı (`tools/register_slot_layout.py`) atlandı.
> Yeni `camera_slot_identity` tablosu + `SlotIdentityResolver`
> (camera_id, raw_slot_key) → kalıcı `parkSlotId` eşlemesini auto-mint ile
> kurar; `SlotLayoutEntry`'ye eklenen opsiyonel `parkSlotId` alanı, kamera
> değişiminde operatörün eski kimliği devralmasını (carryover) sağlar.
> Mevcut prod verisi ayrı bir migrasyon script'i olmadan, her slot bir
> sonraki event'inde kendiliğinden (lazy) yeni formata taşınıyor. selfpark-backend
> commit `88ba948`. Testler: 13 mevcut `ParkOccupancyManagerTest` değişmeden
> geçiyor + 4 yeni senaryo + yeni `SlotIdentityResolverTest` (4 test) = 86 test yeşil.
> Backend bu projede ArgoCD/k8s değil doğrudan docker-compose ile çalıştığı için
> deploy = `docker compose up -d --build selfpark-backend`; bu yapıldı, container
> sağlıklı (`/actuator/health` → `UP`, DB bağlantısı doğrulandı) ve `camera_slot_identity`
> tablosu hatasız oluştu. `GET /api/v1/parkOccupancy/{parkInfoId}` canlı çıktısında
> doğrulandı: yeni event alan slotlar kalıcı UUID `parkSlotId` key'ine geçiyor, eski
> ham `{cameraCode}-{slotId}` key'i siliniyor (çift sayım yok), henüz dokunulmamış
> slotlar eski formatta kalıp sıradaki event'te kendiliğinden migrate oluyor.

**Hedef:** Kamera değişiminde slot kimliğinin kopmasını önlemek, orphan key birikimini durdurmak.
**Bağımlılıklar:** Aşama 2 (yeni event formatı slot kimliğini taşıyacağı için, kimlik modeli değişmeden önce event şemasının oturmuş olması mantıklı — ama teknik olarak bağımsız da yürütülebilir, sıralama önerisidir, zorunluluk değildir).
**Değişmesi beklenen modüller:** Backend `ParkSlotOccupancy`/`SlotStateDTO` anahtarlama stratejisi, `ParkOccupancyManager.processDetection`/`upsertSlotLayout`, `tools/register_slot_layout.py` (edge tarafı, kalıcı `parkSlotId` üretimi/kullanımı), ROI/slot config şemasına `version` alanı eklenmesi.
**Test kriterleri:** Kamera kodu değiştirildiğinde (aynı `parkSlotId` korunarak) mevcut slot geçmişinin (x/y/label, durum) kaybolmadığını doğrulayan test; eski anahtarlama ile kayıtlı mevcut verinin migrasyon sonrası doğru okunabildiğini doğrulayan bir geriye-dönük-uyumluluk testi.
**Kabul kriterleri:** Bir kamera fiziksel olarak değiştirildiğinde (yeni `cameraCode`, aynı slotlar), backend'de orphan kayıt oluşmuyor, mevcut `ParkSlotOccupancy` kaydı güncelleniyor.
**Risk:** Orta — mevcut production verisinde zaten birikmiş olabilecek `{cameraCode}-{slotId}` formatlı kayıtlar için bir migrasyon/geri-uyumluluk stratejisi gerekir (bu roadmap kapsamında ayrıntılandırılmadı, ayrı bir migrasyon planı gerektirir).

---

## Aşama 4 — Reconciliation + Kamera Watchdog (ADR-008, GAP #5)

> **Durum (2026-11-25): Tamamlandı (edge-only).** Backend'de hiçbir değişiklik
> gerekmedi — `ParkOccupancyManager.processDetection` zaten merge-upsert
> (ADR-003'ün "edge kazanır" kararını her zaten koşulsuz yazıyordu, snapshot'ın
> daha sık gelmesi davranışı değiştirmiyor).
> 1. **Kamera watchdog:** `StateStore.mark_all_unknown()` — hysteresis'i
>    bypass ederek tüm (override'lı olmayan) slotları ANINDA UNKNOWN'a çeker;
>    `Pipeline._check_watchdog()`, `_loop`'un her turunda son başarılı kareden
>    (`_last_frame_at`) geçen süreyi `watchdog.stale_after_seconds`
>    (varsayılan 120s) ile karşılaştırır. Kamera kareye dönünce normal
>    `update_batch` akışı devreye girer (occlusion recovery ile aynı yol,
>    2x `confirm_seconds`).
> 2. **Periyodik snapshot:** `OutboxWorker.snapshot_once()` — `_run`
>    döngüsünde `outbox.snapshot_interval_seconds` (varsayılan 300s) her
>    geçtiğinde, delta kuyruğundan bağımsız tam `slot_state` snapshot'ı
>    gönderilir. ADR-008'in (a) bacağı (reconnect/restart, Aşama 2'de
>    `enable_startup_snapshot`/`on_connect`) zaten vardı; bu, (b) bacağını
>    (zaman tabanlı, bağlantı kopmasa da) tamamlıyor.
> 3. Yeni config: `settings.watchdog` (`enabled`, `stale_after_seconds`),
>    `settings.outbox.snapshot_interval_seconds`.
> 4. Testler: 11 yeni test (`StateStore.mark_all_unknown` x4,
>    `Pipeline._check_watchdog` x3, `OutboxWorker.snapshot_once` x3,
>    toplamda +1 zamanlama düzeltmesi) — 123 test yeşil (tüm `tests/unit`).
> 5. **Deploy + prod doğrulaması (2026-11-25):** CI, commit `964a893`'ü
>    otomatik build edip ghcr'a push etti, k8s manifestlerini güncelleyen
>    deploy commit'i (`7986b3f`) ArgoCD tarafından sync edildi (3 pod da
>    yeni image'da, `Synced`/`Healthy`). cam1-3 loglarında hata/uyarı yok
>    (watchdog yanlış tetiklenmiyor). Kafka consumer group lag=0 (her iki
>    topic). **Periyodik snapshot canlıda doğrulandı:** pod başlangıcındaki
>    on-connect patlamasından (10:35:06) ayrı, delta kuyruğundan bağımsız
>    yeni snapshot mesajları tam ~300s sonra (10:39:51, CAM-002/CAM-003)
>    Kafka'ya düştü — `snapshot_interval_seconds` beklenen cadence'i
>    doğruluyor. Watchdog'un gerçek tetiklenmesi (kamera donması) canlı
>    trafiği bozmamak için prod'da zorlanmadı — birim testler + log
>    gözlemiyle (yanlış tetiklenme yok) doğrulandı.

**Hedef:** Uzun süreli offline/drift senaryolarını ve donuk-stale-state riskini kapatmak.
**Bağımlılıklar:** Aşama 1 (snapshot için outbox altyapısı), Aşama 2 (snapshot event formatı/topic'i).
**Değişmesi beklenen modüller:** Edge `outbox.py`'ye periyodik snapshot tetikleyici, `capture/rtsp.py`/`pipeline.py`'ye `last_frame_at` watchdog (eşik aşılırsa ilgili kameranın slotlarını `UNKNOWN`'a çekme), backend'de snapshot event'leri için consumer dalı.
**Test kriterleri:** Watchdog eşiği aşıldığında slotların `UNKNOWN`'a düştüğünü doğrulayan test; periyodik snapshot'ın backend projeksiyonunu drift varken düzelttiğini doğrulayan entegrasyon testi.
**Kabul kriterleri:** Kamera N dakika boyunca kare üretmezse, ilgili slotlar hem edge WS'de hem merkezi WS'de `UNKNOWN` olarak görünüyor; edge uzun süre offline kaldıktan sonra tekrar bağlandığında backend projeksiyonu bir sonraki snapshot ile düzeliyor.
**Risk:** Düşük-orta — watchdog eşiğinin yanlış kalibre edilmesi (çok agresif → gereksiz UNKNOWN flapping, çok gevşek → geç tespit) saha testiyle ayarlanmalı.

---

## Aşama 5 — Altyapı Temizliği (GAP #9, P2)

> **Durum (2026-11-25): Tamamlandı.** selfpark-backend commit `4d162d6`,
> push edildi. `MqttConnectionConfig`'ten hiçbir yerde referans edilmeyen
> (errorChannel binding'i yok) `kafkaFailureChannel`/`kafkaFailureHandler`
> bean'leri kaldırıldı. `KafkaProducerConfig.java` tamamen silindi — hiçbir
> yerden inject edilmeyen, `KafkaConfig.producerConfigs()` ile duplike bir
> `producerConfigs()` bean'i (gerçek üretimde kullanılan tek producer config
> `KafkaConfig.java`'daki özel `ProducerFactory`/`KafkaTemplate` bean'leri).
> `application.yml`'deki `spring.kafka.producer.acks: 1` satırı kaldırıldı —
> Spring Boot'un bu bloktan üreteceği producer bean'i hiç devreye girmiyordu
> (gerçek değer `acks=all`, `KafkaConfig.producerConfigs()`'te hardcoded).
> Davranış değişikliği yok, 86 test yeşil.

**Hedef:** Ölü kod ve çelişkili config'leri temizlemek (işlevsel etkisi yok, bakım kalitesi için).
**Bağımlılıklar:** Yok — herhangi bir aşamadan bağımsız, paralel yapılabilir.
**Değişmesi beklenen modüller:** `MqttConnectionConfig.java` (`kafkaFailureChannel`'ı kaldır veya gerçekten kullan), `KafkaProducerConfig.java` (duplike bean'i kaldır), `application.yml` (`acks=1` satırını gerçek değerle senkronla veya kaldır).
**Test kriterleri:** Mevcut Kafka producer/consumer testlerinin (varsa) hâlâ geçtiğini doğrulamak — davranış değişmemeli, sadece ölü kod kalkıyor.
**Kabul kriterleri:** Build temiz, davranış değişikliği yok, config dosyalarında tutarsızlık kalmıyor.
**Risk:** Çok düşük.

---

## Aşama 6 — Roadmap'e girmemiş kalan Gap'ler (GAP #6 yarısı, #7, #10)

> **Durum (2026-11-25): Tamamlandı.** 2026-11-25'teki tam denetimde, Aşama 0-5'in
> `ARCHITECTURE_GAP_ANALYSIS.md`'deki 11 maddenin 3'ünü (6'nın yarısı, 7, 10) hiç
> kapsamadığı fark edildi — GAP #8 (Kafka RF≥2) ve #11 (anomaly_detection) kullanıcı
> kararıyla bilerek ayrı/sonraki faz olarak bırakılmıştı, ama 6/7/10 hiç gündeme
> gelmemişti. Bu aşama o üçünü kapatıyor:
> - **GAP #6 (yarısı, selfpark-backend commit `1601329`):** `upsertSlotStatus`
>   artık `SlotTransition(becameAvailable, becameOccupied)` döndürüyor;
>   EMPTY→OCCUPIED geçişi `notifyInterestedUsers`'ın simetriği
>   `notifyOccupancyFilled`'ı tetikliyor (aynı TODO-stub konvansiyonu - henüz
>   hedefleme/abonelik kuralı yok, sadece çağrı noktası var). 3 yeni test.
> - **GAP #7 (selfpark-backend commit `1601329`):** `MqttConnectOptions.setAutomaticReconnect(true)`
>   + `maxReconnectDelay=30s`. Kaynak kodu doğrulandı (spring-integration-mqtt 6.3.1):
>   `MqttPahoMessageDrivenChannelAdapter`'ın kendi periyodik reconnect zamanlayıcısı
>   yok, `connectionLost()` sadece loglayıp event yayınlıyor - bu ayar olmadan broker
>   kesintisinde bağlantı restart'a kadar KALICI kopuk kalıyordu. `connectComplete`
>   reconnect sonrası otomatik re-subscribe ettiği için ek değişiklik gerekmedi.
>   `mqttInputChannel`'ı `ExecutorChannel`'a çevirme kısmı bilerek YAPILMADI - kanıtlanmış
>   bir thread-starvation/backpressure sorunu yok, sıralama garantisini riske atardı.
> - **GAP #10 (park_slot commit — bu aşamada):** `CameraSlotConfig`'e `version`/`updated_at`
>   alanları eklendi; `annotation/gui.py` (`save_to_file`), `tools/mark_perspective.py`,
>   `tools/audit_slots.py --fix` - config dosyasını yazan 3 araç da artık her yazımda
>   `version`'ı artırıp `updated_at`'i güncelliyor. Sadece izlenebilirlik - `SlotManager`
>   bu alanları okumuyor, davranış değişmiyor. 2 yeni test (`test_gui.py`).
> - Backend: 89 test yeşil. park_slot: 125 test yeşil.

---

## Aşama 7 — Version/Sequence Bütünlüğü ve JSONB Lost-Update Düzeltmesi (GAP #12, #13)

> **Durum (2026-11-26): PLAN v3 — henüz uygulanmadı.** v1 (2026-11-25) →
> v2: `epoch`'un `uuid4()` olması ile backend'in sadece `seq`'e bakan örnek
> SQL'i arasındaki tutarsızlık düzeltildi (wall-clock epoch + tuple
> karşılaştırması). v2 → v3: ileri saat kayması, kamera sahiplik kontrolü
> (GAP #4 ile karıştırılmaması), legacy modda epoch/seq sıfırlama, enforce
> geçiş kriterinin somutlaştırılması, snapshot'ın seq üretmemesi, dual-write
> atomikliği ve tam test listesi eklendi. Bu aşama kod değişikliği
> İÇERMİYOR — sadece uygulama planı; onaylandıktan sonra PR (a)'dan
> başlanacak.

**Hedef:** Edge→backend zincirinde "hangi event daha yeni" sorusunun, restart'tan
ve outbox retention temizliğinden bağımsız, güvenilir bir cevabı olmasını
sağlamak; backend'de aynı parkın farklı slotlarına eşzamanlı yazımda
lost-update'i yapısal olarak ortadan kaldırmak. `replicas>1`/Kafka listener
`concurrency>1`'e geçmeden ÖNCE kapatılması gereken P0 önkoşul. GAP #14/#15
(WS fan-out, MQTT manualAcks/clientId) kasıtlı olarak bu planın DIŞINDA —
bkz. "Kapsam dışı — Aşama 8" bölümü.

**Bağımlılıklar:** Yok (bağımsız), ama Aşama 8 (P1 kapatma + replika
genişlemesi) için sert önkoşul.

**Kapsam / değişecek dosyalar:**

*Edge (park_slot):*
- `src/park_slot/services/outbox.py`: slot-version'ı artık `MAX(version)
  WHERE slot_id` (outbox geçmişinden, retention'la silinebilir) ile DEĞİL,
  kalıcı bir `(epoch, seq)` çiftinden hesaplar:
  - `epoch`: **process başlangıcında bir kez** üretilir, process'in tamamı
    için tek değer (slot başına değil) — bkz. "Epoch üretimi ve saat
    senkronu" bölümü.
  - `seq`: o `epoch` içinde **slot başına** monoton artan sayaç; `slot_state`
    tablosuna (zaten var, `outbox.py:35-40`, `purge_sent_older_than`'in asla
    dokunmadığı tek-satır/slot tablo) `epoch`/`seq` kolonları eklenerek
    tutulur — ayrı bir `slot_sequence` tablosu açmaya gerek yok, mevcut
    purge-muaf tablo bu iş için zaten doğru yerde.
  - Process-epoch'un kendisi de aynı veri dizininde tek satırlık bir
    `process_epoch` tablosunda/kaydında persist edilir (bkz. aşağıdaki
    epoch üretim kuralı).
- `src/park_slot/services/mqtt_publisher.py`: `publish_slot_changed` ve
  `publish_raw` payload'larına `epoch`/`seq` eklenir (snapshot dahil —
  opsiyonel/default'lu, geriye uyumlu).
- `k8s/cam1.yaml`, `cam2.yaml`, `cam3.yaml`: outbox veri dizini için
  `data-logs`/`labeled-data` ile AYNI desende yeni bir `hostPath` volume +
  `volumeMount` (örn. `/var/lib/park-slot/cam{N}/outbox` → `/app/data`) —
  Python kodu değil, salt deploy config değişikliği. Bu, `epoch`'un PVC
  üzerinden restart'lar arası okunabilmesinin ön koşulu.

*Backend (selfpark-backend):*
- `CameraSlotChangedEvent.avsc` / `CameraDetectionEvent.avsc`: yeni `epoch`
  alanı `["null","long"]` union tipiyle, `default: null` olarak eklenir
  (standart geriye-uyumlu Avro evrim deseni). `version` alanı zaten var,
  anlamı `seq`'e evrilir. CI'da Schema Registry uyumluluk kontrolü (`BACKWARD`
  veya proje konvansiyonuna göre) eklenir — uyumsuz bir şema değişikliği
  merge edilemez.
- **Şema geçişi (JSONB → ilişkisel tablo):** `ParkSlotOccupancy.slots` (tek
  `jsonb` kolon, "oku-değiştir-tümünü-geri-yaz") yerine yeni `park_slot_state`
  tablosu: `park_info_id, slot_id, status, epoch, seq, camera_id, camera_code,
  x, y, label, updated_at` — `PRIMARY KEY(park_info_id, slot_id)`. Her slot
  kendi satırı; yazım **tuple karşılaştırmasıyla** DB-level atomik koşullu
  upsert: `INSERT ... ON CONFLICT (park_info_id, slot_id) DO UPDATE SET ...
  WHERE (excluded.epoch, excluded.seq) > (park_slot_state.epoch,
  park_slot_state.seq)` — sadece `seq` değil, `(epoch, seq)` çiftinin
  TAMAMI karşılaştırılır (v1'deki tutarsızlığın düzeltmesi budur).
- `ParkOccupancyManager.upsertSlotStatus`: koşulsuz `slots.put(...)` yerine
  önce **sahiplik kontrolü** (bkz. "Kamera sahiplik kontrolü"), sonra yeni
  `ParkSlotStateDao.upsertIfNewer(...)` çağrısı. **Etkilenen satır sayısı 0
  ise** (event eskiyse reddedildi): WS broadcast VE `notifyOccupancyFilled`
  TETİKLENMEZ, bunun yerine yeni `stale_event_rejected_total` metriği
  (camera_code/slot_id etiketli) artırılır — reddedilen yazımlar artık
  sessiz değil, gözlemlenebilir.
- `getOccupancy`: geçiş süresince hem JSONB hem `park_slot_state` dolu
  olacağı için okuma yolu AŞAMALI değişir — bkz. "Geçiş stratejisi".
- `CameraSlotChangedConsumer`/`CameraSlotChangeConsumer`: `event.getVersion()`
  artık gerçekten `applySlotChange`/`processDetection`'a geçiriliyor.
- Yeni config: `park-slot.version-gating.mode` = `legacy` | `enforce` (bkz.
  "Devreye alma stratejisi").

**Epoch üretimi ve saat senkronu:**
- `epoch` = process başlangıcındaki wall-clock zaman (ms). Rastgele UUID
  DEĞİL — iki ayrı restart'ın epoch'ları bu sayede doğrudan sayısal/zaman
  sırasına göre karşılaştırılabilir.
- PVC'de önceden kaydedilmiş bir `last_epoch` varsa: yeni epoch =
  `max(now_ms, last_epoch + 1)` — sistem saati geriye kaydıysa veya aynı
  milisaniyede art arda restart olduysa bile epoch'un asla eşit/geriye
  gitmemesini garanti eder. Yeni değer hemen persist edilir.
- **Edge NTP kontrolü:** process başlangıcında (ve periyodik olarak) yerel
  saat ile NTP/referans zaman arasındaki fark bir eşiği aşarsa (örn. >5s):
  process ÇALIŞMAYA DEVAM EDER (kameradan okuma/state tracking durmaz) AMA
  **yayın yapmaz** — outbox birikmeye devam eder, saat senkronu düzelince
  normal yayın otomatik devam eder; ayrıca bir alarm/log üretilir. Bu, saati
  bozuk bir pod'un ileri kaymış bir `epoch` yayınlayıp o slotu kalıcı olarak
  "kazanmasını" (bkz. aşağıdaki backend kontrolü) kaynağında önler.
- **Backend ileri-kaymış epoch reddi:** backend, `epoch > server_now_ms +
  5dk` olan event'leri YAZMAZ — bariz şekilde gelecekteki bir epoch, saat
  senkron hatasının veya hatalı bir cihazın belirtisidir ve kabul edilirse
  o slotu gerçek zaman o noktaya erişene kadar kalıcı olarak "kazanmış"
  hale getirirdi (hiçbir gerçek event onu geçemezdi). Reddedilince
  `future_epoch_rejected_total` metriği artırılır + alarm.
- Bu iki kontrol birlikte: edge kendi tarafında saat bozuksa yayın yapmaz
  (kaynağında önleme), backend ise her ihtimale karşı (edge kontrolü
  bypass edilmiş, test/manuel event, vb.) ayrıca bir güvenlik ağı taşır.

**Kamera sahiplik kontrolü (GAP #4 bu aşamanın kapsamı DIŞINDA kalıyor):**
- **Düzeltme (v2'deki hatalı iddia):** v2'de "wall-clock epoch tasarımı
  GAP #4'ü (kamera değişimi) örtük olarak kapatıyor" denmişti — bu YANLIŞ.
  Tuple karşılaştırması sadece "en son başlayan kamera kazanır" anlamına
  gelir. İki kamera aynı fiziksel slotu aynı anda görüp ikisi de event
  gönderirse, daha ESKİ epoch'lu kamera sessizce ve KALICI olarak kaybeder
  — bu bir "sahiplik" çözümü değil, zamanlamaya bağlı bir yan etkidir ve
  GAP #4'ün (hangi kameranın bir slotun yetkili kaynağı olduğu) gerçek
  çözümü değildir.
- **Gerçek mekanizma — kod doğrulaması yapıldı:** backend'de zaten
  `camera_slot_identity` tablosu var (`CameraSlotIdentity` entity,
  `models/occupancy/CameraSlotIdentity.java:28`, DDL
  `V017__Create_Camera_Slot_Identity_Table.sql`) — `(camera_id,
  raw_slot_key) → park_slot_id` eşlemesi, `UNIQUE(park_info_id,
  park_slot_id)` kısıtıyla aynı anda bir `park_slot_id`'yi en fazla bir
  `camera_id` tutabiliyor. Bu, "bu slotun şu anki yetkili kamerası" sorusuna
  zaten doğru cevabı veren bir kaynak.
- **Yeni kontrol:** `upsertSlotStatus` içinde, koşullu upsert'ten ÖNCE,
  `camera_slot_identity`'den `(park_info_id, park_slot_id)` için atanmış
  `camera_id` okunur. Gelen event'in `camera_id`'si bu değerle eşleşmiyorsa
  yazım tamamen reddedilir (epoch/seq'e bakılmaz) ve
  `non_owner_camera_rejected_total` metriği (camera_id/park_slot_id
  etiketli) artırılır.
- **Not:** `SlotIdentityResolver.resolve()`'daki otomatik (explicit-id'siz)
  yol, başka bir kameranın zaten sahip olduğu bir `raw_slot_key` için
  kontrolsüzce YENİ bir `park_slot_id` basıyor olabilir
  (`SlotIdentityResolver.java:56-68`) — bu, GAP #4'ün kendisiyle ilgili,
  Aşama 7'nin kapsamı dışında, AYRI bir bulgu; burada sadece var olan
  eşlemeye göre yazım zamanı kontrolü ekleniyor, `SlotIdentityResolver`
  mantığı değişmiyor. **GAP #4 ve ADR-009 açık kalıyor.**

**Devreye alma stratejisi (feature flag, legacy → enforce):**
- `park-slot.version-gating.mode=legacy` (başlangıç/varsayılan): `epoch`/`seq`
  alanı olmayan (veya olan) event'ler koşulsuz yazılır — mevcut davranış
  korunur, `park_slot_state` dolar ama henüz hiçbir yazım reddedilmez.
- Sıra: (1) backend `legacy` modda deploy edilir (PR a) → (2) tüm edge'ler
  `epoch`/`seq` göndermeye başlar (PR b) → (3) üretimde bir süre `legacy`
  modda çalışıp aşağıdaki somut kriterle doğrulanır → (4) flag `enforce`'a
  çevrilir (PR c).
- **Enforce'a geçiş kriteri (somut):** ardışık 24 saat boyunca, TÜM
  kameralardan gelen event'lerin **%100'ü** `epoch`/`seq` taşıyor (hiçbir
  legacy-format event yok) VE bu 24 saatlik pencerede "reddedilirdi"
  simülasyonu (`enforce`'a geçmeden önce koşulu sadece loglayıp yazmaya
  devam eden bir gölge-değerlendirme) çalıştırıldığında ortaya çıkan
  kayıtlar YALNIZCA beklenen tekrar/duplicate event'lerden (örn. outbox
  retry, at-least-once redelivery) oluşuyor — gerçek/yeni veri kaybı işareti
  yok. Bu iki koşul karşılanmadan flag `enforce`'a çevrilmez.
- `enforce` modunda: koşullu upsert fiilen reddedebilir (yukarıdaki
  `WHERE (epoch,seq) >` koşulu devrede).
- **Geri dönüş:** flag'i `legacy`'ye çevirmek — kod rollback'i gerekmez.

**Snapshot event'leri seq üretmez:** periyodik tam-state snapshot event'i
(`CameraDetectionEvent`) her slot için YENİ bir `seq` ÜRETMEZ — o slot için
o ana kadar bilinen SON `(epoch, seq)` değerini taşır (edge tarafında
snapshot oluşturulurken `slot_state`'teki mevcut `epoch`/`seq` okunup
aynen eklenir). Böylece bir snapshot, gerçekte hiçbir değişiklik olmamış
bir slotu yanlışlıkla "daha yeni" gibi göstermez; sadece gerçek delta
event'leri `seq`'i artırır.

**Geçiş stratejisi (JSONB → park_slot_state):**
1. İlk yükleme: mevcut `park_slot_occupancy.slots` JSONB verisi bir seferlik
   migrasyon script'iyle `park_slot_state`'e kopyalanır, `epoch=0, seq=0`
   ile — Aşama 3'teki lazy-migration deseniyle aynı mantık: ilk gerçek event
   (`epoch>0`) geldiğinde otomatik üzerine yazılır.
2. Geçiş süresince **dual-write, AYNI DB transaction'ında**: her yazım HEM
   eski JSONB kolonuna HEM yeni `park_slot_state`'e, tek bir transaction
   içinde yapılır — biri başarısız olursa ikisi de rollback olur, iki yol
   arasında asla tutarsızlık penceresi açılmaz.
3. Okuma yolu: dual-write bir süre doğrulandıktan sonra `getOccupancy`
   `park_slot_state`'i okumaya geçirilir (JSONB okuma yolu bu noktada
   devre dışı, ama kolon/dual-write SİLİNMEZ).
4. JSONB kolonunun fiilen KALDIRILMASI: PR (c)'nin kapsamına dahil (bkz.
   "PR bölünmesi") — ayrı bir takip işi değil.
- **Legacy modda epoch/seq sıfırlama:** `epoch`/`seq` göndermeyen eski
  formatlı event'ler, `park_slot_state`'e yazılırken `epoch=0, seq=0`
  olarak kaydedilir — event'in kendi (güvenilmez, GAP #12'nin tam konusu
  olan) `version` alanı seq olarak KULLANILMAZ. Sadece `legacy` modda kabul
  edilir; karışık sürümlü filo bu sayede sorunsuz çalışır, `enforce`'a
  geçişten önce TÜM edge'lerin yeni formata geçmiş olması rollout sırasıyla
  ve yukarıdaki %100 kriteriyle garanti edilir.

**Test senaryoları (tam liste):**
1. Edge restart sonrası epoch/seq: `slot_state`/`process_epoch` (retention'dan
   ayrı, kalıcı) restart simülasyonunda (yeni instance, aynı PVC) epoch'un
   `max(now, last_epoch+1)` kuralıyla ASLA geriye gitmediği; sistem saati
   geri alınmış bir restart simülasyonunda da bu garantinin tutması.
2. Eşzamanlı slot güncellemesi: aynı `parkInfoId`'nin iki/üç farklı
   `slotId`'si **concurrency=3** simülasyonuyla eşzamanlı işlendiğinde her
   slotun DB'de doğru göründüğü — bu test, eski JSONB yoluna karşı KIRMIZI
   (lost-update regresyonu kanıtı), yeni `park_slot_state` + koşullu upsert
   yoluna karşı YEŞİL olmalı.
3. Tuple karşılaştırması: `(epoch,seq)` daha düşük bir event, DB'de zaten
   daha yüksek `(epoch,seq)`'li bir kayıttan SONRA işlendiğinde DB'nin
   değişmediği VE bu durumda WS broadcast/`notifyOccupancyFilled`'ın
   ÇAĞRILMADIĞI VE `stale_event_rejected_total`'ın arttığı.
4. Outbox retention purge sonrası seq: 7 günlük `purge_sent_older_than`
   çalıştıktan sonra bir slotun `seq`'inin SIFIRLANMADIĞI (`slot_state`
   purge'den etkilenmemeli).
5. Snapshot seq üretmiyor: bir slotta hiçbir delta olmadan ardışık iki
   snapshot event'i geldiğinde DB'deki `(epoch,seq)`'in DEĞİŞMEDİĞİ; bir
   delta'dan hemen sonra gelen snapshot'ın delta'nın `(epoch,seq)`'ini
   taşıdığı (kendi `seq`'ini üretmediği).
6. Dual-write atomikliği: `park_slot_state` yazımı simüle edilmiş bir
   hatayla başarısız olduğunda JSONB kolonunun da güncellenmediği (ve tersi)
   — aynı transaction'da ikisinin birlikte commit/rollback olduğu.
7. `legacy`→`enforce` geçişi, karışık edge sürümleri: bazı kameralar henüz
   `epoch`/`seq` göndermiyorken flag `legacy`'de iken sorunsuz çalıştığı
   (legacy event'ler `epoch=0,seq=0` ile yazılıyor); TÜM kameralar yeni
   formata geçtikten sonra flag `enforce`'a çevrildiğinde davranışın
   beklendiği gibi değiştiği.
8. İleri kaymış epoch reddi: `epoch > server_now_ms + 5dk` olan bir event
   backend'de YAZILMADIĞI ve `future_epoch_rejected_total`'ın arttığı;
   edge tarafında NTP senkron eşiği aşıldığında process'in çalışmaya devam
   edip YAYIN YAPMADIĞI, outbox'ın birikmeye devam ettiği, senkron
   düzelince birikmiş event'lerin normal yayına döndüğü.
9. Atanmamış kameradan event reddi: `camera_slot_identity`'de bir
   `park_slot_id`'ye atanmamış bir `camera_id`'den gelen event'in (epoch/seq
   ne olursa olsun) reddedildiği VE `non_owner_camera_rejected_total`'ın
   arttığı; atanmış kameradan gelen event'in normal işlendiği.

**Kabul kriterleri:** Edge pod'u herhangi bir sebeple (restart, reschedule,
saat kayması, 7+ gün retention purge) yeniden başlasa da üretilen
`(epoch,seq)` hiçbir koşulda geriye gitmiyor; backend `enforce` modunda
hiçbir yazımda `(epoch,seq)` tuple kontrolü ve kamera sahiplik kontrolü
yapmadan DB'ye yazmıyor; reddedilen her yazım ilgili metrikle
(`stale_event_rejected_total` / `future_epoch_rejected_total` /
`non_owner_camera_rejected_total`) gözlemlenebilir; aynı parkın farklı
slotlarına concurrency=3 ile eşzamanlı yapılan güncellemeler birbirini
kaybetmiyor; dual-write hiçbir koşulda iki tablo arasında tutarsızlık
bırakmıyor; ileri kaymış saatli bir edge pod sessizce veri üretemiyor.

**Risk:** Yüksek — şema geçişi (JSONB→ilişkisel tablo, dual-write) ve
concurrency semantiği değişiyor; staging'de yük testiyle (eşzamanlı çoklu
slot güncellemesi) doğrulanmalı; rollback planı hem kod seviyesinde
(`legacy` flag'i, JSONB kolonu süresince silinmemesi) hem veri seviyesinde
(dual-write) güvence altında.

**Kapsam dışı — Aşama 8 (henüz roadmap'te yok, P1):** GAP #14 (WS fan-out'un
paylaşılan broker'a taşınması) ve GAP #15 (MQTT manualAcks + senkron Kafka
send + clientId benzersizleştirme/tek-aktif-bridge) bilerek bu planın
KAPSAMI DIŞINDA tutuldu — bu iki PR'a (a/b/c) dahil edilmeyecek, `replicas>1`'e
geçiş kararı alındığında ayrı bir Aşama 8 olarak ele alınacak.

**Uygulama PR bölünmesi (plan onaylandıktan sonra):**
- **PR (a) — Backend temel:** `park_slot_state` şeması + koşullu upsert
  (tuple karşılaştırması) + **kamera sahiplik kontrolü**
  (`camera_slot_identity` lookup + `non_owner_camera_rejected_total`) +
  `stale_event_rejected_total` / `future_epoch_rejected_total` metrikleri +
  feature flag (varsayılan `legacy`) + dual-write (aynı transaction). Edge
  tarafında hiçbir değişiklik gerektirmez, tek başına deploy edilebilir.
- **PR (b) — Edge:** `(epoch, seq)` üretimi/persist (PVC dahil k8s değişikliği,
  `max(now,last_epoch+1)` kuralı) + **NTP senkron kontrolü** (eşik aşılırsa
  yayın durdurma + alarm) + Avro alan eklemeleri + snapshot'ın kendi `seq`
  üretmemesi + `mqtt_publisher.py` payload değişikliği. Backend hâlâ
  `legacy` modda olduğu için güvenli.
- **PR (c) — Enforce geçişi + JSONB kaldırma:** 24 saatlik %100 kriteri
  sağlandıktan sonra flag'i `enforce`'a çevirme + okuma yolunun
  `park_slot_state`'e taşınması + JSONB kolonunun ve dual-write kodunun
  FİİLEN KALDIRILMASI (bu PR'ın kapsamına dahil, ayrı bir takip işi değil).

---

## Bağımlılık Özeti

```
Aşama 0 (hızlı fix) ──▶ Aşama 1 (SQLite outbox) ──▶ Aşama 2 (delta/snapshot + Kafka consumer) ──┬─▶ Aşama 3 (slot kimliği)
                                                                                                  └─▶ Aşama 4 (reconciliation + watchdog)

Aşama 5 (temizlik) — herhangi bir zamanda, bağımsız

Aşama 7 (version/sequence + JSONB lost-update, PLAN) — Aşama 2'nin event
şemasına ek alan ekliyor, bağımsız yürütülebilir ama replicas>1/concurrency>1
öncesi ZORUNLU önkoşul.
```

Aşama 2 tamamlanmadan Aşama 4'teki snapshot event'i taşınacak bir topic/consumer bulamaz; Aşama 3, Aşama 2'nin event şemasına (slot kimliği taşıyan alanlar) bağımlı olduğu için ondan sonra önerilir ama kesin bir teknik zorunluluk değildir.

`anomaly_detection` genişlemesi (GAP #11, P3) bu roadmap'e dahil edilmedi — Aşama 2 tamamlanıp Kafka gerçekten tüketilir hale geldikten sonra, ayrı bir karar/roadmap olarak ele alınmalı.

Çok kameralı sensör füzyonu (birden fazla kameranın AYNI ANDA aynı fiziksel
slotu görmesi — Aşama 3'ün çözdüğü "kamera değişti" senaryosundan farklı)
araştırıldı ve **ADR-009** olarak kaydedildi (bkz. `ADR_DOCUMENTS.md`).
Saha işi (kameralar arası ortak fiziksel referans noktası ölçümü,
`mark_perspective.py` genişletmesi) gerektirdiği için bu roadmap'e dahil
edilmedi — çakışan görüş alanlı çoklu kamera kurulumu planlandığında ayrı
bir faz olarak ele alınmalı.
