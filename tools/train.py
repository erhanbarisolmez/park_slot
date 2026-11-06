"""Model eğitim aracı.

Kullanım:
    python tools/train.py
    python tools/train.py --data data/labeled --epochs 100 --lr 0.0005
    python tools/train.py --config config/cam2.yaml   # data/output/hyperparametreler config'ten okunur

--config verilirse, verilmeyen CLI flag'leri için varsayılan değerler o
dosyanın `training`/`model`/`inference` bölümlerinden alınır (elle her
kamerada aynı --data/--output'u tekrar tekrar yazmaya gerek kalmaz). Açıkça
verilen bir CLI flag'i her zaman config'teki değeri ezer.
"""

import argparse
from pathlib import Path

from park_slot.settings import load_settings
from park_slot.training.trainer import Trainer

# train.py'nin kendi yerleşik varsayılanları (--config verilmezse kullanılır)
_FALLBACK = {
    "data": "data/labeled",
    "output": "data/models",
    "epochs": 50,
    "batch_size": 32,
    "lr": 0.001,
    "patience": 7,
    "device": "cpu",
    "train_split": 0.8,
}


def main():
    parser = argparse.ArgumentParser(description="ParkSlotCNN eğitimi")
    parser.add_argument("--config", default=None, help="Kamera config YAML'ı (örn. config/cam2.yaml)")
    parser.add_argument("--data", default=None, help="Etiketli veri klasörü")
    parser.add_argument("--output", default=None, help="Checkpoint klasörü")
    parser.add_argument("--epochs", type=int, default=None, help="Epoch sayısı")
    parser.add_argument("--batch-size", type=int, default=None, help="Batch boyutu")
    parser.add_argument("--lr", type=float, default=None, help="Learning rate")
    parser.add_argument("--patience", type=int, default=None, help="Early stopping patience")
    parser.add_argument("--device", default=None, help="cpu veya cuda")
    parser.add_argument("--train-split", type=float, default=None, help="Train/val bölünme oranı")
    args = parser.parse_args()

    defaults = dict(_FALLBACK)
    if args.config:
        settings = load_settings(Path(args.config))
        defaults.update({
            "data": settings.training.data_dir,
            "output": str(Path(settings.model.checkpoint_path).parent),
            "epochs": settings.training.epochs,
            "batch_size": settings.training.batch_size,
            "lr": settings.training.learning_rate,
            "patience": settings.training.early_stopping_patience,
            "device": settings.inference.device,
            "train_split": settings.training.train_split,
        })

    data = args.data if args.data is not None else defaults["data"]
    output = args.output if args.output is not None else defaults["output"]
    epochs = args.epochs if args.epochs is not None else defaults["epochs"]
    batch_size = args.batch_size if args.batch_size is not None else defaults["batch_size"]
    lr = args.lr if args.lr is not None else defaults["lr"]
    patience = args.patience if args.patience is not None else defaults["patience"]
    device = args.device if args.device is not None else defaults["device"]
    train_split = args.train_split if args.train_split is not None else defaults["train_split"]

    trainer = Trainer(
        data_dir=data,
        checkpoint_dir=output,
        batch_size=batch_size,
        learning_rate=lr,
        epochs=epochs,
        patience=patience,
        device=device,
        train_split=train_split,
    )

    model = trainer.train()
    print(f"\nEğitim tamamlandı! Model: {output}/best.pth")


if __name__ == "__main__":
    main()
