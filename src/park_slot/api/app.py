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

from park_slot.api.routes import init_routes, router, set_pipeline_status
from park_slot.api.ws import init_ws, websocket_endpoint
from park_slot.services.monitor import Monitor
from park_slot.services.state_store import StateStore

logger = logging.getLogger(__name__)


def create_app(
    state_store: StateStore,
    monitor: Monitor,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """FastAPI uygulamasını oluştur.

    Args:
        state_store: Slot durumlarını tutan depo
        monitor: Performans metrikleri
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
    init_routes(state_store, monitor)
    app.include_router(router)

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
