"""training.dataset.group_split birim testleri (sızıntısız train/val bölme)."""

from pathlib import Path

from park_slot.training.dataset import group_split, parse_slot_and_time


def _sample(slot_id: int, timestamp_ms: int, label: int = 0) -> tuple[Path, int]:
    return Path(f"slot{slot_id}_{timestamp_ms}.jpg"), label


def test_parse_slot_and_time_extracts_from_filename():
    slot_id, ts = parse_slot_and_time(Path("slot7_1700000000123.jpg"))
    assert slot_id == 7
    assert ts == 1700000000123


def test_parse_slot_and_time_fallback_for_unknown_format():
    slot_id, _ts = parse_slot_and_time(Path("rastgele_dosya.jpg"))
    assert slot_id == -1


def test_group_split_keeps_same_slot_same_window_together():
    """Aynı slotun aynı ~30dk'lık penceresindeki kareleri bölünmemeli."""
    base_ts = 1_700_000_000_000
    # Slot 1'in aynı pencereden (30dk içinde) 5 karesi
    samples = [_sample(1, base_ts + i * 1000) for i in range(5)]
    # Slot 2'nin tamamen farklı (uzak) bir zaman penceresinden 5 karesi
    samples += [_sample(2, base_ts + 10 * 60 * 60 * 1000 + i * 1000) for i in range(5)]

    train_idx, val_idx = group_split(samples, train_ratio=0.5, seed=1)

    slot1_indices = set(range(5))
    slot2_indices = set(range(5, 10))

    # Her grup (slot1, slot2) kendi içinde tamamen train'de ya da tamamen val'de olmalı
    assert slot1_indices.issubset(train_idx) or slot1_indices.issubset(val_idx)
    assert slot2_indices.issubset(train_idx) or slot2_indices.issubset(val_idx)
    assert set(train_idx) | set(val_idx) == set(range(10))
    assert set(train_idx) & set(val_idx) == set()


def test_group_split_ratio_is_approximately_respected():
    base_ts = 1_700_000_000_000
    # 20 farklı (slot, zaman-penceresi) grubu, her biri 5 örnek
    samples = []
    for g in range(20):
        samples += [_sample(g, base_ts + g * 60 * 60 * 1000 + i * 1000) for i in range(5)]

    train_idx, val_idx = group_split(samples, train_ratio=0.8, seed=42)

    total = len(samples)
    assert 0.6 <= len(train_idx) / total <= 0.95
    assert len(train_idx) + len(val_idx) == total


def test_group_split_is_deterministic_with_same_seed():
    base_ts = 1_700_000_000_000
    samples = [_sample(i, base_ts + i * 60 * 60 * 1000) for i in range(10)]

    run1 = group_split(samples, train_ratio=0.7, seed=5)
    run2 = group_split(samples, train_ratio=0.7, seed=5)
    assert run1 == run2
