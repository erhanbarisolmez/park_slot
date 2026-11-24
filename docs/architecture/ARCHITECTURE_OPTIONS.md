# ARCHITECTURE_OPTIONS.md — Alternatiflerin Karşılaştırılması

> Dayanak: [CURRENT_STATE.md](./CURRENT_STATE.md), [ARCHITECTURE_GAP_ANALYSIS.md](./ARCHITECTURE_GAP_ANALYSIS.md). Seçilen kararların gerekçeleri [ADR_DOCUMENTS.md](./ADR_DOCUMENTS.md)'de ADR formatında.
> İlke: **en az karmaşıklıkla üretim gereksinimini karşıla, mevcut çalışan sistemi koru, kanıtlanmamış ihtiyaca göre teknoloji ekleme.**

## 1. Event Modeli: Her İnference / Yalnızca Değişiklik / Confidence+Temporal / Periyodik Snapshot + Delta

| Alternatif | Avantaj | Dezavantaj | Mevcut sisteme uyum |
|---|---|---|---|
| Her inference sonucunda event | En basit, hiç "ne zaman gönder" kararı yok | Event hacmi inference FPS'e eşit olur (gereksiz trafik, Kafka/MQTT/DB yükü); zaten reddedilmiş bir yaklaşım | Mevcut `StateStore` mimarisiyle çelişir (zaten değişiklik-tetiklemeli) |
| Yalnızca state değişikliğinde (**mevcut durum**) | Event hacmi gerçek değişiklikle orantılı | Değişikliğin NEDENİ (hangi slot, hangi yönde) event'te kaybolabilir (bkz. aşağıdaki Snapshot/Delta tartışması) | Zaten uygulanıyor (`state_store.py:_notify`) |
| Confidence + temporal validation sonrası (**mevcut durum, zaten dahil**) | Gürültülü tek-kare yanlışlarını eler | Daha fazla gecikme (confirm_seconds=4s) | Zaten uygulanıyor (hysteresis) |
| Periyodik snapshot + event-driven delta birlikte (**önerilen**) | Delta = düşük hacim + geçiş semantiği; snapshot = düzenli reconciliation, drift önler | İki event tipi = iki Avro record/topic yönetimi | Mevcut tekli-event modeline ek karmaşıklık, ama gerekçeli (bkz. ADR-001) |

**Trade-off tablosu — Snapshot vs Delta:**

| Boyut | Snapshot (mevcut) | Delta (önerilen, normal akış için) |
|---|---|---|
| Event boyutu | Kamera başına tüm slot sayısıyla orantılı | Sabit, tek slot |
| FREE→OCCUPIED temsili | Yok (sadece sonuç durumu) | Var (`previousStatus`/`currentStatus` alanları) |
| Reconnect/senkronizasyon | Doğal olarak uygun (her zaman tam durum) | Tek başına yetersiz — ilk bağlantıda ayrı bir snapshot gerekir |
| Sıra/idempotency karmaşıklığı | Düşük (her mesaj kendi kendine yeterli) | Orta (eski bir delta'nın yeni state'in üzerine yazmaması için version/sıra kontrolü gerekir) |
| **Öneri** | Yalnızca: ilk bağlantı, edge restart, periyodik reconciliation (örn. 5 dk) | Normal akışın birincil modeli |

## 2. Slot Kimliği: ROI Index = Slot ID / Ayrı ID / Kamera-bazlı / Park-bazlı Merkezi Mapping

| Alternatif | Avantaj | Dezavantaj | Mevcut sisteme uyum |
|---|---|---|---|
| ROI index = slot ID (**mevcut, edge içi**) | Basit, tek kimlik alanı | — (zaten doğru tasarlanmış, bkz. CURRENT_STATE §8) | Korunmalı |
| Ayrı ROI ID + slot ID | Geometri değişikliği kimlikten tamamen bağımsızlaşır | Edge'de şu an gerçek bir ihtiyaç kanıtlanmadı (id'ler zaten kalıcı tutuluyor) | Gereksiz karmaşıklık — **önerilmiyor** |
| Kamera-bazlı mapping (**mevcut, backend key'i**) | Basit string-key, ek tablo yok | Kamera değişince kimlik kopuyor (orphan, bkz. GAP #4) | Mevcut haliyle **sorunlu** |
| Park-bazlı merkezi mapping + edge'e konfigürasyon aktarımı (**önerilen**) | Kalıcı `parkSlotId`, kamera↔slot eşlemesi ayrı tutulur, kamera değişimi kimliği etkilemez | Backend'de yeni bir küçük eşleme tablosu gerekir | Mevcut `ParkSlotOccupancy`/`SlotLayoutEntry` modeline küçük bir ekleme ile uygulanabilir, yeni servis/domain gerekmez (bkz. ADR-002) |

## 3. Kafka → Backend → Client Dağıtımı: 3 Alternatif

### Alternatif 1 — Backend Kafka Consumer → WebSocket (aynı Spring Boot uygulaması)
### Alternatif 2 — Kafka + Redis + WebSocket Gateway (ayrı bileşen)
### Alternatif 3 — Edge Local WebSocket + Central WebSocket (ikisi birlikte)

| Boyut | Alt. 1 (Backend Consumer→WS) | Alt. 2 (Kafka+Redis+Gateway) | Alt. 3 (Edge WS + Central WS) |
|---|---|---|---|
| Yeni bileşen | Yok — mevcut Spring Boot WS altyapısı (`SimpMessagingTemplate`) + bir `@KafkaListener` | Redis + (opsiyonel) ayrı Gateway servisi | Edge WS zaten var; merkezi taraf Alt. 1 ile aynı |
| Operasyonel maliyet | Düşük | Orta-yüksek (yeni bileşen izleme/HA) | Düşük (edge WS zaten minimal) |
| Çoklu backend instance fan-out | **Hayır** (Kafka consumer tek instance'da çalışırsa diğer instance'ların WS session'larına ulaşmaz — bu, Redis/başka bir fan-out eklenene kadar sınır) | **Evet** (Redis Pub/Sub ile instance'lar arası fan-out) | Edge WS zaten tek-node (saha), merkezi taraf Alt.1 ile aynı sınırı taşır |
| Veri tutarlılığı | Kafka sırası korunursa iyi | Redis Pub/Sub **kalıcı değildir** — bir subscriber bağlı değilken mesaj kaybolur, Kafka'nın yerine geçmez | Edge WS zaten anlık/kalıcı olmayan; merkezi taraf Alt.1 ile aynı |
| Failure davranışı | Consumer düşerse mesajlar Kafka'da kalır (replay edilebilir) | Redis düşerse fan-out durur ama Kafka'daki veri güvende; Gateway düşerse tüm merkezi istemciler etkilenir | Merkezi backend kapanırsa yerel istemciler (edge WS) **etkilenmez** — saha operasyonu devam eder |
| Mevcut sisteme uyum | **Yüksek** — mevcut `SimpMessagingTemplate` kullanımını, sadece tetikleyiciyi (handler-içi senkron çağrı → Kafka consumer) değiştirerek korur | Düşük — yeni bileşen, yeni operasyonel yük | Zaten kısmen uygulanmış (edge WS var); merkezi taraf eklentisi Alt.1 ile aynı |
| **Öneri** | **Tek instance için yeterli ve önerilen** (bkz. ADR-006) | Yalnızca çoklu-instance ihtiyacı **kanıtlandığında** devreye alınır | Zaten doğru, korunmalı (yerel+merkezi ikisi birlikte) |

**Redis'in konumu (eğer/ne zaman gerekirse):** Backend gerçekten çoklu instance'a çıktığında, Redis Pub/Sub yalnızca **WS instance'lar arası fan-out** için eklenir — Kafka'nın yerine geçmez, durable backbone olmaya devam eder. Bugün bu ihtiyaç kanıtlanmamış (tek instance) → **şimdi eklenmiyor**.

## 4. State Recovery: Snapshot / Event Replay / İkisi

| Alternatif | Avantaj | Dezavantaj |
|---|---|---|
| Yalnızca snapshot | Basit, tek event tipi okunur | Ara geçişlerin geçmişi kaybolur (analytics için sınırlı) |
| Yalnızca event replay (Kafka'dan baştan okuma) | Tam geçmiş korunur | Yavaş, büyük partition'larda pahalı; reconnect gecikmesi artar |
| **İkisi birlikte (önerilen)** | Reconnect/restart → hızlı snapshot; analytics/denetim → delta event geçmişi Kafka'da zaten duruyor (retention süresince) | İki event tipinin birlikte yönetimi (madde 1 ile aynı) |

## 5. Ordering: Timestamp / Aggregate Version

| Alternatif | Avantaj | Dezavantaj |
|---|---|---|
| Timestamp bazlı | Basit, ek alan gerekmez (zaten var) | Saat senkronizasyon sorunlarına (edge/backend clock skew) karşı kırılgan |
| **Aggregate version (monotonic sayaç, önerilen)** | Saat senkronuna bağımlı değil, "eski event'i reddet" kuralı net (`version <= mevcut ise reddet`) | Edge'de her slot için bir sayaç tutma ihtiyacı (StateStore'a küçük bir ek) |

## 6. Ölçeklenebilirlik ve Performans (varsayımsal senaryolar — ASSUMPTION olarak işaretli)

> Gerçek ölçüm yok; aşağıdaki sayılar mevcut kod parametrelerinden (confirm_seconds=4s, frame_interval, QoS1) türetilen **kaba varsayımlardır**, üretim ölçümüyle doğrulanmalıdır.

| Ölçek | Kamera (varsayım: park başına ~2) | Slot/kamera (varsayım: ~20) | Inference FPS ≠ event sıklığı |
|---|---|---|---|
| 1 otopark | 2 kamera | ~40 slot | FPS yüksek olabilir (örn. 1-5 FPS/kamera), ama değişiklik-tetiklemeli event hacmi bundan çok daha düşük — bir slotun saatte birkaç kez durum değiştirmesi tipik (**ASSUMPTION**) |
| 10 otopark | 20 kamera | ~400 slot | Kafka throughput'u hâlâ önemsiz (saniyede birkaç event); asıl darboğaz muhtemelen GPU inference kapasitesi, Kafka değil |
| 100 otopark | 200 kamera | ~4.000 slot | Kafka partition sayısı/consumer paralelliği gözden geçirilmeli (parkInfoId:slotId key ile partition sayısı consumer paralelliğini belirler); single-broker SPOF (GAP #8) bu ölçekte **P0**'a yükselir |
| 1.000 otopark | 2.000 kamera | ~40.000 slot | Çok-broker Kafka cluster, WebSocket bağlantı sayısı için backend yatay ölçekleme (→ Redis fan-out ihtiyacı burada **kanıtlanmış** sayılır), merkezi backend consumer kapasitesi ayrı bir kapasite planlaması gerektirir |

**Önemli ayrım (kullanıcının vurguladığı):** Inference FPS (GPU/CPU'nun ürettiği kare işleme hızı) ile Kafka event throughput'u (StateStore'un onayladığı DEĞİŞİKLİK sayısı) **aynı şey değildir** ve mevcut mimaride zaten ayrışmıştır (CURRENT_STATE §3) — GPU/CPU kapasitesi tek başına Kafka/WebSocket yükünü belirlemez, asıl belirleyici fiziksel doluluk değişim sıklığıdır.

**Darboğaz ayrımı (varsayımsal, ölçülmedi):**
- GPU inference kapasitesi → kamera başına FPS'i sınırlar, event hacmini değil.
- SQLite disk I/O (eklenecek outbox için) → tek-process yazım hızı, düşük hacimde (saniyede birkaç satır) sorun teşkil etmez.
- WebSocket bağlantı yönetimi → merkezi backend'in instance başına tutabileceği açık bağlantı sayısı, istemci (mobil+web) sayısıyla orantılı, kamera/slot sayısıyla değil.

## 7. Karar Tablosu (Bölüm 6 — özet)

| Karar | Seçilen alternatif | Gerekçe (kısa — tam ADR'de) |
|---|---|---|
| State değişikliği | Doğrulanmış değişiklik (mevcut, korunuyor) | Zaten doğru |
| Event modeli | Delta (normal) + Snapshot (senkronizasyon) | §1 |
| Slot kimliği | Park-bazlı merkezi mapping, kalıcı `parkSlotId` | §2 |
| Edge storage | SQLite (WAL) | ADR-004 |
| Event delivery | Outbox (mevcut best-effort publish'in üstüne) | ADR-004 |
| Kafka topic | Delta ve snapshot ayrı topic | ADR-001 |
| Partition key | `parkInfoId:slotId` | ADR-005 |
| Local client | Edge WS (korunuyor) | §3 |
| Central client | Backend WS (Kafka consumer tetikli) | §3, Alt.1 |
| Fan-out | Doğrudan (Redis yalnızca kanıtlanmış çoklu-instance ihtiyacında) | §3 |
| State recovery | Snapshot + event replay (Kafka retention süresince) | §4 |
| Ordering | Aggregate version | §5 |
