"""Model eğitim aracı.

Kullanım:
    python tools/train.py
    python tools/train.py --data data/labeled --epochs 100 --lr 0.0005
"""

import argparse

from park_slot.training.trainer import Trainer


def main():
    parser = argparse.ArgumentParser(description="ParkSlotCNN eğitimi")
    parser.add_argument("--data", default="data/labeled", help="Etiketli veri klasörü")
    parser.add_argument("--output", default="data/models", help="Checkpoint klasörü")
    parser.add_argument("--epochs", type=int, default=50, help="Epoch sayısı")
    parser.add_argument("--batch-size", type=int, default=32, help="Batch boyutu")
    parser.add_argument("--lr", type=float, default=0.001, help="Learning rate")
    parser.add_argument("--patience", type=int, default=7, help="Early stopping patience")
    parser.add_argument("--device", default="cpu", help="cpu veya cuda")
    args = parser.parse_args()

    trainer = Trainer(
        data_dir=args.data,
        checkpoint_dir=args.output,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        epochs=args.epochs,
        patience=args.patience,
        device=args.device,
    )

    model = trainer.train()
    print(f"\nEğitim tamamlandı! Model: {args.output}/best.pth")


if __name__ == "__main__":
    main()
