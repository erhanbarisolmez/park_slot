"""API response şemaları.

FastAPI bu modelleri kullanarak:
- Otomatik JSON serialization yapar
- Swagger/OpenAPI dokümantasyonu oluşturur
- Response validasyonu yapar
"""

from pydantic import BaseModel

from park_slot.annotation.schema import ParkingStatus, SlotState


class HealthResponse(BaseModel):
    """GET /health yanıtı."""
    status: str = "ok"
    pipeline_running: bool = False
    uptime_seconds: float = 0.0
    total_frames: int = 0
    fps: float = 0.0
    avg_inference_ms: float = 0.0


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


class WebSocketMessage(BaseModel):
    """WebSocket üzerinden gönderilen mesaj."""
    event: str  # "update", "full_status"
    data: dict
