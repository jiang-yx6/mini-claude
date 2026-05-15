from __future__ import annotations

import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel

from channels.web import WebChannel
from gateway import Gateway

ETHAN_ROOT = Path(__file__).resolve().parent.parent
(ETHAN_ROOT / "logs").mkdir(parents=True, exist_ok=True)

logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level} | {message}")
logger.add(
    str(ETHAN_ROOT / "logs" / "app.log"),
    rotation="100 MB",
    retention="30 days",
    compression="zip",
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} - {message}",
)


def _cors_origins() -> list[str]:
    raw = os.environ.get("ETHAN_CORS_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


@asynccontextmanager
async def lifespan(app: FastAPI):
    gateway = Gateway(ETHAN_ROOT)
    web_channel = WebChannel(gateway.bus)
    gateway.register_channel("web", web_channel)

    await gateway.start()

    app.state.gateway = gateway
    app.state.web_channel = web_channel

    logger.info("EthanAgent API started, workspace={}", ETHAN_ROOT)
    try:
        yield
    finally:
        await gateway.stop()
        logger.info("EthanAgent API: shutdown complete")


app = FastAPI(title="EthanAgent API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SessionCreateResponse(BaseModel):
    session_id: str


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.post("/api/sessions", response_model=SessionCreateResponse)
async def create_session():
    raw_id = uuid.uuid4().hex
    session_key = f"web:{raw_id}"
    agent_loop = app.state.gateway.agent_loop
    session = agent_loop.sessions.get_or_create(session_key)
    agent_loop.sessions.save(session)
    return SessionCreateResponse(session_id=raw_id)


@app.websocket("/ws")
async def websocket_chat(websocket: WebSocket):
    session_id = websocket.query_params.get("session_id", "").strip()
    if not session_id:
        await websocket.close(code=4401, reason="missing session_id")
        return

    web_ch: WebChannel = app.state.web_channel
    await websocket.accept()
    await web_ch.register(session_id, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            await web_ch.handle_inbound(session_id, raw)
    except WebSocketDisconnect:
        pass
    finally:
        await web_ch.unregister(session_id, websocket)
