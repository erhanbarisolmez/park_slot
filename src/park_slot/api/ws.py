"""WebSocket canlı güncelleme.

İstemci /ws/live'a bağlanır, slot durumu değişince otomatik bildirim alır.
Polling'e gerek kalmaz → daha az trafik, anlık güncelleme.

Akış:
1. İstemci bağlanır → bağlı istemciler listesine eklenir
2. StateStore'da durum değişince → tüm istemcilere broadcast
3. İstemci koparsa → listeden çıkarılır
"""

import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from park_slot.annotation.schema import SlotState
from park_slot.services.state_store import StateStore

logger = logging.getLogger(__name__)


class WebSocketManager:
    """WebSocket bağlantı yöneticisi."""

    def __init__(self):
        self._connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        """Yeni istemci bağlantısı kabul et."""
        await ws.accept()
        async with self._lock:
            self._connections.append(ws)
        logger.info("WebSocket bağlandı (%d aktif)", len(self._connections))

    async def disconnect(self, ws: WebSocket) -> None:
        """İstemci bağlantısını kaldır."""
        async with self._lock:
            if ws in self._connections:
                self._connections.remove(ws)
        logger.info("WebSocket ayrıldı (%d aktif)", len(self._connections))

    async def broadcast(self, message: dict) -> None:
        """Tüm bağlı istemcilere mesaj gönder."""
        async with self._lock:
            dead = []
            for ws in self._connections:
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.append(ws)

            # Kopan bağlantıları temizle
            for ws in dead:
                self._connections.remove(ws)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


# Global instance
ws_manager = WebSocketManager()

# Event loop referansı (thread'den async fonksiyon çağırmak için)
_loop: asyncio.AbstractEventLoop | None = None


def init_ws(loop: asyncio.AbstractEventLoop, state_store: StateStore) -> None:
    """WebSocket'i state store callback'ine bağla."""
    global _loop
    _loop = loop

    def on_slot_change(state: SlotState) -> None:
        """StateStore'dan çağrılır (pipeline thread'inden)."""
        if _loop is None or ws_manager.connection_count == 0:
            return

        message = {
            "event": "slot_update",
            "data": state.model_dump(),
        }

        # Thread-safe şekilde async broadcast'i çağır
        asyncio.run_coroutine_threadsafe(
            ws_manager.broadcast(message), _loop,
        )

    state_store.on_change(on_slot_change)


async def websocket_endpoint(ws: WebSocket, state_store: StateStore) -> None:
    """WebSocket endpoint handler.

    Bağlantıda önce mevcut durumu gönderir,
    sonra değişiklikleri dinler.
    """
    await ws_manager.connect(ws)

    try:
        # Mevcut durumu gönder
        status = state_store.get_status()
        await ws.send_json({
            "event": "full_status",
            "data": status.model_dump(),
        })

        # Bağlantı açık kaldığı sürece dinle
        while True:
            # İstemciden mesaj bekliyoruz (ping/pong veya komut)
            data = await ws.receive_text()
            # İstemci "status" isterse güncel durumu gönder
            if data == "status":
                status = state_store.get_status()
                await ws.send_json({
                    "event": "full_status",
                    "data": status.model_dump(),
                })

    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)
