"""check_clock_sync birim testleri - gerçek NTP sunucusuna gitmez, sahte
client factory enjekte edilir (mevcut injectable-clock deseniyle aynı üslup)."""

from park_slot.services.ntp_check import check_clock_sync


class FakeNtpResponse:
    def __init__(self, offset: float):
        self.offset = offset


class FakeNtpClient:
    def __init__(self, offset: float = 0.0, raise_error: bool = False):
        self._offset = offset
        self._raise_error = raise_error

    def request(self, server: str, version: int = 3, timeout: float = 5.0):
        if self._raise_error:
            raise OSError("NTP sunucusuna erişilemedi (simülasyon)")
        return FakeNtpResponse(self._offset)


def test_check_clock_sync_within_threshold_returns_true():
    client = FakeNtpClient(offset=1.0)

    result = check_clock_sync("pool.ntp.org", threshold_seconds=5.0, ntp_client_factory=lambda: client)

    assert result is True


def test_check_clock_sync_exceeds_threshold_returns_false():
    client = FakeNtpClient(offset=12.0)

    result = check_clock_sync("pool.ntp.org", threshold_seconds=5.0, ntp_client_factory=lambda: client)

    assert result is False


def test_check_clock_sync_negative_offset_exceeding_threshold_returns_false():
    """Drift her iki yönde de (ileri/geri) aynı muameleyi görmeli - abs(offset)."""
    client = FakeNtpClient(offset=-12.0)

    result = check_clock_sync("pool.ntp.org", threshold_seconds=5.0, ntp_client_factory=lambda: client)

    assert result is False


def test_check_clock_sync_exactly_at_threshold_returns_true():
    client = FakeNtpClient(offset=5.0)

    result = check_clock_sync("pool.ntp.org", threshold_seconds=5.0, ntp_client_factory=lambda: client)

    assert result is True


def test_check_clock_sync_server_unreachable_is_fail_safe_false():
    """NTP sorgusu exception fırlatırsa (sunucuya erişilemiyor) - 'senkron
    doğrulanamıyor' ile 'senkron bozuk' aynı muameleyi görmeli (fail-safe)."""
    client = FakeNtpClient(raise_error=True)

    result = check_clock_sync("pool.ntp.org", threshold_seconds=5.0, ntp_client_factory=lambda: client)

    assert result is False


def test_check_clock_sync_factory_itself_raising_is_fail_safe_false():
    def failing_factory():
        raise RuntimeError("client oluşturulamadı")

    result = check_clock_sync("pool.ntp.org", threshold_seconds=5.0, ntp_client_factory=failing_factory)

    assert result is False


def test_check_clock_sync_passes_bounded_timeout_to_request():
    """code-review bulgusu: worst-case bloklanma süresini sınırlamak için
    request_timeout_seconds, client.request()'e gerçekten geçirilmeli."""
    captured = {}

    class RecordingClient:
        def request(self, server, version=3, timeout=5.0):
            captured["timeout"] = timeout
            return FakeNtpResponse(0.0)

    check_clock_sync(
        "pool.ntp.org", threshold_seconds=5.0,
        ntp_client_factory=lambda: RecordingClient(), request_timeout_seconds=1.5,
    )

    assert captured["timeout"] == 1.5
