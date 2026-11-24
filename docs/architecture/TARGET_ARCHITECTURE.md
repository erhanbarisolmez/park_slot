# TARGET_ARCHITECTURE.md — Önerilen Hedef Mimari

> Bu doküman **önerilen** hedef durumu anlatır — [CURRENT_STATE.md](./CURRENT_STATE.md)'deki mevcut durumla karıştırılmamalıdır. Hiçbir değişiklik bu konuşmada uygulanmadı; bu bir tasarım önerisidir. Gerekçeler [ADR_DOCUMENTS.md](./ADR_DOCUMENTS.md)'de.

**Tasarım ilkesi:** Mevcut çalışan sistemi koru (StateStore, hysteresis/occlusion mantığı, Edge WS, DLQ altyapısı, Avro+Schema Registry hiç değişmiyor). Yalnızca 3 P0 boşluğu (edge güvenilirlik, Kafka'nın tüketilmemesi, WS'nin tek-instance'a kilitli olması) ve kimlik/versiyonlama boşluklarını kapat. Yeni servis, yeni domain, yeni veritabanı **yok**.

---

## 1. Kamera → PyTorch → Slot State Engine

```
┌─────────────┐   ┌──────────────────┐   ┌───────────────────┐
│ Camera Image │──▶│ PyTorch Inference │──▶│   ROI Detection    │
└─────────────┘   │ (CNN, conf=0.7)   │   │ (hibrit araç-dedek.)│
                   └──────────────────┘   └──────────┬─────────┘
                                                      │ updates[]
                                                      ▼
                                        ┌───────────────────────────┐
                                        │   Slot State Engine        │
                                        │  (StateStore — DEĞİŞMİYOR)  │
                                        │  hysteresis + occlusion +   │
                                        │  override                   │
                                        └──────────────┬─────────────┘
                                                        │ confirmed change
                                                        ▼
                                          [bkz. Diagram 2: SQLite→Outbox]
```
**Değişiklik yok** — bu katman zaten doğru tasarlanmış (CURRENT_STATE §3). Tek ek: kamera bağlantı kopmasında bir `last_frame_at` watchdog, eşik aşılırsa ilgili slotları `UNKNOWN`'a çeksin (GAP #5).

## 2. Slot State Engine → SQLite → Outbox → Kafka

```
StateStore._notify(confirmed_state)
        │
        ▼
┌───────────────────────────────────────────┐
│  SQLite (WAL mode, tek dosya)               │
│  BEGIN TRANSACTION                          │
│    UPDATE slot_state SET status=..., ...    │  ← son onaylı durum (restart kurtarma için)
│    INSERT INTO outbox (slot_id, status,     │  ← aynı transaction, atomik
│                 prev_status, version,       │
│                 created_at, sent_at=NULL)   │
│  COMMIT                                     │
└───────────────────┬─────────────────────────┘
                     │ poller thread (ayrı, periyodik)
                     ▼
         ┌─────────────────────────┐
         │ SELECT * FROM outbox     │
         │ WHERE sent_at IS NULL    │
         │ ORDER BY id LIMIT N      │
         └───────────┬─────────────┘
                      │ MQTT publish (delta event)
                      ▼
              ┌───────────────┐        başarılı → UPDATE outbox SET sent_at=now()
              │  MQTT Broker   │        başarısız → attempt_count++, bir sonraki pollde tekrar dene
              └───────┬───────┘
                      ▼
        backend/mqtt/CameraDetectionMqttHandler
                      │
          ┌───────────┴────────────┐
          ▼                        ▼
   DB yazımı (senkron)      Kafka publish (Avro, async)
          │                        │
          ▼                        ▼
   [Diagram 4: Kafka→Backend→WS]   selfpark.event.cameraSlotChanged.v1 (delta)
                                   selfpark.event.cameraSlotSnapshot.v1 (snapshot, ayrı topic)
```
**Yeni:** SQLite dosyası + outbox tablosu + poller thread (edge'de, mevcut `MqttPublisher`'ın yerini değil, önüne/üstüne geçer). **Değişmeyen:** StateStore mantığı, MQTT broker, backend handler'ın DB-yazım adımı.

## 3. Edge Local WebSocket → Local Clients

```
StateStore._notify ──▶ api/ws.py (/ws/live) ──▶ Yerel tarayıcı istemciler (/, /live, /mobile)
```
**Değişiklik yok** (CURRENT_STATE §9). İnternet/merkezi backend kesintisinde saha operasyonu bağımsız çalışmaya devam eder — bu zaten doğru ve korunmalı.

## 4. Kafka → Central Backend → Central WebSocket → Remote Clients

```
selfpark.event.cameraSlotChanged.v1 (delta, key=parkInfoId:slotId)
        │
        ▼
┌─────────────────────────────┐
│ @KafkaListener (YENİ)        │   ← bugün bu yok, topic publish-only (GAP #2)
│ CameraSlotChangeConsumer     │
└──────────────┬───────────────┘
               │ ParkOccupancyManager.applyChange(...)  ← mevcut DB yazım mantığı taşınır, değişmez
               ▼
        ParkSlotOccupancy (DB, projeksiyon/cache — otorite değil)
               │
               ▼
     SimpMessagingTemplate.convertAndSend("/topic/parkOccupancy/{parkInfoId}")
               │
               ▼
     Remote Clients (mobil/web, STOMP WebSocket)
```
**Değişen:** `CameraDetectionMqttHandler`'daki senkron DB+WS adımı, bir Kafka consumer'a taşınır (handler artık yalnızca MQTT→Kafka publish yapar). **Kazanım:** Kafka fiilen omurga olur (GAP #2 kapanır), çoklu backend instance'ında her instance aynı consumer group'tan okuyup kendi WS session'larına yayın yapabilir hale gelir (GAP #3 kapanır — tam yatay ölçek için Redis fan-out hâlâ gerekebilir, bkz. ARCHITECTURE_OPTIONS §3, ama bu adım onun ön koşulu).

## 5. Kafka → Anomaly Detection → Alarm Events

```
selfpark.event.cameraSlotChanged.v1
        │
        ▼ (P3 — şimdi zorunlu değil, Kafka tüketimi kurulduktan SONRA ayrı karar)
┌───────────────────────────────┐
│ anomaly_detection (yeni, küçük  │
│ bir detector — flip-rate/      │
│ occlusion sinyalleri)           │
└───────────────────────────────┘
```
Bugün `anomaly_detection` bu veriyle bağlantısız (CURRENT_STATE §6). Diagram 4 tamamlanmadan bu adımın bir anlamı yok — sıralama önemli (bkz. IMPLEMENTATION_ROADMAP).

## 6. Reconnect → Snapshot → Reconciliation

```
Edge yeniden bağlanır (process restart VEYA MQTT broker reconnect VEYA uzun offline sonrası)
        │
        ▼
Edge: SQLite'daki son onaylı state'ten TAM bir Snapshot event üretir
        │  (selfpark.event.cameraSlotSnapshot.v1, ayrı topic)
        ▼
Backend Snapshot Consumer:
  ParkSlotOccupancy[parkInfoId] = snapshot.slots   ← edge'in versiyonu HER ZAMAN kazanır
  (edge = state otoritesi, backend = projeksiyon — çakışmada edge esas alınır)
        │
        ▼
SimpMessagingTemplate.convertAndSend(...) ← istemciler de böylece senkronlanır

Periyodik (örn. 5 dk'da bir, drift'i önlemek için):
  Edge → aynı Snapshot event'i gönderir (reconnect'ten bağımsız, düzenli reconciliation)
```
**Kazanım:** GAP tablosundaki "edge uzun süre offline kalır" ve "eski event gelir" senaryoları (ARCHITECTURE_GAP_ANALYSIS §2) için somut bir mekanizma — event replay değil, basit snapshot-üzerine-yaz modeli (karmaşıklığı düşük tutuyor, ADR-008).

---

## Bileşen Sorumlulukları ve Veri Sahipliği (hedef durumda)

| Katman | Sorumluluk | Veri Sahipliği |
|---|---|---|
| PyTorch | Ham tahmin (empty/occupied + confidence) | Hiçbiri — sadece girdi üretir |
| Edge Slot State Engine (StateStore) | Onaylı (confirmed) state hesaplama | **Otorite** — gerçek state'in tek sahibi |
| SQLite (edge) | Son state + outbox kalıcılığı | Edge otoritesinin kalıcı yansıması |
| Kafka | Durable event backbone (delta + snapshot) | Taşıyıcı, sahiplik taşımaz — ama artık fiilen tüketiliyor |
| Central Backend (`ParkSlotOccupancy`) | Merkezi iş projeksiyonu | **Cache/projeksiyon**, otorite değil — çakışmada edge kazanır |
| Redis (opsiyonel, P3) | Yalnızca WS instance'lar arası fan-out (gerekirse) | Hiçbiri — kalıcı değil |
| Client | Görüntülenen state | Hiçbiri — her zaman son alınan snapshot/delta'yı yansıtır |
