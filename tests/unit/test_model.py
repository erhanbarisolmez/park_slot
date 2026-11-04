"""CNN model birim testleri."""

import torch
from park_slot.model.cnn import ParkSlotCNN


def test_model_output_shape():
    """Model çıktı boyutu doğru mu?"""
    model = ParkSlotCNN(num_classes=2)
    x = torch.randn(4, 3, 64, 64)  # batch=4
    output = model(x)
    assert output.shape == (4, 2), f"Beklenen (4, 2), alınan {output.shape}"


def test_model_single_input():
    """Tek girdi ile çalışıyor mu?"""
    model = ParkSlotCNN()
    x = torch.randn(1, 3, 64, 64)
    output = model(x)
    assert output.shape == (1, 2)


def test_model_predict():
    """predict() sınıf ve olasılık döndürüyor mu?"""
    model = ParkSlotCNN()
    x = torch.randn(3, 3, 64, 64)
    classes, probs = model.predict(x)

    assert classes.shape == (3,)
    assert probs.shape == (3, 2)
    # Olasılıklar toplamı 1 olmalı
    sums = probs.sum(dim=1)
    for s in sums:
        assert abs(s.item() - 1.0) < 1e-5


def test_model_parameter_count():
    """Parametre sayısı makul aralıkta mı?"""
    model = ParkSlotCNN()
    count = model.count_parameters()
    # ~300K civarı olmalı
    assert 200_000 < count < 500_000, f"Parametre sayısı: {count}"
