import os
import asyncio
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import APP_TITLE, APP_VERSION, shutdown_resources
from .ws_manager import warmup_caches, broadcast_loop
from .routes import router

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=warmup_caches, daemon=True, name="warmup").start()
    task = asyncio.create_task(broadcast_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        shutdown_resources()


def create_app() -> FastAPI:
    application = FastAPI(title=APP_TITLE, version=APP_VERSION, lifespan=lifespan)
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    application.include_router(router)
    return application


app = create_app()