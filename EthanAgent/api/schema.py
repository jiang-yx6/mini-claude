from pydantic import BaseModel

class SessionCreateResponse(BaseModel):
    session_id: str


class SessionItem(BaseModel):
    session_id: str
    key: str
    created_at: str | None = None
    updated_at: str | None = None
    message_count: int = 0


class SessionListResponse(BaseModel):
    sessions: list[SessionItem]


class SessionMessagesResponse(BaseModel):
    session_id: str
    key: str
    messages: list[dict]

# ── Agent status models ──────────────────────────────────────────────

class StatusAgent(BaseModel):
    status: str
    model: str
    uptime_seconds: float
    version: str
    pid: int
    start_time: str

class StatusSessions(BaseModel):
    total: int
    web: int
    cli: int
    cron: int

class StatusChannels(BaseModel):
    active_connections: int
    registered: int

class StatusCronJob(BaseModel):
    id: str
    name: str
    enabled: bool
    schedule_kind: str
    next_run_at_ms: int | None
    last_status: str | None
    last_duration_ms: int | None

class StatusCron(BaseModel):
    total: int
    jobs: list[StatusCronJob]

class StatusTools(BaseModel):
    total: int
    names: list[str]

class StatusMcp(BaseModel):
    connected: bool
    servers: list[str]

class StatusMemory(BaseModel):
    initialized: bool

class StatusUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    context_total: int

class StatusActivity(BaseModel):
    time: str
    text: str
    kind: str

class AgentStatusResponse(BaseModel):
    agent: StatusAgent
    sessions: StatusSessions
    channels: StatusChannels
    cron: StatusCron | None
    tools: StatusTools
    mcp: StatusMcp
    memory: StatusMemory
    usage: StatusUsage
    activity: list[StatusActivity]
