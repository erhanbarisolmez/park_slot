"""selfpark-backend'e kamera doluluk verisi yayınlayan MQTT publisher.

PredictionLog ile aynı desen: StateStore.on_change callback zincirine katılır
(bkz. __main__.py). Backend'in beklediği sözleşme (docs/camera/README.md,
selfpark-backend reposu):

    Topic:   selfpark/camera/{camera_code}/detection
    Payload: {"slots": {"<camera_code>-<slot_id>": "EMPTY"|"OCCUPIED"|"UNKNOWN", ...}}

UNKNOWN de dahil TÜM slotlar her yayında gönderilir - backend artık 3 durumu
da ayırt ediyor (boolean sözleşmesi kaldırıldı). Koordinat/layout bilgisi bu
akışın parçası değildir; o, tools/register_slot_layout.py ile bir kez ayrı
kaydedilir (bkz. o dosyanın docstring'i).

Slot key'leri camera_code ile prefixlenir ("CAM-001-1", "CAM-002-1", ...):
backend tarafında park_slot_occupancy tek bir parkı (park_info_id) temsil
eder ve birden fazla kamera aynı parka bağlanabilir (bkz. selfpark-backend
ParkOccupancyManager) - ham slot id'leri (1, 2, 3, ...) kameralar arasında
çakışabildiğinden, prefix olmadan farklı kameraların aynı slot id'si aynı
map anahtarını paylaşıp birbirinin durumunu/layout'unu ezer.

Publish her zaman best-effort'tur: MQTT'ye bağlanamamak veya publish hatası
pipeline'ı asla durdurmaz, sadece loglanır.
"""

import json
import logging
import threading
from typing import Optional

from park_slot.annotation.schema import ParkingStatus, SlotState
from park_slot.settings import MqttConfig

logger = logging.getLogger(__name__)


class MqttPublisher:
    def __init__(
        self,
        settings: MqttConfig,
        camera_id: str,
        state_store,
        client=None,
    ):
        self._settings = settings
        self._camera_id = camera_id
        self._state_store = state_store
        self._lock = threading.Lock()
        self._last_payload: Optional[dict] = None
        self._client = None

        if not settings.enabled:
            logger.info("MQTT publisher devre dışı (mqtt.enabled=false).")
            return

        self._client = client if client is not None else self._connect()

    def _connect(self):
        try:
            import paho.mqtt.client as mqtt

            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"park_slot_{self._camera_id}_pub",
            )
            client.connect_async(self._settings.broker_host, self._settings.broker_port)
            # loop_start() kalıcı bir arka plan network thread'i açar - şu an
            # tüm kamera config'lerinde mqtt.enabled=false olduğu için bu
            # kod yolu tetiklenmiyor; enabled=true'ya çevirmek CPU_LIMIT'e
            # pinlenmemiş yeni bir thread eklemek demek, bilinçli yapılmalı.
            client.loop_start()
            logger.info(
                "MQTT publisher broker'a bağlanıyor: %s:%d (camera_code=%s)",
                self._settings.broker_host, self._settings.broker_port, self._settings.camera_code,
            )
            return client
        except Exception as e:
            logger.error("MQTT broker'a bağlanılamadı: %s", e)
            return None

    def on_slot_change(self, _state: SlotState) -> None:
        """StateStore.on_change callback'i. Parametre olarak gelen tek slot
        kullanılmaz - tetiklendiğinde kameranın TAM snapshot'ı yayınlanır."""
        if self._client is None:
            return
        try:
            self.publish(self._state_store.get_status())
        except Exception as e:
            logger.error("MQTT publish hazırlığı başarısız: %s", e)

    def publish(self, parking_status: ParkingStatus) -> None:
        if self._client is None:
            return

        payload_slots = {
            f"{self._settings.camera_code}-{slot.id}": slot.status.value.upper()
            for slot in parking_status.slots
        }

        with self._lock:
            if payload_slots == self._last_payload:
                return
            self._last_payload = payload_slots

        topic = f"selfpark/camera/{self._settings.camera_code}/detection"
        body = json.dumps({"slots": payload_slots})
        try:
            self._client.publish(topic, body, qos=self._settings.qos)
        except Exception as e:
            logger.error("MQTT publish başarısız (topic=%s): %s", topic, e)
