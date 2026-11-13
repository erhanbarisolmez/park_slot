"""tools/review_overrides.py'nin saf (interaktif olmayan) dosya işlemleri."""

from pathlib import Path

from tools.review_overrides import approve, list_pending, reject


def make_pending_tree(tmp_path: Path) -> Path:
    pending = tmp_path / "data" / "labeled_cam1" / "pending"
    (pending / "empty").mkdir(parents=True)
    (pending / "occupied").mkdir(parents=True)
    return pending


def test_list_pending_returns_both_labels(tmp_path):
    pending = make_pending_tree(tmp_path)
    (pending / "empty" / "slot1_1.jpg").write_bytes(b"x")
    (pending / "occupied" / "slot2_2.jpg").write_bytes(b"x")

    result = list_pending(pending)

    assert len(result) == 2
    assert {p.name for p in result} == {"slot1_1.jpg", "slot2_2.jpg"}


def test_approve_moves_to_labeled_root(tmp_path):
    # approve() parent/parent/parent'ı kullanıyor: pending/{label}/file ->
    # labeled_camN/{label}/file - bu ilişkiyi gerçek klasör yapısıyla kur.
    pending = make_pending_tree(tmp_path)
    src = pending / "occupied" / "slot1_123.jpg"
    src.write_bytes(b"fake-jpeg-bytes")

    target = approve(src)

    assert not src.exists()
    assert target.exists()
    assert target == tmp_path / "data" / "labeled_cam1" / "occupied" / "slot1_123.jpg"
    assert target.read_bytes() == b"fake-jpeg-bytes"


def test_reject_deletes_file(tmp_path):
    pending = make_pending_tree(tmp_path)
    src = pending / "empty" / "slot3_9.jpg"
    src.write_bytes(b"x")

    reject(src)

    assert not src.exists()
