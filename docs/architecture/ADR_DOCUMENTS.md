# ADR_DOCUMENTS.md — Mimari Karar Kayıtları

> Dayanak: [CURRENT_STATE.md](./CURRENT_STATE.md), [ARCHITECTURE_GAP_ANALYSIS.md](./ARCHITECTURE_GAP_ANALYSIS.md), [ARCHITECTURE_OPTIONS.md](./ARCHITECTURE_OPTIONS.md). Diyagramlar [TARGET_ARCHITECTURE.md](./TARGET_ARCHITECTURE.md)'de.

---

## ADR-001: Delta vs Snapshot Event Modeli

**Context:** Mevcut `CameraDetectionEvent` her zaman kameranın TÜM slotlarının o anki durumunu taşıyor (snapshot), sadece değişiklikte tetikleniyor. FREE→OCCUPIED/EMPTY→OCCUPIED geçişi event'in içinde temsil edilmiyor (CURRENT_STATE §10).

**Problem:** İş değeri yüksek senaryolar (bir slotun ne zaman/hangi yönde değiştiğini bilmek — örn. uyarı/rezervasyon ihlali tespiti) mevcut modelle üretilemiyor; tüketici taraf (bugün hiç yok, ADR-006 ile eklenecek) her iki ardışık event'i kendi tutup diff almak zorunda kalır, bu da backend'de zaten var olan state'in gereksiz tekrarı olur.

**Options:**
1. Mevcut snapshot modelini koru.
2. Sadece delta'ya geç (previousStatus/currentStatus, tek slot).
3. Delta (normal akış) + Snapshot (yalnızca senkronizasyon amaçlı) — ayrı Avro record, ayrı topic.

**Decision:** Seçenek 3. Normal akışta delta event (`CameraSlotChanged`: `parkInfoId, slotId, previousStatus, currentStatus, version, timestamp, correlationId`), snapshot (`CameraSlotSnapshot`: mevcut `CameraDetectionEvent`'e yakın, tüm kamera durumu) yalnızca ilk bağlantı/restart/periyodik reconciliation için (bkz. ADR-008).

**Trade-offs:** İki event tipinin birlikte bakımı ek karmaşıklık getirir; buna karşılık event boyutu küçülür, geçiş semantiği event'in kendisinde taşınır, reconciliation ayrı ve nadir bir yola indirgenir.

**Consequences:** `CameraDetectionEvent.avsc` yerine iki yeni Avro record gerekir (yeni topic'ler — mevcut v0→v1 refleksiyle tutarlı bir versiyonlama). Backend tarafında delta ve snapshot için ayrı consumer mantığı gerekir (ADR-006 ile aynı consumer'ın iki alt-dalı olabilir).

**Rejected Alternatives:** Seçenek 2 (sadece delta) reddedildi çünkü reconnect/restart/drift senaryolarında (ARCHITECTURE_GAP_ANALYSIS §2: "edge uzun süre offline kalır") delta'lar tek başına yetersiz kalır — kaçırılan bir delta telafi edilemez, snapshot bu riski düşük karmaşıklıkla kapatıyor.

---

## ADR-002: ROI ID vs Kalıcı Slot ID

**Context:** Edge'de `SlotDefinition.id` zaten kalıcı bir kimlik olarak tasarlanmış (ROI index ile aynı, ama id'ler ardışık değil — silinenlerden boşluk kalıyor, CURRENT_STATE §8). Backend'de ise slot anahtarı `{cameraCode}-{slotId}` string'i; kamera değişince (örn. fiziksel kamera değişimi) bu anahtar kopuyor, eski kayıt backend'de temizlenmeden orphan kalıyor (GAP #4).

**Problem:** Slot kimliği, onu üreten kamera donanımına bağımlı — oysa fiziksel park slotu kameradan bağımsız, kalıcı bir iş varlığı.

**Options:**
1. Mevcut haliyle bırak (`{cameraCode}-{slotId}`).
2. Edge'de ayrı bir ROI ID daha tanımla (slot ID'den bağımsız).
3. Backend'de kalıcı `parkSlotId` + ayrı kamera↔slot eşleme kaydı (park-bazlı merkezi mapping).

**Decision:** Seçenek 3. Edge'deki `SlotDefinition.id` değişmez (zaten doğru). Backend'de slot anahtarı artık `{cameraCode}-{slotId}` yerine, ilk kayıtta üretilen kalıcı bir `parkSlotId` olur; hangi kameranın şu an o slotu izlediği ayrı, güncellenebilir bir alanda (`SlotStateDTO.cameraCode` zaten var — sadece anahtar olarak kullanılmaktan çıkarılır, salt bilgi alanına indirgenir) tutulur.

**Trade-offs:** Backend'de mevcut `Map<String, SlotStateDTO>` anahtarlama stratejisinin değişmesi gerekir (küçük, lokalize bir değişiklik — yeni tablo/servis gerekmez). Buna karşılık kamera değişiminde slot geçmişi/kimliği korunur, orphan birikmez.

**Consequences:** `tools/register_slot_layout.py` ve `ParkOccupancyManager.processDetection`'ın anahtar üretim mantığı güncellenmeli; ROI/slot config şemasına bir `version`/`updated_at` alanı eklenmesi de bu ADR kapsamında ele alınmalı (GAP #10).

**Rejected Alternatives:** Seçenek 2 reddedildi — edge'de ROI index zaten kalıcı kimlik olarak davranıyor (CURRENT_STATE §8, id'lerin ardışık olmaması bunu kanıtlıyor), ayrı bir roiId eklemek kanıtlanmamış bir ihtiyaca karmaşıklık eklemek olurdu.

> **Status update (2026-11-24 doğrulama turu):** Backend'de `24ab3ad` commit'i bu ADR'nin yönüne kısmi bir adım attı — `ParkSlotOccupancy`'ye `lastCameraId`/`lastCameraCode` eklendi, entity yorumunda slot key'i kavramsal olarak "bare slotIndex" diye yeniden tanımlandı. Ama asıl karar (Seçenek 3 — kalıcı `parkSlotId` + key'in kamera koduna bağımlılıktan ayrılması) **henüz uygulanmadı**: `ParkOccupancyManager.processDetection` hâlâ edge'den gelen ham, kamera-koduna-bağımlı string'i (`{cameraCode}-{slotId}`) doğrudan key olarak kullanıyor (stripping/normalizasyon yok), edge de hâlâ bu formatı gönderiyor. ADR hâlâ **kabul edilmiş ama uygulanmamış** durumda.

---

## ADR-003: Edge State Ownership

**Context:** Bugün fiilen Edge `StateStore` tek "onaylı" state hesaplayıcısı (hysteresis/occlusion/override mantığı orada), ama bu hiçbir yerde açıkça "otorite" olarak tanımlanmamış; backend `ParkSlotOccupancy` kendi başına hiçbir doğrulama yapmadan gelen değeri yazıyor.

**Problem:** Edge ile backend arasında bir tutarsızlık (örn. gecikmeli/sıra dışı mesaj) oluşursa, hangi tarafın "doğru" kabul edileceği kod tarafından tanımlı değil.

**Options:**
1. Backend'i otorite yap (edge sadece ham veri gönderir, onay backend'de).
2. Edge'i otorite yap, backend yalnızca projeksiyon/cache.
3. İkisi eşit, "son yazan kazanır".

**Decision:** Seçenek 2. Edge StateStore state'in tek otoritesi; backend `ParkSlotOccupancy` bunun bir projeksiyonu/cache'idir. Çakışma durumunda (örn. snapshot reconciliation) her zaman edge'in gönderdiği değer esas alınır.

**Trade-offs:** Backend'in kendi başına "akıllı" bir doğrulama/düzeltme yapmaması, hataların edge'de düzeltilmesini gerektirir — ama bu zaten mevcut davranış (backend hiçbir zaman hysteresis uygulamıyor), sadece netleştiriliyor.

**Consequences:** ADR-008 (reconciliation) bu kararın doğrudan uygulamasıdır: snapshot event'i backend projeksiyonunun üzerine koşulsuz yazar.

**Rejected Alternatives:** Seçenek 1 (backend otorite) reddedildi — hysteresis/occlusion/confidence mantığının backend'e taşınması büyük bir yeniden yapılandırma gerektirir ve "mevcut sistemi koru" ilkesini çiğner. Seçenek 3 ("son yazan kazanır") reddedildi — ağ gecikmesi/sıra dışı mesajlarda öngörülemez davranışa yol açar (GAP tablosu, "eski event gelir" senaryosu).

---

## ADR-004: SQLite Transactional Outbox

**Context:** Kullanıcının başlangıç hipotezinin aksine, edge'de hiçbir SQLite/outbox mevcut değil (CURRENT_STATE §4). `MqttPublisher` best-effort'tur ve aktif bir hata (`_last_payload` optimistic update) sessiz veri kaybına yol açabiliyor.

**Problem:** Edge'den backend'e giden tek kanal (MQTT) güvenilmez; süreç yeniden başlarsa veya broker'a erişilemezse, o andaki state değişikliği hiçbir yerde kalıcı olmuyor.

**Options:**
1. Mevcut best-effort publish'i koru, sadece `_last_payload` bug'ını düzelt.
2. SQLite tabanlı outbox ekle (WAL mode, state update + outbox insert aynı transaction'da).
3. PostgreSQL'e geçiş (edge'de ayrı bir DB server).

**Decision:** Seçenek 2. Tek dosya SQLite, WAL mode, şema:
```sql
CREATE TABLE slot_state (slot_id INTEGER PRIMARY KEY, status TEXT, confidence REAL, updated_at INTEGER);
CREATE TABLE outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, slot_id INTEGER, status TEXT, prev_status TEXT,
                      version INTEGER, created_at INTEGER, sent_at INTEGER NULL, attempt_count INTEGER DEFAULT 0);
```
`StateStore._notify` tetiklendiğinde, `slot_state` UPDATE'i ve `outbox` INSERT'i **tek transaction'da** (`BEGIN...COMMIT`) yapılır. Ayrı bir poller thread `sent_at IS NULL` kayıtları MQTT'ye gönderir, ACK/başarı sonrası `sent_at`'i işaretler.

**Trade-offs:** SQLite tek-writer modelidir — [sqlite.org/isolation.html](https://sqlite.org/isolation.html) "yalnızca bir yazıcı aktif olabilir" der; bu edge'in zaten tek-process/tek-thread-yazan yapısına birebir uyuyor, ek bir kısıtlama getirmiyor. WAL mode'da dayanıklılık `PRAGMA synchronous` ayarına bağlı — [sqlite.org/wal.html](https://www.sqlite.org/wal.html) NORMAL modda her commit'te senkron disk yazımı yapılmadığını belirtiyor; gerçek crash-dayanıklılığı için `synchronous=FULL` (veya en azından NORMAL + WAL'ın kendi garantileri, process crash'e karşı yeterli ama OS/disk çökmesine karşı değil) seçilmeli.

**Consequences:** Yeni bir bağımlılık yok (Python'un stdlib `sqlite3` modülü yeterli). Disk I/O, mevcut düşük event hacminde (ARCHITECTURE_OPTIONS §6) önemsiz. Outbox büyümesi için basit bir temizlik politikası gerekir (örn. `sent_at IS NOT NULL AND created_at < now()-7d` olanları sil).

**Rejected Alternatives:** Seçenek 3 (PostgreSQL) reddedildi — somut bir ihtiyaç (çoklu-yazıcı, uzak erişim, büyük veri hacmi) kanıtlanmadı; "gereksiz teknoloji ekleme" ilkesine aykırı olurdu. Seçenek 1 (sadece bug fix) tek başına yetersiz — process restart'ta hâlâ hiçbir kurtarma yok.

---

## ADR-005: Kafka Partition Key

**Context:** Mevcut partition key `cameraCode` (CURRENT_STATE §5). Bugün zararsız çünkü hiç tüketen yok, ama ADR-006 ile bir consumer eklenince sıralama garantisi önem kazanıyor.

**Problem:** Bir parkın birden fazla kamerası olabildiği için (`ParkingCamera.parkInfo` @ManyToOne), `cameraCode` key'i yalnızca kamera-içi sıralamayı garanti ediyor; park-bazlı sıralı okuma garantisi yok.

**Options:**
1. `cameraCode` (mevcut).
2. `parkInfoId`.
3. `parkInfoId:slotId`.

**Decision:** Seçenek 3. Aynı slotun tüm event'leri (delta + snapshot'taki ilgili kayıt) her zaman aynı partition'a düşer, bu da ADR-001'deki version-bazlı sıra kontrolünü (ADR-003/ordering) partition içinde garanti eder.

**Trade-offs:** `parkInfoId` tek başına da kabul edilebilir bir seçenek olurdu (daha az partition çeşitliliği, consumer paralelliği için daha kaba taneli) ama slot-bazlı anahtar, "eski event yeni state'in üzerine yazmasın" kuralının (GAP tablosu) partition içinde natural sırayla desteklenmesini sağlıyor.

**Consequences:** `CameraDetectionEventPublisherService`/yeni delta-publisher'ın key üretimi `cameraCode` yerine `parkInfoId + ":" + slotId` olmalı. Partition sayısı, büyük ölçekte (ARCHITECTURE_OPTIONS §6, 100-1000 otopark) consumer paralelliğini belirleyeceği için topic oluşturulurken gözden geçirilmeli.

**Rejected Alternatives:** Seçenek 1 (mevcut `cameraCode`) reddedildi — park-bazlı tutarlılık garantisi vermiyor. Seçenek 2 (`parkInfoId`) kabul edilebilir ama slot-bazlı anahtar daha güçlü garanti verdiği ve ek maliyeti olmadığı için tercih edilmedi.

---

## ADR-006: Local vs Central WebSocket (Kafka Consumer Tetikli Merkezi Yayın)

**Context:** Bugün merkezi WebSocket yayını, `CameraDetectionMqttHandler` içinde Kafka'yı atlayarak senkron yapılıyor; Kafka topic'i hiç tüketilmiyor (GAP #2, #3). Edge'de ayrıca bağımsız bir yerel WebSocket (`api/ws.py`) zaten var ve doğru çalışıyor.

**Problem:** Mevcut merkezi dağıtım, backend'in tek instance olduğu varsayımına kilitli; Kafka'nın "durable event backbone" olma amacı fiilen gerçekleşmiyor.

**Options:**
1. Mevcut haliyle bırak (handler-içi senkron WS broadcast).
2. Backend içinde bir `@KafkaListener` ekleyip WS yayınını oraya taşı (aynı Spring Boot uygulaması, ayrı Gateway değil).
3. Ayrı bir WebSocket Gateway servisi kur.

**Decision:** Seçenek 2. `CameraDetectionMqttHandler` artık yalnızca MQTT→Kafka publish yapar (ADR-001'deki delta/snapshot event'lerini üretir); yeni bir `@KafkaListener` (`CameraSlotChangeConsumer`) bu event'leri tüketip `ParkOccupancyManager`'ın mevcut DB-yazım mantığını çağırır ve `SimpMessagingTemplate` ile WS'e yayınlar. Edge'deki yerel WebSocket (`api/ws.py`) **değişmeden** korunur.

**Trade-offs:** Bu, Kafka'yı gerçek kritik yola sokar — Kafka erişilemezse merkezi WS güncellemesi de gecikir (bugün bu bağımlılık yok, Kafka publish best-effort ve WS ondan bağımsız). Karşılığında Kafka fiilen "omurga" olur ve çoklu-instance'a geçişin önkoşulu kurulmuş olur.

**Consequences:** Tek instance'da davranış kullanıcı açısından neredeyse aynı kalır (ek bir Kafka round-trip kadar gecikme). Çoklu instance'a geçildiğinde her instance kendi consumer'ından okuyup kendi WS session'larına yayın yapabilir — tam fan-out için Redis (ADR-007) hâlâ gerekebilir ama bu adım olmadan Redis'in bir anlamı olmazdı.

**Rejected Alternatives:** Seçenek 3 (ayrı Gateway) reddedildi — mevcut Spring Boot WS altyapısı (`SimpMessagingTemplate`, zaten test edilmiş) yeterliyken yeni bir servis eklemek "en az karmaşıklık" ilkesine aykırı; kullanıcının "gereksiz servis oluşturma" talimatına da aykırı olurdu.

---

## ADR-007: Redis Kullanım Kararı

**Context:** Kullanıcının başlangıç hipotezi "Kafka → Consumer → Redis → WebSocket" zincirini varsayıyordu; araştırma Redis'in bu akışta **hiç kullanılmadığını** doğruladı (CURRENT_STATE §5, §9).

**Problem:** Redis gerçekten gerekli mi, yoksa önceden eklenmiş bir karmaşıklık mı olurdu?

**Options:**
1. Şimdi Redis Pub/Sub ekle (ADR-006 ile birlikte).
2. Redis'i hiç ekleme, yalnızca tek-instance Kafka Consumer→WS (ADR-006) ile yetin.
3. Çoklu-instance ihtiyacı kanıtlandığında ekle.

**Decision:** Seçenek 3. Bugün backend tek instance (docker-compose'da gözlemlendi) — Redis'in çözeceği problem (WS session'ların instance'lar arası fan-out'u) henüz yok. ADR-006 tamamlanıp gerçek bir yatay ölçekleme ihtiyacı (ARCHITECTURE_OPTIONS §6, ~100+ otopark ölçeği) ortaya çıktığında Redis Pub/Sub eklenir.

**Trade-offs:** Redis Pub/Sub **kalıcı mesajlaşma sağlamaz** — bir subscriber o an bağlı değilse mesaj kaybolur; bu yüzden Kafka'nın yerine asla geçmez, sadece WS instance'lar arası anlık fan-out katmanı olarak konumlanır.

**Consequences:** Bugün hiçbir aksiyon yok. İleride eklenirse: her backend instance Kafka consumer'ından okur → Redis'e publish eder → her instance kendi Redis subscription'ından okuyup yalnızca kendi bağlı WS session'larına dağıtır (yetki filtresi — "bir client yalnızca yetkili olduğu parkın event'lerini almalı" — bu filtre zaten `/topic/parkOccupancy/{parkInfoId}` kanal ayrımıyla kısmen var, STOMP subscription yetkilendirmesi ayrıca denetlenmeli).

**Rejected Alternatives:** Seçenek 1 (şimdiden ekle) reddedildi — kanıtlanmamış ihtiyaca göre teknoloji eklememe ilkesine aykırı, operasyonel yük ekler (izleme, HA, bir bileşen daha).

---

## ADR-008: Reconciliation Stratejisi

**Context:** Bugün hiçbir reconciliation mekanizması yok — edge uzun süre offline kaldığında veya restart olduğunda (CURRENT_STATE §3, "Restart sonrası state"), hiçbir telafi/senkronizasyon adımı yok.

**Problem:** Edge (otorite, ADR-003) ile backend (projeksiyon) arasında zamanla drift oluşabilir; bunu düzeltecek bir mekanizma gerekiyor, ama event replay gibi ağır bir çözüm "en az karmaşıklık" ilkesine aykırı olur.

**Options:**
1. Hiçbir mekanizma yok (mevcut durum).
2. Tam event replay (Kafka'dan topic'in başından okuma).
3. Snapshot-üzerine-yaz (reconnect/restart/periyodik tetiklemeli).

**Decision:** Seçenek 3. Edge, (a) her reconnect/restart sonrası, (b) periyodik olarak (örn. 5 dakikada bir) bir `CameraSlotSnapshot` event'i (ADR-001) gönderir; backend'deki ilgili consumer bu snapshot'ı `ParkSlotOccupancy` üzerine koşulsuz yazar (ADR-003 — edge kazanır).

**Trade-offs:** Event replay'e göre çok daha düşük karmaşıklık ve gecikme; buna karşılık ara geçişlerin ayrıntılı geçmişi reconciliation anında kullanılmaz (bu geçmiş zaten Kafka'nın delta topic'inde retention süresince duruyor, gerekirse ayrı bir analytics ihtiyacı için oradan okunabilir — ADR kapsamı dışı).

**Consequences:** Edge'de outbox'a (ADR-004) bir de "periyodik snapshot tetikleyici" eklenmeli (basit bir timer, mevcut poller thread'e bir dal). Backend'de snapshot event'leri için ayrı (veya aynı consumer'ın ayrı bir dalı olan) bir işleyici gerekir.

**Rejected Alternatives:** Seçenek 2 (tam event replay) reddedildi — büyük partition'larda yavaş, reconnect gecikmesini artırır, ve zaten ADR-003 gereği edge'in "şu an"ki durumu yeterli (geçmiş geçişlerin replay'i iş değeri katmıyor bu senaryoda).

> **Status update (2026-11-25, Aşama 4):** Karar uygulandı. (a) bacağı
> (reconnect/restart) zaten Aşama 2'de vardı (`MqttPublisher.enable_startup_snapshot`);
> bu turda (b) bacağı (periyodik, zaman tabanlı) eklendi — `OutboxWorker.snapshot_once()`,
> `_run` döngüsünde `snapshot_interval` (varsayılan 300s) her geçtiğinde tetiklenir.
> Ayrıca ADR-008'in kapsamına GAP #5'in "donuk/stale state" yarısı da eklendi:
> `StateStore.mark_all_unknown()` + `Pipeline._check_watchdog()` — kamera
> `watchdog.stale_after_seconds` (varsayılan 120s) süresince kare üretmezse
> slotlar ANINDA (hysteresis bypass) UNKNOWN'a çekilir. Backend'de hiçbir
> değişiklik gerekmedi — `ParkOccupancyManager.processDetection` zaten
> gelen her slot için koşulsuz upsert yapıyordu (ADR-003 "edge kazanır"),
> snapshot'ın sıklığı bu davranışı etkilemiyor.

---

## ADR-009: Çok Kameralı Sensör Füzyonu için Ortak Fiziksel Koordinat Sistemi

**Context:** Aşama 3 (ADR-002), bir kameranın fiziksel olarak değiştiği senaryoyu çözdü (`camera_slot_identity` + kalıcı `parkSlotId`, carryover ile). Ama farklı bir senaryo hâlâ çözülmedi: birden fazla kamera AYNI ANDA aynı fiziksel slotu farklı açılardan görürse (örn. geniş bir park alanının iki kamerayla kapsanması), her kamera kendi (camera, raw_slot_key) çifti için bağımsız bir `parkSlotId` auto-mint eder — aynı fiziksel yer için iki ayrı kayıt oluşur (çift sayım riski).

**Problem:** Hangi iki (kamera, raw_slot_key) çiftinin aynı fiziksel yeri temsil ettiğini anlamanın bugün bir yolu yok. `tools/mark_perspective.py`'deki her kameranın `perspective_transform`'u, o kameranın kendi karesinde gözle seçilen 4 köşeyle hesaplanıyor (doğrulandı: `config/slots/cam1.json`/`cam2.json`/`cam3.json`'daki 3x3 matrisler birbirinden bağımsız, ortak bir fiziksel referans noktasına oturmuyor) — dolayısıyla iki kameranın minimap x/y'leri karşılaştırılabilir değil.

**Araştırma notu (2026-11-25):** Üretim sistemlerinde genel yaklaşım "asset'i (fiziksel slot) modelle, sensörü değil" — slotlar ortak bir ground-plane/site koordinatında (genelde GeoJSON + homografi, GNSS ile füzyon) tanımlanır, kameralar bu ortak düzleme kalibre edilir. Çapraz-sensör eşleştirme üretim sistemlerinde her zaman insan onaylı yapılır (geometri/yakınlık bazlı öneri + operatör onayı) — otomatik birleştirme, yanlış-birleşme riski nedeniyle tercih edilmiyor.

**Options:**
1. Hiçbir şey yapma — çakışan görüş alanlı kamera kurulumu şimdilik desteklenmiyor kabul edilsin.
2. GPS lat/lng ile her slota koordinat ata (`ParkInfo.lat/lng`'nin slot seviyesine indirgenmesi).
3. Yerel (metre bazlı), saha-ölçümlü ortak referans: `mark_perspective.py`'ye kameralar arası paylaşılan 1-2 fiziksel referans noktası (örn. bir köşe direği, boyalı işaret) eklenir, bir kez şerit metre/lazerle ölçülür; tüm kameraların homografisi bu ortak düzleme oturtulur. Sonuç, `parkSlotId` başına kalıcı bir `siteX`/`siteY` (metre) olarak saklanır — `camera_slot_identity`'den ayrı, yeni bir `park_slot` kaydı (backend).

**Decision:** Seçenek 3. GPS (Seçenek 2) reddedildi — tipik GPS hassasiyeti (3-5m) bitişik park yerlerini (~2.5m) ayırt edemez. Yakınlık-bazlı otomatik "bunlar aynı slot" tespiti/birleştirmesi bilerek KAPSAM DIŞI bırakıldı — Aşama 3'te kurulan operatör-onaylı carryover mekanizması (`PUT /api/v0/camera/{cameraCode}/slots/layout` + `parkSlotId`) bu iş için zaten yeterli bir araç; "bunlar aynı olabilir" diye işaretleyen bir yakınlık sorgusu/rapor ayrı, sonraki bir karar.

**Trade-offs:** Saha ekibinin kurulumda birkaç fiziksel referans noktasını bir kez ölçmesi gerekir — ama bu, zaten var olan `mark_perspective.py` adımına (4 köşe tıklama) doğal bir ek, yeni bir araç/süreç değil.

**Consequences:** Acil değil — bugün tek kameralı her park için Aşama 3 çözümü yeterli. Çakışan görüş alanlı çoklu kamera kurulumu planlandığında (`mark_perspective.py` genişletmesi + backend'de `park_slot` tablosu) uygulanmalı.

**Rejected Alternatives:** Seçenek 1 (hiçbir şey yapmama) kalıcı bir çözüm değil, sadece bugünkü (tek-kamera-per-alan) durumu sürdürür — not olarak bırakıldı ama gerçek bir çoklu-kamera-kesişimi kurulumunda yetersiz kalacağı biliniyor.

---

## Kaynaklar (Section 8 doğrulaması)

- Kafka idempotent producer gereksinimleri (`acks=all`, `retries>0`, `max.in.flight.requests.per.connection≤5`): [Confluent — Optimizing for Durability](https://docs.confluent.io/cloud/current/client-apps/optimizing/durability.md), [Conduktor — Idempotent Producers](https://docs.conduktor.io/learn/advanced/producers/idempotent-producers)
- SQLite tek-yazıcı modeli ve WAL dayanıklılığı: [sqlite.org/isolation.html](https://sqlite.org/isolation.html), [sqlite.org/wal.html](https://www.sqlite.org/wal.html)
- MQTT QoS/retained message semantiği: [NATS — QoS, sessions, and retained messages](https://docs.nats.io/learn/mqtt/qos-sessions-and-retained), [MQTT v3.1 protokol referansı](https://public.dhe.ibm.com/software/dw/webservices/ws-mqtt/mqtt-v3r1.html)
- Avro şema evrimi (map value tipi dahil, genel promosyon kuralları): [Confluent — Schema Evolution and Compatibility](https://docs.confluent.io/platform/current/schema-registry/fundamentals/schema-evolution.md), [Expedia — Practical Schema Evolution with Avro](https://medium.com/expedia-group-tech/practical-schema-evolution-with-avro-c07af8ba1725)

> Not: Yukarıdaki web kaynakları ikincil/vendor dokümantasyonudur (arama, resmi Apache Kafka/Avro spesifikasyon sayfalarının tam metnini döndürmedi); ADR'lerdeki iddialar bu kaynaklarla tutarlı ama kritik bir üretim kararından önce resmi Apache Kafka/Avro spesifikasyonlarıyla çapraz doğrulanması önerilir.
