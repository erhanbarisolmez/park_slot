"""SQLite tabanlı transactional outbox - MQTT gönderiminin process restart ve
broker kesintilerine dayanıklı olmasını sağlar (bkz. docs/architecture/ADR_DOCUMENTS.md
ADR-004, IMPLEMENTATION_ROADMAP.md Aşama 1).

Akış: StateStore.on_change -> Outbox.on_slot_change (slot_state + outbox'a tek
transaction'da yazar) -> OutboxWorker (ayrı thread, periyodik olarak bekleyen
kayıtları MQTT'ye göndermeyi dener). MQTT'ye giden TEK yol bu worker'dır -
StateStore'dan MqttPublisher'a doğrudan bir canlı-publish yolu YOK (bkz.
__main__.py); böylece "gönderildi" durumu her zaman tek bir yerden yönetilir.
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from park_slot.annotation.schema import SlotState
from park_slot.services.ntp_check import check_clock_sync

logger = logging.getLogger(__name__)


class Outbox:
    def __init__(self, db_path: str, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()
        # Aşama 7 (PR b, GAP #12): process başına BİR KEZ, kalıcı epoch.
        # Constructor'da otomatik üretilir (_init_schema ile aynı desen) -
        # OutboxWorker.flush_once/snapshot_once çağrılmadan önce her zaman hazır.
        self.epoch = self.load_or_generate_epoch()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS slot_state (
                    slot_id    INTEGER PRIMARY KEY,
                    status     TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0,
                    seq        INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS outbox (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    slot_id       INTEGER NOT NULL,
                    status        TEXT NOT NULL,
                    prev_status   TEXT,
                    version       INTEGER NOT NULL,
                    created_at    INTEGER NOT NULL,
                    sent_at       INTEGER,
                    attempt_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox (sent_at)
                    WHERE sent_at IS NULL;

                -- Aşama 7 (PR b): tek satırlık, process başına bir kez üretilen
                -- kalıcı epoch kaynağı (bkz. load_or_generate_epoch) - GAP #12'nin
                -- version'ı artık outbox geçmişinden DEĞİL, bu tablo + slot_state.seq'ten
                -- geliyor, restart/retention purge'den bağımsız.
                CREATE TABLE IF NOT EXISTS process_epoch (
                    id    INTEGER PRIMARY KEY CHECK (id = 0),
                    epoch INTEGER NOT NULL
                );
                """
            )
            # Bu PR'dan ÖNCE oluşturulmuş bir slot_state dosyası seq kolonunu
            # taşımayabilir - CREATE TABLE IF NOT EXISTS bu durumda hiçbir şey
            # yapmaz, idempotent bir ALTER TABLE ile tamamlanır.
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(slot_state)")}
            if "seq" not in columns:
                self._conn.execute("ALTER TABLE slot_state ADD COLUMN seq INTEGER NOT NULL DEFAULT 0")
                # code-review bulgusu: DEFAULT 0 ile eklenen kolon, bu PR'dan ÖNCE
                # zaten ilerlemiş bir slotun sayacını migration sınırında geriye
                # sıfırlardı (tam da GAP #12'nin önlemeye çalıştığı regresyonun
                # kendisi) - eski outbox geçmişindeki MAX(version)'dan backfill
                # ederek bu sınırda sürekliliği korur. Taze bir DB'de (slot_state
                # boş) no-op.
                self._conn.execute(
                    "UPDATE slot_state SET seq = ("
                    "  SELECT COALESCE(MAX(o.version), 0) FROM outbox o WHERE o.slot_id = slot_state.slot_id"
                    ")"
                )

    def load_or_generate_epoch(self) -> int:
        """Aşama 7 (PR b): process başına BİR KEZ üretilen, restart'tan bağımsız
        kalıcı epoch (ms). Önceki bir çalıştırmadan kalan epoch varsa, yeni
        epoch en azından bir fazlası olacak şekilde garanti edilir - sistem
        saati geri alınmış bir restart'ta bile epoch asla eşit/geriye gitmez."""
        with self._lock:
            with self._conn:
                row = self._conn.execute(
                    "SELECT epoch FROM process_epoch WHERE id = 0"
                ).fetchone()
                now_ms = int(self._clock() * 1000)
                new_epoch = max(now_ms, row[0] + 1) if row else now_ms
                self._conn.execute(
                    "INSERT INTO process_epoch (id, epoch) VALUES (0, ?) "
                    "ON CONFLICT(id) DO UPDATE SET epoch = excluded.epoch",
                    (new_epoch,),
                )
                return new_epoch

    def record_change(self, slot_id: int, status: str, confidence: float) -> None:
        """slot_state (son bilinen durum) ve outbox (gönderim kuyruğu) satırlarını
        tek transaction'da yazar. Hata olursa `with self._conn:` otomatik rollback
        yapar (hiçbir tablo değişmeden kalır) ve exception yukarı fırlatılır -
        çağıran (on_slot_change) bunu loglayıp yutar."""
        now = int(self._clock())
        with self._lock:
            with self._conn:
                row = self._conn.execute(
                    "SELECT status, seq FROM slot_state WHERE slot_id = ?", (slot_id,)
                ).fetchone()
                prev_status = row[0] if row else None
                # Aşama 7 (PR b, GAP #12): next_seq artık slot_state'in KENDİ
                # kalıcı sayacından geliyor - outbox geçmişinden MAX(version)+1
                # hesaplamak (eski davranış) 7 günlük retention purge'den sonra
                # veya outbox tablosu başka bir sebeple boşaldığında sessizce
                # 1'den başlardı. slot_state purge'den hiç etkilenmiyor.
                next_seq = (row[1] if row else 0) + 1

                self._conn.execute(
                    "INSERT INTO slot_state (slot_id, status, confidence, seq, updated_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(slot_id) DO UPDATE SET "
                    "status=excluded.status, confidence=excluded.confidence, "
                    "seq=excluded.seq, updated_at=excluded.updated_at",
                    (slot_id, status, confidence, next_seq, now),
                )

                self._conn.execute(
                    "INSERT INTO outbox (slot_id, status, prev_status, version, "
                    "created_at, sent_at, attempt_count) VALUES (?, ?, ?, ?, ?, NULL, 0)",
                    (slot_id, status, prev_status, next_seq, now),
                )

    def on_slot_change(self, state: SlotState) -> None:
        """StateStore.on_change callback'i."""
        try:
            self.record_change(state.id, state.status.value.upper(), state.confidence)
        except Exception as e:
            logger.error("Outbox yazımı başarısız (slot_id=%s): %s", state.id, e)

    def pending_count(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL"
            ).fetchone()[0]

    def read_pending(self) -> tuple[dict, Optional[int]]:
        """Tek, atomik okuma: güncel slot_state TAM anlık görüntüsü + o anda
        bekleyen outbox satırlarının en büyük id'si. Bekleyen yoksa ({}, None)
        döner. max_id, mark_sent_up_to ile birlikte kullanılır - publish ağ
        çağrısı sürerken araya giren YENİ bir değişikliğin yanlışlıkla
        'gönderildi' işaretlenmesini önlemek için."""
        with self._lock:
            max_id = self._conn.execute(
                "SELECT MAX(id) FROM outbox WHERE sent_at IS NULL"
            ).fetchone()[0]
            if max_id is None:
                return {}, None
            rows = self._conn.execute("SELECT slot_id, status FROM slot_state").fetchall()
            return {slot_id: status for slot_id, status in rows}, max_id

    def read_full_state(self) -> dict[int, str]:
        """slot_state tablosunun TAM anlık görüntüsü - outbox'taki bekleyen
        durumdan bağımsız. Sadece restart/ilk bağlantıda BİR KEZ (ADR-001
        snapshot, bkz. __main__.py) kullanılır; periyodik reconciliation
        (ADR-008) bu turun kapsamı dışında."""
        with self._lock:
            rows = self._conn.execute("SELECT slot_id, status FROM slot_state").fetchall()
            return {slot_id: status for slot_id, status in rows}

    def read_pending_changes(self, limit: int = 200) -> list[tuple[int, int, str, Optional[str], int]]:
        """Bekleyen outbox satırlarını id sırasıyla (id, slot_id, status,
        prev_status, version) olarak döner - ADR-001 delta yayını için
        OutboxWorker'ın kullandığı yol. read_pending (tam snapshot) hâlâ ayrı
        bir yetenek olarak duruyor, artık OutboxWorker tarafından çağrılmıyor."""
        with self._lock:
            return self._conn.execute(
                "SELECT id, slot_id, status, prev_status, version FROM outbox "
                "WHERE sent_at IS NULL ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()

    def mark_sent_up_to(self, max_id: int) -> None:
        now = int(self._clock())
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE outbox SET sent_at = ? WHERE sent_at IS NULL AND id <= ?",
                    (now, max_id),
                )

    def record_attempt_failure(self) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE outbox SET attempt_count = attempt_count + 1 WHERE sent_at IS NULL"
                )

    def purge_sent_older_than(self, days: int) -> None:
        cutoff = int(self._clock()) - days * 86400
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "DELETE FROM outbox WHERE sent_at IS NOT NULL AND created_at < ?",
                    (cutoff,),
                )

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class OutboxWorker:
    """Periyodik olarak outbox'taki bekleyen kayıtları MQTT'ye göndermeyi
    deneyen arka plan thread'i - MQTT'ye giden tek yol budur (bkz. modül
    docstring'i)."""

    def __init__(
        self,
        outbox: Outbox,
        publisher,
        poll_interval: float = 5.0,
        retention_days: int = 7,
        snapshot_interval: float = 300.0,
        ntp_server: str = "pool.ntp.org",
        ntp_drift_threshold_seconds: float = 5.0,
        ntp_check_interval_seconds: float = 300.0,
        ntp_check_fn: Callable[[str, float], bool] = check_clock_sync,
    ):
        self._outbox = outbox
        self._publisher = publisher
        self._poll_interval = poll_interval
        self._retention_days = retention_days
        self._purge_every_n_ticks = max(1, int(3600 / poll_interval))
        # ADR-008 Seçenek 3, (b) bacağı: reconnect/restart'a bağlı snapshot
        # (MqttPublisher.enable_startup_snapshot, on_connect'e bağlı) zaten
        # var - bu, bağlantı KOPMADAN da periyodik drift düzeltmesi sağlar.
        self._snapshot_every_n_ticks = max(1, int(snapshot_interval / poll_interval))
        # Aşama 7 (PR b): NTP senkron kontrolü - process başlangıcında (tick=0)
        # ve periyodik olarak tekrarlanır. Drift/erişilemezlik tespit edilirse
        # yayın (flush_once/snapshot_once) durur, ama outbox/slot_state yazımı
        # ASLA durmaz - sadece MQTT'ye giden yol kapanır, outbox birikir.
        self._ntp_server = ntp_server
        self._ntp_drift_threshold_seconds = ntp_drift_threshold_seconds
        self._ntp_check_every_n_ticks = max(1, int(ntp_check_interval_seconds / poll_interval))
        self._ntp_check_fn = ntp_check_fn
        self._publishing_paused = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def flush_once(self) -> bool:
        """ADR-001: bekleyen kayıt yoksa no-op (True). Varsa her satırı id
        sırasıyla TEK BİR delta mesajı olarak göndermeyi dener (publish_raw/tam
        snapshot artık burada kullanılmıyor - o sadece restart/ilk bağlantıda
        bir kez, __main__.py'de). İlk başarısız satırda durur (sıra garantisi);
        o noktaya kadar başarıyla gönderilenler 'gönderildi' işaretlenir,
        kalanlar (ve bir sonraki tick'te eklenecek yeni satırlar) attempt_count
        artırılıp bir sonraki tick'e bırakılır.

        Aşama 7 (PR b): self._publishing_paused true ise (NTP drift/erişim
        hatası) no-op (True) döner - outbox birikmeye devam eder, yayın yapılmaz."""
        if self._publishing_paused:
            return True

        rows = self._outbox.read_pending_changes()
        if not rows:
            return True

        last_sent_id = None
        for row_id, slot_id, status, prev_status, version in rows:
            if self._publisher.publish_slot_changed(
                slot_id, prev_status, status, version, epoch=self._outbox.epoch
            ):
                last_sent_id = row_id
            else:
                break

        if last_sent_id is not None:
            self._outbox.mark_sent_up_to(last_sent_id)

        if last_sent_id == rows[-1][0]:
            return True

        self._outbox.record_attempt_failure()
        return False

    def snapshot_once(self) -> bool:
        """ADR-008 periyodik reconciliation: outbox'taki durumdan BAĞIMSIZ
        (delta kuyruğu boş olsa da), slot_state tablosunun tam anlık
        görüntüsünü snapshot kanalından (publish_raw) gönderir. Bu, bağlantı
        olayına bağlı enable_startup_snapshot'tan farklı - zaman tabanlı,
        broker bağlantısı hiç kopmasa bile periyodik drift düzeltmesi sağlar.

        Aşama 7 (PR b): self._publishing_paused true ise no-op (True) döner."""
        if self._publishing_paused:
            return True

        full_state = self._outbox.read_full_state()
        if not full_state:
            return True
        return self._publisher.publish_raw(full_state, epoch=self._outbox.epoch)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="outbox-worker")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        tick = 0
        while not self._stop_event.is_set():
            if tick % self._ntp_check_every_n_ticks == 0:
                try:
                    self._publishing_paused = not self._ntp_check_fn(
                        self._ntp_server, self._ntp_drift_threshold_seconds
                    )
                except Exception as e:
                    logger.error("NTP senkron kontrolü başarısız: %s", e)
                    self._publishing_paused = True

            try:
                self.flush_once()
            except Exception as e:
                logger.error("Outbox flush hatası: %s", e)

            tick += 1
            if tick % self._purge_every_n_ticks == 0:
                try:
                    self._outbox.purge_sent_older_than(self._retention_days)
                except Exception as e:
                    logger.error("Outbox temizliği başarısız: %s", e)

            if tick % self._snapshot_every_n_ticks == 0:
                try:
                    self.snapshot_once()
                except Exception as e:
                    logger.error("Periyodik snapshot hatası: %s", e)

            self._stop_event.wait(self._poll_interval)
