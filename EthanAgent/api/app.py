from __future__ import annotations

import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from channels.web import WebChannel
from gateway import Gateway

from schema import (
    SessionCreateResponse,
    SessionItem,
    SessionListResponse,
    SessionMessagesResponse,
    StatusActivity,
    StatusAgent,
    StatusChannels,
    StatusCron,
    StatusCronJob,
    StatusMcp,
    StatusMemory,
    StatusSessions,
    StatusTools,
    StatusUsage,
    AgentStatusResponse
)
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


@app.get("/api/sessions", response_model=SessionListResponse)
async def list_sessions():
    agent_loop = app.state.gateway.agent_loop
    sessions = agent_loop.sessions.list_sessions()
    items: list[SessionItem] = []
    for s in sessions:
        key: str = s["key"]
        session_id = key[4:] if key.startswith("web:") else key
        try:
            session = agent_loop.sessions.get_or_create(key)
            message_count = len(session.messages)
        except Exception:
            message_count = 0
        items.append(SessionItem(
            session_id=session_id,
            key=key,
            created_at=s.get("created_at"),
            updated_at=s.get("updated_at"),
            message_count=message_count,
        ))
    return SessionListResponse(sessions=items)


@app.get("/api/sessions/{session_id:path}/messages", response_model=SessionMessagesResponse)
async def get_session_messages(session_id: str):
    agent_loop = app.state.gateway.agent_loop
    key = f"web:{session_id}"
    try:
        session = agent_loop.sessions.get_or_create(key)
    except Exception:
        session = None
    if session is None:
        return SessionMessagesResponse(
            session_id=session_id,
            key=key,
            messages=[],
        )
    return SessionMessagesResponse(
        session_id=session_id,
        key=key,
        messages=session.messages,
    )




def _build_agent_status(gateway: Gateway, web_ch: WebChannel) -> dict:
    loop = gateway.agent_loop
    now = time.time()
    uptime = now - (loop._start_time or now)

    # ── agent ──
    agent = {
        "status": "running" if getattr(loop, "_running", False) else "stopped",
        "model": loop.model,
        "uptime_seconds": round(uptime),
        "version": "0.1.0",
        "pid": os.getpid(),
        "start_time": "",
    }
    if loop._start_time:
        from datetime import datetime, timezone
        agent["start_time"] = datetime.fromtimestamp(loop._start_time).strftime("%Y-%m-%d %H:%M")

    # ── sessions ──
    sessions_raw = loop.sessions.list_sessions()
    sessions = {
        "total": len(sessions_raw),
        "web": 0, "cli": 0, "cron": 0,
    }
    for s in sessions_raw:
        key = s.get("key", "")
        if key.startswith("web:"): sessions["web"] += 1
        elif key.startswith("cli:"): sessions["cli"] += 1
        elif key.startswith("cron:"): sessions["cron"] += 1

    # ── channels ──
    channels = {
        "active_connections": sum(len(v) for v in web_ch._sockets.values()),
        "registered": len(gateway.channel_manager.channels),
    }

    # ── cron ──
    cron = None
    if loop.cron_service:
        cron_jobs = loop.cron_service.list_jobs(include_disabled=True)
        cron = {
            "total": len(cron_jobs),
            "jobs": [
                {
                    "id": j.id, "name": j.name, "enabled": j.enabled,
                    "schedule_kind": j.schedule.kind,
                    "next_run_at_ms": j.state.next_run_at_ms,
                    "last_status": j.state.last_status,
                    "last_duration_ms": j.state.run_history[-1].duration_ms if j.state.run_history else None,
                }
                for j in cron_jobs
            ],
        }

    # ── tools ──
    tools = {
        "total": len(loop.tools),
        "names": loop.tools.tool_names(),
    }

    # ── mcp ──
    mcp = {
        "connected": loop._mcp_connected,
        "servers": list(loop._mcp_servers.keys()),
    }

    # ── memory ──
    memory = {
        "initialized": bool(loop.embed_store and loop.embed_store._enabled),
    }

    # ── usage ──
    usage = {
        "prompt_tokens": loop._total_prompt_tokens,
        "completion_tokens": loop._total_completion_tokens,
        "context_total": loop.context_window_tokens,
    }

    # ── activity ──
    activities: list[dict] = []
    for s in sorted(sessions_raw, key=lambda s: s.get("updated_at", ""), reverse=True)[:3]:
        activities.append({
            "time": (s.get("updated_at", "") or "")[:16],
            "text": f"会话活跃 — {s.get('key', '')}",
            "kind": "session",
            "context_total": loop.context_window_tokens,
            "context_token": loop.consolidator.estimate_session_prompt_tokens(loop.sessions.get_or_create(s.get("key")))
        })

    if loop.cron_service:
        cron_jobs = loop.cron_service.list_jobs(include_disabled=False)
        for j in cron_jobs[:3]:
            if j.state.last_run_at_ms:
                from datetime import datetime
                ts = datetime.fromtimestamp(j.state.last_run_at_ms / 1000).strftime("%H:%M")
                status = j.state.last_status or "unknown"
                activities.append({
                    "time": ts,
                    "text": f"定时任务 — {j.name} ({status})",
                    "kind": "cron",
                })
    activities.sort(key=lambda a: a["time"], reverse=True)
    activities = activities[:5]

    return {
        "agent": agent,
        "sessions": sessions,
        "channels": channels,
        "cron": cron,
        "tools": tools,
        "mcp": mcp,
        "memory": memory,
        "usage": usage,
        "activity": activities,
    }


@app.get("/api/agent/status", response_model=AgentStatusResponse)
async def agent_status():
    gateway: Gateway = app.state.gateway
    web_ch: WebChannel = app.state.web_channel
    return _build_agent_status(gateway, web_ch)


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
            print("app.py",raw)
            await web_ch.handle_inbound(session_id, raw)
    except WebSocketDisconnect:
        pass
    finally:
        await web_ch.unregister(session_id, websocket)
