"""REST API endpoint'leri.

Endpoint'ler:
- GET /health          → Sistem sağlık durumu + metrikler
- GET /api/v1/status   → Otopark doluluk özeti
- GET /api/v1/slots    → Tüm slotların detaylı durumu
- GET /api/v1/slots/{id} → Tek bir slot'un durumu
- GET /api/v1/quality  → Proxy kalite/drift sinyalleri (flip rate, unknown rate, güven)
- GET /live/frame.jpg  → Overlay çizilmiş son kare (tek JPEG)
- GET /live/stream.mjpg → Aynı karenin sürekli MJPEG akışı (tarayıcıda canlı izleme)
- POST /api/v1/slots/click → Tıklanan piksel koordinatındaki slotu bul
- POST /api/v1/slots/{id}/override → Slotu operatör kararı olarak sabitle
- DELETE /api/v1/slots/{id}/override → Sabitlemeyi kaldır
"""

import time

import cv2
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

from park_slot.api.models import (
    HealthResponse,
    QualityResponse,
    SlotClickRequest,
    SlotClickResponse,
    SlotOverrideRequest,
    SlotsResponse,
    StatusResponse,
)
from park_slot.inference.pipeline import Pipeline
from park_slot.services.monitor import Monitor
from park_slot.services.prediction_log import PredictionLog
from park_slot.services.state_store import StateStore

router = APIRouter()

# Bu değişkenler app.py'de set edilir
_state_store: StateStore | None = None
_monitor: Monitor | None = None
_prediction_log: PredictionLog | None = None
_pipeline: Pipeline | None = None
_pipeline_running: bool = False


def init_routes(state_store: StateStore, monitor: Monitor, prediction_log: PredictionLog | None = None) -> None:
    """Route'ların kullanacağı servisleri ayarla."""
    global _state_store, _monitor, _prediction_log
    _state_store = state_store
    _monitor = monitor
    _prediction_log = prediction_log


def set_pipeline(pipeline: Pipeline | None) -> None:
    """/live/* endpoint'lerinin kareyi okuyacağı pipeline'ı ayarla."""
    global _pipeline
    _pipeline = pipeline


def set_pipeline_status(running: bool) -> None:
    """Pipeline çalışma durumunu ayarla."""
    global _pipeline_running
    _pipeline_running = running


@router.get("/health", response_model=HealthResponse)
def health():
    """Sistem sağlık kontrolü."""
    stats = _monitor.get_stats() if _monitor else {}
    return HealthResponse(
        status="ok",
        pipeline_running=_pipeline_running,
        uptime_seconds=stats.get("uptime_seconds", 0),
        total_frames=stats.get("total_frames", 0),
        fps=stats.get("fps", 0),
        avg_inference_ms=stats.get("avg_inference_time_ms", 0),
        detected_vehicle_count=_pipeline.get_vehicle_count() if _pipeline else None,
    )


@router.get("/api/v1/status", response_model=StatusResponse)
def get_status():
    """Otopark doluluk özeti."""
    if _state_store is None:
        raise HTTPException(status_code=503, detail="Sistem hazır değil")

    status = _state_store.get_status()
    rate = status.occupied / status.total_slots if status.total_slots > 0 else 0

    return StatusResponse(
        camera_id=status.camera_id,
        total_slots=status.total_slots,
        occupied=status.occupied,
        empty=status.empty,
        unknown=status.unknown,
        occupancy_rate=round(rate, 3),
    )


@router.get("/api/v1/slots", response_model=SlotsResponse)
def get_slots():
    """Tüm slotların detaylı durumu."""
    if _state_store is None:
        raise HTTPException(status_code=503, detail="Sistem hazır değil")

    status = _state_store.get_status()
    return SlotsResponse(
        camera_id=status.camera_id,
        slots=status.slots,
    )


@router.get("/api/v1/slots/{slot_id}")
def get_slot(slot_id: int):
    """Tek bir slot'un durumu."""
    if _state_store is None:
        raise HTTPException(status_code=503, detail="Sistem hazır değil")

    slot = _state_store.get_slot(slot_id)
    if slot is None:
        raise HTTPException(status_code=404, detail=f"Slot {slot_id} bulunamadı")

    return slot.model_dump()


@router.post("/api/v1/slots/click", response_model=SlotClickResponse)
def click_slot(body: SlotClickRequest):
    """/live sayfasında tıklanan (x, y) piksel koordinatını içeren slotu bul.

    Operatörün hangi slota tıkladığını belirlemek için - override
    endpoint'inden önce çağrılır.
    """
    if _pipeline is None or _state_store is None:
        raise HTTPException(status_code=503, detail="Sistem hazır değil")

    slot_id = _pipeline.find_slot_at(body.x, body.y)
    if slot_id is None:
        raise HTTPException(status_code=404, detail="Bu noktada slot yok")

    slot = _state_store.get_slot(slot_id)
    if slot is None:
        raise HTTPException(status_code=404, detail=f"Slot {slot_id} bulunamadı")

    return SlotClickResponse(
        slot_id=slot.id, label=slot.label, status=slot.status,
        manual_override=slot.manual_override,
    )


@router.post("/api/v1/slots/{slot_id}/override")
def set_slot_override(slot_id: int, body: SlotOverrideRequest):
    """Slotu operatör kararı olarak sabitle (otomatik tahminler görmezden gelinir)."""
    if _state_store is None:
        raise HTTPException(status_code=503, detail="Sistem hazır değil")

    slot = _state_store.set_override(slot_id, body.status)
    if slot is None:
        raise HTTPException(status_code=404, detail=f"Slot {slot_id} bulunamadı")

    return slot.model_dump()


@router.delete("/api/v1/slots/{slot_id}/override")
def clear_slot_override(slot_id: int):
    """Operatör sabitlemesini kaldır - otomatik tahminler tekrar geçerli olur."""
    if _state_store is None:
        raise HTTPException(status_code=503, detail="Sistem hazır değil")

    slot = _state_store.clear_override(slot_id)
    if slot is None:
        raise HTTPException(status_code=404, detail=f"Slot {slot_id} bulunamadı")

    return slot.model_dump()


@router.get("/api/v1/quality", response_model=QualityResponse)
def get_quality(window_seconds: float = 3600.0):
    """Proxy kalite/drift sinyalleri (ground-truth yok - accuracy değildir)."""
    if _prediction_log is None:
        raise HTTPException(status_code=503, detail="Tahmin günlüğü etkin değil")

    return QualityResponse(**_prediction_log.get_quality_metrics(window_seconds))


def _encode_latest_jpeg() -> bytes:
    """Pipeline'ın son overlay'li karesini JPEG'e çevir."""
    if _pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline hazır değil")

    frame = _pipeline.get_display_frame()
    if frame is None:
        raise HTTPException(status_code=503, detail="Henüz kare yok")

    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise HTTPException(status_code=500, detail="JPEG encode başarısız")
    return buf.tobytes()


@router.get("/live/frame.jpg")
def live_frame():
    """Overlay (slot durumu) çizilmiş son kare - tek JPEG."""
    return Response(content=_encode_latest_jpeg(), media_type="image/jpeg")


@router.get("/live/stream.mjpg")
def live_stream():
    """Aynı kareyi sürekli gönderen MJPEG akışı - doğrudan tarayıcıda açılabilir."""
    if _pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline hazır değil")

    def generate():
        while True:
            try:
                jpg = _encode_latest_jpeg()
            except HTTPException:
                time.sleep(0.5)
                continue
            yield (
                b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(jpg)).encode()
                + b"\r\n\r\n"
                + jpg
                + b"\r\n"
            )
            time.sleep(0.2)

    return StreamingResponse(
        generate(), media_type="multipart/x-mixed-replace; boundary=frame"
    )
