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

> **Not (2026-11-24 doğrulama turu):** Backend'de bu aşamaya kısmi bir zemin hazırlığı zaten var (`24ab3ad`: `ParkSlotOccupancy.lastCameraId`/`lastCameraCode` alanları, entity yorumunda bare-slotIndex niyeti) — ama gerçek anahtar normalizasyonu (ADR-002 Seçenek 3: kalıcı `parkSlotId`) henüz yapılmadı, `processDetection` hâlâ ham `{cameraCode}-{slotId}` string'ini key olarak kullanıyor. Bu aşama hâlâ gerekli.

**Hedef:** Kamera değişiminde slot kimliğinin kopmasını önlemek, orphan key birikimini durdurmak.
**Bağımlılıklar:** Aşama 2 (yeni event formatı slot kimliğini taşıyacağı için, kimlik modeli değişmeden önce event şemasının oturmuş olması mantıklı — ama teknik olarak bağımsız da yürütülebilir, sıralama önerisidir, zorunluluk değildir).
**Değişmesi beklenen modüller:** Backend `ParkSlotOccupancy`/`SlotStateDTO` anahtarlama stratejisi, `ParkOccupancyManager.processDetection`/`upsertSlotLayout`, `tools/register_slot_layout.py` (edge tarafı, kalıcı `parkSlotId` üretimi/kullanımı), ROI/slot config şemasına `version` alanı eklenmesi.
**Test kriterleri:** Kamera kodu değiştirildiğinde (aynı `parkSlotId` korunarak) mevcut slot geçmişinin (x/y/label, durum) kaybolmadığını doğrulayan test; eski anahtarlama ile kayıtlı mevcut verinin migrasyon sonrası doğru okunabildiğini doğrulayan bir geriye-dönük-uyumluluk testi.
**Kabul kriterleri:** Bir kamera fiziksel olarak değiştirildiğinde (yeni `cameraCode`, aynı slotlar), backend'de orphan kayıt oluşmuyor, mevcut `ParkSlotOccupancy` kaydı güncelleniyor.
**Risk:** Orta — mevcut production verisinde zaten birikmiş olabilecek `{cameraCode}-{slotId}` formatlı kayıtlar için bir migrasyon/geri-uyumluluk stratejisi gerekir (bu roadmap kapsamında ayrıntılandırılmadı, ayrı bir migrasyon planı gerektirir).

---

## Aşama 4 — Reconciliation + Kamera Watchdog (ADR-008, GAP #5)

**Hedef:** Uzun süreli offline/drift senaryolarını ve donuk-stale-state riskini kapatmak.
**Bağımlılıklar:** Aşama 1 (snapshot için outbox altyapısı), Aşama 2 (snapshot event formatı/topic'i).
**Değişmesi beklenen modüller:** Edge `outbox.py`'ye periyodik snapshot tetikleyici, `capture/rtsp.py`/`pipeline.py`'ye `last_frame_at` watchdog (eşik aşılırsa ilgili kameranın slotlarını `UNKNOWN`'a çekme), backend'de snapshot event'leri için consumer dalı.
**Test kriterleri:** Watchdog eşiği aşıldığında slotların `UNKNOWN`'a düştüğünü doğrulayan test; periyodik snapshot'ın backend projeksiyonunu drift varken düzelttiğini doğrulayan entegrasyon testi.
**Kabul kriterleri:** Kamera N dakika boyunca kare üretmezse, ilgili slotlar hem edge WS'de hem merkezi WS'de `UNKNOWN` olarak görünüyor; edge uzun süre offline kaldıktan sonra tekrar bağlandığında backend projeksiyonu bir sonraki snapshot ile düzeliyor.
**Risk:** Düşük-orta — watchdog eşiğinin yanlış kalibre edilmesi (çok agresif → gereksiz UNKNOWN flapping, çok gevşek → geç tespit) saha testiyle ayarlanmalı.

---

## Aşama 5 — Altyapı Temizliği (GAP #9, P2)

**Hedef:** Ölü kod ve çelişkili config'leri temizlemek (işlevsel etkisi yok, bakım kalitesi için).
**Bağımlılıklar:** Yok — herhangi bir aşamadan bağımsız, paralel yapılabilir.
**Değişmesi beklenen modüller:** `MqttConnectionConfig.java` (`kafkaFailureChannel`'ı kaldır veya gerçekten kullan), `KafkaProducerConfig.java` (duplike bean'i kaldır), `application.yml` (`acks=1` satırını gerçek değerle senkronla veya kaldır).
**Test kriterleri:** Mevcut Kafka producer/consumer testlerinin (varsa) hâlâ geçtiğini doğrulamak — davranış değişmemeli, sadece ölü kod kalkıyor.
**Kabul kriterleri:** Build temiz, davranış değişikliği yok, config dosyalarında tutarsızlık kalmıyor.
**Risk:** Çok düşük.

---

## Bağımlılık Özeti

```
Aşama 0 (hızlı fix) ──▶ Aşama 1 (SQLite outbox) ──▶ Aşama 2 (delta/snapshot + Kafka consumer) ──┬─▶ Aşama 3 (slot kimliği)
                                                                                                  └─▶ Aşama 4 (reconciliation + watchdog)

Aşama 5 (temizlik) — herhangi bir zamanda, bağımsız
```

Aşama 2 tamamlanmadan Aşama 4'teki snapshot event'i taşınacak bir topic/consumer bulamaz; Aşama 3, Aşama 2'nin event şemasına (slot kimliği taşıyan alanlar) bağımlı olduğu için ondan sonra önerilir ama kesin bir teknik zorunluluk değildir.

`anomaly_detection` genişlemesi (GAP #11, P3) bu roadmap'e dahil edilmedi — Aşama 2 tamamlanıp Kafka gerçekten tüketilir hale geldikten sonra, ayrı bir karar/roadmap olarak ele alınmalı.
