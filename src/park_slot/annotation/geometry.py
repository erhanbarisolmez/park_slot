"""Polygon geometri yardımcıları: sağlamlık kontrolü ve dörtgene indirme.

Annotator'da noktalar fare tıklama sırasına göre kaydedilir. Köşeler
perimetre etrafında sırayla tıklanmazsa (örn. çapraz atlanırsa) polygon
kendi kendini kesen ("bowtie") bir şekle dönüşür - bu hem görsel olarak
kafa karıştırıcı kırpmalara hem de perspektif dönüşüm için kullanılamayan
bir şekle yol açar. Bu modül bunu tespit eder ve otomatik düzeltir.
"""

import cv2
import numpy as np


def shoelace_area(points: list[list[float]]) -> float:
    """Shoelace formülü ile polygon alanını hesapla (işaretli, mutlak değilse yön bilgisi taşır)."""
    pts = np.array(points, dtype=np.float64)
    x, y = pts[:, 0], pts[:, 1]
    return float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y) / 2.0)


def _segments_intersect(p1, p2, p3, p4) -> bool:
    """İki doğru parçası (p1-p2 ve p3-p4) kesişiyor mu? (genel pozisyon varsayımı)"""
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    d1 = cross(p3, p4, p1)
    d2 = cross(p3, p4, p2)
    d3 = cross(p1, p2, p3)
    d4 = cross(p1, p2, p4)

    return (d1 * d2 < 0) and (d3 * d4 < 0)


def is_simple_polygon(points: list[list[float]]) -> bool:
    """Polygon kendi kenarlarıyla kesişiyor mu diye kontrol et (bitişik olmayan kenar çiftleri)."""
    n = len(points)
    if n < 4:
        return True

    edges = [(points[i], points[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            # Bitişik kenarları (ortak köşe paylaşanları) atla
            if j == i or j == (i + 1) % n or i == (j + 1) % n:
                continue
            if _segments_intersect(edges[i][0], edges[i][1], edges[j][0], edges[j][1]):
                return False
    return True


def order_points_by_angle(points: list[list[int]]) -> list[list[int]]:
    """Noktaları centroid etrafındaki açıya göre sırala - basit/convex bir polygon üretir.

    Tıklama sırası karışık olsa da (örn. köşeler rastgele sırayla tıklanmışsa)
    bu, perimetre etrafında tutarlı bir sıralama ile basit bir polygon'a döner.
    """
    pts = np.array(points, dtype=np.float64)
    cx, cy = pts[:, 0].mean(), pts[:, 1].mean()
    angles = np.arctan2(pts[:, 1] - cy, pts[:, 0] - cx)
    order = np.argsort(angles)
    return pts[order].astype(int).tolist()


def polygon_to_quad(points: list[list[float]]) -> list[list[float]]:
    """Polygonu 4 köşeli, basit (self-intersection'sız) bir dörtgene indir.

    4 nokta varsa centroid etrafındaki açıya göre sıralanır (order_points_by_angle).
    Daha fazla noktalı polygonlar için önce cv2.minAreaRect ile en yakın dönmüş
    dikdörtgen hesaplanır, sonra aynı şekilde sıralanır.

    Not: Önceden köşeleri "toplam/fark" (sol-üst/sağ-üst/...) ile görsel
    konumuna göre etiketleyen bir yöntem kullanılıyordu, ama bu ~45 derece
    döndürülmüş dikdörtgenlerde (minAreaRect açısı -45° olduğunda) iki farklı
    köşeyi aynı noktaya çakıştırıp dejenere (tamamen siyah) crop üretiyordu -
    bkz. gerçek örnek: cam1.json'daki slot 9. order_points_by_angle bu tip
    eşitlik (tie) durumlarına karşı sağlam, her zaman 4 ayrı köşe üretir.
    """
    if len(points) == 4:
        quad_src = points
    else:
        pts = np.array(points, dtype=np.float32)
        rect = cv2.minAreaRect(pts)
        quad_src = cv2.boxPoints(rect).tolist()

    return [[float(x), float(y)] for x, y in order_points_by_angle(quad_src)]
