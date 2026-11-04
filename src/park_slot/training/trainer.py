"""Eğitim döngüsü + early stopping.

Eğitim akışı:
1. Her epoch'ta tüm eğitim verisini batch'ler halinde model'e ver
2. Loss hesapla, geri yayılım (backprop), ağırlıkları güncelle
3. Doğrulama seti ile performans ölç
4. Doğrulama loss'u düşmüyorsa patience kadar bekle, sonra dur (early stopping)
5. En iyi modeli kaydet

Early stopping neden önemli:
- Model eğitim verisini ezberleyebilir (overfitting)
- Doğrulama loss'u artmaya başladığında eğitimi durdur
- En iyi checkpoint'e geri dön
"""

import logging
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, random_split

from park_slot.model.cnn import ParkSlotCNN
from park_slot.training.dataset import ParkSlotDataset
from park_slot.training.metrics import MetricsTracker

logger = logging.getLogger(__name__)


class Trainer:
    """Model eğitim yöneticisi."""

    def __init__(
        self,
        data_dir: str | Path,
        checkpoint_dir: str | Path = "data/models",
        input_size: int = 64,
        batch_size: int = 32,
        learning_rate: float = 0.001,
        epochs: int = 50,
        patience: int = 7,
        train_split: float = 0.8,
        device: str = "cpu",
    ):
        self._device = torch.device(device)
        self._epochs = epochs
        self._patience = patience
        self._checkpoint_dir = Path(checkpoint_dir)
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)

        # Dataset oluştur
        full_dataset = ParkSlotDataset(data_dir, input_size=input_size, augment=True)
        val_dataset = ParkSlotDataset(data_dir, input_size=input_size, augment=False)

        # Train/validation bölme
        total = len(full_dataset)
        train_size = int(total * train_split)
        val_size = total - train_size

        train_subset, _ = random_split(full_dataset, [train_size, val_size])
        _, val_subset = random_split(val_dataset, [train_size, val_size])

        self._train_loader = DataLoader(
            train_subset, batch_size=batch_size, shuffle=True, num_workers=0,
        )
        self._val_loader = DataLoader(
            val_subset, batch_size=batch_size, shuffle=False, num_workers=0,
        )

        logger.info("Eğitim: %d, Doğrulama: %d", train_size, val_size)

        # Model
        self._model = ParkSlotCNN().to(self._device)
        logger.info("Model parametreleri: %d", self._model.count_parameters())

        # Sınıf ağırlıkları (dengesiz veri için)
        weights = full_dataset.get_class_weights().to(self._device)
        self._criterion = nn.CrossEntropyLoss(weight=weights)

        # Optimizer: Adam (adaptive learning rate)
        self._optimizer = torch.optim.Adam(
            self._model.parameters(), lr=learning_rate,
        )

        # Learning rate scheduler: plateau'da lr'yi düşür
        self._scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            self._optimizer, mode="min", factor=0.5, patience=3,
        )

    def _train_epoch(self) -> MetricsTracker:
        """Bir epoch eğitim."""
        self._model.train()  # Eğitim modu (dropout aktif, BN güncellenir)
        metrics = MetricsTracker()

        for images, labels in self._train_loader:
            images = images.to(self._device)
            labels = labels.to(self._device)

            # İleri geçiş
            outputs = self._model(images)
            loss = self._criterion(outputs, labels)

            # Geri yayılım
            self._optimizer.zero_grad()  # Gradient'leri sıfırla
            loss.backward()              # Gradient hesapla
            self._optimizer.step()       # Ağırlıkları güncelle

            # Metrik kaydet
            preds = outputs.argmax(dim=1).cpu().numpy()
            metrics.update(preds, labels.cpu().numpy(), loss.item())

        return metrics

    @torch.no_grad()
    def _validate(self) -> MetricsTracker:
        """Doğrulama seti ile değerlendir."""
        self._model.eval()  # Değerlendirme modu (dropout kapalı, BN sabit)
        metrics = MetricsTracker()

        for images, labels in self._val_loader:
            images = images.to(self._device)
            labels = labels.to(self._device)

            outputs = self._model(images)
            loss = self._criterion(outputs, labels)

            preds = outputs.argmax(dim=1).cpu().numpy()
            metrics.update(preds, labels.cpu().numpy(), loss.item())

        return metrics

    def _save_checkpoint(self, epoch: int, val_loss: float) -> None:
        """Model checkpoint kaydet."""
        path = self._checkpoint_dir / "best.pth"
        torch.save({
            "epoch": epoch,
            "model_state_dict": self._model.state_dict(),
            "optimizer_state_dict": self._optimizer.state_dict(),
            "val_loss": val_loss,
        }, path)
        logger.info("Checkpoint kaydedildi: %s (epoch %d)", path, epoch)

    def train(self) -> ParkSlotCNN:
        """Eğitimi başlat ve en iyi modeli döndür."""
        best_val_loss = float("inf")
        patience_counter = 0

        print(f"\nEğitim başlıyor ({self._epochs} epoch, patience={self._patience})")
        print(f"Device: {self._device}")
        print("-" * 60)

        for epoch in range(1, self._epochs + 1):
            # Eğitim
            train_metrics = self._train_epoch()

            # Doğrulama
            val_metrics = self._validate()

            # Learning rate güncelle
            self._scheduler.step(val_metrics.avg_loss)
            current_lr = self._optimizer.param_groups[0]["lr"]

            # Sonuçları yazdır
            print(
                f"Epoch {epoch:3d}/{self._epochs} | "
                f"Train Loss: {train_metrics.avg_loss:.4f} Acc: {train_metrics.accuracy:.4f} | "
                f"Val Loss: {val_metrics.avg_loss:.4f} Acc: {val_metrics.accuracy:.4f} F1: {val_metrics.macro_f1:.4f} | "
                f"LR: {current_lr:.6f}"
            )

            # Early stopping kontrolü
            if val_metrics.avg_loss < best_val_loss:
                best_val_loss = val_metrics.avg_loss
                patience_counter = 0
                self._save_checkpoint(epoch, best_val_loss)
            else:
                patience_counter += 1
                if patience_counter >= self._patience:
                    print(f"\nEarly stopping! {self._patience} epoch boyunca iyileşme yok.")
                    break

        # En iyi modeli yükle
        best_path = self._checkpoint_dir / "best.pth"
        if best_path.exists():
            checkpoint = torch.load(best_path, map_location=self._device)
            self._model.load_state_dict(checkpoint["model_state_dict"])
            print(f"\nEn iyi model yüklendi (epoch {checkpoint['epoch']})")

        # Final değerlendirme
        final_metrics = self._validate()
        print("\n" + "=" * 60)
        print("Final Değerlendirme:")
        print(final_metrics.summary())

        return self._model
