"""Operatör override feedback loop'unun onay bekleyen ("pending") verisi.

Bu modül tools/review_overrides.py (insan gözden geçirmesi) VE api/routes.py
(/api/v1/pending-overrides - web arayüzüne uyarı için) tarafından paylaşılır -
tek bir yerde tanımlı tutmak için (bkz. Pipeline.save_override_label, aynı
dosya adlandırma düzenini - slot{id}_{timestamp_ms}.jpg - kullanır).
"""

import re
from pathlib import Path

_SLOT_ID_RE = re.compile(r"^slot(\d+)_")


def pending_dir_for(camera_id: str) -> Path:
    return Path(f"data/labeled_{camera_id}/pending")


def list_pending(pending_dir: Path) -> list[Path]:
    """pending/{empty,occupied}/ altındaki tüm görüntüleri, eskiden yeniye sırala."""
    images = []
    for label_dir in ("empty", "occupied"):
        images.extend((pending_dir / label_dir).glob("*.jpg"))
    return sorted(images, key=lambda p: p.stat().st_mtime)


def approve(path: Path) -> Path:
    """pending/{label}/dosya.jpg -> {label}/dosya.jpg (asıl eğitim seti).

    pending/'in bir üst klasörü (data/labeled_cam{N}/) aynı {label} alt
    klasörünü zaten içerir - training/collector.py'nin de yazdığı yer budur.
    """
    label = path.parent.name
    target_dir = path.parent.parent.parent / label
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    path.rename(target)
    return target


def reject(path: Path) -> None:
    path.unlink()


def list_pending_for_slot(camera_id: str, slot_id: int) -> list[tuple[str, Path]]:
    """Bir slota ait onay bekleyen görüntüler - [(label, path), ...], eskiden yeniye."""
    result = []
    for path in list_pending(pending_dir_for(camera_id)):
        match = _SLOT_ID_RE.match(path.name)
        if match and int(match.group(1)) == slot_id:
            result.append((path.parent.name, path))
    return result


def resolve_pending_image(camera_id: str, slot_id: int, label: str, filename: str) -> Path | None:
    """slot/label/filename için pending dosyasının gerçek yolunu GÜVENLİ şekilde çöz.

    filename web arayüzünden (DELETE/GET isteği) doğrudan kullanıcı girdisi
    olarak geliyor - path traversal riskine karşı dosya adının TAM OLARAK
    beklenen düzene (slot{id}_{timestamp_ms}.jpg) uyması şart, yoksa None
    döner (çağıran taraf bunu 404 olarak ele almalı, dosya sistemine asla
    doğrulanmamış bir yol ile dokunulmaz).
    """
    if label not in ("empty", "occupied"):
        return None
    if not re.fullmatch(rf"slot{slot_id}_\d+\.jpg", filename):
        return None

    path = pending_dir_for(camera_id) / label / filename
    return path if path.is_file() else None


def count_pending_by_slot(camera_id: str) -> dict[int, int]:
    """Her slot_id için onay bekleyen görüntü sayısı.

    Bir slotun sayısı yüksekse (bkz. PENDING_OVERRIDE_WARN_THRESHOLD,
    api/routes.py) bu genelde operatörün tek seferlik bir hatası değil,
    o slotta modelin SÜREKLİ yanlış bildiği (örn. kamera açısı/gölge
    deseni) bir bölge olduğuna işaret eder - web arayüzünde uyarılır.
    """
    counts: dict[int, int] = {}
    for path in list_pending(pending_dir_for(camera_id)):
        match = _SLOT_ID_RE.match(path.name)
        if not match:
            continue
        slot_id = int(match.group(1))
        counts[slot_id] = counts.get(slot_id, 0) + 1
    return counts
