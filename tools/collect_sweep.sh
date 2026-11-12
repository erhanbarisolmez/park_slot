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
#   bash tools/collect_sweep.sh config/slots/cam2.json data/labeled_cam2 S30   # sadece S30'u hedefle (yeni eklenen slot gibi)
#   bash tools/collect_sweep.sh config/slots/cam2.json "" S30                 # output varsayılanla, sadece S30
#
# Zaman listesini değiştirmek için MINUTES_AGO_LIST ortam değişkenini
# (boşlukla ayrılmış dakika listesi) ver - önceden örneklenmemiş yeni saat
# dilimlerini hedeflemek için (örn. öğleden sonra / gece yarısı):
#   MINUTES_AGO_LIST="1200 2640 4080 566 2006 3446" bash tools/collect_sweep.sh config/slots/cam2.json data/labeled_cam2 S30

set -uo pipefail

SLOTS="${1:-config/slots/cam1.json}"
CAMERA_ID=$(basename "$SLOTS" .json)
OUTPUT="${2:-data/labeled_${CAMERA_ID}}"
SLOT_FILTER="${3:-}"
MINUTES_AGO_LIST="${MINUTES_AGO_LIST:-0 360 720 1440 2160 2880 3600 4320 5040 5760}"

echo "Kamera: $CAMERA_ID | Cikti klasoru: $OUTPUT${SLOT_FILTER:+ | Slot filtresi: $SLOT_FILTER}"
echo "Zaman noktalari (dakika once): $MINUTES_AGO_LIST"

SLOT_ARGS=()
if [ -n "$SLOT_FILTER" ]; then
    SLOT_ARGS=(--slot "$SLOT_FILTER")
fi

for m in $MINUTES_AGO_LIST; do
    echo "=== --minutes-ago $m ($(date -d "-$m minutes" '+%A %H:%M')) ==="
    python tools/collect_data.py --slots "$SLOTS" --output "$OUTPUT" \
        --minutes-ago "$m" --window-minutes 60 --interval 1800 "${SLOT_ARGS[@]}"

    exit_code=$?
    if [ "$exit_code" -eq 2 ]; then
        echo "q ile çıkıldı - sweep tamamen durduruldu (kalan zaman noktaları atlandı)."
        exit 0
    elif [ "$exit_code" -ne 0 ]; then
        echo "Beklenmeyen hata (exit $exit_code), sweep durduruldu."
        exit "$exit_code"
    fi
done
