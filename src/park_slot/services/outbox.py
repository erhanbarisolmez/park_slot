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

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS slot_state (
                    slot_id    INTEGER PRIMARY KEY,
                    status     TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0,
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
                """
            )

    def record_change(self, slot_id: int, status: str, confidence: float) -> None:
        """slot_state (son bilinen durum) ve outbox (gönderim kuyruğu) satırlarını
        tek transaction'da yazar. Hata olursa `with self._conn:` otomatik rollback
        yapar (hiçbir tablo değişmeden kalır) ve exception yukarı fırlatılır -
        çağıran (on_slot_change) bunu loglayıp yutar."""
        now = int(self._clock())
        with self._lock:
            with self._conn:
                row = self._conn.execute(
                    "SELECT status FROM slot_state WHERE slot_id = ?", (slot_id,)
                ).fetchone()
                prev_status = row[0] if row else None

                self._conn.execute(
                    "INSERT INTO slot_state (slot_id, status, confidence, updated_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(slot_id) DO UPDATE SET "
                    "status=excluded.status, confidence=excluded.confidence, "
                    "updated_at=excluded.updated_at",
                    (slot_id, status, confidence, now),
                )

                next_version = self._conn.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM outbox WHERE slot_id = ?",
                    (slot_id,),
                ).fetchone()[0]

                self._conn.execute(
                    "INSERT INTO outbox (slot_id, status, prev_status, version, "
                    "created_at, sent_at, attempt_count) VALUES (?, ?, ?, ?, ?, NULL, 0)",
                    (slot_id, status, prev_status, next_version, now),
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
    ):
        self._outbox = outbox
        self._publisher = publisher
        self._poll_interval = poll_interval
        self._retention_days = retention_days
        self._purge_every_n_ticks = max(1, int(3600 / poll_interval))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def flush_once(self) -> bool:
        """Bekleyen kayıt yoksa no-op (True). Varsa son bilinen TAM anlık
        görüntüyü publish_raw ile göndermeyi dener; başarılı olursa o anda
        bekleyen satırları 'gönderildi' işaretler, başarısız olursa
        attempt_count'u artırır ve satırlar bir sonraki tick için bekler."""
        snapshot, max_id = self._outbox.read_pending()
        if max_id is None:
            return True

        if self._publisher.publish_raw(snapshot):
            self._outbox.mark_sent_up_to(max_id)
            return True

        self._outbox.record_attempt_failure()
        return False

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

            self._stop_event.wait(self._poll_interval)
