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

from park_slot.api.routes import init_routes, router, set_pipeline, set_pipeline_status
from park_slot.api.ws import init_ws, websocket_endpoint
from park_slot.inference.pipeline import Pipeline
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


_LIVE_PAGE = """<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<title>ParkSlot - Canlı Görüntü</title>
<style>
  body { background:#1a1a1a; color:#eee; font-family: sans-serif; display:flex; flex-direction:column; align-items:center; padding:20px; }
  h1 { font-size:16px; font-weight:normal; color:#aaa; text-align:center; }
  #wrap { position:relative; display:inline-block; }
  img { max-width:95vw; border:1px solid #444; border-radius:4px; cursor:crosshair; display:block; }
  #panel {
    position:fixed; display:none; background:#262626; border:1px solid #555;
    border-radius:6px; padding:8px; box-shadow:0 4px 12px rgba(0,0,0,.5); z-index:10;
  }
  #panel .label { font-size:12px; color:#aaa; margin-bottom:6px; text-align:center; }
  #panel button {
    display:block; width:140px; margin:4px 0; padding:6px 10px; border:none;
    border-radius:4px; font-size:13px; cursor:pointer; color:#fff;
  }
  #panel .b-occupied { background:#c0392b; }
  #panel .b-empty { background:#27ae60; }
  #panel .b-auto { background:#555; }
  #panel .b-cancel { background:#333; color:#999; }
  #override-warning {
    display:none; max-width:95vw; margin-bottom:10px; padding:8px 14px;
    background:#7a4a00; border:1px solid #c98a1f; border-radius:4px;
    font-size:13px; color:#ffe0a3;
  }
  #override-warning button.warn-slot-btn {
    margin:2px 4px; padding:3px 8px; border:1px solid #ffb700; border-radius:4px;
    background:#9c6300; color:#ffe0a3; font-size:12px; cursor:pointer;
  }
  #pending-modal {
    display:none; position:fixed; inset:0; background:rgba(0,0,0,.7);
    z-index:20; align-items:center; justify-content:center;
  }
  #pending-modal .modal-box {
    background:#222; border:1px solid #555; border-radius:6px; padding:14px;
    max-width:90vw; max-height:85vh; overflow-y:auto;
  }
  #pending-modal .modal-header {
    display:flex; justify-content:space-between; align-items:center;
    margin-bottom:10px; font-size:14px; color:#ddd;
  }
  #pending-modal .modal-header button {
    background:none; border:none; color:#ccc; font-size:20px; cursor:pointer; line-height:1;
  }
  #modal-grid { display:flex; flex-wrap:wrap; gap:10px; }
  .thumb-cell { display:flex; flex-direction:column; align-items:center; width:140px; }
  .thumb-cell img { width:140px; height:140px; object-fit:cover; border:1px solid #444; border-radius:4px; }
  .thumb-cell .thumb-label { font-size:11px; color:#999; margin-top:3px; }
  .thumb-cell button.thumb-delete {
    margin-top:4px; padding:3px 10px; border:none; border-radius:4px;
    background:#c0392b; color:#fff; font-size:12px; cursor:pointer;
  }
</style>
</head>
<body>
  <h1>ParkSlot - Canlı Görüntü (yeşil=boş, kırmızı=dolu, sarı=belirsiz, beyaz çerçeve=operatör sabitlemesi)<br>Bir slota tıklayarak durumunu elle değiştirebilirsiniz.</h1>
  <div id="override-warning"></div>
  <div id="wrap">
    <img id="cam" src="/live/frame.jpg" alt="canlı görüntü">
  </div>
  <div id="status" style="margin-top:8px;font-size:12px;color:#888;"></div>

  <div id="panel">
    <div class="label" id="panel-label"></div>
    <button class="b-occupied" id="btn-occupied">Dolu işaretle</button>
    <button class="b-empty" id="btn-empty">Boş işaretle</button>
    <button class="b-auto" id="btn-auto">Otomatiğe döndür</button>
    <button class="b-cancel" id="btn-cancel">İptal</button>
    <div style="font-size:10px;color:#777;margin-top:4px;max-width:140px;">
      Not: model 30sn kesintisiz aksini söylerse sabitleme otomatik kalkar.
    </div>
  </div>

  <div id="pending-modal">
    <div class="modal-box">
      <div class="modal-header">
        <span id="modal-title"></span>
        <button id="modal-close">×</button>
      </div>
      <div id="modal-grid"></div>
    </div>
  </div>

<script>
// multipart/x-mixed-replace (<img src="...stream.mjpg">) tarayıcılar arasında
// tutarsız davranabiliyor; bunun yerine /live/frame.jpg'yi periyodik olarak
// önceden yükleyip (flicker olmadan) img.src'ye atıyoruz.
const img = document.getElementById("cam");
const status = document.getElementById("status");
const panel = document.getElementById("panel");
const panelLabel = document.getElementById("panel-label");

let refreshTimer = null;

function refresh() {
  const next = new Image();
  next.onload = () => {
    img.src = next.src;
    status.textContent = "güncellendi: " + new Date().toLocaleTimeString();
  };
  next.onerror = () => {
    status.textContent = "kare alınamadı, tekrar denenecek...";
  };
  next.src = "/live/frame.jpg?t=" + Date.now();
}

function startAutoRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(refresh, 500);
}

// Bir slotta onay bekleyen (henüz review_overrides.py/web arayüzünden
// incelenmemiş) override birikirse, bu genelde tek seferlik bir operatör
// hatası değil, modelin o bölgede SÜREKLİ yanıldığı kalıcı bir sorunu
// işaret eder - burada uyarıp tıklandığında görselleri gösteriyoruz.
// Kare refresh'inden (500ms) daha seyrek kontrol ediliyor, kritik değil.
const warningBox = document.getElementById("override-warning");
const pendingModal = document.getElementById("pending-modal");
const modalTitle = document.getElementById("modal-title");
const modalGrid = document.getElementById("modal-grid");

async function checkPendingOverrides() {
  try {
    const res = await fetch("/api/v1/pending-overrides");
    if (!res.ok) return;
    const data = await res.json();
    if (data.over_threshold.length === 0) {
      warningBox.style.display = "none";
      warningBox.innerHTML = "";
      return;
    }
    warningBox.innerHTML =
      `⚠ Eşik (${data.threshold}) aşıldı, model şu slotlarda sürekli yanılıyor olabilir - ` +
      `incelemek için tıklayın: `;
    for (const slotId of data.over_threshold) {
      const btn = document.createElement("button");
      btn.className = "warn-slot-btn";
      btn.textContent = `Slot ${slotId} (${data.per_slot[slotId]})`;
      btn.addEventListener("click", () => openPendingModal(slotId));
      warningBox.appendChild(btn);
    }
    warningBox.style.display = "block";
  } catch (e) { /* sessizce yoksay - sadece bilgilendirme amaçlı */ }
}

setInterval(checkPendingOverrides, 15000);
checkPendingOverrides();

async function openPendingModal(slotId) {
  modalTitle.textContent = `Slot ${slotId} - onay bekleyen görseller`;
  modalGrid.innerHTML = "yükleniyor...";
  pendingModal.style.display = "flex";

  try {
    const res = await fetch(`/api/v1/pending-overrides/${slotId}/images`);
    if (!res.ok) { modalGrid.textContent = "yüklenemedi."; return; }
    const data = await res.json();
    renderPendingImages(slotId, data.images);
  } catch (e) {
    modalGrid.textContent = "yüklenemedi.";
  }
}

function renderPendingImages(slotId, images) {
  modalGrid.innerHTML = "";
  if (images.length === 0) {
    modalGrid.textContent = "Onay bekleyen görsel yok (hepsi silinmiş/onaylanmış olabilir).";
    return;
  }
  for (const image of images) {
    const cell = document.createElement("div");
    cell.className = "thumb-cell";

    const imgEl = document.createElement("img");
    imgEl.src = `/api/v1/pending-overrides/${slotId}/images/${image.label}/${image.filename}`;
    cell.appendChild(imgEl);

    const labelEl = document.createElement("div");
    labelEl.className = "thumb-label";
    labelEl.textContent = image.label === "occupied" ? "dolu" : "boş";
    cell.appendChild(labelEl);

    const delBtn = document.createElement("button");
    delBtn.className = "thumb-delete";
    delBtn.textContent = "Sil";
    delBtn.addEventListener("click", async () => {
      delBtn.disabled = true;
      await fetch(
        `/api/v1/pending-overrides/${slotId}/images/${image.label}/${image.filename}`,
        { method: "DELETE" },
      );
      cell.remove();
      if (modalGrid.children.length === 0) {
        modalGrid.textContent = "Onay bekleyen görsel yok.";
      }
      checkPendingOverrides(); // banner'daki sayıyı güncelle
    });
    cell.appendChild(delBtn);

    modalGrid.appendChild(cell);
  }
}

document.getElementById("modal-close").addEventListener("click", () => {
  pendingModal.style.display = "none";
});
pendingModal.addEventListener("click", (ev) => {
  if (ev.target === pendingModal) pendingModal.style.display = "none";
});

function hidePanel() {
  panel.style.display = "none";
}

async function onImageClick(ev) {
  const rect = img.getBoundingClientRect();
  if (!img.naturalWidth) return;
  const scaleX = img.naturalWidth / rect.width;
  const scaleY = img.naturalHeight / rect.height;
  const x = Math.round((ev.clientX - rect.left) * scaleX);
  const y = Math.round((ev.clientY - rect.top) * scaleY);

  let slot;
  try {
    const res = await fetch("/api/v1/slots/click", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ x, y }),
    });
    if (!res.ok) { hidePanel(); return; }
    slot = await res.json();
  } catch (e) { hidePanel(); return; }

  panelLabel.textContent = slot.label + " - şu an: " +
    (slot.status === "occupied" ? "dolu" : slot.status === "empty" ? "boş" : "belirsiz") +
    (slot.manual_override ? " (sabitli)" : "");

  panel.dataset.slotId = slot.slot_id;
  panel.style.left = Math.min(ev.clientX + 8, window.innerWidth - 170) + "px";
  panel.style.top = Math.min(ev.clientY + 8, window.innerHeight - 180) + "px";
  panel.style.display = "block";
}

async function setOverride(status) {
  const slotId = panel.dataset.slotId;
  if (!slotId) return;
  await fetch(`/api/v1/slots/${slotId}/override`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  hidePanel();
  refresh();
}

async function clearOverride() {
  const slotId = panel.dataset.slotId;
  if (!slotId) return;
  await fetch(`/api/v1/slots/${slotId}/override`, { method: "DELETE" });
  hidePanel();
  refresh();
}

img.addEventListener("click", onImageClick);
document.getElementById("btn-occupied").addEventListener("click", () => setOverride("occupied"));
document.getElementById("btn-empty").addEventListener("click", () => setOverride("empty"));
document.getElementById("btn-auto").addEventListener("click", clearOverride);
document.getElementById("btn-cancel").addEventListener("click", hidePanel);
document.addEventListener("click", (ev) => {
  if (!panel.contains(ev.target) && ev.target !== img) hidePanel();
});

startAutoRefresh();
</script>
</body>
</html>
"""

# Son kullanıcı (mobil) için salt-okunur özet sayfası - /live'ın aksine
# HAM KAMERA GÖRÜNTÜSÜ GÖSTERMEZ (sadece soyut özet + mini-harita noktaları)
# ve düzenleme/override kontrolü içermez. Neden: kameralar herkese açık
# YouTube yayınları olsa da, bunları kendi sitemizde yeniden işleyip son
# kullanıcıya video olarak sunmak hem YouTube ToS hem de (tanımlanabilir
# plaka/yüz içerebileceği için) veri işleme/gizlilik riski taşıyor; sadece
# türetilmiş "doluluk durumu" göstermek bu riski büyük ölçüde azaltıyor.
# Düzenleme kontrolü de bilerek yok - override /live'daki (operatör)
# sayfada kalmalı, herkes slot durumunu değiştirebilmemeli.
#
# 3 kamera ayrı pod/port olduğu için (tek bir aggregator servis yok) bu
# sayfa istemci tarafında (JS fetch) üçünün /api/v1/slots'una da bağlanıyor.
# NOT: port numaraları k8s/cam{1,2,3}.yaml'daki NodePort değerleriyle
# (30081-83) eşleşmeli - deployment değişirse burası da güncellenmeli.
_MOBILE_PAGE = """<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ParkSlot - Müsait Yer</title>
<style>
  body { background:#1a1a1a; color:#eee; font-family: sans-serif; margin:0; padding:12px 12px 90px; }
  h1 { font-size:16px; font-weight:normal; color:#aaa; text-align:center; margin:4px 0 16px; }
  .camera { background:#222; border-radius:8px; padding:10px; margin-bottom:16px; }
  .camera h2 { font-size:14px; color:#ccc; margin:0 0 8px; display:flex; justify-content:space-between; font-weight:normal; }
  .summary { display:flex; gap:10px; font-size:12px; margin-bottom:8px; flex-wrap:wrap; }
  .summary span.empty { color:#4caf50; }
  .summary span.occupied { color:#e53935; }
  .summary span.unknown { color:#fdd835; }
  canvas { width:100%; max-width:100%; background:#282828; border-radius:4px; display:block; }
  .err { color:#e53935; font-size:12px; margin-top:4px; }
  #panel {
    position:fixed; left:0; right:0; bottom:0; background:#262626; border-top:1px solid #444;
    padding:14px; display:none; z-index:20;
  }
  #panel .row { display:flex; justify-content:space-between; font-size:14px; margin-bottom:6px; }
  #panel button { width:100%; padding:10px; border:none; border-radius:6px; background:#444; color:#eee; font-size:14px; margin-top:6px; }
</style>
</head>
<body>
<h1>Müsait Park Yeri (canlı, otomatik yenilenir)</h1>
<div id="cameras"></div>
<div id="panel">
  <div class="row"><b id="panel-label"></b><span id="panel-status"></span></div>
  <div class="row" style="font-size:12px;color:#999;"><span id="panel-updated"></span></div>
  <button id="panel-close">Kapat</button>
</div>

<script>
const CAMERAS = [
  { id: "cam1", name: "Kamera 1", port: 30081 },
  { id: "cam2", name: "Kamera 2", port: 30082 },
  { id: "cam3", name: "Kamera 3", port: 30083 },
];
const COLORS = { empty: "#4caf50", occupied: "#e53935", unknown: "#fdd835" };
const LABELS = { empty: "boş", occupied: "dolu", unknown: "belirsiz" };

const container = document.getElementById("cameras");
const panel = document.getElementById("panel");
const state = {};

for (const cam of CAMERAS) {
  const el = document.createElement("div");
  el.className = "camera";
  el.innerHTML = `
    <h2><span>${cam.name}</span><span id="${cam.id}-ratio"></span></h2>
    <div class="summary" id="${cam.id}-summary"></div>
    <canvas id="${cam.id}-canvas" width="300" height="200"></canvas>
    <div class="err" id="${cam.id}-err"></div>
  `;
  container.appendChild(el);
  state[cam.id] = { slots: {}, canvas: document.getElementById(cam.id + "-canvas") };
  state[cam.id].canvas.addEventListener("click", (ev) => onCanvasClick(cam, ev));
}

function render(cam) {
  const st = state[cam.id];
  const canvas = st.canvas;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const slots = Object.values(st.slots);
  let empty = 0, occupied = 0, unknown = 0, missing = 0;
  for (const s of slots) {
    if (s.status === "empty") empty++;
    else if (s.status === "occupied") occupied++;
    else unknown++;
    if (s.minimap_x == null || s.minimap_x === undefined) { missing++; continue; }
    const x = s.minimap_x * canvas.width;
    const y = s.minimap_y * canvas.height;
    ctx.beginPath();
    ctx.arc(x, y, 9, 0, Math.PI * 2);
    ctx.fillStyle = COLORS[s.status] || COLORS.unknown;
    ctx.fill();
    ctx.strokeStyle = "#ddd"; ctx.lineWidth = 1; ctx.stroke();
    s._x = x; s._y = y;
  }
  const total = slots.length;
  document.getElementById(cam.id + "-summary").innerHTML =
    `<span>Toplam: ${total}</span> <span class="empty">Boş: ${empty}</span> <span class="occupied">Dolu: ${occupied}</span> <span class="unknown">Belirsiz: ${unknown}</span>`;
  document.getElementById(cam.id + "-ratio").textContent = total ? ("%" + Math.round(100 * empty / total) + " boş") : "";
  document.getElementById(cam.id + "-err").textContent = (missing === total && total > 0)
    ? "konum verisi yok (perspective_transform ayarlanmamış)" : "";
}

function onCanvasClick(cam, ev) {
  const st = state[cam.id];
  const rect = st.canvas.getBoundingClientRect();
  const scaleX = st.canvas.width / rect.width;
  const scaleY = st.canvas.height / rect.height;
  const x = (ev.clientX - rect.left) * scaleX;
  const y = (ev.clientY - rect.top) * scaleY;
  let closest = null, bestDist = 22;
  for (const s of Object.values(st.slots)) {
    if (s._x === undefined) continue;
    const d = Math.hypot(s._x - x, s._y - y);
    if (d < bestDist) { bestDist = d; closest = s; }
  }
  if (!closest) return;
  document.getElementById("panel-label").textContent = cam.name + " - " + closest.label;
  const statusEl = document.getElementById("panel-status");
  statusEl.textContent = LABELS[closest.status] || closest.status;
  statusEl.style.color = COLORS[closest.status] || "#fff";
  const since = closest.stable_since ? new Date(closest.stable_since * 1000).toLocaleTimeString() : "-";
  document.getElementById("panel-updated").textContent =
    "son güncelleme: " + since + " | güven: %" + Math.round((closest.confidence || 0) * 100);
  panel.style.display = "block";
}

async function poll(cam) {
  try {
    const res = await fetch("//" + location.hostname + ":" + cam.port + "/api/v1/slots");
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    const st = state[cam.id];
    st.slots = {};
    for (const s of data.slots) st.slots[s.id] = s;
    document.getElementById(cam.id + "-err").textContent = "";
    render(cam);
  } catch (e) {
    document.getElementById(cam.id + "-err").textContent = "bağlanılamadı";
  }
}

function pollAll() {
  for (const cam of CAMERAS) poll(cam);
}

document.getElementById("panel-close").addEventListener("click", () => panel.style.display = "none");

pollAll();
setInterval(pollAll, 3000);
</script>
</body>
</html>
"""


def create_app(
    state_store: StateStore,
    monitor: Monitor,
    prediction_log: PredictionLog | None = None,
    cors_origins: list[str] | None = None,
    pipeline: Pipeline | None = None,
) -> FastAPI:
    """FastAPI uygulamasını oluştur.

    Args:
        state_store: Slot durumlarını tutan depo
        monitor: Performans metrikleri
        prediction_log: Proxy kalite/drift metrikleri için tahmin günlüğü (opsiyonel)
        cors_origins: İzin verilen origin'ler (varsayılan: hepsi)
        pipeline: /live/frame.jpg'nin kareyi okuduğu pipeline (opsiyonel)
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
    set_pipeline(pipeline)
    app.include_router(router)

    # Canlı kuşbakışı mini-harita sayfası (geçici/örnek - bkz. _MINIMAP_PAGE)
    @app.get("/", response_class=HTMLResponse)
    async def minimap_page():
        return _MINIMAP_PAGE

    # Overlay'li canlı görüntü - /live/frame.jpg'yi periyodik yenilenen <img>
    # içinde gösterir (model doğruluğunu gözle doğrulamak için; k3s/container'da
    # cv2.imshow kullanılamadığından eklendi)
    @app.get("/live", response_class=HTMLResponse)
    async def live_page():
        return _LIVE_PAGE

    # Son kullanıcı (mobil) için salt-okunur özet - 3 kamerayı tek sayfada
    # gösterir, ham görüntü/override yok (bkz. _MOBILE_PAGE docstring'i)
    @app.get("/mobile", response_class=HTMLResponse)
    async def mobile_page():
        return _MOBILE_PAGE

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
