"""Görsel overlay - slot durumlarını kare üzerine çiz.

Boş slotlar yeşil, dolu slotlar kırmızı, belirsiz slotlar sarı.
Yarı saydam polygon + etiket gösterir.
"""

from typing import Optional

import cv2
import numpy as np

from park_slot.annotation.schema import ParkingStatus, SlotState, SlotStatus

# Renkler (BGR)
COLORS = {
    SlotStatus.EMPTY: (0, 200, 0),       # Yeşil
    SlotStatus.OCCUPIED: (0, 0, 200),     # Kırmızı
    SlotStatus.UNKNOWN: (0, 200, 200),    # Sarı
}

# Kuşbakışı mini-harita tuval boyutu. tools/mark_perspective.py bu değerleri
# import edip homografinin hedef dikdörtgenini bununla hesaplıyor - ikisi
# birbirinden bağımsız değişirse mini-harita yanlış ölçeklenir.
MINIMAP_WIDTH = 280
MINIMAP_HEIGHT = 180
MINIMAP_MARGIN = 10


def draw_overlay(
    frame: np.ndarray,
    status: ParkingStatus,
    slot_polygons: dict[int, list[list[int]]],
    alpha: float = 0.35,
    perspective_transform: Optional[list[list[float]]] = None,
    vehicle_count: Optional[int] = None,
) -> np.ndarray:
    """Slot durumlarını kare üzerine çiz.

    Args:
        frame: BGR görüntü
        status: Tüm slotların durumu
        slot_polygons: {slot_id: [[x,y], ...]} polygon koordinatları
        alpha: Overlay saydamlığı (0=saydam, 1=opak)
        perspective_transform: Varsa, zemin düzlemine kuşbakışı homografi
            matrisi (tools/mark_perspective.py ile üretilir) - sağ alt köşeye
            küçük bir kuşbakışı mini-harite eklenir.
        vehicle_count: Varsa (araç dedektörü etkinse), bilgi paneline
            "Bilinmiyor" satırının altına "Dedektör: N araç" eklenir.

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

        # Kenar çizgisi - operatörün elle sabitlediği slotlar beyaz kalın
        # çerçeveyle ayırt edilir (bkz. StateStore.set_override)
        if slot_state.manual_override:
            cv2.polylines(output, [pts], True, (255, 255, 255), 3)
        else:
            cv2.polylines(output, [pts], True, color, 2)

        # Etiket
        _draw_label(output, slot_state, pts)

    # Alpha blending: overlay ile orijinali karıştır
    cv2.addWeighted(overlay, alpha, output, 1 - alpha, 0, output)

    # Bilgi paneli
    _draw_info_panel(output, status, vehicle_count)

    if perspective_transform is not None:
        minimap = _draw_minimap(status, slot_polygons, perspective_transform)
        _composite_minimap(output, minimap)

    return output


def project_to_minimap(polygon: list[list[int]], matrix: np.ndarray) -> tuple[float, float]:
    """Bir slot polygon'unun merkezini kuşbakışı mini-harita piksel uzayına projekte et.

    SlotManager (API için normalize 0-1 koordinat hesaplarken) ve bu
    modüldeki _draw_minimap (render için) tarafından paylaşılan tek
    projeksiyon mantığı - ikisinin birbirinden sapmaması için.

    Returns:
        (x, y) MINIMAP_WIDTH/MINIMAP_HEIGHT piksel uzayında, kenetlenmemiş ham değer.
    """
    pts = np.array(polygon, dtype=np.float64)
    cx, cy = pts[:, 0].mean(), pts[:, 1].mean()

    src = np.array([[[cx, cy]]], dtype=np.float64)
    dst = cv2.perspectiveTransform(src, matrix)
    x, y = dst[0, 0]
    return float(x), float(y)


def _draw_minimap(
    status: ParkingStatus,
    slot_polygons: dict[int, list[list[int]]],
    perspective_transform: list[list[float]],
) -> np.ndarray:
    """Slot merkezlerini zemin düzlemine (kuşbakışı) projekte edip küçük bir harita çiz."""
    canvas = np.full((MINIMAP_HEIGHT, MINIMAP_WIDTH, 3), 40, dtype=np.uint8)
    matrix = np.array(perspective_transform, dtype=np.float64)

    for slot_state in status.slots:
        polygon = slot_polygons.get(slot_state.id)
        if polygon is None:
            continue

        x, y = project_to_minimap(polygon, matrix)

        # Homografi dışındaki (ölçülen alanın dışına düşen) slotlar tuval
        # sınırına kenetlenir - tamamen gizlenmez, kenarda görünür kalır.
        x = int(np.clip(x, 2, MINIMAP_WIDTH - 3))
        y = int(np.clip(y, 2, MINIMAP_HEIGHT - 3))

        color = COLORS.get(slot_state.status, COLORS[SlotStatus.UNKNOWN])
        cv2.circle(canvas, (x, y), 6, color, -1)
        cv2.circle(canvas, (x, y), 6, (220, 220, 220), 1)

    cv2.rectangle(canvas, (0, 0), (MINIMAP_WIDTH - 1, MINIMAP_HEIGHT - 1), (200, 200, 200), 1)
    cv2.putText(canvas, "Kusbakisi", (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

    return canvas


def _composite_minimap(frame: np.ndarray, minimap: np.ndarray) -> None:
    """Mini-haritayı ana karenin sağ alt köşesine yerleştir (yerinde değiştirir)."""
    h, w = frame.shape[:2]
    mh, mw = minimap.shape[:2]

    x0 = w - mw - MINIMAP_MARGIN
    y0 = h - mh - MINIMAP_MARGIN
    if x0 < 0 or y0 < 0:
        return

    frame[y0:y0 + mh, x0:x0 + mw] = minimap


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
    text = f"{label} {conf}" + (" [M]" if state.manual_override else "")

    # Arka plan kutusu
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
    cv2.rectangle(frame, (cx - 2, cy - th - 4), (cx + tw + 2, cy + 2), (0, 0, 0), -1)
    cv2.putText(frame, text, (cx, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)


def _draw_info_panel(frame: np.ndarray, status: ParkingStatus, vehicle_count: Optional[int] = None) -> None:
    """Üst köşeye özet bilgi paneli çiz."""
    h, w = frame.shape[:2]

    # vehicle_count verilmişse (dedektör etkinse) panel bir satır uzar -
    # "kaç slot dolu" ile "dedektörün gördüğü gerçek araç sayısı"nı aynı
    # panelde yan yana karşılaştırmak için.
    panel_height = 120 if vehicle_count is not None else 100

    # Panel arka planı
    cv2.rectangle(frame, (0, 0), (250, panel_height), (0, 0, 0), -1)

    # Metin
    cv2.putText(frame, f"Toplam: {status.total_slots}", (10, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.putText(frame, f"Bos: {status.empty}", (10, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLORS[SlotStatus.EMPTY], 1)
    cv2.putText(frame, f"Dolu: {status.occupied}", (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLORS[SlotStatus.OCCUPIED], 1)
    cv2.putText(frame, f"Bilinmiyor: {status.unknown}", (10, 80),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLORS[SlotStatus.UNKNOWN], 1)
    if vehicle_count is not None:
        cv2.putText(frame, f"Dedektor: {vehicle_count} arac", (10, 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 150, 0), 1)

    # Doluluk oranı
    if status.total_slots > 0:
        ratio = status.occupied / status.total_slots
        cv2.putText(frame, f"%{ratio:.0%}", (150, 45),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
