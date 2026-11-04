"""StateStore birim testleri."""

from park_slot.annotation.schema import SlotStatus
from park_slot.services.state_store import StateStore


def test_update_and_get():
    """Slot güncelle ve oku."""
    store = StateStore(camera_id="test")
    store.update_slot(1, "S1", SlotStatus.EMPTY, 0.9)

    slot = store.get_slot(1)
    assert slot is not None
    assert slot.status == SlotStatus.EMPTY
    assert slot.confidence == 0.9


def test_batch_update():
    """Toplu güncelleme."""
    store = StateStore(camera_id="test")
    store.update_batch([
        (1, "S1", SlotStatus.EMPTY, 0.9),
        (2, "S2", SlotStatus.OCCUPIED, 0.85),
        (3, "S3", SlotStatus.UNKNOWN, 0.5),
    ])

    status = store.get_status()
    assert status.total_slots == 3
    assert status.empty == 1
    assert status.occupied == 1
    assert status.unknown == 1


def test_callback_on_change():
    """Durum değişince callback çağrılmalı."""
    store = StateStore(camera_id="test")
    changes = []
    store.on_change(lambda state: changes.append(state))

    store.update_slot(1, "S1", SlotStatus.EMPTY, 0.9)
    assert len(changes) == 1

    # Aynı durum tekrar yazılırsa callback çağrılmamalı
    store.update_slot(1, "S1", SlotStatus.EMPTY, 0.95)
    assert len(changes) == 1

    # Durum değişirse callback çağrılmalı
    store.update_slot(1, "S1", SlotStatus.OCCUPIED, 0.8)
    assert len(changes) == 2
