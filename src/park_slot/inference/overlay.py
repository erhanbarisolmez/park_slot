"""Görsel overlay - slot durumlarını kare üzerine çiz.

Boş slotlar yeşil, dolu slotlar kırmızı, belirsiz slotlar sarı.
Yarı saydam polygon + etiket gösterir.
"""

import cv2
import numpy as np

from park_slot.annotation.schema import ParkingStatus, SlotState, SlotStatus

# Renkler (BGR)
COLORS = {
    SlotStatus.EMPTY: (0, 200, 0),       # Yeşil
    SlotStatus.OCCUPIED: (0, 0, 200),     # Kırmızı
    SlotStatus.UNKNOWN: (0, 200, 200),    # Sarı
}


def draw_overlay(
    frame: np.ndarray,
    status: ParkingStatus,
    slot_polygons: dict[int, list[list[int]]],
    alpha: float = 0.35,
) -> np.ndarray:
    """Slot durumlarını kare üzerine çiz.

    Args:
        frame: BGR görüntü
        status: Tüm slotların durumu
        slot_polygons: {slot_id: [[x,y], ...]} polygon koordinatları
        alpha: Overlay saydamlığı (0=saydam, 1=opak)

    Returns:
        Overlay çizilmiş görüntü
    """
    overlay = frame.copy()
    output = frame.copy()

    for slot_state in status.slots:
        polygon = slot_polygons.get(slot_state.id)
        if polygon is None:
            continue

        pts = np.array(polygon, dtype=np.int32)
        color = COLORS.get(slot_state.status, COLORS[SlotStatus.UNKNOWN])

        # Yarı saydam dolgu
        cv2.fillPoly(overlay, [pts], color)

        # Kenar çizgisi
        cv2.polylines(output, [pts], True, color, 2)

        # Etiket
        _draw_label(output, slot_state, pts)

    # Alpha blending: overlay ile orijinali karıştır
    cv2.addWeighted(overlay, alpha, output, 1 - alpha, 0, output)

    # Bilgi paneli
    _draw_info_panel(output, status)

    return output


def _draw_label(
    frame: np.ndarray,
    state: SlotState,
    polygon: np.ndarray,
) -> None:
    """Slot üzerine etiket yaz."""
    # Polygon merkezi
    cx = int(polygon[:, 0].mean())
    cy = int(polygon[:, 1].mean())

    label = state.label or f"S{state.id}"
    conf = f"{state.confidence:.0%}"
    text = f"{label} {conf}"

    # Arka plan kutusu
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
    cv2.rectangle(frame, (cx - 2, cy - th - 4), (cx + tw + 2, cy + 2), (0, 0, 0), -1)
    cv2.putText(frame, text, (cx, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)


def _draw_info_panel(frame: np.ndarray, status: ParkingStatus) -> None:
    """Üst köşeye özet bilgi paneli çiz."""
    h, w = frame.shape[:2]

    # Panel arka planı
    cv2.rectangle(frame, (0, 0), (250, 80), (0, 0, 0), -1)

    # Metin
    cv2.putText(frame, f"Toplam: {status.total_slots}", (10, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.putText(frame, f"Bos: {status.empty}", (10, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLORS[SlotStatus.EMPTY], 1)
    cv2.putText(frame, f"Dolu: {status.occupied}", (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLORS[SlotStatus.OCCUPIED], 1)

    # Doluluk oranı
    if status.total_slots > 0:
        ratio = status.occupied / status.total_slots
        cv2.putText(frame, f"%{ratio:.0%}", (150, 45),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
