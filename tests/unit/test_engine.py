"""InferenceEngine birim testleri.

Önceden sıfır coverage'a sahip modül - gerçek bir sahte/fixture checkpoint
yerine repo'ya zaten commit edilmiş gerçek cam1 checkpoint'i kullanılıyor
(data/models_cam1/best.pth - .gitignore'da bilerek dahil edilmiş, bkz.
KULLANIM.md); amaç doğruluk değil, predict_batch'in sözleşmesini (shape,
olasılık aralığı, boş girdi) doğrulamak.
"""

from pathlib import Path

import pytest
import torch

from park_slot.annotation.schema import SlotStatus
from park_slot.inference.engine import InferenceEngine

CHECKPOINT_PATH = Path("data/models_cam1/best.pth")

pytestmark = pytest.mark.skipif(
    not CHECKPOINT_PATH.exists(),
    reason="data/models_cam1/best.pth bulunamadı (checkout eksik olabilir)",
)


def make_engine() -> InferenceEngine:
    return InferenceEngine(checkpoint_path=CHECKPOINT_PATH, device="cpu")


def test_model_version_contains_checkpoint_name():
    engine = make_engine()
    assert engine.model_version is not None
    assert "best.pth" in engine.model_version


def test_predict_batch_empty_input():
    engine = make_engine()
    assert engine.predict_batch([]) == []


def test_predict_batch_shape_and_range():
    engine = make_engine()
    tensors = [torch.rand(3, 64, 64) for _ in range(5)]

    results = engine.predict_batch(tensors)

    assert len(results) == 5
    for status, confidence in results:
        assert isinstance(status, SlotStatus)
        assert 0.0 <= confidence <= 1.0
