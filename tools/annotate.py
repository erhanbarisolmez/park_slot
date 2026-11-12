"""Slot işaretleme aracı.

Kullanım:
    python tools/annotate.py --source video.mp4 --camera cam1
    python tools/annotate.py --source https://youtube.com/watch?v=... --camera cam1

    # URL bir kere verilip kaydedildikten sonra (cam1.json'a yazılır),
    # sonraki çalıştırmalarda --source tekrar yazmaya gerek yok:
    python tools/annotate.py --camera cam1

    # Mevcut slotları koruyup sadece bazılarını düzeltmek için (--edit):
    # eski slotlar yeşil çizilir, silmek için üzerine sağ tık.
    python tools/annotate.py --camera cam2 --edit

    # RTSP (kimlik doğrulamalı yerel IP kamera) - kullanıcı adı/şifreyi URL'ye
    # GÖMMEYİN, .env dosyasında bir değişken tanımlayıp ${VAR} yer tutucusu
    # kullanın (şablon: .env.example). Yer tutucu config/slots/<camera>.json'a
    # OLDUĞU GİBİ kaydedilir, gerçek değer sadece bağlantı anında çözümlenir.
    # ÖNEMLİ: TEK TIRNAK kullanın - çift tırnak/tırnaksız olursa bash ${VAR}'ı
    # Python'dan ÖNCE kendi (boş olabilen) ortam değişkenleriyle değiştirir:
    python tools/annotate.py --camera cam_local \\
        --source 'rtsp://${CAM_LOCAL_USER}:${CAM_LOCAL_PASS}@192.168.1.50:554/cam/realmonitor?channel=1&subtype=1'
"""

import argparse
import json
import sys
from pathlib import Path

import cv2

from park_slot.capture.file import FileSource
from park_slot.capture.rtsp import RTSPSource
from park_slot.capture.youtube import YouTubeSource
from park_slot.annotation.gui import SlotAnnotator
from park_slot.annotation.schema import SlotDefinition
from park_slot.utils.env import resolve_env_placeholders


def get_frame(source_path: str) -> any:
    """Kaynaktan ilk kareyi al.

    Args:
        source_path: Çözümlenmiş (gerçek kimlik bilgili) URL - ${VAR} yer
            tutucuları çağıran tarafta (main) resolve_env_placeholders ile
            çözümlenmiş olmalı.
    """
    if source_path.startswith("rtsp://"):
        source = RTSPSource(source_path)
    elif source_path.startswith("http"):
        source = YouTubeSource(source_path)
    else:
        source = FileSource(source_path)

    source.open()
    frame = source.read()
    source.close()

    if frame is None:
        print("Hata: Kare okunamadı!")
        sys.exit(1)

    return frame


def main():
    parser = argparse.ArgumentParser(description="Park slotu işaretleme aracı")
    parser.add_argument(
        "--source", default=None,
        help="Video dosyası veya YouTube URL (verilmezse config/slots/<camera>.json içindeki kayıtlı source_url kullanılır)",
    )
    parser.add_argument("--camera", default="cam1", help="Kamera ID (varsayılan: cam1)")
    parser.add_argument("--output", default=None, help="Çıktı dosyası (varsayılan: config/slots/<camera>.json)")
    parser.add_argument(
        "--edit", action="store_true",
        help="Mevcut slotları yükle (silinmedikçe korunur), sadece eksik/yanlış olanları ekle/düzelt",
    )
    args = parser.parse_args()

    save_path = args.output or f"config/slots/{args.camera}.json"

    existing_source_url = ""
    initial_slots: list[SlotDefinition] = []
    if Path(save_path).exists():
        with open(save_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        existing_source_url = existing.get("source_url", "")
        if args.edit:
            initial_slots = [SlotDefinition(**s) for s in existing.get("slots", [])]
            print(f"Düzenleme modu: {len(initial_slots)} mevcut slot yüklendi (silmek için üzerine sağ tık).")

    source = args.source or existing_source_url
    if not source:
        parser.error(
            "--source verilmedi ve kayıtlı bir source_url bulunamadı. "
            "İlk çalıştırmada --source ile URL/dosya yolu belirtmelisiniz."
        )

    print(f"Kaynak: {source}")  # source burada hep ${VAR} yer tutuculu/placeholder halidir, şifre asla basılmaz
    print("İlk kare alınıyor...")

    # ${VAR} yer tutucuları SADECE burada, bağlantı açılırken bellekte
    # çözümlenir - source değişkeni (ve dolayısıyla kaydedilen/loglanan her
    # şey) her zaman placeholder halinde kalır, bkz. utils/env.py.
    frame = get_frame(resolve_env_placeholders(source))

    print(f"Kare boyutu: {frame.shape[1]}x{frame.shape[0]}")
    print("Annotator başlatılıyor...")

    source_url_to_save = (
        source if source.startswith(("http", "rtsp://")) else existing_source_url
    )
    annotator = SlotAnnotator(
        frame, camera_id=args.camera, source_url=source_url_to_save, initial_slots=initial_slots,
    )
    slots = annotator.run(save_path=save_path)

    print(f"Toplam {len(slots)} slot işaretlendi.")


if __name__ == "__main__":
    main()
