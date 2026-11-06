"""FastAPI uygulama factory.

Factory pattern kullanıyoruz çünkü:
- Test'te farklı konfigürasyonla app oluşturabilirsin
- Servisleri dışarıdan enjekte edebilirsin
- Birden fazla app instance'ı olabilir
"""

import asyncio
import logging

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from park_slot.api.routes import init_routes, router, set_pipeline_status
from park_slot.api.ws import init_ws, websocket_endpoint
from park_slot.services.monitor import Monitor
from park_slot.services.prediction_log import PredictionLog
from park_slot.services.state_store import StateStore

logger = logging.getLogger(__name__)

# Canlı kuşbakışı mini-harita sayfası (GET /). Bağımsız, kütüphanesiz
# vanilla JS/Canvas - /ws/live WebSocket'ine bağlanıp minimap_x/minimap_y
# (SlotState, StateStore.minimap_coords'tan geliyor) ile nokta çizer.
# Canlı video veremeyen istemciler (mobil uygulama vb.) için bir örnek/geçici
# gösterim amaçlı - bkz. tools/mark_perspective.py (perspective_transform
# ayarlanmamışsa slotlarda minimap_x/y boş gelir, sayfa bunu bildirir).
_MINIMAP_PAGE = """<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<title>ParkSlot - Kuşbakışı</title>
<style>
  body { background:#1a1a1a; color:#eee; font-family: sans-serif; display:flex; flex-direction:column; align-items:center; padding:20px; }
  h1 { font-size:18px; font-weight:normal; color:#aaa; }
  #summary { display:flex; gap:20px; margin-bottom:12px; font-size:15px; }
  #summary span.empty { color:#4caf50; }
  #summary span.occupied { color:#e53935; }
  #summary span.unknown { color:#fdd835; }
  canvas { background:#282828; border:1px solid #444; border-radius:4px; }
  #status { margin-top:10px; font-size:12px; color:#888; }
  #warn { color:#fdd835; font-size:13px; margin-top:8px; display:none; }
</style>
</head>
<body>
  <h1>ParkSlot - Canlı Kuşbakışı Mini-Harita</h1>
  <div id="summary">
    <span>Toplam: <b id="s-total">-</b></span>
    <span class="empty">Boş: <b id="s-empty">-</b></span>
    <span class="occupied">Dolu: <b id="s-occupied">-</b></span>
    <span class="unknown">Bilinmiyor: <b id="s-unknown">-</b></span>
  </div>
  <canvas id="map" width="560" height="360"></canvas>
  <div id="warn">perspective_transform ayarlanmamış - önce 'python tools/mark_perspective.py --camera &lt;id&gt;' çalıştırılmalı.</div>
  <div id="status">bağlanıyor...</div>

<script>
const COLORS = { empty: "#4caf50", occupied: "#e53935", unknown: "#fdd835" };
const canvas = document.getElementById("map");
const ctx = canvas.getContext("2d");
let slots = {};

function render() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  let total=0, empty=0, occupied=0, unknown=0, missingCoords=0;

  for (const s of Object.values(slots)) {
    total++;
    if (s.status === "empty") empty++;
    else if (s.status === "occupied") occupied++;
    else unknown++;

    if (s.minimap_x === null || s.minimap_x === undefined) { missingCoords++; continue; }

    const x = s.minimap_x * canvas.width;
    const y = s.minimap_y * canvas.height;
    ctx.beginPath();
    ctx.arc(x, y, 10, 0, Math.PI * 2);
    ctx.fillStyle = COLORS[s.status] || COLORS.unknown;
    ctx.fill();
    ctx.strokeStyle = "#ddd";
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = "#111";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(s.label || s.id, x, y + 3);
  }

  document.getElementById("s-total").textContent = total;
  document.getElementById("s-empty").textContent = empty;
  document.getElementById("s-occupied").textContent = occupied;
  document.getElementById("s-unknown").textContent = unknown;
  document.getElementById("warn").style.display = (total > 0 && missingCoords === total) ? "block" : "none";
}

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws/live`);

  ws.onopen = () => { document.getElementById("status").textContent = "bağlandı"; };
  ws.onclose = () => {
    document.getElementById("status").textContent = "bağlantı koptu, yeniden deneniyor...";
    setTimeout(connect, 2000);
  };
  ws.onerror = () => ws.close();

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.event === "full_status") {
      slots = {};
      for (const s of msg.data.slots) slots[s.id] = s;
    } else if (msg.event === "slot_update") {
      slots[msg.data.id] = msg.data;
    }
    render();
  };
}

connect();
</script>
</body>
</html>
"""


def create_app(
    state_store: StateStore,
    monitor: Monitor,
    prediction_log: PredictionLog | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """FastAPI uygulamasını oluştur.

    Args:
        state_store: Slot durumlarını tutan depo
        monitor: Performans metrikleri
        prediction_log: Proxy kalite/drift metrikleri için tahmin günlüğü (opsiyonel)
        cors_origins: İzin verilen origin'ler (varsayılan: hepsi)
    """
    app = FastAPI(
        title="ParkSlot API",
        description="Otopark doluluk tespit sistemi",
        version="0.1.0",
    )

    # CORS ayarları (frontend farklı port'tan erişebilsin)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # REST route'larını bağla
    init_routes(state_store, monitor, prediction_log)
    app.include_router(router)

    # Canlı kuşbakışı mini-harita sayfası (geçici/örnek - bkz. _MINIMAP_PAGE)
    @app.get("/", response_class=HTMLResponse)
    async def minimap_page():
        return _MINIMAP_PAGE

    # WebSocket endpoint
    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket):
        await websocket_endpoint(ws, state_store)

    # Startup event: WebSocket'i state store'a bağla
    @app.on_event("startup")
    async def on_startup():
        loop = asyncio.get_event_loop()
        init_ws(loop, state_store)
        set_pipeline_status(True)
        logger.info("API başlatıldı")

    @app.on_event("shutdown")
    async def on_shutdown():
        set_pipeline_status(False)
        logger.info("API kapatılıyor")

    return app
