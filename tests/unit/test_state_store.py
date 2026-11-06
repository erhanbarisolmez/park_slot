"""StateStore birim testleri."""

from park_slot.annotation.schema import SlotStatus
from park_slot.services.state_store import StateStore


def make_clock(step: float = 1.1):
    """Testlerde gerçek time.sleep() yapmadan zamanı ilerleten sahte saat."""
    state = {"t": 0.0}

    def clock() -> float:
        state["t"] += step
        return state["t"]

    return clock


def test_update_and_get():
    """Slot güncelle ve oku."""
    store = StateStore(camera_id="test")
    store.update_slot(1, "S1", SlotStatus.EMPTY, 0.9)

    slot = store.get_slot(1)
    assert slot is not None
    assert slot.status == SlotStatus.EMPTY
    assert slot.confidence == 0.9


def test_batch_update():
    """Toplu güncelleme."""
    store = StateStore(camera_id="test")
    store.update_batch([
        (1, "S1", SlotStatus.EMPTY, 0.9),
        (2, "S2", SlotStatus.OCCUPIED, 0.85),
        (3, "S3", SlotStatus.UNKNOWN, 0.5),
    ])

    status = store.get_status()
    assert status.total_slots == 3
    assert status.empty == 1
    assert status.occupied == 1
    assert status.unknown == 1


def test_callback_on_change():
    """Onaylı durum değişince (hysteresis sonrası) callback çağrılmalı."""
    store = StateStore(camera_id="test", confirm_seconds=2.0, clock=make_clock())
    changes = []
    store.on_change(lambda state: changes.append(state))

    # İlk gözlem hemen kabul edilir
    store.update_slot(1, "S1", SlotStatus.EMPTY, 0.9)
    assert len(changes) == 1

    # Aynı durum tekrar yazılırsa callback çağrılmamalı
    store.update_slot(1, "S1", SlotStatus.EMPTY, 0.95)
    assert len(changes) == 1

    # Tek bir farklı kare onaylı durumu hemen çevirmemeli (gürültüye/kısa süreli
    # engellenmeye karşı koruma - confirm_seconds süresi henüz geçmedi)
    store.update_slot(1, "S1", SlotStatus.OCCUPIED, 0.8)
    assert len(changes) == 1
    assert store.get_slot(1).status == SlotStatus.EMPTY
    assert store.get_slot(1).raw_status == SlotStatus.OCCUPIED

    # confirm_seconds kadar sürekli farklı durum gelirse onaylı durum değişir
    store.update_slot(1, "S1", SlotStatus.OCCUPIED, 0.8)
    store.update_slot(1, "S1", SlotStatus.OCCUPIED, 0.8)
    assert len(changes) == 2
    assert store.get_slot(1).status == SlotStatus.OCCUPIED


def test_hysteresis_noise_does_not_flip_state():
    """Ardışık olmayan (gürültülü) farklı tahminler onaylı durumu çevirmemeli."""
    store = StateStore(camera_id="test", confirm_seconds=2.0, clock=make_clock())
    store.update_slot(1, "S1", SlotStatus.EMPTY, 0.9)

    # Art arda değil, arada tekrar EMPTY geliyor -> bekleme süresi sıfırlanmalı
    store.update_slot(1, "S1", SlotStatus.OCCUPIED, 0.8)
    store.update_slot(1, "S1", SlotStatus.EMPTY, 0.9)
    store.update_slot(1, "S1", SlotStatus.OCCUPIED, 0.8)
    store.update_slot(1, "S1", SlotStatus.OCCUPIED, 0.8)

    assert store.get_slot(1).status == SlotStatus.EMPTY


def test_brief_occlusion_does_not_flip_state():
    """Kısa süreli engellenme (örn. kamyon geçişi) confirm_seconds'tan kısa
    sürerse onaylı durum değişmemeli - sonra eski duruma dönerse hiç değişmez."""
    store = StateStore(camera_id="test", confirm_seconds=3.0, clock=make_clock(step=1.0))
    store.update_slot(1, "S1", SlotStatus.EMPTY, 0.9)

    # Kamyon slotu ~2 saniyeliğine kapatıyor (raw tahmin OCCUPIED'e döner)
    store.update_slot(1, "S1", SlotStatus.OCCUPIED, 0.8)
    store.update_slot(1, "S1", SlotStatus.OCCUPIED, 0.8)

    # Kamyon geçti, slot tekrar EMPTY görünüyor
    store.update_slot(1, "S1", SlotStatus.EMPTY, 0.9)

    assert store.get_slot(1).status == SlotStatus.EMPTY


def test_sustained_change_flips_state_regardless_of_frame_rate():
    """Gerçek bir park etme olayı (confirm_seconds'tan uzun sürekli değişim)
    kare oranından bağımsız olarak onaylanmalı - FPS artsa da pencere sabit."""
    store = StateStore(camera_id="test", confirm_seconds=3.0, clock=make_clock(step=0.2))
    store.update_slot(1, "S1", SlotStatus.EMPTY, 0.9)

    # 0.2s aralıklarla 20 kare OCCUPIED gelir (= 4s > confirm_seconds)
    for _ in range(20):
        store.update_slot(1, "S1", SlotStatus.OCCUPIED, 0.8)

    assert store.get_slot(1).status == SlotStatus.OCCUPIED


def test_mass_occlusion_marks_slots_unknown_then_recovers_after_timeout():
    """Birden fazla slot aynı anda OCCUPIED'e geçmeye çalışırsa (örn. trafikte
    duran bir kamyonun birkaç slotu birden kapatması), önce UNKNOWN'a alınır
    (yanlış OCCUPIED anında onaylanmaz) - ama occlusion_max_seconds dolunca
    (orada gerçekten bir araç olabileceği ihtimaline karşı) serbest bırakılır
    ve normal hysteresis ile gerçek duruma onaylanır - sonsuza kadar UNKNOWN'da
    kilitli kalmaz."""
    store = StateStore(
        camera_id="test", confirm_seconds=1.0, clock=make_clock(step=1.0),
        occlusion_min_slots=3, occlusion_max_seconds=3.0,
    )
    store.update_batch([
        (1, "S1", SlotStatus.EMPTY, 0.9),
        (2, "S2", SlotStatus.EMPTY, 0.9),
        (3, "S3", SlotStatus.EMPTY, 0.9),
        (4, "S4", SlotStatus.EMPTY, 0.9),
    ])

    # Gerçekte orada bir araç var (ham tahmin hep OCCUPIED) - ama 3 slot aynı
    # anda değiştiği için başlangıçta toplu engellenme sanılır, UNKNOWN'a
    # alınır; occlusion_max_seconds dolunca serbest bırakılıp gerçek duruma
    # onaylanmalı.
    for _ in range(8):
        store.update_batch([
            (1, "S1", SlotStatus.OCCUPIED, 0.9),
            (2, "S2", SlotStatus.OCCUPIED, 0.9),
            (3, "S3", SlotStatus.OCCUPIED, 0.9),
            (4, "S4", SlotStatus.EMPTY, 0.9),
        ])

    assert store.get_slot(1).status == SlotStatus.OCCUPIED
    assert store.get_slot(2).status == SlotStatus.OCCUPIED
    assert store.get_slot(3).status == SlotStatus.OCCUPIED
    assert store.get_slot(4).status == SlotStatus.EMPTY  # etkilenmeyen slot hiç değişmedi


def test_occlusion_still_catches_the_transition_moment():
    """Toplu engellenme tespiti geçiş anını yakalamaya devam etmeli: kamyon
    kısa süreliğine engelleyip geçerse (ham tahmin tekrar eskiye dönerse)
    yanlış OCCUPIED hiç onaylanmamalı."""
    store = StateStore(
        camera_id="test", confirm_seconds=2.0, clock=make_clock(step=1.0), occlusion_min_slots=3
    )
    store.update_batch([
        (1, "S1", SlotStatus.EMPTY, 0.9),
        (2, "S2", SlotStatus.EMPTY, 0.9),
        (3, "S3", SlotStatus.EMPTY, 0.9),
    ])

    # Kamyon anlık olarak 3 slotu da kapatıyor, hemen geçiyor
    store.update_batch([
        (1, "S1", SlotStatus.OCCUPIED, 0.9),
        (2, "S2", SlotStatus.OCCUPIED, 0.9),
        (3, "S3", SlotStatus.OCCUPIED, 0.9),
    ])
    store.update_batch([
        (1, "S1", SlotStatus.EMPTY, 0.9),
        (2, "S2", SlotStatus.EMPTY, 0.9),
        (3, "S3", SlotStatus.EMPTY, 0.9),
    ])

    assert store.get_slot(1).status == SlotStatus.EMPTY
    assert store.get_slot(2).status == SlotStatus.EMPTY
    assert store.get_slot(3).status == SlotStatus.EMPTY


def test_occlusion_detection_ignores_uncorrelated_frame_noise():
    """Regresyon testi: gerçek kullanımda, toplu engellenme tespiti bir
    önceki HAM tahmine göre karşılaştırıldığında (eski/hatalı tasarım),
    yüksek kare hızında (0.2s) dakikalar içinde binlerce kez yanlışlıkla
    tetiklenmişti - rastgele 3+ slotun aynı anda "farklı" görünmesi
    istatistiksel olarak sık oluyordu. Karşılaştırma artık bir önceki
    ONAYLI duruma göre yapılıyor; bu test, art arda olmayan (gürültülü,
    birbirinden bağımsız) tek tük slot değişimlerinin asla toplu
    engellenme sanılmadığını doğrular."""
    store = StateStore(
        camera_id="test", confirm_seconds=100.0, clock=make_clock(step=0.2), occlusion_min_slots=3,
    )
    slot_ids = [1, 2, 3, 4, 5]
    store.update_batch([(sid, f"S{sid}", SlotStatus.EMPTY, 0.9) for sid in slot_ids])

    # Her turda sadece 2 slot (hep 3'ten az - occlusion_min_slots'un altında)
    # rastgele OCCUPIED gösteriyor, geri kalanı EMPTY - confirm_seconds çok
    # yüksek olduğu için hiçbiri gerçekten onaylanmıyor (sadece ham gürültü).
    noisy_pairs = [(1, 2), (3, 4), (5, 1), (2, 3), (4, 5)]
    for noisy_a, noisy_b in noisy_pairs * 20:
        updates = []
        for sid in slot_ids:
            status = SlotStatus.OCCUPIED if sid in (noisy_a, noisy_b) else SlotStatus.EMPTY
            updates.append((sid, f"S{sid}", status, 0.9))
        store.update_batch(updates)

    # Hiçbir slot toplu engellenme sanılıp UNKNOWN'a çekilmemiş olmalı
    for sid in slot_ids:
        assert store.get_slot(sid).status == SlotStatus.EMPTY


def test_spatially_distant_slots_not_bundled_as_single_occlusion():
    """Regresyon testi (cam2'de gerçekte görüldü): [1, 2, 9] gibi slotlar aynı
    karede birlikte değişti diye tek bir toplu engellenme sanılmamalı - S1/S2
    kuşbakışında bitişik (gerçek bir kamyon ikisini birden kapatabilir), ama
    S9 görüntünün tamamen başka bir köşesinde (minimap mesafesi >> eşik).
    S1/S2 ikisi tek başına occlusion_min_slots'un altında kaldığı için normal
    hysteresis ile onaylanmalı, S9 de ayrıca kendi başına onaylanmalı - hiçbiri
    UNKNOWN'a çekilmemeli."""
    minimap_coords = {
        1: (0.93, 0.77),
        2: (0.81, 0.68),
        9: (0.22, 0.43),
    }
    store = StateStore(
        camera_id="test", confirm_seconds=1.0, clock=make_clock(step=1.0),
        occlusion_min_slots=3, minimap_coords=minimap_coords,
    )
    store.update_batch([
        (1, "S1", SlotStatus.EMPTY, 0.9),
        (2, "S2", SlotStatus.EMPTY, 0.9),
        (9, "S9", SlotStatus.OCCUPIED, 0.9),
    ])

    # S1/S2 birlikte EMPTY'e geçiyor (bitişik - gerçek olabilir), S9 ayrı
    # bir olayla OCCUPIED'e geçiyor (uzak - alakasız). Üçü aynı karede.
    for _ in range(3):
        store.update_batch([
            (1, "S1", SlotStatus.EMPTY, 0.9),
            (2, "S2", SlotStatus.EMPTY, 0.9),
            (9, "S9", SlotStatus.OCCUPIED, 0.9),
        ])

    assert store.get_slot(1).status == SlotStatus.EMPTY
    assert store.get_slot(2).status == SlotStatus.EMPTY
    assert store.get_slot(9).status == SlotStatus.OCCUPIED


def test_spatially_clustered_occlusion_still_detected():
    """Fiziksel yakınlık filtresi gerçek toplu engellenmeyi (3 bitişik slot
    aynı anda aynı yöne geçmeye çalışıyor) hâlâ yakalamalı - filtre sadece
    alakasız/uzak slotları ayıklamak için, gerçek kümeyi görmezden gelmemeli."""
    minimap_coords = {1: (0.90, 0.75), 2: (0.80, 0.68), 3: (0.70, 0.60)}
    store = StateStore(
        camera_id="test", confirm_seconds=2.0, clock=make_clock(step=1.0),
        occlusion_min_slots=3, minimap_coords=minimap_coords,
    )
    store.update_batch([
        (1, "S1", SlotStatus.EMPTY, 0.9),
        (2, "S2", SlotStatus.EMPTY, 0.9),
        (3, "S3", SlotStatus.EMPTY, 0.9),
    ])

    for _ in range(3):
        store.update_batch([
            (1, "S1", SlotStatus.OCCUPIED, 0.9),
            (2, "S2", SlotStatus.OCCUPIED, 0.9),
            (3, "S3", SlotStatus.OCCUPIED, 0.9),
        ])

    assert store.get_slot(1).status == SlotStatus.UNKNOWN
    assert store.get_slot(2).status == SlotStatus.UNKNOWN
    assert store.get_slot(3).status == SlotStatus.UNKNOWN


def test_single_slot_change_not_treated_as_occlusion():
    """occlusion_min_slots'tan az sayıda slot değişirse normal hysteresis
    uygulanır - gerçek bağımsız park etme olayı bastırılmamalı."""
    store = StateStore(
        camera_id="test", confirm_seconds=2.0, clock=make_clock(step=1.0), occlusion_min_slots=3
    )
    store.update_batch([
        (1, "S1", SlotStatus.EMPTY, 0.9),
        (2, "S2", SlotStatus.EMPTY, 0.9),
    ])

    for _ in range(5):
        store.update_batch([
            (1, "S1", SlotStatus.OCCUPIED, 0.9),
            (2, "S2", SlotStatus.EMPTY, 0.9),
        ])

    assert store.get_slot(1).status == SlotStatus.OCCUPIED
