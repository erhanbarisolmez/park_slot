"""Slot konfigürasyon yükleme ve kırpma yönetimi.

Bu servis:
- JSON'dan slot tanımlarını yükler
- Bir karedeki tüm slotları kırpıp tensor'a çevirir
- Inference engine'e hazır batch oluşturur
"""

import json
import logging
from pathlib import Path

import numpy as np
import torch

from park_slot.annotation.schema import CameraSlotConfig, SlotDefinition
from park_slot.inference.overlay import MINIMAP_HEIGHT, MINIMAP_WIDTH, project_to_minimap
from park_slot.utils.image import crop_polygon_warped, equalize_lighting, normalize

logger = logging.getLogger(__name__)


class SlotManager:
    """Slot konfigürasyonlarını yönetir ve kırpma yapar."""

    def __init__(self, config_path: str | Path, input_size: int = 64):
        self._input_size = input_size
        self._config = self._load_config(config_path)
        self._minimap_coords = self._compute_minimap_coords()
        logger.info(
            "SlotManager: %s, %d slot yüklendi",
            self._config.camera_id,
            len(self._config.slots),
        )

    def _compute_minimap_coords(self) -> dict[int, tuple[float, float]]:
        """perspective_transform varsa, her slot için normalize (0-1) kuşbakışı
        koordinat hesapla - API/WebSocket üzerinden canlı video veremeyen
        istemcilere (örn. mobil uygulama) sunmak için. Yoksa boş sözlük döner."""
        pt = self._config.perspective_transform
        if not pt:
            return {}

        matrix = np.array(pt, dtype=np.float64)
        coords = {}
        for slot in self._config.slots:
            x, y = project_to_minimap(slot.polygon, matrix)
            nx = float(np.clip(x / MINIMAP_WIDTH, 0.0, 1.0))
            ny = float(np.clip(y / MINIMAP_HEIGHT, 0.0, 1.0))
            coords[slot.id] = (nx, ny)
        return coords

    @property
    def minimap_coords(self) -> dict[int, tuple[float, float]]:
        return self._minimap_coords

    @staticmethod
    def _load_config(path: str | Path) -> CameraSlotConfig:
        """JSON dosyasından slot konfigürasyonu yükle."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Slot config bulunamadı: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return CameraSlotConfig(**data)

    @property
    def config(self) -> CameraSlotConfig:
        return self._config

    @property
    def slots(self) -> list[SlotDefinition]:
        return self._config.slots

    @property
    def camera_id(self) -> str:
        return self._config.camera_id

    @property
    def input_size(self) -> int:
        return self._input_size

    def crop_all(self, frame: np.ndarray) -> list[tuple[SlotDefinition, torch.Tensor]]:
        """Bir karedeki tüm slotları kırp ve tensor'a çevir.

        Args:
            frame: BGR görüntü (OpenCV formatı)

        Returns:
            [(slot_def, tensor), ...] her slot için tanım ve tensor çifti
        """
        results = []
        frame = equalize_lighting(frame)

        for slot in self._config.slots:
            try:
                cropped = crop_polygon_warped(frame, slot.polygon, self._input_size)
                tensor = normalize(cropped)
                results.append((slot, tensor))
            except Exception as e:
                logger.warning("Slot %s kırpılamadı: %s", slot.label, e)

        return results

    def get_tensors(self, frame: np.ndarray) -> tuple[list[SlotDefinition], list[torch.Tensor]]:
        """Kırpma sonuçlarını ayrı listeler olarak döndür.

        Inference engine batch olarak bekler, bu format daha uygun.
        """
        pairs = self.crop_all(frame)
        if not pairs:
            return [], []

        slots, tensors = zip(*pairs)
        return list(slots), list(tensors)
