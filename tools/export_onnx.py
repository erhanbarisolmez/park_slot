"""ONNX export aracı.

Kullanım:
    python tools/export_onnx.py
    python tools/export_onnx.py --checkpoint data/models/best.pth --output data/exports/model.onnx
"""

import argparse

from park_slot.model.export import export_to_onnx


def main():
    parser = argparse.ArgumentParser(description="ParkSlotCNN ONNX export")
    parser.add_argument("--checkpoint", default="data/models/best.pth", help="PyTorch checkpoint")
    parser.add_argument("--output", default="data/exports/model.onnx", help="ONNX çıktı dosyası")
    parser.add_argument("--input-size", type=int, default=64, help="Model girdi boyutu")
    args = parser.parse_args()

    print(f"Checkpoint: {args.checkpoint}")
    print(f"Çıktı: {args.output}")
    print(f"Girdi boyutu: {args.input_size}x{args.input_size}")

    export_to_onnx(args.checkpoint, args.output, args.input_size)
    print("ONNX export tamamlandı!")


if __name__ == "__main__":
    main()
