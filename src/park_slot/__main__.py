"""ParkSlot ana giriş noktası.

Kullanım:
    python -m park_slot
    python -m park_slot --config config/default.yaml
    python -m park_slot --show-window    # OpenCV penceresi göster

Bu dosya tüm parçaları bir araya getirir:
1. Ayarları yükle
2. Kamera kaynağını oluştur
3. Model'i yükle
4. Pipeline'ı başlat
5. API sunucusunu çalıştır
6. Ctrl+C ile düzgün kapat
"""

import argparse
import logging
import logging.config
import threading
from pathlib import Path

import cv2
import torch
import yaml
import uvicorn

from park_slot.api.app import create_app
from park_slot.capture.file import FileSource
from park_slot.capture.youtube import YouTubeSource
from park_slot.capture.rtsp import RTSPSource
from park_slot.inference.engine import InferenceEngine
from park_slot.inference.pipeline import Pipeline
from park_slot.inference.vehicle_detector import VehicleDetector
from park_slot.services.monitor import Monitor
from park_slot.services.mqtt_publisher import MqttPublisher
from park_slot.services.prediction_log import PredictionLog
from park_slot.services.slot_manager import SlotManager
from park_slot.services.state_store import StateStore
from park_slot.settings import get_cpu_thread_limit, load_settings
from park_slot.utils.signal import ShutdownHandler

logger = logging.getLogger(__name__)


def setup_logging(config_path: Path) -> None:
    """Logging konfigürasyonunu yükle."""
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # logs/ klasörünü oluştur
        Path("logs").mkdir(exist_ok=True)
        logging.config.dictConfig(config)
    else:
        logging.basicConfig(level=logging.INFO)


def create_source(settings):
    """Ayarlara göre kamera kaynağı oluştur."""
    source_type = settings.capture.source_type
    url = settings.capture.source_url

    if source_type == "youtube":
        return YouTubeSource(url, settings.capture.resolution)
    elif source_type == "rtsp":
        return RTSPSource(url)
    elif source_type == "file":
        return FileSource(url, loop=True)
    else:
        raise ValueError(f"Bilinmeyen kaynak tipi: {source_type}")


def main():
    parser = argparse.ArgumentParser(description="ParkSlot - Otopark Doluluk Tespit Sistemi")
    parser.add_argument("--config", default="config/default.yaml", help="Konfigürasyon dosyası")
    parser.add_argument("--slots", default=None, help="Slot konfigürasyon JSON dosyası")
    parser.add_argument("--show-window", action="store_true", help="OpenCV penceresi göster")
    args = parser.parse_args()

    # Logging
    setup_logging(Path("config/logging.yaml"))

    # PyTorch/OpenCV, container'ın cgroup CPU limitini bilmeden host'un tüm
    # çekirdeklerine göre thread açar - bu yüzden erkenden (herhangi bir
    # model/tensor işleminden önce) container'a verilen limitle sabitleniyor
    # (bkz. settings.get_cpu_thread_limit docstring'i).
    cpu_threads = get_cpu_thread_limit()
    torch.set_num_threads(cpu_threads)
    cv2.setNumThreads(cpu_threads)
    logger.info("CPU thread limiti: %d", cpu_threads)

    # Ayarlar
    settings = load_settings(Path(args.config))
    logger.info("Ayarlar yüklendi: %s", args.config)

    # Slot konfigürasyonu
    slots_path = args.slots or f"config/slots/{settings.capture.source_type}.json"
    slot_manager = SlotManager(slots_path, input_size=settings.model.input_size)

    # Kamera kaynağı
    source = create_source(settings)

    # Inference engine
    engine = InferenceEngine(
        checkpoint_path=settings.model.checkpoint_path,
        onnx_path=settings.model.onnx_path,
        device=settings.inference.device,
        confidence_threshold=settings.model.confidence_threshold,
    )

    # Araç tespiti (opsiyonel, hibrit düzeltme - bkz. inference/vehicle_detector.py)
    vehicle_detector = None
    if settings.detector.enabled:
        vehicle_detector = VehicleDetector(
            model_path=settings.detector.model_path,
            imgsz=settings.detector.imgsz,
            conf_threshold=settings.detector.conf_threshold,
            device=settings.inference.device,
        )
        logger.info("Araç tespiti etkin: %s", settings.detector.model_path)

    # Servisler
    state_store = StateStore(
        camera_id=slot_manager.camera_id,
        minimap_coords=slot_manager.minimap_coords,
    )
    monitor = Monitor()
    prediction_log = PredictionLog(log_path="data/logs/predictions.jsonl")
    state_store.on_change(prediction_log.record)

    mqtt_publisher = MqttPublisher(settings.mqtt, camera_id=slot_manager.camera_id, state_store=state_store)
    state_store.on_change(mqtt_publisher.on_slot_change)

    # Pipeline
    pipeline = Pipeline(
        source=source,
        engine=engine,
        slot_manager=slot_manager,
        state_store=state_store,
        monitor=monitor,
        frame_interval=settings.capture.frame_interval,
        show_window=args.show_window,
        vehicle_detector=vehicle_detector,
    )

    # Graceful shutdown
    shutdown = ShutdownHandler()
    shutdown.register(pipeline.stop)

    # Pipeline başlat
    pipeline.start()
    logger.info("Pipeline başlatıldı")

    # FastAPI uygulaması
    app = create_app(
        state_store=state_store,
        monitor=monitor,
        prediction_log=prediction_log,
        cors_origins=settings.api.cors_origins,
        pipeline=pipeline,
    )

    # Uvicorn ile API sunucusunu çalıştır
    logger.info("API başlatılıyor: %s:%d", settings.api.host, settings.api.port)

    if args.show_window:
        # cv2 GUI çağrıları Qt backend'inde ana thread'den yapılmak zorunda,
        # bu yüzden API'yi arka plana alıp pencere döngüsünü burada çalıştırıyoruz.
        config = uvicorn.Config(
            app, host=settings.api.host, port=settings.api.port, log_level="info"
        )
        server = uvicorn.Server(config)
        api_thread = threading.Thread(target=server.run, daemon=True)
        api_thread.start()

        # Birden fazla kamera aynı anda izlenebilsin diye pencere başlığına
        # kamera id'si eklenir (yoksa tüm pencereler aynı isimde olur).
        window_title = f"ParkSlot Live - {slot_manager.camera_id}"

        logger.info("Görsel pencere açıldı (çıkmak için pencerede 'q' veya Ctrl+C)")
        try:
            while not shutdown.requested:
                frame = pipeline.get_display_frame()
                if frame is not None:
                    cv2.imshow(window_title, frame)
                key = cv2.waitKey(30) & 0xFF
                if key in (ord("q"), 27):
                    break
        finally:
            cv2.destroyAllWindows()
            server.should_exit = True
            if not shutdown.requested:
                pipeline.stop()
            api_thread.join(timeout=5)
    else:
        uvicorn.run(
            app,
            host=settings.api.host,
            port=settings.api.port,
            log_level="info",
        )


if __name__ == "__main__":
    main()
