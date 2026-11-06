"""youtube_history birim testleri."""

from park_slot.capture.youtube_history import compute_target_sequence


def test_zero_minutes_returns_live_sequence():
    """0 dakika önce = canlı sequence."""
    assert compute_target_sequence(live_sq=1000, minutes_ago=0) == 1000


def test_one_hour_ago():
    """1 saat = 3600s / 2.0s fragman = 1800 fragman geriye."""
    assert compute_target_sequence(live_sq=10_000, minutes_ago=60) == 8200


def test_thirty_seconds_rounds_to_nearest_fragment():
    """30 saniye (0.5 dakika) = 15 fragman geriye."""
    assert compute_target_sequence(live_sq=100, minutes_ago=0.5) == 85


def test_custom_fragment_duration():
    """Farklı fragman süresiyle hesaplama doğru olmalı."""
    assert compute_target_sequence(live_sq=100, minutes_ago=1, fragment_seconds=1.0) == 40
