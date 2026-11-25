"""MqttPublisher birim testleri - gerçek broker gerekmez, sahte client enjekte edilir."""

from park_slot.annotation.schema import ParkingStatus, SlotState, SlotStatus
from park_slot.services.mqtt_publisher import MqttPublisher
from park_slot.settings import MqttConfig


class FakeMqttClient:
    """Gerçek paho-mqtt Client'ın yerine geçen, yayınları bellekte tutan sahte client."""

    def __init__(self, raise_on_publish: bool = False, connected: bool = True):
        self.published: list[tuple[str, str, int]] = []
        self._raise_on_publish = raise_on_publish
        self._connected = connected
        self.on_connect = None

    def is_connected(self) -> bool:
        return self._connected

    def publish(self, topic: str, payload: str, qos: int = 1):
        if self._raise_on_publish:
            raise RuntimeError("broker bağlantısı yok")
        self.published.append((topic, payload, qos))

    def simulate_connect(self, reason_code: int = 0) -> None:
        """Gerçek paho client'ın bağlantı kurulunca self.on_connect'i
        çağırmasını simüle eder (bkz. MqttPublisher._on_connect)."""
        self._connected = True
        if self.on_connect is not None:
            self.on_connect(self, None, None, reason_code)


def make_status(slots: dict[int, SlotStatus], camera_id: str = "cam1") -> ParkingStatus:
    return ParkingStatus(
        camera_id=camera_id,
        total_slots=len(slots),
        slots=[SlotState(id=sid, label=f"S{sid}", status=status) for sid, status in slots.items()],
    )


def make_settings(enabled: bool = True) -> MqttConfig:
    return MqttConfig(enabled=enabled, broker_host="localhost", broker_port=1883, camera_code="CAM-001", qos=1)


def test_publish_includes_unknown_slots():
    """UNKNOWN slotlar da payload'a dahil edilmeli - filtrelenmemeli."""
    client = FakeMqttClient()
    publisher = MqttPublisher(make_settings(), camera_id="cam1", state_store=None, client=client)

    status = make_status({1: SlotStatus.EMPTY, 2: SlotStatus.OCCUPIED, 3: SlotStatus.UNKNOWN})
    publisher.publish(status)

    assert len(client.published) == 1
    topic, payload, qos = client.published[0]
    assert topic == "selfpark/camera/CAM-001/detection"
    assert qos == 1
    import json
    body = json.loads(payload)
    assert body == {"slots": {"CAM-001-1": "EMPTY", "CAM-001-2": "OCCUPIED", "CAM-001-3": "UNKNOWN"}}


def test_publish_prefixes_slot_ids_with_camera_code():
    """Slot key'leri camera_code ile prefixlenmeli - aynı parka bağlı başka bir
    kamera aynı ham slot id'sini (örn. 1) kullanırsa backend'de map anahtarı
    çakışmasın (bkz. mqtt_publisher.py docstring'i)."""
    client = FakeMqttClient()
    settings = MqttConfig(enabled=True, broker_host="localhost", broker_port=1883, camera_code="CAM-002", qos=1)
    publisher = MqttPublisher(settings, camera_id="cam2", state_store=None, client=client)

    status = make_status({1: SlotStatus.EMPTY}, camera_id="cam2")
    publisher.publish(status)

    import json
    body = json.loads(client.published[0][1])
    assert body == {"slots": {"CAM-002-1": "EMPTY"}}


def test_dedup_skips_identical_snapshot():
    """Aynı snapshot art arda gelirse ikinci publish atlanmalı."""
    client = FakeMqttClient()
    publisher = MqttPublisher(make_settings(), camera_id="cam1", state_store=None, client=client)

    status = make_status({1: SlotStatus.EMPTY})
    publisher.publish(status)
    publisher.publish(status)

    assert len(client.published) == 1


def test_changed_snapshot_publishes_again():
    """Snapshot değişirse tekrar publish edilmeli."""
    client = FakeMqttClient()
    publisher = MqttPublisher(make_settings(), camera_id="cam1", state_store=None, client=client)

    publisher.publish(make_status({1: SlotStatus.EMPTY}))
    publisher.publish(make_status({1: SlotStatus.OCCUPIED}))

    assert len(client.published) == 2


def test_disabled_does_not_publish():
    """mqtt.enabled=false ise client hiç oluşturulmaz, publish no-op'tur."""
    publisher = MqttPublisher(make_settings(enabled=False), camera_id="cam1", state_store=None)

    assert publisher._client is None
    # client None olduğu için publish sessizce hiçbir şey yapmamalı (exception fırlatmamalı).
    publisher.publish(make_status({1: SlotStatus.EMPTY}))


def test_publish_exception_is_swallowed():
    """Client.publish hata fırlatırsa pipeline'ı etkilememeli (exception yutulur)."""
    client = FakeMqttClient(raise_on_publish=True)
    publisher = MqttPublisher(make_settings(), camera_id="cam1", state_store=None, client=client)

    # Exception fırlatmamalı:
    publisher.publish(make_status({1: SlotStatus.EMPTY}))


def test_failed_publish_is_retried_on_next_call():
    """Publish başarısız olursa dedup state güncellenmemeli - aynı state bir
    sonraki publish() çağrısında tekrar denenmeli (sessiz veri kaybı bug'ının
    düzeltmesi: _last_payload artık yalnızca başarılı publish sonrası yazılıyor)."""
    client = FakeMqttClient(raise_on_publish=True)
    publisher = MqttPublisher(make_settings(), camera_id="cam1", state_store=None, client=client)

    status = make_status({1: SlotStatus.EMPTY})
    publisher.publish(status)
    assert len(client.published) == 0

    client._raise_on_publish = False
    publisher.publish(status)

    assert len(client.published) == 1


def test_publish_raw_returns_false_when_disconnected():
    """paho-mqtt, bağlantı koptuğunda QoS>=1'de publish()'te exception
    fırlatmaz (sessizce kuyruklar) - bu yüzden is_connected() ÖNCEDEN
    kontrol edilmeli; disconnected durumda hiç publish denenmemeli."""
    client = FakeMqttClient(connected=False)
    publisher = MqttPublisher(make_settings(), camera_id="cam1", state_store=None, client=client)

    result = publisher.publish_raw({1: "EMPTY"})

    assert result is False
    assert len(client.published) == 0


def test_publish_returns_true_on_success():
    """publish()/publish_raw() artık bool döndürüyor - OutboxWorker'ın
    başarı/başarısızlığı bilmesinin tek yolu bu."""
    client = FakeMqttClient()
    publisher = MqttPublisher(make_settings(), camera_id="cam1", state_store=None, client=client)

    assert publisher.publish(make_status({1: SlotStatus.EMPTY})) is True


def test_publish_returns_false_on_exception():
    client = FakeMqttClient(raise_on_publish=True)
    publisher = MqttPublisher(make_settings(), camera_id="cam1", state_store=None, client=client)

    assert publisher.publish(make_status({1: SlotStatus.EMPTY})) is False


def test_publish_slot_changed_sends_delta_to_slot_changed_topic():
    """ADR-001: delta mesajı ayrı bir topic'e (.../slotChanged), tam snapshot
    (.../detection) değil."""
    client = FakeMqttClient()
    publisher = MqttPublisher(make_settings(), camera_id="cam1", state_store=None, client=client)

    result = publisher.publish_slot_changed(1, "EMPTY", "OCCUPIED", 3)

    assert result is True
    assert len(client.published) == 1
    topic, payload, qos = client.published[0]
    assert topic == "selfpark/camera/CAM-001/slotChanged"
    assert qos == 1
    import json
    body = json.loads(payload)
    assert body["slotId"] == "CAM-001-1"
    assert body["previousStatus"] == "EMPTY"
    assert body["currentStatus"] == "OCCUPIED"
    assert body["version"] == 3
    assert isinstance(body["timestamp"], int)


def test_publish_slot_changed_returns_false_when_disconnected():
    client = FakeMqttClient(connected=False)
    publisher = MqttPublisher(make_settings(), camera_id="cam1", state_store=None, client=client)

    result = publisher.publish_slot_changed(1, None, "EMPTY", 1)

    assert result is False
    assert len(client.published) == 0


def test_publish_slot_changed_returns_false_on_exception():
    client = FakeMqttClient(raise_on_publish=True)
    publisher = MqttPublisher(make_settings(), camera_id="cam1", state_store=None, client=client)

    result = publisher.publish_slot_changed(1, None, "EMPTY", 1)

    assert result is False


def test_publish_slot_changed_no_dedup_same_payload_sent_twice():
    """Delta kanalında dedup yok - her outbox satırı tekil bir değişikliği
    temsil ediyor, publish_raw'daki _last_payload mantığı burada uygulanmaz."""
    client = FakeMqttClient()
    publisher = MqttPublisher(make_settings(), camera_id="cam1", state_store=None, client=client)

    publisher.publish_slot_changed(1, "EMPTY", "OCCUPIED", 3)
    publisher.publish_slot_changed(1, "EMPTY", "OCCUPIED", 3)

    assert len(client.published) == 2


def test_enable_startup_snapshot_sends_immediately_when_already_connected():
    """Client enable_startup_snapshot çağrıldığında zaten bağlıysa (paho'nun
    connect_async'i bu çağrıdan önce tamamlanmış), on_connect bir daha
    tetiklenmeyeceği için provider hemen bir kez çağrılıp publish edilmeli."""
    client = FakeMqttClient(connected=True)
    publisher = MqttPublisher(make_settings(), camera_id="cam1", state_store=None, client=client)

    publisher.enable_startup_snapshot(lambda: {1: "EMPTY", 2: "OCCUPIED"})

    assert len(client.published) == 1
    topic, payload, _ = client.published[0]
    assert topic == "selfpark/camera/CAM-001/detection"
    import json
    assert json.loads(payload) == {"slots": {"CAM-001-1": "EMPTY", "CAM-001-2": "OCCUPIED"}}


def test_enable_startup_snapshot_sends_on_later_connect():
    """Client henüz bağlı değilken enable_startup_snapshot çağrılırsa, hemen
    publish denenmemeli; bağlantı kurulduğunda (on_connect tetiklenince)
    provider'dan okunan en güncel state gönderilmeli."""
    client = FakeMqttClient(connected=False)
    publisher = MqttPublisher(make_settings(), camera_id="cam1", state_store=None, client=client)

    publisher.enable_startup_snapshot(lambda: {1: "EMPTY"})
    assert len(client.published) == 0

    client.simulate_connect()

    assert len(client.published) == 1
    topic, payload, _ = client.published[0]
    assert topic == "selfpark/camera/CAM-001/detection"
    import json
    assert json.loads(payload) == {"slots": {"CAM-001-1": "EMPTY"}}


def test_on_slot_change_uses_state_store_snapshot():
    """on_slot_change, gelen tek slotu değil state_store.get_status()'un TAM snapshot'ını yayınlamalı."""

    class FakeStateStore:
        def get_status(self):
            return make_status({1: SlotStatus.EMPTY, 2: SlotStatus.OCCUPIED})

    client = FakeMqttClient()
    publisher = MqttPublisher(make_settings(), camera_id="cam1", state_store=FakeStateStore(), client=client)

    changed_slot = SlotState(id=1, label="S1", status=SlotStatus.EMPTY)
    publisher.on_slot_change(changed_slot)

    assert len(client.published) == 1
    import json
    body = json.loads(client.published[0][1])
    assert body == {"slots": {"CAM-001-1": "EMPTY", "CAM-001-2": "OCCUPIED"}}
