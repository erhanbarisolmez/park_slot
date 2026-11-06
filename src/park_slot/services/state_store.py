"""Thread-safe durum deposu.

Neden thread-safe:
- Pipeline thread'i sürekli durumu günceller
- API thread'i aynı anda durumu okur
- WebSocket thread'i değişiklikleri broadcast eder
- Aynı anda okuma/yazma olursa veri bozulabilir
- threading.Lock ile bunu önlüyoruz

Temporal Decision (hysteresis/debounce):
- Modelin ham tahmini her karede gürültülü olabilir (tek bir yanlış kare
  bir slotu anında EMPTY<->OCCUPIED arasında çevirmemeli)
- Dışa raporlanan "onaylı" durum, yeni bir ham durum en az `confirm_seconds`
  saniye sürekli gelmeden değişmez - bkz. _apply_hysteresis
- Kare sayısı değil, gerçek zaman kullanılıyor: kamyon gibi yüksek bir aracın
  kamera açısını kısa süre kesmesi (slot önünden geçip gitmesi) durumu
  yanlışlıkla değiştirmesin diye. frame_interval ne olursa olsun (FPS
  artsa/azalsa da) koruma penceresi sabit kalır.
"""

import logging
import threading
import time
from typing import Callable, Optional

from park_slot.annotation.schema import ParkingStatus, SlotState, SlotStatus

logger = logging.getLogger(__name__)


class StateStore:
    """Tüm slotların anlık durumunu tutar."""

    def __init__(
        self,
        camera_id: str = "cam1",
        confirm_seconds: float = 4.0,
        clock: Callable[[], float] = time.time,
        occlusion_min_slots: int = 3,
        occlusion_max_seconds: float = 25.0,
        occlusion_max_distance: float = 0.25,
        minimap_coords: dict[int, tuple[float, float]] | None = None,
    ):
        """
        Args:
            occlusion_min_slots: Tek bir update_batch çağrısında bu kadar (veya
                daha fazla) slot AYNI ANDA aynı yöne (örn. hepsi OCCUPIED'e)
                geçmeye çalışırsa, bu bağımsız park olayları değil, tek bir
                toplu engellenme olayı (örn. trafikte duran bir kamyonun
                birden fazla slotu kapatması) olarak kabul edilir ve
                etkilenen slotlar UNKNOWN'a çekilir, yanlış OCCUPIED/EMPTY
                olarak onaylanmaz. Karşılaştırma, gürültüye dayanıklı olması
                için bir önceki ONAYLI duruma göre yapılır (bir önceki ham
                tahmine göre değil - o, yüksek kare hızında çok gürültülü
                olup rastgele slot gruplarını "toplu engellenme" sanabilir).
            occlusion_max_seconds: Bir slot, toplu engellenme şüphesiyle en
                fazla bu kadar saniye UNKNOWN'da tutulur. Süre dolunca
                (gerçekten orada bir araç olabileceği ihtimaline karşı)
                serbest bırakılır, normal hysteresis ile gerçek duruma
                onaylanır - aksi halde (onaylı durum zaten UNKNOWN olduğu
                için ham tahmin hep "farklı" göründüğünden) sonsuza dek
                kilitlenebilir.
            occlusion_max_distance: Toplu engellenme adayı slotlar, minimap_coords
                normalize (0-1) uzayında bu mesafeden daha uzaksa aynı kümeye
                sayılmaz (bkz. _spatial_clusters) - görüntünün birbirinden
                uzak köşelerindeki slotların aynı karede tesadüfen birlikte
                değişmesi tek bir fiziksel engellenme olayı sanılmasın diye.
            minimap_coords: SlotManager'dan gelen, statik (bir kez hesaplanmış)
                {slot_id: (normalize_x, normalize_y)} - varsa her SlotState'e
                eklenir, API/WebSocket üzerinden kuşbakışı UI için sunulur;
                ayrıca toplu engellenme tespitinde fiziksel yakınlık filtresi
                için kullanılır (bkz. occlusion_max_distance).
        """
        self._camera_id = camera_id
        self._confirm_seconds = confirm_seconds
        self._minimap_coords = minimap_coords or {}
        self._clock = clock
        self._occlusion_min_slots = occlusion_min_slots
        self._occlusion_max_seconds = occlusion_max_seconds
        self._occlusion_max_distance = occlusion_max_distance
        self._occlusion_since: dict[int, float] = {}
        # occlusion_max_seconds dolup serbest bırakılan ama onaylı durumu
        # henüz gerçek değerine dönmemiş (hâlâ UNKNOWN) slotlar - bunlar
        # normal hysteresis işini bitirene kadar tekrar "toplu engellenme
        # adayı" sayılmaz (aksi halde old.status hâlâ UNKNOWN olduğu için
        # anında yeniden yakalanıp sonsuz döngüye girerlerdi).
        self._recovering: set[int] = set()
        self._slots: dict[int, SlotState] = {}
        self._pending: dict[int, tuple[SlotStatus, float]] = {}
        self._lock = threading.Lock()
        self._last_update: float = 0
        self._callbacks: list = []  # Durum değişince çağrılacak fonksiyonlar

    def _spatial_clusters(self, slot_ids: list[int]) -> list[list[int]]:
        """Aynı anda değişen slotları, kuşbakışı (minimap_coords) konumlarına
        göre fiziksel yakınlık kümelerine ayırır (union-find).

        Neden gerekli: tek bir büyük araç (kamyon/otobüs) slotları fiziksel
        olarak bitişik olduğu için birlikte kapatır - ama görüntünün
        birbirinden tamamen uzak iki köşesindeki slotlar aynı karede tesadüfen
        birlikte değişirse (örn. biri gerçekten park ederken diğeri ayrılıyor)
        bunlar bağımsız olaylardır, tek bir engellenme sayılmamalı.
        minimap_coords tüm adaylar için mevcut değilse (perspective_transform
        ayarlanmamış kamera) filtre uygulanamaz - eski (konumsuz) davranışa
        düşülür: tek bir küme olarak döner.
        """
        coords = self._minimap_coords
        if len(slot_ids) < 2 or not all(sid in coords for sid in slot_ids):
            return [slot_ids]

        parent = {sid: sid for sid in slot_ids}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i, a in enumerate(slot_ids):
            ax, ay = coords[a]
            for b in slot_ids[i + 1:]:
                bx, by = coords[b]
                if ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 <= self._occlusion_max_distance:
                    union(a, b)

        groups: dict[int, list[int]] = {}
        for sid in slot_ids:
            groups.setdefault(find(sid), []).append(sid)
        return list(groups.values())

    def _apply_hysteresis(self, slot_id: int, raw_status: SlotStatus, now: float) -> tuple[SlotStatus, bool]:
        """Ham durumu onaylı duruma çevir.

        Returns:
            (onaylı_durum, değişti_mi)
        """
        old = self._slots.get(slot_id)
        if old is None:
            # İlk gözlem: hemen kabul et
            return raw_status, True

        if raw_status == old.status:
            self._pending.pop(slot_id, None)
            return old.status, False

        pending_status, first_seen = self._pending.get(slot_id, (None, now))
        if pending_status != raw_status:
            first_seen = now

        # UNKNOWN'dan (toplu engellenme şüphesiyle) çıkarken normalden daha
        # uzun bir onay süresi uygulanır - kamyon kısa süreliğine sabit
        # kalıp tekrar hareket edebileceğinden ekstra temkinli davranılır.
        required = self._confirm_seconds * 2 if old.status == SlotStatus.UNKNOWN else self._confirm_seconds

        if now - first_seen >= required:
            self._pending.pop(slot_id, None)
            return raw_status, True

        self._pending[slot_id] = (raw_status, first_seen)
        return old.status, False

    def update_slot(self, slot_id: int, label: str, status: SlotStatus, confidence: float) -> None:
        """Tek bir slot'un durumunu güncelle (ham tahmin - hysteresis uygulanır)."""
        now = self._clock()
        with self._lock:
            confirmed_status, changed = self._apply_hysteresis(slot_id, status, now)
            old = self._slots.get(slot_id)
            mx, my = self._minimap_coords.get(slot_id, (None, None))
            new_state = SlotState(
                id=slot_id,
                label=label,
                status=confirmed_status,
                confidence=confidence,
                raw_status=status,
                stable_since=now if changed else (old.stable_since if old else now),
                minimap_x=mx,
                minimap_y=my,
            )
            self._slots[slot_id] = new_state
            self._last_update = now

        if changed:
            self._notify(new_state)

    def update_batch(self, updates: list[tuple[int, str, SlotStatus, float]]) -> None:
        """Birden fazla slot'u tek seferde güncelle.

        Args:
            updates: [(slot_id, label, status, confidence), ...] (ham tahminler)
        """
        now = self._clock()
        changed_states = []
        with self._lock:
            # Toplu engellenme tespiti: birden fazla slot aynı anda aynı
            # yöne geçmeye çalışıyorsa, bağımsız park olayı değil tek bir
            # engelleme olayı olarak değerlendir (örn. trafikte duran kamyon).
            # Not: bir önceki HAM tahmine göre değil, bir önceki ONAYLI
            # duruma (old.status) göre karşılaştırılır. Ham tahmin kare kare
            # gürültülü olabiliyor (0.2s gibi yüksek kare hızında rastgele
            # 3+ slotun aynı anda "farklı" görünmesi istatistiksel olarak
            # sık oluyor - gerçekte denendi, dakikalar içinde binlerce kez
            # yanlışlıkla tetiklendi). Onaylı durum sadece gerçek bir
            # onaylanmış değişiklikte değiştiği için çok daha stabil bir
            # referans.
            # _recovering'deki slotlar (bkz. __init__) bu turda aday
            # sayılmaz - onaylı durumları hâlâ UNKNOWN olduğu için normal
            # hysteresis işini bitirmeden tekrar "farklı" görünüp anında
            # yeniden yakalanmalarını (ve hiç bitmeyen bir döngüyü) önler.
            candidates_by_target: dict[SlotStatus, list[int]] = {}
            for slot_id, _label, status, _confidence in updates:
                if slot_id in self._recovering:
                    continue
                old = self._slots.get(slot_id)
                if old is not None and status != old.status:
                    candidates_by_target.setdefault(status, []).append(slot_id)

            occluded_slots: set[int] = set()
            for target_status, slot_ids in candidates_by_target.items():
                for cluster in self._spatial_clusters(slot_ids):
                    if len(cluster) >= self._occlusion_min_slots:
                        occluded_slots.update(cluster)
                        logger.warning(
                            "Olası toplu engellenme: %d slot aynı anda %s'ye geçmeye "
                            "çalışıyor (%s, fiziksel olarak yakın) - UNKNOWN'a alınıyor",
                            len(cluster), target_status.value, cluster,
                        )

            # occlusion_max_seconds'tan uzun süredir baskılanan slotlar
            # serbest bırakılır (sonsuza kadar kilitlenmeyi önler) ve
            # _recovering'e alınır - gerçek duruma onaylanana kadar yukarıdaki
            # aday listesine bir daha girmezler.
            final_occluded: set[int] = set()
            for slot_id in occluded_slots:
                started = self._occlusion_since.setdefault(slot_id, now)
                if now - started < self._occlusion_max_seconds:
                    final_occluded.add(slot_id)
                else:
                    self._recovering.add(slot_id)
            for slot_id in list(self._occlusion_since.keys()):
                if slot_id not in final_occluded:
                    self._occlusion_since.pop(slot_id, None)

            for slot_id, label, status, confidence in updates:
                hysteresis_input = SlotStatus.UNKNOWN if slot_id in final_occluded else status
                confirmed_status, changed = self._apply_hysteresis(slot_id, hysteresis_input, now)

                # Kurtarma tamamlandı mı? (onaylı durum artık gerçekten UNKNOWN değil)
                if slot_id in self._recovering and confirmed_status != SlotStatus.UNKNOWN:
                    self._recovering.discard(slot_id)

                old = self._slots.get(slot_id)
                mx, my = self._minimap_coords.get(slot_id, (None, None))
                new_state = SlotState(
                    id=slot_id,
                    label=label,
                    status=confirmed_status,
                    confidence=confidence,
                    raw_status=status,
                    stable_since=now if changed else (old.stable_since if old else now),
                    minimap_x=mx,
                    minimap_y=my,
                )
                self._slots[slot_id] = new_state

                if changed:
                    changed_states.append(new_state)

            self._last_update = now

        for state in changed_states:
            self._notify(state)

    def get_status(self) -> ParkingStatus:
        """Tüm otoparkın anlık durumunu döndür."""
        with self._lock:
            slots = list(self._slots.values())

        occupied = sum(1 for s in slots if s.status == SlotStatus.OCCUPIED)
        empty = sum(1 for s in slots if s.status == SlotStatus.EMPTY)
        unknown = sum(1 for s in slots if s.status == SlotStatus.UNKNOWN)

        return ParkingStatus(
            camera_id=self._camera_id,
            total_slots=len(slots),
            occupied=occupied,
            empty=empty,
            unknown=unknown,
            slots=slots,
        )

    def get_slot(self, slot_id: int) -> Optional[SlotState]:
        """Tek bir slot'un durumunu döndür."""
        with self._lock:
            return self._slots.get(slot_id)

    @property
    def last_update(self) -> float:
        """Son güncelleme zamanı (timestamp)."""
        return self._last_update

    def on_change(self, callback) -> None:
        """Durum değişikliğinde çağrılacak fonksiyon kaydet.

        WebSocket broadcast için kullanılır.
        """
        self._callbacks.append(callback)

    def _notify(self, state: SlotState) -> None:
        """Callback'leri çağır."""
        for cb in self._callbacks:
            try:
                cb(state)
            except Exception as e:
                logger.error("State callback hatası: %s", e)
