#!/usr/bin/env bash
# collect_sweep.sh ile AYNI yapı, farklı zaman noktaları: öğleden sonra
# (~13:00) ve gece yarısına yakın (~23:30), 3 farklı gün - collect_sweep.sh'in
# sabit listesindeki saatlerle (akşam ~20-22, sabah ~08-10) ÇAKIŞMAYAN,
# önceden örneklenmemiş saat dilimlerini hedefler.
#
# Her nokta için 60 dakikalık pencere, 30 dakikada bir örnek (collect_sweep.sh
# ile aynı - nokta başına ~3 kare).
#
# Kullanım:
#   bash tools/collect_sweep2.sh config/slots/cam2.json
#   bash tools/collect_sweep2.sh config/slots/cam2.json data/labeled_cam2 S30   # sadece S30'u hedefle

set -uo pipefail

SLOTS="${1:-config/slots/cam1.json}"
CAMERA_ID=$(basename "$SLOTS" .json)
OUTPUT="${2:-data/labeled_${CAMERA_ID}}"
SLOT_FILTER="${3:-}"

echo "Kamera: $CAMERA_ID | Cikti klasoru: $OUTPUT${SLOT_FILTER:+ | Slot filtresi: $SLOT_FILTER}"

SLOT_ARGS=()
if [ -n "$SLOT_FILTER" ]; then
    SLOT_ARGS=(--slot "$SLOT_FILTER")
fi

# Oğleden sonra ~13:00 (dün, 2 gün önce, 3 gün önce) + gece yarısına yakın
# ~23:30 (dün, 2 gün önce, 3 gün önce) - bkz. tools/collect_sweep.sh'teki
# sabit listenin (0,360,...,5760) akşam/sabah kümelenmesiyle çakışmaması
# için hesaplanan değerler.
for m in 1200 2640 4080 566 2006 3446; do
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
