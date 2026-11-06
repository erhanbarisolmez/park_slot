"""annotation.geometry birim testleri."""

from park_slot.annotation.geometry import (
    is_simple_polygon,
    order_points_by_angle,
    polygon_to_quad,
    shoelace_area,
)


def test_simple_square_is_simple():
    square = [[0, 0], [10, 0], [10, 10], [0, 10]]
    assert is_simple_polygon(square) is True


def test_bowtie_quad_is_not_simple():
    # Köşegen sırayla değil çapraz tıklanmış: (0,0)->(10,10)->(10,0)->(0,10)
    bowtie = [[0, 0], [10, 10], [10, 0], [0, 10]]
    assert is_simple_polygon(bowtie) is False


def test_real_slot3_polygon_is_detected_as_broken():
    # cam1.json'daki (düzeltme öncesi) slot 3 polygon'u
    slot3 = [[1005, 357], [975, 315], [1014, 372], [1084, 376], [1043, 318], [974, 315]]
    assert is_simple_polygon(slot3) is False


def test_order_points_by_angle_fixes_shuffled_convex_polygon():
    square = [[0, 0], [10, 0], [10, 10], [0, 10]]
    shuffled = [square[2], square[0], square[3], square[1]]
    assert is_simple_polygon(shuffled) is False

    fixed = order_points_by_angle(shuffled)
    assert is_simple_polygon(fixed) is True
    assert abs(shoelace_area(fixed)) == 100


def test_order_points_by_angle_fixes_known_broken_slot():
    slot3 = [[1005, 357], [975, 315], [1014, 372], [1084, 376], [1043, 318], [974, 315]]
    fixed = order_points_by_angle(slot3)
    assert is_simple_polygon(fixed) is True


def test_shoelace_area_of_square():
    square = [[0, 0], [10, 0], [10, 10], [0, 10]]
    assert abs(shoelace_area(square)) == 100


def test_polygon_to_quad_with_four_points_orders_corners():
    # Köşeler karışık sırada verilse de tl/tr/br/bl'ye oturmalı
    shuffled = [[10, 10], [0, 0], [0, 10], [10, 0]]
    quad = polygon_to_quad(shuffled)
    tl, tr, br, bl = quad
    assert tl == [0, 0]
    assert tr == [10, 0]
    assert br == [10, 10]
    assert bl == [0, 10]


def test_polygon_to_quad_with_hexagon_reduces_to_four_points():
    hexagon = [[0, 0], [5, -2], [10, 0], [10, 10], [5, 12], [0, 10]]
    quad = polygon_to_quad(hexagon)
    assert len(quad) == 4


def test_polygon_to_quad_handles_45_degree_rotated_rectangle():
    """Regresyon: cam1.json'daki slot 9 (minAreaRect acisi -45 derece olunca
    eski toplam/fark tabanli siralama iki koseyi ayni noktaya cakistirip
    dejenere/siyah crop uretiyordu).
    """
    slot9 = [[330, 266], [390, 269], [390, 270], [321, 342], [263, 333]]
    quad = polygon_to_quad(slot9)

    unique_points = {tuple(p) for p in quad}
    assert len(unique_points) == 4
    assert is_simple_polygon(quad) is True
    assert abs(shoelace_area(quad)) > 1000  # anlamli (sifira yakin olmayan) bir alan
