"""Eğitim metrikleri: Accuracy, F1-score, Confusion Matrix.

Neden sadece accuracy yetmez:
- 900 empty + 100 occupied varsa, hepsine "empty" dersen %90 accuracy olur
- Ama occupied'ı hiç bulamadın! Bu tehlikeli.
- F1-score hem precision hem recall'u dengeler
- Confusion matrix tam resmi gösterir
"""

import numpy as np


class MetricsTracker:
    """Eğitim/değerlendirme metriklerini takip eder."""

    def __init__(self, num_classes: int = 2):
        self._num_classes = num_classes
        self.reset()

    def reset(self) -> None:
        """Metrikleri sıfırla (her epoch başında çağır)."""
        self._confusion = np.zeros((self._num_classes, self._num_classes), dtype=int)
        self._total_loss = 0.0
        self._num_batches = 0

    def update(self, predictions: np.ndarray, targets: np.ndarray, loss: float = 0.0) -> None:
        """Bir batch'in sonuçlarını ekle.

        Args:
            predictions: Model tahminleri (sınıf indeksleri)
            targets: Gerçek etiketler
            loss: Batch loss değeri
        """
        for pred, target in zip(predictions, targets):
            self._confusion[target][pred] += 1

        self._total_loss += loss
        self._num_batches += 1

    @property
    def accuracy(self) -> float:
        """Doğru tahmin oranı."""
        total = self._confusion.sum()
        if total == 0:
            return 0.0
        correct = np.trace(self._confusion)  # Köşegen toplamı = doğru tahminler
        return correct / total

    @property
    def avg_loss(self) -> float:
        """Ortalama loss."""
        if self._num_batches == 0:
            return 0.0
        return self._total_loss / self._num_batches

    def precision(self, class_id: int) -> float:
        """Precision: Modelin 'X' dediğinin kaçı gerçekten X?

        Örnek: Model 100 kez "occupied" dedi, 90'ı doğruysa → %90 precision
        """
        col_sum = self._confusion[:, class_id].sum()
        if col_sum == 0:
            return 0.0
        return self._confusion[class_id][class_id] / col_sum

    def recall(self, class_id: int) -> float:
        """Recall: Gerçek X'lerin kaçını bulduk?

        Örnek: 100 gerçek occupied var, 80'ini bulduksa → %80 recall
        """
        row_sum = self._confusion[class_id].sum()
        if row_sum == 0:
            return 0.0
        return self._confusion[class_id][class_id] / row_sum

    def f1(self, class_id: int) -> float:
        """F1-score: Precision ve recall'un harmonik ortalaması.

        İkisi de yüksek olmalı. Biri düşükse F1 de düşer.
        """
        p = self.precision(class_id)
        r = self.recall(class_id)
        if p + r == 0:
            return 0.0
        return 2 * (p * r) / (p + r)

    @property
    def macro_f1(self) -> float:
        """Tüm sınıfların F1 ortalaması."""
        scores = [self.f1(i) for i in range(self._num_classes)]
        return np.mean(scores)

    @property
    def confusion_matrix(self) -> np.ndarray:
        """Confusion matrix kopyası."""
        return self._confusion.copy()

    def summary(self) -> str:
        """Okunabilir metrik özeti."""
        lines = [
            f"Loss: {self.avg_loss:.4f} | Accuracy: {self.accuracy:.4f} | F1: {self.macro_f1:.4f}",
            f"  Empty    - P: {self.precision(0):.3f}  R: {self.recall(0):.3f}  F1: {self.f1(0):.3f}",
            f"  Occupied - P: {self.precision(1):.3f}  R: {self.recall(1):.3f}  F1: {self.f1(1):.3f}",
            f"  Confusion Matrix:",
            f"              Pred Empty  Pred Occupied",
            f"  True Empty    {self._confusion[0][0]:>6}    {self._confusion[0][1]:>6}",
            f"  True Occup    {self._confusion[1][0]:>6}    {self._confusion[1][1]:>6}",
        ]
        return "\n".join(lines)
