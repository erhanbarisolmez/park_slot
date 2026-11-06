"""PyTorch Dataset ve veri artırma (augmentation).

Veri artırma neden önemli:
- Küçük dataset'lerde (2-5K görüntü) overfitting riski yüksek
- Aynı görüntüyü farklı şekillerde göstererek veri çeşitliliği artırılır
- Model parlaklık, döndürme, ayna gibi değişikliklere dayanıklı olur

Uygulanan augmentation'lar:
- Yatay çevirme (flip): Araç sağdan veya soldan gelebilir
- Parlaklık/kontrast: Gündüz/gece, gölge farkları
- Döndürme (±15°): Kamera açısı farklılıkları
- Gaussian blur: Odak/netlik farkları
"""

import logging
import random
import re
from pathlib import Path

import cv2
import numpy as np
import torch
from torch.utils.data import Dataset

from park_slot.utils.image import normalize

logger = logging.getLogger(__name__)

# Sınıf etiketleri
CLASS_MAP = {"empty": 0, "occupied": 1}
CLASS_NAMES = {v: k for k, v in CLASS_MAP.items()}

# collector.py'nin ürettiği dosya adı formatı: slot{id}_{timestamp_ms}.jpg
_FILENAME_RE = re.compile(r"slot(\d+)_(\d+)")


def parse_slot_and_time(path: Path) -> tuple[int, int]:
    """Dosya adından (slot_id, timestamp_ms) çıkar.

    Eşleşmezse her dosya kendi tekil grubu olur (dosya yolunun hash'i),
    böylece grup bazlı split'te sessizce yanlış gruplanmaz.
    """
    match = _FILENAME_RE.search(path.stem)
    if match:
        return int(match.group(1)), int(match.group(2))

    logger.warning("Dosya adından slot/zaman çıkarılamadı, tekil grup atanıyor: %s", path)
    return -1, hash(str(path))


def group_split(
    samples: list[tuple[Path, int]],
    train_ratio: float = 0.8,
    seed: int = 42,
    bucket_minutes: int = 30,
) -> tuple[list[int], list[int]]:
    """Aynı slot + aynı zaman penceresindeki örnekleri bölmeden train/val ayır.

    Neden gerekli: flat random_split, aynı slotun birbirine yakın zamanlı
    (örn. aynı toplama oturumundaki) kareleri hem train hem val'e dağıtabilir
    - bu, val accuracy'yi yanıltan bir data leakage'dır. Bunun yerine
    (slot_id, zaman-bucket) grupları bütün halinde bir tarafa atanır.

    Returns:
        (train_indices, val_indices) - samples listesine göre indeksler
    """
    bucket_ms = bucket_minutes * 60 * 1000
    groups: dict[tuple[int, int], list[int]] = {}
    for idx, (path, _label) in enumerate(samples):
        slot_id, timestamp_ms = parse_slot_and_time(path)
        key = (slot_id, timestamp_ms // bucket_ms if slot_id != -1 else timestamp_ms)
        groups.setdefault(key, []).append(idx)

    group_keys = list(groups.keys())
    random.Random(seed).shuffle(group_keys)

    target_train = int(len(samples) * train_ratio)
    train_indices: list[int] = []
    val_indices: list[int] = []
    for key in group_keys:
        if len(train_indices) < target_train:
            train_indices.extend(groups[key])
        else:
            val_indices.extend(groups[key])

    return train_indices, val_indices


class ParkSlotDataset(Dataset):
    """Park slotu görüntü dataset'i.

    Klasör yapısı:
        data_dir/
        ├── empty/      # Boş slot görüntüleri
        │   ├── img1.jpg
        │   └── ...
        └── occupied/   # Dolu slot görüntüleri
            ├── img1.jpg
            └── ...
    """

    def __init__(
        self,
        data_dir: str | Path,
        input_size: int = 64,
        augment: bool = False,
    ):
        self._data_dir = Path(data_dir)
        self._input_size = input_size
        self._augment = augment
        self._samples: list[tuple[Path, int]] = []

        self._load_samples()

    def _load_samples(self) -> None:
        """Görüntü dosyalarını ve etiketlerini yükle."""
        for class_name, class_id in CLASS_MAP.items():
            class_dir = self._data_dir / class_name
            if not class_dir.exists():
                logger.warning("Klasör bulunamadı: %s", class_dir)
                continue

            for img_path in class_dir.glob("*.jpg"):
                self._samples.append((img_path, class_id))

            # PNG desteği de ekle
            for img_path in class_dir.glob("*.png"):
                self._samples.append((img_path, class_id))

        logger.info(
            "Dataset yüklendi: %d görüntü (empty=%d, occupied=%d)",
            len(self._samples),
            sum(1 for _, c in self._samples if c == 0),
            sum(1 for _, c in self._samples if c == 1),
        )

    def _apply_augmentation(self, image: np.ndarray) -> np.ndarray:
        """Rastgele veri artırma uygula."""
        # %50 olasılıkla yatay çevir
        if np.random.random() > 0.5:
            image = cv2.flip(image, 1)

        # Parlaklık ve kontrast değişimi
        if np.random.random() > 0.5:
            alpha = np.random.uniform(0.8, 1.2)  # Kontrast
            beta = np.random.randint(-20, 20)     # Parlaklık
            image = cv2.convertScaleAbs(image, alpha=alpha, beta=beta)

        # ±15 derece döndürme
        if np.random.random() > 0.5:
            angle = np.random.uniform(-15, 15)
            h, w = image.shape[:2]
            center = (w // 2, h // 2)
            matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
            image = cv2.warpAffine(image, matrix, (w, h))

        # Gaussian blur
        if np.random.random() > 0.3:
            ksize = np.random.choice([3, 5])
            image = cv2.GaussianBlur(image, (ksize, ksize), 0)

        return image

    @property
    def samples(self) -> list[tuple[Path, int]]:
        return self._samples

    def __len__(self) -> int:
        return len(self._samples)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, int]:
        """Bir örnek döndür: (tensor, label)."""
        img_path, label = self._samples[idx]

        # Görüntüyü oku
        image = cv2.imread(str(img_path))
        if image is None:
            raise RuntimeError(f"Görüntü okunamadı: {img_path}")

        # Boyutlandır
        image = cv2.resize(image, (self._input_size, self._input_size))

        # Augmentation (sadece eğitimde)
        if self._augment:
            image = self._apply_augmentation(image)

        # Normalize et ve tensor'a çevir
        tensor = normalize(image)

        return tensor, label

    def get_class_weights(self) -> torch.Tensor:
        """Sınıf dengesizliği için ağırlık hesapla.

        Eğer 1000 empty, 500 occupied varsa:
        - empty ağırlığı: 1500 / (2 * 1000) = 0.75
        - occupied ağırlığı: 1500 / (2 * 500) = 1.50
        Böylece az olan sınıf daha önemli hale gelir.
        """
        counts = [0, 0]
        for _, label in self._samples:
            counts[label] += 1

        total = sum(counts)
        num_classes = len(counts)
        weights = [total / (num_classes * c) if c > 0 else 1.0 for c in counts]

        return torch.tensor(weights, dtype=torch.float32)
