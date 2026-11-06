"""annotation.transform birim testleri."""

import importlib.util
from pathlib import Path

import numpy as np

from park_slot.annotation.transform import compute_perspective_matrix, warp_points


def test_warp_points_round_trip_through_known_matrix():
    # Eksen-hizali olmayan, gerçekçi bir dörtgen (açılı kamera gibi)
    quad = [[50, 20], [300, 15], [320, 180], [40, 190]]
    matrix = compute_perspective_matrix(quad, 1000, 600)

    original = [[100, 50], [250, 120], [60, 170]]
    forward = warp_points(original, matrix)
    back = warp_points(forward, np.linalg.inv(matrix))

    for (ox, oy), (bx, by) in zip(original, back):
        assert abs(ox - bx) <= 1
        assert abs(oy - by) <= 1


def test_warp_points_rounds_instead_of_truncating():
    # Kimlik matrisini küçük bir öteleme ile birleştirip .9'luk bir değer üret
    matrix = np.array([[1, 0, 0.9], [0, 1, 0.9], [0, 0, 1]], dtype=np.float64)
    result = warp_points([[1, 1]], matrix)
    assert result == [[2, 2]]  # 1.9 -> 2 (yuvarlama), 1 degil (kesme)


def _load_birdseye_module():
    path = Path(__file__).resolve().parents[2] / "tools" / "annotate_birdseye.py"
    spec = importlib.util.spec_from_file_location("annotate_birdseye", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recover_ground_points_matches_mark_perspective_construction():
    birdseye = _load_birdseye_module()

    known_points = [[100.0, 80.0], [900.0, 60.0], [950.0, 500.0], [60.0, 520.0]]
    dst = np.array(
        [
            [0, 0],
            [birdseye.MINIMAP_WIDTH - 1, 0],
            [birdseye.MINIMAP_WIDTH - 1, birdseye.MINIMAP_HEIGHT - 1],
            [0, birdseye.MINIMAP_HEIGHT - 1],
        ],
        dtype=np.float32,
    )
    src = np.array(known_points, dtype=np.float32)
    import cv2

    matrix = cv2.getPerspectiveTransform(src, dst).tolist()

    recovered = birdseye.recover_ground_points(matrix)

    for (kx, ky), (rx, ry) in zip(known_points, recovered):
        assert abs(kx - rx) <= 1
        assert abs(ky - ry) <= 1
