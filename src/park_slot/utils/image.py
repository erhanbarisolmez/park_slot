"""Görüntü işleme yardımcıları: kırpma, resize, normalize."""

import cv2
import numpy as np
import torch

from park_slot.annotation.geometry import polygon_to_quad
from park_slot.annotation.transform import compute_perspective_matrix, warp_image


def crop_polygon_warped(image: np.ndarray, polygon: list[list[int]], output_size: int = 64) -> np.ndarray:
    """Polygonu kırp + perspektif düzelt + kare boyuta getir - tek adımda.

    Açılı kameralarda slot'lar yamuk görünür; polygon'un 4 köşesini (veya
    >4 noktalı polygonlar için en yakın dönmüş dikdörtgenini) kuşbakışı bir
    kareye warp ederek, slot'un kameraya göre konumundan/açısından bağımsız,
    tutarlı bir crop üretir. Training ve inference AYNI fonksiyonu kullanmalı.
    """
    quad = polygon_to_quad(polygon)
    matrix = compute_perspective_matrix(quad, output_size, output_size)
    return warp_image(image, matrix, output_size, output_size)


def equalize_lighting(image: np.ndarray) -> np.ndarray:
    """CLAHE (adaptif histogram eşitleme) ile aydınlatma değişimlerine karşı
    dayanıklılık sağla - TÜM KAREYE, slot kırpılmadan ÖNCE uygulanmalı.

    Neden gerekli: gece sahnelerinde far ışığı, dekoratif ışık döngüsü, bulut
    geçişi gibi mutlak parlaklık değişimleri fiziksel olarak bitişik
    slotları aynı anda etkileyebiliyor (bkz. StateStore._spatial_clusters) -
    bu da gerçek bir engellenme olmadığı halde toplu engellenme tespitini
    tetikleyip slotları gereksiz yere UNKNOWN'da kilitleyebiliyor. CLAHE,
    LAB renk uzayının sadece L (parlaklık) kanalına uygulanarak renk
    bilgisi bozulmadan yerel kontrast öne çıkarılır - model artık mutlak
    parlaklıktan çok şekil/doku desenine dayanır.

    Neden tam karede (64x64 kırpılmış slot'ta DEĞİL): tileGridSize=(8,8), bir
    720p karede ~90-160px'lik makul karolara denk gelir; aynı ızgara 64x64'lük
    bir crop'ta sadece 8x8 piksellik karolara düşer - bu da gözle görülür
    bloklu/gürültülü bir sonuç üretir (collector.py'nin önizleme ekranında
    NEAREST ile 256x256'ya büyütülünce iyice belirginleşir, etiketlemeyi
    zorlaştırır). Bu yüzden çağıran taraf (collector.py, slot_manager.py)
    bunu crop_polygon_warped'tan ÖNCE, ham kareye bir kez uygular - hem daha
    pürüzsüz sonuç hem de kare başına 14 kez değil 1 kez çalışır.
    """
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_eq = clahe.apply(l_channel)
    lab_eq = cv2.merge((l_eq, a_channel, b_channel))
    return cv2.cvtColor(lab_eq, cv2.COLOR_LAB2BGR)


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
