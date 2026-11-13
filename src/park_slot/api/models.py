"""API response şemaları.

FastAPI bu modelleri kullanarak:
- Otomatik JSON serialization yapar
- Swagger/OpenAPI dokümantasyonu oluşturur
- Response validasyonu yapar
"""

from pydantic import BaseModel

from park_slot.annotation.schema import ParkingStatus, SlotState, SlotStatus


class HealthResponse(BaseModel):
    """GET /health yanıtı."""
    status: str = "ok"
    pipeline_running: bool = False
    uptime_seconds: float = 0.0
    total_frames: int = 0
    fps: float = 0.0
    avg_inference_ms: float = 0.0
    # Dedektörün slot'tan BAĞIMSIZ ham araç sayısı (detector kapalıysa null) -
    # bkz. inference/pipeline.py Pipeline.get_vehicle_count
    detected_vehicle_count: int | None = None
    # Hangi checkpoint/ONNX dosyası çalışıyor - bkz. InferenceEngine.model_version
    model_version: str | None = None


class StatusResponse(BaseModel):
    """GET /api/v1/status yanıtı."""
    camera_id: str
    total_slots: int
    occupied: int
    empty: int
    unknown: int
    occupancy_rate: float = 0.0


class SlotsResponse(BaseModel):
    """GET /api/v1/slots yanıtı."""
    camera_id: str
    slots: list[SlotState]


class SlotClickRequest(BaseModel):
    """POST /api/v1/slots/click isteği - /live sayfasında tıklanan piksel
    koordinatı (orijinal kare çözünürlüğünde, tarayıcı tarafında ölçeklenir)."""
    x: int
    y: int


class SlotClickResponse(BaseModel):
    """POST /api/v1/slots/click yanıtı."""
    slot_id: int
    label: str
    status: SlotStatus
    manual_override: bool


class SlotOverrideRequest(BaseModel):
    """POST /api/v1/slots/{id}/override isteği."""
    status: SlotStatus


class WebSocketMessage(BaseModel):
    """WebSocket üzerinden gönderilen mesaj."""
    event: str  # "update", "full_status"
    data: dict


class SlotQualityMetrics(BaseModel):
    """Tek bir slot için proxy kalite metrikleri."""
    flip_rate_per_hour: float
    unknown_rate: float
    avg_confidence: float


class QualityResponse(BaseModel):
    """GET /api/v1/quality yanıtı.

    Not: Ground-truth olmadan gerçek doğruluk ölçülemez - bunlar accuracy
    DEĞİL, operasyonel anormallik/drift sinyalleridir.
    """
    window_seconds: float
    state_change_count: int
    flip_rate_per_hour: float
    unknown_rate: float
    avg_confidence: float
    per_slot: dict[int, SlotQualityMetrics] = {}
