"""ONNX export - Modeli PyTorch'tan bağımsız hale getir.

ONNX nedir:
- Open Neural Network Exchange
- PyTorch modelini evrensel formata çevirir
- onnxruntime ile çalıştırılır (PyTorch'a gerek kalmaz)
- Daha hızlı inference, daha az bellek
- C++, Java, C# gibi dillerden de kullanılabilir
"""

import logging
from pathlib import Path

import torch

from park_slot.model.cnn import ParkSlotCNN

logger = logging.getLogger(__name__)


def export_to_onnx(
    checkpoint_path: str | Path,
    output_path: str | Path,
    input_size: int = 64,
) -> None:
    """PyTorch checkpoint'ini ONNX formatına çevir.

    Args:
        checkpoint_path: .pth dosyası
        output_path: .onnx çıktı dosyası
        input_size: Model girdi boyutu
    """
    checkpoint_path = Path(checkpoint_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Model yükle
    model = ParkSlotCNN()
    checkpoint = torch.load(checkpoint_path, map_location="cpu")
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()

    # Örnek girdi (export için gerekli)
    dummy_input = torch.randn(1, 3, input_size, input_size)

    # ONNX export
    torch.onnx.export(
        model,
        dummy_input,
        str(output_path),
        input_names=["image"],
        output_names=["logits"],
        dynamic_axes={
            "image": {0: "batch_size"},    # Batch boyutu değişken
            "logits": {0: "batch_size"},
        },
        opset_version=17,
    )

    logger.info("ONNX export tamamlandı: %s", output_path)

    # Doğrulama
    _verify_onnx(output_path, dummy_input, model)


def _verify_onnx(onnx_path: Path, dummy_input: torch.Tensor, model: ParkSlotCNN) -> None:
    """ONNX modelinin doğru çalıştığını kontrol et."""
    try:
        import onnx
        import onnxruntime as ort
        import numpy as np

        # ONNX model geçerli mi?
        onnx_model = onnx.load(str(onnx_path))
        onnx.checker.check_model(onnx_model)
        logger.info("ONNX model doğrulaması başarılı")

        # PyTorch vs ONNX çıktılarını karşılaştır
        session = ort.InferenceSession(str(onnx_path))
        ort_input = {"image": dummy_input.numpy()}
        ort_output = session.run(None, ort_input)[0]

        with torch.no_grad():
            torch_output = model(dummy_input).numpy()

        diff = np.abs(torch_output - ort_output).max()
        logger.info("PyTorch vs ONNX maksimum fark: %.6f", diff)

        if diff > 1e-4:
            logger.warning("Fark beklenenin üstünde! Modeli kontrol edin.")
        else:
            logger.info("ONNX export doğrulandı - sonuçlar tutarlı")

    except ImportError:
        logger.warning("onnx/onnxruntime yüklü değil, doğrulama atlandı")
