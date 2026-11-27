import os
import time
import asyncio

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from brain import brain_service
from brain.expert_analyst import expert_analyst
from .config import (
    APP_VERSION,
    FINNHUB_API_KEY,
    VALID_INTERVALS,
    ALLOWED_PRESETS,
    NO_CACHE_HEADERS,
)
from .payload import build_full_payload_sync
from .chat_engine import generate_ai_reply
from .ws_manager import manager

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML = os.path.join(BASE_DIR, "static", "index.html")

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    interval: str = "5m"
    preset: str | None = None


@router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "version": APP_VERSION,
        "brain_loaded": brain_service.bundle is not None,
        "finnhub_key": bool(FINNHUB_API_KEY),
        "ws_clients": await manager.count(),
        "ts": int(time.time()),
    }


@router.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@router.get("/api/gold")
async def get_gold_rest(interval: str = Query("5m", pattern="^(1m|5m|15m|1h|4h|1d)$")):
    payload = await asyncio.to_thread(build_full_payload_sync, interval, True)
    if not payload:
        return {"error": "កំពុងភ្ជាប់ទិន្នន័យមាសឡើងវិញ សូមរង់ចាំបន្តិច"}
    return payload


@router.get("/api/brain")
async def get_brain_status():
    payload = await asyncio.to_thread(build_full_payload_sync, "1h", True)
    if not payload:
        return {"error": "កំពុងភ្ជាប់ទិន្នន័យមាសឡើងវិញ"}
    return payload.get("brain", {})


@router.get("/api/expert-analyst")
async def get_expert_analyst_5m():
    """អ្នកជំនាញវិភាគកម្រិតខ្ពស់ (5m Institutional AI + Self-Learning Brain)"""
    payload_5m = await asyncio.to_thread(build_full_payload_sync, "5m", True)
    if not payload_5m:
        return {"error": "កំពុងភ្ជាប់ទិន្នន័យ 5m ឡើងវិញ សូមរង់ចាំបន្តិច"}
    return await asyncio.to_thread(expert_analyst.analyze, payload_5m)


@router.post("/api/ai-chat")
async def ai_chat(req: ChatRequest):
    if req.preset not in ALLOWED_PRESETS:
        return {
            "rejected": True,
            "title": "សំណួរមិនទាន់មានក្នុងប្រព័ន្ធ",
            "badge": "NOTICE",
            "lines": [
                "សូមអភ័យទោស! សំណួរនេះមិនទាន់មានក្នុងប្រព័ន្ធវិភាគនៅឡើយទេ។",
                "សូមមេត្តាជ្រើសរើសសំណួរដែលមានស្រាប់ក្នុងបញ្ជីខាងក្រោម ដើម្បីទទួលបានការវិភាគជាក់ស្តែង និងសុក្រឹតបំផុត។",
            ],
        }
    interval = req.interval if req.interval in VALID_INTERVALS else "5m"
    payload = await asyncio.to_thread(build_full_payload_sync, interval, True)
    if not payload:
        return {
            "title": "ស្ថានភាពទិន្នន័យ",
            "badge": "RETRY",
            "lines": ["កំពុងទាញទិន្នន័យឡើងវិញ សូមសាកល្បងម្តងទៀត។"],
        }
    return generate_ai_reply(
        req.preset,
        payload,
        interval,
        payload["mtf"],
        payload["news"],
        payload["inst_levels"],
        payload.get("brain"),
    )


@router.websocket("/ws/gold/{interval}")
async def gold_websocket(websocket: WebSocket, interval: str):
    tf = interval if interval in VALID_INTERVALS else "5m"
    await manager.connect(websocket, tf)
    try:
        fast_payload = await asyncio.to_thread(build_full_payload_sync, tf, False)
        if fast_payload:
            ok = await manager.safe_send(websocket, fast_payload, timeout=5.0)
            if not ok:
                return

            full_payload = await asyncio.to_thread(build_full_payload_sync, tf, True)
            if full_payload:
                ok = await manager.safe_send(websocket, full_payload, timeout=6.0)
                if not ok:
                    return

        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        await manager.disconnect(websocket)


@router.get("/")
def serve_home():
    return FileResponse(INDEX_HTML, headers=NO_CACHE_HEADERS)