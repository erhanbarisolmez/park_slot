FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DENO_INSTALL=/opt/deno \
    PATH="/opt/deno/bin:${PATH}"

WORKDIR /app

# ca-certificates: TLS (YouTube/yt-dlp), curl: deno kurulum scripti
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# deno: yt-dlp'nin YouTube "n challenge" JS çözümü için gerekli
# (bkz. src/park_slot/capture/youtube.py YTDLP_JS_CHALLENGE_ARGS)
RUN curl -fsSL https://deno.land/install.sh | sh

COPY requirements.txt .

# torch/torchvision: CUDA build yerine CPU-only wheel (GPU yok, config
# device: cpu) - imaj boyutunu birkaç GB küçültür.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision \
    && grep -v -E '^(torch|torchvision)' requirements.txt > requirements-docker.txt \
    && pip install -r requirements-docker.txt \
    # opencv-python yerine headless sürüm: X11/GL kütüphanelerine gerek kalmaz
    && pip uninstall -y opencv-python \
    && pip install opencv-python-headless

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-deps -e .

# Faster R-CNN MobileNetV3 ağırlıkları (COCO önceden eğitilmiş, torchvision/
# BSD-3-Clause - araç tespiti hibrit düzeltmesi için, bkz.
# inference/vehicle_detector.py) - runtime'da internetten beklemek yerine
# build sırasında indirip imaja göm.
RUN python -c "\
from torchvision.models.detection import fasterrcnn_mobilenet_v3_large_320_fpn, FasterRCNN_MobileNet_V3_Large_320_FPN_Weights; \
fasterrcnn_mobilenet_v3_large_320_fpn(weights=FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.DEFAULT)"

COPY config/ config/
COPY data/models_cam1/ data/models_cam1/
COPY data/models_cam2/ data/models_cam2/
COPY data/models_cam3/ data/models_cam3/
COPY tools/export_onnx.py tools/export_onnx.py

# data/exports/ .gitignore'da (sadece .pth checkpoint'leri takip ediliyor) -
# ONNX'leri host'ta önceden var olmasına bağımlı kalmadan build sırasında üret
RUN python tools/export_onnx.py --checkpoint data/models_cam1/best.pth --output data/exports/cam1_model.onnx \
    && python tools/export_onnx.py --checkpoint data/models_cam2/best.pth --output data/exports/cam2_model.onnx \
    && python tools/export_onnx.py --checkpoint data/models_cam3/best.pth --output data/exports/cam3_model.onnx

RUN mkdir -p logs data/logs

EXPOSE 8000

ENTRYPOINT ["python", "-m", "park_slot"]
CMD ["--config", "config/default.yaml"]
