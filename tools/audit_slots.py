"""Slot konfigürasyonlarındaki bozuk (kendi kendini kesen) polygon'ları bul ve düzelt.

Annotator artık yeni kaydedilen her slotu otomatik sıralıyor (bkz.
SlotAnnotator._save_current_slot), ama daha önce kaydedilmiş config
dosyalarında (örn. cam1.json'daki slot 3) hâlâ bozuk polygon olabilir.
Bu araç tüm config/slots/*.json dosyalarını tarar, bozuk polygon'ları
raporlar ve --fix ile düzeltir.

Kullanım:
    python tools/audit_slots.py                       # sadece raporla
    python tools/audit_slots.py --fix                  # düzelt ve kaydet
    python tools/audit_slots.py --slots config/slots/cam1.json --fix
"""

import argparse
import json
from glob import glob
from pathlib import Path

from park_slot.annotation.geometry import is_simple_polygon, order_points_by_angle


def audit_file(path: Path, fix: bool) -> int:
    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)

    broken = 0
    for slot in config.get("slots", []):
        if is_simple_polygon(slot["polygon"]):
            continue

        broken += 1
        label = slot.get("label", f"id={slot.get('id')}")
        if fix:
            fixed = order_points_by_angle(slot["polygon"])
            still_broken = not is_simple_polygon(fixed)
            slot["polygon"] = fixed
            status = "düzeltilemedi (manuel kontrol gerekli)" if still_broken else "düzeltildi"
            print(f"  {path.name}: slot {label} -> {status}")
        else:
            print(f"  {path.name}: slot {label} kendi kendini kesiyor (bozuk polygon)")

    if fix and broken:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

    return broken


def main():
    parser = argparse.ArgumentParser(description="Slot polygon sağlamlık denetimi")
    parser.add_argument("--slots", default=None, help="Tek bir config dosyası (verilmezse config/slots/*.json taranır)")
    parser.add_argument("--fix", action="store_true", help="Bozuk polygon'ları otomatik düzelt ve dosyaya yaz")
    args = parser.parse_args()

    paths = [Path(args.slots)] if args.slots else [Path(p) for p in glob("config/slots/*.json")]
    if not paths:
        print("Hiç config/slots/*.json dosyası bulunamadı.")
        return

    total_broken = 0
    for path in paths:
        total_broken += audit_file(path, args.fix)

    if total_broken == 0:
        print("Tüm polygon'lar sağlam (self-intersection yok).")
    else:
        action = "düzeltildi" if args.fix else "bulundu (düzeltmek için --fix kullan)"
        print(f"\nToplam {total_broken} bozuk polygon {action}.")


if __name__ == "__main__":
    main()
