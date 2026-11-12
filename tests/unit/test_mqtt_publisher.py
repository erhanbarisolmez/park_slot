"""MqttPublisher birim testleri - gerçek broker gerekmez, sahte client enjekte edilir."""

from park_slot.annotation.schema import ParkingStatus, SlotState, SlotStatus
from park_slot.services.mqtt_publisher import MqttPublisher
from park_slot.settings import MqttConfig


class FakeMqttClient:
    """Gerçek paho-mqtt Client'ın yerine geçen, yayınları bellekte tutan sahte client."""

    def __init__(self, raise_on_publish: bool = False):
        self.published: list[tuple[str, str, int]] = []
        self._raise_on_publish = raise_on_publish

    def publish(self, topic: str, payload: str, qos: int = 1):
        if self._raise_on_publish:
            raise RuntimeError("broker bağlantısı yok")
        self.published.append((topic, payload, qos))


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
