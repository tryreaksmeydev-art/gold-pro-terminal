import time
import asyncio
from fastapi import WebSocket

from brain import brain_service
from .feeds import fetch_live_quote, fetch_gold_data, fetch_usd_news, fetch_order_flow
from .payload import build_full_payload_sync


class ConnectionManager:
    """គ្រប់គ្រងការតភ្ជាប់ WebSocket និងផ្ញើទិន្នន័យដំណាលគ្នាដោយមាន Timeout ការពារ Client គាំង"""

    def __init__(self):
        self.clients: dict[WebSocket, str] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, interval: str):
        await ws.accept()
        async with self._lock:
            self.clients[ws] = interval

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self.clients.pop(ws, None)

    async def snapshot(self) -> list[tuple[WebSocket, str]]:
        async with self._lock:
            return list(self.clients.items())

    async def count(self) -> int:
        async with self._lock:
            return len(self.clients)

    async def safe_send(self, ws: WebSocket, payload: dict, timeout: float = 2.5) -> bool:
        try:
            await asyncio.wait_for(ws.send_json(payload), timeout=timeout)
            return True
        except Exception:
            await self.disconnect(ws)
            return False

    async def broadcast_many(self, pairs: list[tuple[WebSocket, dict]], timeout: float = 2.5):
        if not pairs:
            return
        await asyncio.gather(
            *(self.safe_send(ws, payload, timeout=timeout) for ws, payload in pairs),
            return_exceptions=True,
        )


manager = ConnectionManager()


def warmup_caches():
    try:
        fetch_live_quote()
        fetch_gold_data("15m")
        fetch_gold_data("1h")
        fetch_usd_news()
        fetch_order_flow()
        brain_service._get_external_context_fast()
        print("[WARMUP] Core gold, Order Flow, News & Macro feeds pre-warmed (V10.8 Turbo)")
    except Exception as e:
        print(f"[WARMUP] skipped: {e}")


async def broadcast_loop():
    """ផ្សាយតម្លៃ Forex ផ្ទាល់រៀងរាល់ 1.0s និង Full AI Snapshot រៀងរាល់ 5.0s"""
    cycle = 0
    while True:
        try:
            await asyncio.sleep(1.0)
            if await manager.count() == 0:
                continue
            cycle += 1
            clients = await manager.snapshot()
            if not clients:
                continue

            # អាប់ដេត Full AI Snapshot រៀងរាល់ 5 វិនាទី (លឿនជាងមុន ៣ ដង)
            if cycle % 5 == 0:
                unique_tfs = list({tf for _, tf in clients})
                results = await asyncio.gather(
                    *(asyncio.to_thread(build_full_payload_sync, tf, True) for tf in unique_tfs),
                    return_exceptions=True,
                )
                tf_payloads = {
                    tf: res for tf, res in zip(unique_tfs, results)
                    if isinstance(res, dict)
                }
                pairs = [(ws, tf_payloads[tf]) for ws, tf in clients if tf in tf_payloads]
                await manager.broadcast_many(pairs, timeout=3.0)
                continue

            quote = await asyncio.to_thread(fetch_live_quote)
            if quote:
                tick_msg = {
                    "type": "tick",
                    "price": quote["price"],
                    "bid": quote["bid"],
                    "ask": quote["ask"],
                    "spread": quote["spread"],
                    "is_forex": bool(quote.get("is_forex")),
                    "ts": int(time.time()),
                }
                await manager.broadcast_many([(ws, tick_msg) for ws, _ in clients], timeout=1.8)

            if cycle % 20 == 0:
                await manager.broadcast_many([(ws, {"type": "ping"}) for ws, _ in clients], timeout=1.8)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[BROADCAST] recovered from error: {e}")
            await asyncio.sleep(1.5)