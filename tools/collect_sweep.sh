#!/usr/bin/env bash
# Zamansal çeşitlilik için örnek tarama: gündüz/gece, 5 farklı gün dönüşümlü.
# Her nokta için 60 dakikalık pencere, 30 dakikada bir örnek.
#
# Her kamera kendi data/labeled_<camera_id>/ klasörüne yazar (ayrı veri/model
# stratejisi - kameralar birbirine karışmaz).
#
# Kullanım:
#   bash tools/collect_sweep.sh config/slots/cam1.json
#   bash tools/collect_sweep.sh config/slots/cam2.json data/labeled_cam2   # output'u elle vermek istersen

set -uo pipefail

SLOTS="${1:-config/slots/cam1.json}"
CAMERA_ID=$(basename "$SLOTS" .json)
OUTPUT="${2:-data/labeled_${CAMERA_ID}}"

echo "Kamera: $CAMERA_ID | Cikti klasoru: $OUTPUT"

for m in 0 360 720 1440 2160 2880 3600 4320 5040 5760; do
    echo "=== --minutes-ago $m ($(date -d "-$m minutes" '+%A %H:%M')) ==="
    python tools/collect_data.py --slots "$SLOTS" --output "$OUTPUT" \
        --minutes-ago "$m" --window-minutes 60 --interval 1800

    exit_code=$?
    if [ "$exit_code" -eq 2 ]; then
        echo "q ile çıkıldı - sweep tamamen durduruldu (kalan zaman noktaları atlandı)."
        exit 0
    elif [ "$exit_code" -ne 0 ]; then
        echo "Beklenmeyen hata (exit $exit_code), sweep durduruldu."
        exit "$exit_code"
    fi
done
