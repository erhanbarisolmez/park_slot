"""Pydantic Settings - YAML config + environment variables."""

from pathlib import Path
from typing import List

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings

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


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"


class Settings(BaseSettings):
    capture: CaptureConfig = CaptureConfig()
    model: ModelConfig = ModelConfig()
    training: TrainingConfig = TrainingConfig()
    inference: InferenceConfig = InferenceConfig()
    api: ApiConfig = ApiConfig()
    logging: LoggingConfig = LoggingConfig()


def load_settings(config_path: Path | None = None) -> Settings:
    """YAML dosyasından ayarları yükle."""
    if config_path is None:
        config_path = CONFIG_DIR / "default.yaml"

    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return Settings(**data)

    return Settings()
