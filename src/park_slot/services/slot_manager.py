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
from park_slot.utils.image import crop_polygon, resize, normalize

logger = logging.getLogger(__name__)


class SlotManager:
    """Slot konfigürasyonlarını yönetir ve kırpma yapar."""

    def __init__(self, config_path: str | Path, input_size: int = 64):
        self._input_size = input_size
        self._config = self._load_config(config_path)
        logger.info(
            "SlotManager: %s, %d slot yüklendi",
            self._config.camera_id,
            len(self._config.slots),
        )

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

    def crop_all(self, frame: np.ndarray) -> list[tuple[SlotDefinition, torch.Tensor]]:
        """Bir karedeki tüm slotları kırp ve tensor'a çevir.

        Args:
            frame: BGR görüntü (OpenCV formatı)

        Returns:
            [(slot_def, tensor), ...] her slot için tanım ve tensor çifti
        """
        results = []

        for slot in self._config.slots:
            try:
                cropped = crop_polygon(frame, slot.polygon)
                resized = resize(cropped, self._input_size)
                tensor = normalize(resized)
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
