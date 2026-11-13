"""Batch sınıflandırma motoru.

Neden batch (toplu) inference:
- 20 slot'u tek tek modele vermek: 20 forward pass
- 20 slot'u batch olarak vermek: 1 forward pass
- GPU'da fark çok büyük, CPU'da bile belirgin hız artışı

Bu modül hem PyTorch hem ONNX backend destekler.
"""

import logging
from pathlib import Path

import numpy as np
import torch

from park_slot.model.cnn import ParkSlotCNN
from park_slot.annotation.schema import SlotStatus
from park_slot.settings import get_cpu_thread_limit

logger = logging.getLogger(__name__)


class InferenceEngine:
    """Slot görüntülerini toplu sınıflandıran motor."""

    def __init__(
        self,
        checkpoint_path: str | Path | None = None,
        onnx_path: str | Path | None = None,
        device: str = "cpu",
        confidence_threshold: float = 0.7,
    ):
        """
        Args:
            checkpoint_path: PyTorch .pth dosyası
            onnx_path: ONNX .onnx dosyası (varsa PyTorch yerine kullanılır)
            device: cpu veya cuda
            confidence_threshold: Bu altındaki tahminler UNKNOWN sayılır
        """
        self._device = torch.device(device)
        self._threshold = confidence_threshold
        self._model: ParkSlotCNN | None = None
        self._ort_session = None

        if onnx_path and Path(onnx_path).exists():
            self._load_onnx(onnx_path)
        elif checkpoint_path and Path(checkpoint_path).exists():
            self._load_pytorch(checkpoint_path)
        else:
            raise FileNotFoundError("Model dosyası bulunamadı (pth veya onnx)")

    def _load_pytorch(self, path: str | Path) -> None:
        """PyTorch checkpoint yükle."""
        self._model = ParkSlotCNN()
        checkpoint = torch.load(path, map_location=self._device)
        self._model.load_state_dict(checkpoint["model_state_dict"])
        self._model.to(self._device)
        self._model.eval()
        logger.info("PyTorch model yüklendi: %s", path)

    def _load_onnx(self, path: str | Path) -> None:
        """ONNX model yükle."""
        import onnxruntime as ort

        # Varsayılanda ORT kendi intra/inter-op thread pool'unu host'un TÜM
        # çekirdeklerine göre açar, container'ın cgroup CPU limitini bilmez
        # (bkz. settings.get_cpu_thread_limit docstring'i) - bu da torch/cv2
        # pinlenmiş olsa bile throttle'a sebep oluyordu.
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = get_cpu_thread_limit()
        opts.inter_op_num_threads = 1
        self._ort_session = ort.InferenceSession(str(path), sess_options=opts)
        logger.info("ONNX model yüklendi: %s", path)

    def predict_batch(
        self, tensors: list[torch.Tensor],
    ) -> list[tuple[SlotStatus, float]]:
        """Birden fazla slot tensor'ını toplu sınıflandır.

        Args:
            tensors: [(3, 64, 64), ...] normalize edilmiş tensor listesi

        Returns:
            [(SlotStatus, confidence), ...] her slot için durum ve güven
        """
        if not tensors:
            return []

        # Tensor'ları batch'e birleştir: (N, 3, 64, 64)
        batch = torch.stack(tensors)

        if self._ort_session is not None:
            return self._predict_onnx(batch)
        else:
            return self._predict_pytorch(batch)

    @torch.no_grad()
    def _predict_pytorch(
        self, batch: torch.Tensor,
    ) -> list[tuple[SlotStatus, float]]:
        """PyTorch ile tahmin."""
        batch = batch.to(self._device)
        logits = self._model(batch)
        probs = torch.softmax(logits, dim=1).cpu().numpy()
        return self._probs_to_status(probs)

    def _predict_onnx(
        self, batch: torch.Tensor,
    ) -> list[tuple[SlotStatus, float]]:
        """ONNX runtime ile tahmin."""
        ort_input = {"image": batch.numpy()}
        logits = self._ort_session.run(None, ort_input)[0]

        # Softmax elle uygula
        exp = np.exp(logits - logits.max(axis=1, keepdims=True))
        probs = exp / exp.sum(axis=1, keepdims=True)
        return self._probs_to_status(probs)

    def _probs_to_status(
        self, probs: np.ndarray,
    ) -> list[tuple[SlotStatus, float]]:
        """Olasılıkları SlotStatus'a çevir.

        probs[:, 0] = empty olasılığı
        probs[:, 1] = occupied olasılığı

        Eğer en yüksek olasılık threshold'un altındaysa → UNKNOWN
        """
        results = []
        for prob in probs:
            max_idx = int(prob.argmax())
            confidence = float(prob[max_idx])

            if confidence < self._threshold:
                status = SlotStatus.UNKNOWN
            elif max_idx == 0:
                status = SlotStatus.EMPTY
            else:
                status = SlotStatus.OCCUPIED

            results.append((status, confidence))

        return results
