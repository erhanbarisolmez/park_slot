"""Outbox/OutboxWorker birim testleri - gerçek broker/dosya sistemi dışı,
geçici sqlite dosyası (tmp_path) kullanılır (repodaki mevcut tmp_path fixture
deseniyle aynı stil, bkz. test_collector.py)."""

from park_slot.annotation.schema import SlotState, SlotStatus
from park_slot.services.outbox import Outbox, OutboxWorker


def make_clock(step: float = 1.0):
    """StateStore testlerindeki aynı desen - gerçek time.sleep() yapmadan
    zamanı ilerleten sahte saat."""
    state = {"t": 0.0}

    def clock() -> float:
        state["t"] += step
        return state["t"]

    return clock


class FakePublisher:
    """MqttPublisher'ın yerine geçen, publish_raw/publish_slot_changed
    çağrılarını kaydeden sahte publisher - FakeMqttClient.raise_on_publish'le
    aynı üslup. fail_on_slot_id verilirse, sadece o slot_id'deki çağrı
    başarısız olur (kısmi başarı senaryolarını test etmek için)."""

    def __init__(self, succeed: bool = True, fail_on_slot_id: int | None = None):
        self.succeed = succeed
        self.fail_on_slot_id = fail_on_slot_id
        self.calls: list[dict] = []
        self.slot_changed_calls: list[tuple] = []

    def publish_raw(self, slot_statuses: dict) -> bool:
        self.calls.append(dict(slot_statuses))
        return self.succeed

    def publish_slot_changed(self, slot_id: int, prev_status, status: str, version: int) -> bool:
        self.slot_changed_calls.append((slot_id, prev_status, status, version))
        if self.fail_on_slot_id is not None and slot_id == self.fail_on_slot_id:
            return False
        return self.succeed


def test_record_change_writes_slot_state_and_outbox(tmp_path):
    outbox = Outbox(db_path=str(tmp_path / "outbox.db"))

    outbox.record_change(1, "OCCUPIED", 0.95)

    assert outbox.pending_count() == 1
    snapshot, max_id = outbox.read_pending()
    assert snapshot == {1: "OCCUPIED"}
    assert max_id == 1


def test_record_change_tracks_prev_status_and_version(tmp_path):
    outbox = Outbox(db_path=str(tmp_path / "outbox.db"))

    outbox.record_change(1, "EMPTY", 0.9)
    outbox.record_change(1, "OCCUPIED", 0.95)

    row = outbox._conn.execute(
        "SELECT status, prev_status, version FROM outbox ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row == ("OCCUPIED", "EMPTY", 2)


def test_multiple_slots_snapshot(tmp_path):
    outbox = Outbox(db_path=str(tmp_path / "outbox.db"))

    outbox.record_change(1, "EMPTY", 0.9)
    outbox.record_change(2, "OCCUPIED", 0.9)

    snapshot, _ = outbox.read_pending()
    assert snapshot == {1: "EMPTY", 2: "OCCUPIED"}


class _FlakyConnWrapper:
    """sqlite3.Connection.execute C-level, salt-okunur bir attribute olduğu
    için doğrudan monkeypatch edilemiyor - gerçek connection'ı saran, belirli
    bir SQL'de hata fırlatan ince bir vekil. __enter__/__exit__ gerçek
    connection'a devredilir, böylece `with self._conn:` idiomunun exception'da
    rollback yapan davranışı korunur."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *args, **kwargs):
        if sql.startswith("INSERT INTO outbox"):
            raise RuntimeError("disk hatası simülasyonu")
        return self._conn.execute(sql, *args, **kwargs)

    def __enter__(self):
        return self._conn.__enter__()

    def __exit__(self, *exc_info):
        return self._conn.__exit__(*exc_info)


def test_record_change_rolls_back_on_failure(tmp_path):
    """outbox tablosuna INSERT sırasında hata olursa, aynı transaction'daki
    slot_state değişikliği de geri alınmalı (roadmap'in atomiklik kriteri)."""
    outbox = Outbox(db_path=str(tmp_path / "outbox.db"))
    outbox.record_change(1, "EMPTY", 0.9)  # başlangıç durumu

    real_conn = outbox._conn
    outbox._conn = _FlakyConnWrapper(real_conn)
    try:
        try:
            outbox.record_change(1, "OCCUPIED", 0.95)
            assert False, "exception bekleniyordu"
        except RuntimeError:
            pass
    finally:
        outbox._conn = real_conn

    # slot_state hâlâ eski durumda olmalı (rollback), yeni outbox satırı hiç eklenmemeli.
    row = outbox._conn.execute("SELECT status FROM slot_state WHERE slot_id=1").fetchone()
    assert row == ("EMPTY",)
    assert outbox.pending_count() == 1  # sadece ilk record_change'in satırı


def test_restart_simulation_preserves_unsent_rows(tmp_path):
    db_path = str(tmp_path / "outbox.db")
    first = Outbox(db_path=db_path)
    first.record_change(1, "OCCUPIED", 0.9)
    first.close()

    second = Outbox(db_path=db_path)
    assert second.pending_count() == 1
    snapshot, max_id = second.read_pending()
    assert snapshot == {1: "OCCUPIED"}
    assert max_id == 1


def test_mark_sent_up_to_does_not_drop_new_row_inserted_during_flush(tmp_path):
    """read_pending() ile max_id alındıktan SONRA, mark_sent_up_to'dan ÖNCE
    yeni bir değişiklik gelirse, o yeni satır 'gönderildi' sayılmamalı."""
    outbox = Outbox(db_path=str(tmp_path / "outbox.db"))
    outbox.record_change(1, "EMPTY", 0.9)

    _, max_id = outbox.read_pending()

    # Publish ağ çağrısı sürerken araya giren yeni değişiklik:
    outbox.record_change(2, "OCCUPIED", 0.9)

    outbox.mark_sent_up_to(max_id)

    assert outbox.pending_count() == 1
    snapshot, _ = outbox.read_pending()
    assert snapshot[2] == "OCCUPIED"


def test_purge_sent_older_than_removes_only_old_sent_rows(tmp_path):
    clock = make_clock(step=1.0)
    outbox = Outbox(db_path=str(tmp_path / "outbox.db"), clock=clock)

    outbox.record_change(1, "EMPTY", 0.9)
    _, max_id = outbox.read_pending()
    outbox.mark_sent_up_to(max_id)

    # retention_days=0 ile "şu an"dan eski her gönderilmiş satır silinsin.
    outbox.purge_sent_older_than(days=0)

    count = outbox._conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
    assert count == 0


def test_on_slot_change_callback_records_and_swallows_errors(tmp_path):
    outbox = Outbox(db_path=str(tmp_path / "outbox.db"))
    state = SlotState(id=5, label="S5", status=SlotStatus.OCCUPIED, confidence=0.88)

    outbox.on_slot_change(state)

    snapshot, _ = outbox.read_pending()
    assert snapshot == {5: "OCCUPIED"}


def test_flush_once_sends_each_pending_row_as_delta_and_marks_all_sent(tmp_path):
    """ADR-001: flush_once artık publish_raw (tam snapshot) değil,
    publish_slot_changed'i her bekleyen satır için ayrı ayrı çağırır."""
    outbox = Outbox(db_path=str(tmp_path / "outbox.db"))
    outbox.record_change(1, "OCCUPIED", 0.9)
    outbox.record_change(2, "EMPTY", 0.9)
    publisher = FakePublisher(succeed=True)
    worker = OutboxWorker(outbox, publisher)

    result = worker.flush_once()

    assert result is True
    assert publisher.calls == []  # publish_raw hiç çağrılmadı
    assert publisher.slot_changed_calls == [
        (1, None, "OCCUPIED", 1),
        (2, None, "EMPTY", 1),
    ]
    assert outbox.pending_count() == 0


def test_flush_once_retries_on_failure(tmp_path):
    outbox = Outbox(db_path=str(tmp_path / "outbox.db"))
    outbox.record_change(1, "OCCUPIED", 0.9)
    publisher = FakePublisher(succeed=False)
    worker = OutboxWorker(outbox, publisher)

    result = worker.flush_once()
    assert result is False
    assert outbox.pending_count() == 1

    publisher.succeed = True
    result = worker.flush_once()
    assert result is True
    assert outbox.pending_count() == 0


def test_flush_once_stops_at_first_failure_preserving_order(tmp_path):
    """İlk başarısız satırda durulmalı - sıra garantisi için, daha sonraki
    satırlar denenmemeli; başarıyla gönderilenler yine de 'gönderildi'
    işaretlenmeli."""
    outbox = Outbox(db_path=str(tmp_path / "outbox.db"))
    outbox.record_change(1, "OCCUPIED", 0.9)
    outbox.record_change(2, "EMPTY", 0.9)
    outbox.record_change(3, "UNKNOWN", 0.9)
    publisher = FakePublisher(succeed=True, fail_on_slot_id=2)
    worker = OutboxWorker(outbox, publisher)

    result = worker.flush_once()

    assert result is False
    # slot 1 gönderildi ve işaretlendi, slot 2'de durdu, slot 3 hiç denenmedi.
    assert [c[0] for c in publisher.slot_changed_calls] == [1, 2]
    assert outbox.pending_count() == 2  # slot 2 ve slot 3 hâlâ bekliyor


def test_flush_once_is_noop_when_nothing_pending(tmp_path):
    outbox = Outbox(db_path=str(tmp_path / "outbox.db"))
    publisher = FakePublisher(succeed=True)
    worker = OutboxWorker(outbox, publisher)

    assert worker.flush_once() is True
    assert publisher.calls == []
    assert publisher.slot_changed_calls == []


def test_snapshot_once_sends_full_slot_state_via_publish_raw(tmp_path):
    """ADR-008 periyodik reconciliation: outbox'ta bekleyen satır olmasa
    bile (delta kuyruğu boş), snapshot_once tam slot_state'i publish_raw
    üzerinden gönderir."""
    outbox = Outbox(db_path=str(tmp_path / "outbox.db"))
    outbox.record_change(1, "EMPTY", 0.9)
    outbox.record_change(2, "OCCUPIED", 0.9)
    _, max_id = outbox.read_pending()
    outbox.mark_sent_up_to(max_id)  # delta kuyruğu boşaltıldı

    publisher = FakePublisher(succeed=True)
    worker = OutboxWorker(outbox, publisher)

    assert worker.snapshot_once() is True
    assert publisher.calls == [{1: "EMPTY", 2: "OCCUPIED"}]


def test_snapshot_once_is_noop_when_no_slot_state_yet(tmp_path):
    outbox = Outbox(db_path=str(tmp_path / "outbox.db"))
    publisher = FakePublisher(succeed=True)
    worker = OutboxWorker(outbox, publisher)

    assert worker.snapshot_once() is True
    assert publisher.calls == []


def test_snapshot_once_propagates_publish_failure(tmp_path):
    outbox = Outbox(db_path=str(tmp_path / "outbox.db"))
    outbox.record_change(1, "EMPTY", 0.9)
    publisher = FakePublisher(succeed=False)
    worker = OutboxWorker(outbox, publisher)

    assert worker.snapshot_once() is False


def test_read_full_state_returns_all_slot_state_rows(tmp_path):
    """read_full_state - outbox'ta bekleyen olup olmadığından bağımsız, TAM
    slot_state görüntüsü (restart/ilk bağlantı snapshot'ı için, ADR-001)."""
    outbox = Outbox(db_path=str(tmp_path / "outbox.db"))
    outbox.record_change(1, "EMPTY", 0.9)
    outbox.record_change(2, "OCCUPIED", 0.9)
    _, max_id = outbox.read_pending()
    outbox.mark_sent_up_to(max_id)  # hepsi gönderildi işaretlendi

    # pending_count 0 olsa da slot_state hâlâ dolu olmalı.
    assert outbox.pending_count() == 0
    assert outbox.read_full_state() == {1: "EMPTY", 2: "OCCUPIED"}


def test_read_pending_changes_returns_rows_in_id_order(tmp_path):
    outbox = Outbox(db_path=str(tmp_path / "outbox.db"))
    outbox.record_change(1, "EMPTY", 0.9)
    outbox.record_change(2, "OCCUPIED", 0.9)
    outbox.record_change(1, "OCCUPIED", 0.95)  # slot 1 version 2

    rows = outbox.read_pending_changes()

    assert [(slot_id, status, version) for _id, slot_id, status, _prev, version in rows] == [
        (1, "EMPTY", 1),
        (2, "OCCUPIED", 1),
        (1, "OCCUPIED", 2),
    ]
