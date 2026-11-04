"""Görüntü işleme yardımcıları: kırpma, resize, normalize."""

import cv2
import numpy as np
import torch


def crop_polygon(image: np.ndarray, polygon: list[list[int]]) -> np.ndarray:
    """Polygon içindeki alanı kırp, kare bounding box olarak döndür."""
    pts = np.array(polygon, dtype=np.int32)
    x, y, w, h = cv2.boundingRect(pts)

    # Mask oluştur: polygon dışını siyah yap
    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    cv2.fillPoly(mask, [pts], 255)

    # Kırp
    cropped = image[y : y + h, x : x + w]
    mask_cropped = mask[y : y + h, x : x + w]

    # Mask uygula
    result = cv2.bitwise_and(cropped, cropped, mask=mask_cropped)
    return result


def resize(image: np.ndarray, size: int = 64) -> np.ndarray:
    """Görüntüyü kare olarak yeniden boyutlandır."""
    return cv2.resize(image, (size, size), interpolation=cv2.INTER_AREA)


def normalize(image: np.ndarray) -> torch.Tensor:
    """BGR görüntüyü normalize edilmiş tensor'a çevir.

    OpenCV BGR -> RGB, [0,255] -> [0,1], HWC -> CHW
    """
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    tensor = torch.from_numpy(rgb).float() / 255.0
    tensor = tensor.permute(2, 0, 1)  # HWC -> CHW

    # ImageNet ortalaması ile normalize
    mean = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
    tensor = (tensor - mean) / std

    return tensor


def prepare_slot(image: np.ndarray, polygon: list[list[int]], size: int = 64) -> torch.Tensor:
    """Tek bir slot için: kırp -> resize -> normalize. Inference'da kullanılır."""
    cropped = crop_polygon(image, polygon)
    resized = resize(cropped, size)
    return normalize(resized)
