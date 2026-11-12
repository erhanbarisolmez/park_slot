"""Slot layout (x/y/label) kayıt aracı - operatör betiği.

selfpark-backend'in PUT /api/v0/camera/{cameraCode}/slots/layout endpoint'ine,
SlotManager'ın zaten hesapladığı minimap_coords + slot etiketlerini BİR KEZ
gönderir. Sürekli çalışan MqttPublisher'ın (services/mqtt_publisher.py) içine
BİLEREK konulmamıştır: admin JWT kimlik bilgisi, anonim/credential'sız çalışan
sürekli detection akışına karıştırılmaz. Kalibrasyon (tools/annotate.py,
tools/mark_perspective.py) her değiştiğinde operatör bu script'i yeniden
çalıştırır.

Ön koşul: kamera backend'de POST /api/v0/camera/add ile zaten kayıtlı olmalı
(bkz. selfpark-backend reposu, docs/camera/README.md).

Slot key'leri --camera-code ile prefixlenir ("CAM-001-1", ...): backend'de
park_slot_occupancy tek bir parkı (park_info_id) temsil eder, birden fazla
kamera aynı parka bağlanabilir - prefix olmadan farklı kameraların aynı ham
slot id'si (1, 2, 3, ...) aynı map anahtarını paylaşıp birbirinin
layout/durumunu ezer (bkz. services/mqtt_publisher.py docstring'i, aynı
prefix kuralı canlı detection akışında da kullanılır).

Kullanım:
    python tools/register_slot_layout.py --slots config/slots/cam1.json \\
        --backend-url http://localhost:8080 --camera-code CAM-001 --token "<ADMIN_JWT>"

    # Token'ı CLI'da yazmak istemiyorsanız env var kullanın:
    export SELFPARK_ADMIN_TOKEN="<ADMIN_JWT>"
    python tools/register_slot_layout.py --slots config/slots/cam1.json \\
        --backend-url http://localhost:8080 --camera-code CAM-001
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from park_slot.services.slot_manager import SlotManager


def build_layout_payload(slot_manager: SlotManager, camera_code: str) -> dict:
    """camera_code ile prefixlenmiş slot key'leri üretir ("CAM-001-1", ...).

    Backend'de park_slot_occupancy tek bir parkı (park_info_id) temsil eder ve
    birden fazla kamera aynı parka bağlanabilir (bkz. MqttPublisher docstring'i) -
    prefix olmadan farklı kameraların aynı ham slot id'si aynı map anahtarını
    paylaşıp birbirinin layout/durumunu ezer.
    """
    coords = slot_manager.minimap_coords
    slots = {}
    for slot_def in slot_manager.slots:
        x, y = coords.get(slot_def.id, (None, None))
        slots[f"{camera_code}-{slot_def.id}"] = {"x": x, "y": y, "label": slot_def.label}
    return {"slots": slots}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Slot layout'unu (x/y/label) selfpark-backend'e kaydet/günceller"
    )
    parser.add_argument("--slots", required=True, help="Slot konfigürasyon JSON dosyası (config/slots/camX.json)")
    parser.add_argument("--backend-url", required=True, help="Backend base URL, örn. http://localhost:8080")
    parser.add_argument("--camera-code", required=True, help="Backend'de kayıtlı cameraCode, örn. CAM-001")
    parser.add_argument(
        "--token", default=None,
        help="Admin/manager JWT (verilmezse SELFPARK_ADMIN_TOKEN env var kullanılır)",
    )
    args = parser.parse_args()

    token = args.token or os.environ.get("SELFPARK_ADMIN_TOKEN")
    if not token:
        print("Hata: --token veya SELFPARK_ADMIN_TOKEN env var gerekli.", file=sys.stderr)
        return 1

    slot_manager = SlotManager(args.slots)
    if not slot_manager.minimap_coords:
        print(
            "Uyarı: minimap_coords boş - bu kamera için perspective_transform tanımlı değil "
            "(bkz. tools/mark_perspective.py). Layout x/y=null olarak kaydedilecek.",
            file=sys.stderr,
        )

    payload = build_layout_payload(slot_manager, args.camera_code)
    url = f"{args.backend_url.rstrip('/')}/api/v0/camera/{args.camera_code}/slots/layout"

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="PUT",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            print(f"OK ({response.status}): {response.read().decode('utf-8')}")
            return 0
    except urllib.error.HTTPError as e:
        print(f"Hata ({e.code}): {e.read().decode('utf-8')}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"Bağlantı hatası: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
