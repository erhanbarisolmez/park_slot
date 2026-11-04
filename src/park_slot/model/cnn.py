"""ParkSlotCNN - Sıfırdan tasarlanmış CNN modeli.

Mimari: VGG-style (3x3 kernel, çift conv bloklar)
Girdi: (B, 3, 64, 64) - 64x64 RGB görüntü
Çıktı: (B, 2) - [empty, occupied] logits
Parametre: ~300K

Neden bu tasarım:
- 3x3 kernel: En küçük spatial ilişki yakalayan kernel boyutu.
  İki 3x3 conv üst üste = bir 5x5 conv'un receptive field'ı,
  ama daha az parametre ve daha fazla non-linearity.

- Kanal ilerlemesi 32→64→128: Her MaxPool spatial boyutu yarılar,
  kanal sayısını ikiye katlarız → bilgi kaybını telafi eder.

- BatchNorm: Her conv'dan sonra. Eğitimi stabilize eder,
  daha yüksek learning rate kullanılabilir, regularization etkisi var.

- AdaptiveAvgPool: Girdi boyutu değişse bile (64x64 veya 96x96)
  her zaman sabit 2x2 çıktı verir → esnek mimari.

- Dropout (0.4 + 0.3): Küçük dataset'te overfitting'i önler.
  İlk katmanda daha agresif (0.4), ikincide daha hafif (0.3).

- ~300K parametre: 2-5K görüntüyle eğitilebilir.
  Daha büyük model daha fazla veri ister. CPU'da ~1ms/batch.
"""

import torch
import torch.nn as nn


class ConvBlock(nn.Module):
    """Conv2d → BatchNorm → ReLU bloğu.

    Bu 3'lü pattern CNN'lerde standart:
    - Conv: Özellik çıkarma
    - BN: Değerleri normalize et, eğitimi stabilize et
    - ReLU: Non-linearity (negatif değerleri sıfırla)
    """

    def __init__(self, in_channels: int, out_channels: int):
        super().__init__()
        self.conv = nn.Conv2d(
            in_channels, out_channels,
            kernel_size=3, padding=1,  # padding=1: çıktı boyutu = girdi boyutu
        )
        self.bn = nn.BatchNorm2d(out_channels)
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.relu(self.bn(self.conv(x)))


class ParkSlotCNN(nn.Module):
    """Park slotu sınıflandırma modeli.

    İleri geçiş:
        (B, 3, 64, 64)
        → Block1: 2xConv(32) + Pool → (B, 32, 32, 32)
        → Block2: 2xConv(64) + Pool → (B, 64, 16, 16)
        → Block3: 1xConv(128) + Pool → (B, 128, 8, 8)
        → Block4: 1xConv(128) + AdaptivePool → (B, 128, 2, 2) = 512
        → Flatten → Dropout(0.4) → FC(64) → ReLU
        → Dropout(0.3) → FC(2) → logits
    """

    def __init__(self, num_classes: int = 2):
        super().__init__()

        # Block 1 - Kenar ve doku tespiti
        # İlk katmanlar düşük seviye özellikler öğrenir: kenarlar, köşeler, renkler
        self.block1 = nn.Sequential(
            ConvBlock(3, 32),     # RGB(3) → 32 filtre
            ConvBlock(32, 32),    # 32 → 32 (derinlik ekle)
            nn.MaxPool2d(2, 2),   # 64x64 → 32x32
        )

        # Block 2 - Şekil ve araç konturu
        # Orta katmanlar şekil, kontur gibi özellikler öğrenir
        self.block2 = nn.Sequential(
            ConvBlock(32, 64),    # 32 → 64 filtre
            ConvBlock(64, 64),    # 64 → 64
            nn.MaxPool2d(2, 2),   # 32x32 → 16x16
        )

        # Block 3 - Üst düzey özellikler
        # Araç parçaları, gölge, zemin desenleri
        self.block3 = nn.Sequential(
            ConvBlock(64, 128),   # 64 → 128 filtre
            nn.MaxPool2d(2, 2),   # 16x16 → 8x8
        )

        # Block 4 - Soyut özellikler
        # "Burada araç var mı?" sorusunun cevabı
        self.block4 = nn.Sequential(
            ConvBlock(128, 128),          # 128 → 128
            nn.AdaptiveAvgPool2d((2, 2)), # 8x8 → 2x2 (sabit çıktı)
        )
        # Çıktı: 128 * 2 * 2 = 512 özellik

        # Classifier - Özelliklerden sınıf tahmini
        self.classifier = nn.Sequential(
            nn.Flatten(),           # (B, 128, 2, 2) → (B, 512)
            nn.Dropout(0.4),        # %40 nöron kapat (overfitting önle)
            nn.Linear(512, 64),     # 512 → 64
            nn.ReLU(inplace=True),
            nn.Dropout(0.3),        # %30 nöron kapat
            nn.Linear(64, num_classes),  # 64 → 2 (empty/occupied)
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """İleri geçiş.

        Args:
            x: (B, 3, 64, 64) girdi tensor

        Returns:
            (B, 2) logits (softmax uygulanmamış)
        """
        x = self.block1(x)
        x = self.block2(x)
        x = self.block3(x)
        x = self.block4(x)
        x = self.classifier(x)
        return x

    def predict(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """Tahmin yap, sınıf ve olasılık döndür.

        Args:
            x: (B, 3, 64, 64) girdi tensor

        Returns:
            classes: (B,) - 0=empty, 1=occupied
            probs: (B, 2) - sınıf olasılıkları
        """
        self.eval()
        with torch.no_grad():
            logits = self.forward(x)
            probs = torch.softmax(logits, dim=1)
            classes = torch.argmax(probs, dim=1)
        return classes, probs

    def count_parameters(self) -> int:
        """Toplam eğitilebilir parametre sayısı."""
        return sum(p.numel() for p in self.parameters() if p.requires_grad)
