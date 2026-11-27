"""Pydantic Settings - YAML config + environment variables."""

import os
from pathlib import Path
from typing import List

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings

from park_slot.utils.env import resolve_env_placeholders

CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


class CaptureConfig(BaseModel):
    source_type: str = "youtube"
    source_url: str = ""
    frame_interval: float = 2.0
    resolution: List[int] = [1280, 720]


class ModelConfig(BaseModel):
    input_size: int = 64
    checkpoint_path: str = "data/models/best.pth"
    onnx_path: str = "data/exports/model.onnx"
    confidence_threshold: float = 0.7


class DetectorConfig(BaseModel):
    """Araç tespiti (hibrit düzeltme, torchvision/BSD - bkz. inference/vehicle_detector.py)"""
    enabled: bool = False
    model_path: str = "fasterrcnn_mobilenet_v3_large_320_fpn"
    imgsz: int = 416
    conf_threshold: float = 0.4
    # Dedektör ~450ms/kare CPU harcıyor (bkz. vehicle_detector.py docstring) -
    # her N pipeline iterasyonunda bir çalıştırılır, aradaki karelerde son
    # sonuç cache'den kullanılır (bkz. Pipeline._vehicle_detector_interval).
    interval: int = 3


class TrainingConfig(BaseModel):
    batch_size: int = 32
    learning_rate: float = 0.001
    epochs: int = 50
    early_stopping_patience: int = 7
    train_split: float = 0.8
    data_dir: str = "data/labeled"


class InferenceConfig(BaseModel):
    batch_size: int = 16
    device: str = "cpu"


class ApiConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: List[str] = ["*"]


class MqttConfig(BaseModel):
    """selfpark-backend'e kamera doluluk verisi yayınlamak için MQTT ayarları.

    camera_code, backend'de POST /api/v0/camera/add ile önceden kayıt edilmiş
    cameraCode ile birebir eşleşmeli - SlotManager.camera_id (örn. "cam1") ile
    karıştırılmamalı, o içsel/dosya bazlı bir kimliktir.
    """
    enabled: bool = False
    broker_host: str = "localhost"
    broker_port: int = 1883
    camera_code: str = ""
    qos: int = 1


class OccupancyConfig(BaseModel):
    """Slot durum değişikliğinin "onaylı" sayılması için gereken minimum
    kararlılık süresi (bkz. services/state_store.py docstring,
    StateStore._apply_hysteresis). Düşük değer gürültülü/geçici flip'leri
    "gerçek" değişiklik sanma riskini artırır, yüksek değer gerçek
    değişiklikleri raporlamakta gecikmeye sebep olur."""
    confirm_seconds: float = 10.0


class OutboxConfig(BaseModel):
    """MQTT gönderiminin restart/broker kesintisine dayanıklı olması için
    SQLite tabanlı outbox ayarları (bkz. docs/architecture/ADR_DOCUMENTS.md
    ADR-004). db_path'teki {camera_code} yer tutucusu, her kameranın (ayrı OS
    process'i) kendi dosyasına yazması için __main__.py'de doldurulur - SQLite
    tek-writer modelinde iki process aynı dosyayı paylaşmamalı."""
    db_path: str = "data/outbox/outbox_{camera_code}.db"
    poll_interval: float = 5.0
    retention_days: int = 7
    # ADR-008 periyodik reconciliation (Aşama 4): kaç saniyede bir tam
    # snapshot gönderilir - delta kuyruğundan bağımsız, bağlantı hiç
    # kopmasa bile zamanla oluşabilecek drift'i düzeltir.
    snapshot_interval_seconds: float = 300.0
    # Aşama 7 (PR b): NTP senkron kontrolü - saati eşiği aşan (veya sunucuya
    # erişilemeyen) bir pod yayın yapmayı durdurur (bkz. services/ntp_check.py).
    ntp_server: str = "pool.ntp.org"
    ntp_drift_threshold_seconds: float = 5.0
    ntp_check_interval_seconds: float = 300.0


class WatchdogConfig(BaseModel):
    """ADR-008 kamera watchdog (Aşama 4): kamera bu kadar saniye hiç kare
    üretmezse (RTSP/kaynak donması), ilgili kameranın tüm slotları UNKNOWN'a
    çekilir (bkz. services/state_store.py StateStore.mark_all_unknown).
    occlusion_max_seconds (25s, kısa süreli engellenme) ve
    override_release_seconds (30s) ile karışmaması için bunlardan belirgin
    şekilde uzun tutulmalı - amaç gerçek bir kamera kesintisini yakalamak,
    kısa bir donmayı değil."""
    enabled: bool = True
    stale_after_seconds: float = 120.0


class Settings(BaseSettings):
    capture: CaptureConfig = CaptureConfig()
    model: ModelConfig = ModelConfig()
    detector: DetectorConfig = DetectorConfig()
    training: TrainingConfig = TrainingConfig()
    inference: InferenceConfig = InferenceConfig()
    api: ApiConfig = ApiConfig()
    mqtt: MqttConfig = MqttConfig()
    outbox: OutboxConfig = OutboxConfig()
    occupancy: OccupancyConfig = OccupancyConfig()
    watchdog: WatchdogConfig = WatchdogConfig()


def load_settings(config_path: Path | None = None) -> Settings:
    """YAML dosyasından ayarları yükle."""
    if config_path is None:
        config_path = CONFIG_DIR / "default.yaml"

    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        # source_url'deki ${VAR} yer tutucuları (örn. RTSP kimlik bilgisi)
        # .env'den çözümlenir - bkz. utils/env.py modül docstring'i.
        source_url = data.get("capture", {}).get("source_url")
        if source_url:
            data["capture"]["source_url"] = resolve_env_placeholders(source_url)

        return Settings(**data)

    return Settings()


def get_cpu_thread_limit() -> int:
    """Container'ın CPU limitine göre kullanılacak thread sayısı.

    PyTorch/OpenCV varsayılan olarak host'un TÜM çekirdeklerini görüp ona
    göre thread açar, container'ın cgroup CPU limitini bilmez - bu da
    verilen limit ne olursa olsun sürekli throttle'a sebep olur. k8s
    manifestinde CPU_LIMIT, Downward API ile limits.cpu'dan otomatik
    dolduruluyor (bkz. k8s/cam*.yaml); burada yoksa (lokal geliştirme)
    host'un çekirdek sayısına düşülür.
    """
    env_value = os.environ.get("CPU_LIMIT")
    if env_value:
        try:
            return max(1, int(env_value))
        except ValueError:
            pass
    return os.cpu_count() or 1
