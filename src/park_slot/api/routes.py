"""REST API endpoint'leri.

Endpoint'ler:
- GET /health          → Sistem sağlık durumu + metrikler
- GET /api/v1/status   → Otopark doluluk özeti
- GET /api/v1/slots    → Tüm slotların detaylı durumu
- GET /api/v1/slots/{id} → Tek bir slot'un durumu
- GET /api/v1/quality  → Proxy kalite/drift sinyalleri (flip rate, unknown rate, güven)
"""

from fastapi import APIRouter, HTTPException

from park_slot.api.models import HealthResponse, StatusResponse, SlotsResponse, QualityResponse
from park_slot.services.monitor import Monitor
from park_slot.services.prediction_log import PredictionLog
from park_slot.services.state_store import StateStore

router = APIRouter()

# Bu değişkenler app.py'de set edilir
_state_store: StateStore | None = None
_monitor: Monitor | None = None
_prediction_log: PredictionLog | None = None
_pipeline_running: bool = False


def init_routes(state_store: StateStore, monitor: Monitor, prediction_log: PredictionLog | None = None) -> None:
    """Route'ların kullanacağı servisleri ayarla."""
    global _state_store, _monitor, _prediction_log
    _state_store = state_store
    _monitor = monitor
    _prediction_log = prediction_log


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


@router.get("/api/v1/quality", response_model=QualityResponse)
def get_quality(window_seconds: float = 3600.0):
    """Proxy kalite/drift sinyalleri (ground-truth yok - accuracy değildir)."""
    if _prediction_log is None:
        raise HTTPException(status_code=503, detail="Tahmin günlüğü etkin değil")

    return QualityResponse(**_prediction_log.get_quality_metrics(window_seconds))
