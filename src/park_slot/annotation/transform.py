"""Perspektif düzeltme.

Açılı kameralarda (yukarıdan bakmayan) park slotları yamuk görünür.
Bu modül, 4 köşe noktası vererek görüntüyü kuşbakışı görünüme dönüştürür.

Her kamera için gerekmez - sadece açılı kameralarda kullanılır.
"""

import cv2
import numpy as np


def compute_perspective_matrix(
    src_points: list[list[float]],
    dst_width: int,
    dst_height: int,
) -> np.ndarray:
    """Perspektif dönüşüm matrisini hesapla.

    Args:
        src_points: Kaynak görüntüdeki 4 köşe noktası
                    [[sol_üst], [sağ_üst], [sağ_alt], [sol_alt]]
        dst_width: Hedef görüntü genişliği
        dst_height: Hedef görüntü yüksekliği

    Returns:
        3x3 perspektif dönüşüm matrisi
    """
    src = np.array(src_points, dtype=np.float32)
    dst = np.array([
        [0, 0],
        [dst_width, 0],
        [dst_width, dst_height],
        [0, dst_height],
    ], dtype=np.float32)

    matrix = cv2.getPerspectiveTransform(src, dst)
    return matrix


def warp_image(
    image: np.ndarray,
    matrix: np.ndarray,
    width: int,
    height: int,
) -> np.ndarray:
    """Görüntüye perspektif dönüşüm uygula.

    Args:
        image: Kaynak görüntü
        matrix: 3x3 dönüşüm matrisi (compute_perspective_matrix'ten)
        width: Çıktı genişliği
        height: Çıktı yüksekliği

    Returns:
        Düzeltilmiş görüntü
    """
    return cv2.warpPerspective(image, matrix, (width, height))


def warp_points(
    points: list[list[int]],
    matrix: np.ndarray,
) -> list[list[int]]:
    """Nokta koordinatlarına perspektif dönüşüm uygula.

    Slot polygon noktalarını dönüştürmek için kullanılır.

    Args:
        points: [[x1,y1], [x2,y2], ...] koordinat listesi
        matrix: 3x3 dönüşüm matrisi

    Returns:
        Dönüştürülmüş koordinatlar
    """
    pts = np.array(points, dtype=np.float32).reshape(-1, 1, 2)
    warped = cv2.perspectiveTransform(pts, matrix)
    return np.round(warped.reshape(-1, 2)).astype(int).tolist()
