"""Metrics birim testleri."""

import numpy as np
from park_slot.training.metrics import MetricsTracker


def test_accuracy_perfect():
    """Tüm tahminler doğruysa accuracy 1.0 olmalı."""
    m = MetricsTracker()
    preds = np.array([0, 0, 1, 1])
    targets = np.array([0, 0, 1, 1])
    m.update(preds, targets)
    assert m.accuracy == 1.0


def test_accuracy_half():
    """Yarısı doğruysa accuracy 0.5 olmalı."""
    m = MetricsTracker()
    preds = np.array([0, 1, 0, 1])
    targets = np.array([0, 0, 1, 1])
    m.update(preds, targets)
    assert m.accuracy == 0.5


def test_f1_score():
    """F1 score hesaplaması."""
    m = MetricsTracker()
    # 3 doğru empty, 1 yanlış occupied (aslında empty)
    preds = np.array([0, 0, 0, 1])
    targets = np.array([0, 0, 0, 0])
    m.update(preds, targets)

    # Empty: precision=1.0 (3/3 doğru), recall=0.75 (3/4 buldu)
    assert m.precision(0) == 1.0
    assert m.recall(0) == 0.75


def test_confusion_matrix():
    """Confusion matrix doğru dolduruluyor mu?"""
    m = MetricsTracker()
    preds = np.array([0, 1, 0, 1])
    targets = np.array([0, 0, 1, 1])
    m.update(preds, targets)

    cm = m.confusion_matrix
    assert cm[0][0] == 1  # True empty
    assert cm[0][1] == 1  # False occupied (aslında empty)
    assert cm[1][0] == 1  # False empty (aslında occupied)
    assert cm[1][1] == 1  # True occupied


def test_reset():
    """Reset sonrası metrikler sıfırlanmalı."""
    m = MetricsTracker()
    m.update(np.array([0, 1]), np.array([0, 1]))
    m.reset()
    assert m.accuracy == 0.0
    assert m.confusion_matrix.sum() == 0
