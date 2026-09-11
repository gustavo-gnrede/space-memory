from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import FileResponse, JSONResponse
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, create_engine, event, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .ratelimit import SlidingWindowRateLimiter
from .vault import decrypt as vault_decrypt, encrypt as vault_encrypt, key_from_hex


class Base(DeclarativeBase):
    pass


class Credential(Base):
    __tablename__ = "credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    space_id: Mapped[str] = mapped_column(String(128), index=True)
    agent_id: Mapped[str] = mapped_column(String(128))


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("space_id", "agent_id", "idempotency_key", name="uq_event_idempotency"),
    )

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    space_id: Mapped[str] = mapped_column(String(128), index=True)
    agent_id: Mapped[str] = mapped_column(String(128))
    session_id: Mapped[str] = mapped_column(String(128))
    project_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(80))
    aggregate_id: Mapped[str] = mapped_column(String(64), index=True)
    aggregate_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    space_id: Mapped[str] = mapped_column(String(128), index=True)
    content: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer)
    written_by: Mapped[str] = mapped_column(String(128))
    session_id: Mapped[str] = mapped_column(String(128))
    project_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    valid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Presence(Base):
    __tablename__ = "presence"

    space_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class VaultItem(Base):
    __tablename__ = "vault_items"
    __table_args__ = (UniqueConstraint("space_id", "key_name", name="uq_vault_key"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    space_id: Mapped[str] = mapped_column(String(128), index=True)
    key_name: Mapped[str] = mapped_column(String(200))
    value_blob: Mapped[str] = mapped_column(Text)
    owner: Mapped[str] = mapped_column(String(128))
    shared_with: Mapped[str] = mapped_column(Text, default="[]")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class VaultAudit(Base):
    __tablename__ = "vault_audit"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    space_id: Mapped[str] = mapped_column(String(128), index=True)
    agent_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(20))
    key_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))


class MemoryCreate(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=100_000)
    kind: str = Field(default="fact", min_length=1, max_length=40)
    project_id: str | None = Field(default=None, max_length=128)
    conversation_id: str | None = Field(default=None, max_length=128)
    valid_at: datetime | None = None


class MemoryUpdate(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=100_000)
    base_version: int = Field(ge=1)


class VaultSet(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=100_000)
    shared_with: list[str] = Field(default_factory=list)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_valid_at(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def memory_dict(memory: Memory) -> dict[str, Any]:
    return {
        "id": memory.id,
        "content": memory.content,
        "kind": memory.kind,
        "version": memory.version,
        "written_by": memory.written_by,
        "session_id": memory.session_id,
        "project_id": memory.project_id,
        "conversation_id": memory.conversation_id,
        "valid_at": memory.valid_at.isoformat() if memory.valid_at else None,
        "updated_at": memory.updated_at.isoformat(),
    }


class RateLimitMiddleware:
    """Pure-ASGI rate limiter that only inspects the request.

    Deliberately does not wrap the response, so streaming (SSE) responses on
    the MCP endpoint are unaffected.
    """

    def __init__(self, app, limiter, key_fn, exempt_paths: set[str] | None = None) -> None:
        self.app = app
        self.limiter = limiter
        self.key_fn = key_fn
        self.exempt_paths = set(exempt_paths or ())

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("path", "") in self.exempt_paths:
            await self.app(scope, receive, send)
            return
        allowed, retry_after = self.limiter.check(self.key_fn(scope))
        if allowed:
            await self.app(scope, receive, send)
            return
        body = b'{"detail":"Rate limit exceeded"}'
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"retry-after", str(int(retry_after) + 1).encode("ascii")),
        ]
        await send({"type": "http.response.start", "status": 429, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def create_app(
    *, database_url: str = "sqlite:///./space-memory.db", token_pepper: str,
    bootstrap_tokens: dict[str, dict[str, str]] | None = None,
    rate_limit_per_minute: int = 300,
    vault_key: str | None = None,
) -> FastAPI:
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {},
    )
    if database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _sqlite_pragma(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    vault_key_bytes: bytes | None = None
    if vault_key:
        try:
            vault_key_bytes = key_from_hex(vault_key)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc

    def token_hash(token: str) -> str:
        return hashlib.sha256(f"{token_pepper}:{token}".encode()).hexdigest()

    if bootstrap_tokens:
        with SessionLocal.begin() as db:
            for token, identity in bootstrap_tokens.items():
                digest = token_hash(token)
                if db.scalar(select(Credential).where(Credential.token_hash == digest)) is None:
                    db.add(Credential(token_hash=digest, **identity))

    def _touch_presence(space_id: str, agent_id: str) -> None:
        now = utcnow().replace(tzinfo=None)
        with SessionLocal.begin() as pdb:
            pdb.execute(
                text(
                    "INSERT INTO presence (space_id, agent_id, last_seen_at) "
                    "VALUES (:space_id, :agent_id, :last_seen_at) "
                    "ON CONFLICT (space_id, agent_id) DO UPDATE SET last_seen_at = :last_seen_at"
                ),
                {"space_id": space_id, "agent_id": agent_id, "last_seen_at": now},
            )

    app = FastAPI(title="Space Memory", version="0.1.0")

    def rate_limit_key(scope: dict) -> str:
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1", "replace")
        if auth.startswith("Bearer "):
            digest = hashlib.sha256(f"{token_pepper}:{auth[7:]}".encode()).hexdigest()[:32]
            return f"token:{digest}"
        client = scope.get("client")
        return f"ip:{client[0] if client else 'unknown'}"

    if rate_limit_per_minute > 0:
        app.add_middleware(
            RateLimitMiddleware,
            limiter=SlidingWindowRateLimiter(rate_limit_per_minute),
            key_fn=rate_limit_key,
            exempt_paths={"/", "/health"},
        )

    def get_db():
        with SessionLocal() as db:
            yield db

    def authenticate(
        authorization: Annotated[str | None, Header()] = None,
        db: Session = Depends(get_db),
    ) -> Credential:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        credential = db.scalar(
            select(Credential).where(Credential.token_hash == token_hash(authorization[7:]))
        )
        if credential is None:
            raise HTTPException(status_code=401, detail="Invalid bearer token")
        _touch_presence(credential.space_id, credential.agent_id)
        return credential

    @app.get("/")
    def panel() -> FileResponse:
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/v1/memories")
    def create_memory(
        payload: MemoryCreate,
        credential: Credential = Depends(authenticate),
        db: Session = Depends(get_db),
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key is required")
        existing_event = db.scalar(
            select(Event).where(
                Event.space_id == credential.space_id,
                Event.agent_id == credential.agent_id,
                Event.idempotency_key == idempotency_key,
            )
        )
        if existing_event:
            existing_memory = db.scalar(
                select(Memory).where(
                    Memory.id == existing_event.aggregate_id,
                    Memory.space_id == credential.space_id,
                )
            )
            return memory_dict(existing_memory)

        memory_id = f"mem_{secrets.token_hex(12)}"
        now = utcnow()
        memory = Memory(
            id=memory_id, space_id=credential.space_id, content=payload.content,
            kind=payload.kind, version=1, written_by=credential.agent_id,
            session_id=payload.session_id, project_id=payload.project_id,
            conversation_id=payload.conversation_id, valid_at=payload.valid_at,
            updated_at=now,
        )
        event = Event(
            event_id=f"evt_{secrets.token_hex(12)}", space_id=credential.space_id,
            agent_id=credential.agent_id, session_id=payload.session_id,
            project_id=payload.project_id, conversation_id=payload.conversation_id,
            idempotency_key=idempotency_key, event_type="memory.created",
            aggregate_id=memory_id, aggregate_version=1, created_at=now,
        )
        db.add_all([event, memory])
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            winning_event = db.scalar(
                select(Event).where(
                    Event.space_id == credential.space_id,
                    Event.agent_id == credential.agent_id,
                    Event.idempotency_key == idempotency_key,
                )
            )
            if winning_event is None:
                raise
            winning_memory = db.scalar(
                select(Memory).where(
                    Memory.id == winning_event.aggregate_id,
                    Memory.space_id == credential.space_id,
                )
            )
            return memory_dict(winning_memory)
        return JSONResponse(content=memory_dict(memory), status_code=status.HTTP_201_CREATED)

    @app.get("/api/v1/memories")
    def list_memories(
        query: str | None = None,
        project_id: str | None = None,
        kind: str | None = None,
        credential: Credential = Depends(authenticate), db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        statement = select(Memory).where(Memory.space_id == credential.space_id)
        if query:
            for term in query.split():
                statement = statement.where(Memory.content.ilike(f"%{term}%"))
        if project_id:
            statement = statement.where(Memory.project_id == project_id)
        if kind:
            statement = statement.where(Memory.kind == kind)
        items = list(db.scalars(statement.order_by(Memory.updated_at.desc())))
        return {"items": [memory_dict(item) for item in items], "total": len(items)}

    @app.get("/api/v1/projects/{project_id}/resume")
    def project_resume(
        project_id: str,
        credential: Credential = Depends(authenticate), db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        memories = list(db.scalars(
            select(Memory).where(
                Memory.space_id == credential.space_id,
                Memory.project_id == project_id,
            ).order_by(Memory.updated_at.desc())
        ))
        if not memories:
            raise HTTPException(status_code=404, detail="Project not found")
        last_handoff = next((m for m in memories if m.kind == "handoff"), None)
        events = list(db.scalars(
            select(Event).where(
                Event.space_id == credential.space_id,
                Event.project_id == project_id,
            ).order_by(Event.sequence.desc()).limit(20)
        ))
        return {
            "project_id": project_id,
            "last_handoff": memory_dict(last_handoff) if last_handoff else None,
            "last_agent": memories[0].written_by,
            "last_activity": memories[0].updated_at.isoformat(),
            "recent_memories": [memory_dict(m) for m in memories[:10]],
            "recent_events": [
                {"sequence": e.sequence, "type": e.event_type, "agent_id": e.agent_id,
                 "aggregate_id": e.aggregate_id, "aggregate_version": e.aggregate_version,
                 "created_at": e.created_at.isoformat()}
                for e in events
            ],
        }

    @app.get("/api/v1/memories/{memory_id}")
    def get_memory(
        memory_id: str, credential: Credential = Depends(authenticate),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        memory = db.scalar(select(Memory).where(Memory.id == memory_id, Memory.space_id == credential.space_id))
        if memory is None:
            raise HTTPException(status_code=404, detail="Memory not found")
        return memory_dict(memory)

    @app.put("/api/v1/memories/{memory_id}")
    def update_memory(
        memory_id: str, payload: MemoryUpdate,
        credential: Credential = Depends(authenticate), db: Session = Depends(get_db),
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key is required")
        existing_event = db.scalar(
            select(Event).where(
                Event.space_id == credential.space_id,
                Event.agent_id == credential.agent_id,
                Event.idempotency_key == idempotency_key,
            )
        )
        if existing_event:
            if existing_event.aggregate_id != memory_id:
                raise HTTPException(status_code=409, detail="Idempotency key reused for another resource")
            existing_memory = db.scalar(
                select(Memory).where(
                    Memory.id == existing_event.aggregate_id,
                    Memory.space_id == credential.space_id,
                )
            )
            return memory_dict(existing_memory)
        memory = db.scalar(select(Memory).where(Memory.id == memory_id, Memory.space_id == credential.space_id))
        if memory is None:
            raise HTTPException(status_code=404, detail="Memory not found")
        if memory.version != payload.base_version:
            raise HTTPException(status_code=409, detail={"message": "Version conflict", "current_version": memory.version})
        now = utcnow()
        result = db.execute(
            update(Memory)
            .where(
                Memory.id == memory_id,
                Memory.space_id == credential.space_id,
                Memory.version == payload.base_version,
            )
            .values(
                content=payload.content,
                version=payload.base_version + 1,
                written_by=credential.agent_id,
                session_id=payload.session_id,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            db.rollback()
            current_version = db.scalar(
                select(Memory.version).where(
                    Memory.id == memory_id, Memory.space_id == credential.space_id
                )
            )
            raise HTTPException(
                status_code=409,
                detail={"message": "Version conflict", "current_version": current_version},
            )
        db.add(Event(
            event_id=f"evt_{secrets.token_hex(12)}", space_id=credential.space_id,
            agent_id=credential.agent_id, session_id=payload.session_id,
            idempotency_key=idempotency_key, event_type="memory.updated",
            aggregate_id=memory_id, aggregate_version=payload.base_version + 1,
            created_at=now,
        ))
        db.commit()
        updated_memory = db.scalar(
            select(Memory).where(Memory.id == memory_id, Memory.space_id == credential.space_id)
        )
        return memory_dict(updated_memory)

    @app.get("/api/v1/events")
    def list_events(
        after: int = Query(default=0, ge=0), credential: Credential = Depends(authenticate),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        events = list(db.scalars(select(Event).where(Event.space_id == credential.space_id, Event.sequence > after).order_by(Event.sequence)))
        items = [{
            "sequence": event.sequence, "event_id": event.event_id,
            "type": event.event_type, "agent_id": event.agent_id,
            "session_id": event.session_id, "aggregate_id": event.aggregate_id,
            "aggregate_version": event.aggregate_version,
            "created_at": event.created_at.isoformat(),
        } for event in events]
        return {"items": items, "next_cursor": items[-1]["sequence"] if items else after}

    @app.get("/api/v1/presence")
    def list_presence(
        credential: Credential = Depends(authenticate), db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        now = utcnow().replace(tzinfo=None)
        cutoff = now - timedelta(minutes=5)
        rows = list(db.scalars(
            select(Presence).where(Presence.space_id == credential.space_id)
        ))
        agents = [
            {
                "agent_id": row.agent_id,
                "last_seen_at": row.last_seen_at.isoformat(),
                "online": row.last_seen_at >= cutoff,
            }
            for row in rows
            if row.agent_id != credential.agent_id
        ]
        agents.sort(key=lambda a: (not a["online"], a["agent_id"]))
        return {"agents": agents, "now": now.isoformat()}

    @app.post("/api/v1/vault/items")
    def vault_set(
        payload: VaultSet, credential: Credential = Depends(authenticate),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        if vault_key_bytes is None:
            raise HTTPException(status_code=503, detail="Vault not configured (set SPACE_MEMORY_VAULT_KEY)")
        existing = db.scalar(select(VaultItem).where(
            VaultItem.space_id == credential.space_id, VaultItem.key_name == payload.key,
        ))
        if existing is not None and existing.owner != credential.agent_id:
            raise HTTPException(status_code=403, detail="Only the owner can update this secret")
        now = utcnow().replace(tzinfo=None)
        blob = vault_encrypt(vault_key_bytes, credential.space_id, payload.key, payload.value)
        if existing is None:
            db.add(VaultItem(
                space_id=credential.space_id, key_name=payload.key, value_blob=blob,
                owner=credential.agent_id, shared_with=json.dumps(payload.shared_with),
                version=1, created_at=now, updated_at=now,
            ))
            version = 1
        else:
            existing.value_blob = blob
            existing.shared_with = json.dumps(payload.shared_with)
            existing.version += 1
            existing.updated_at = now
            version = existing.version
        db.add(VaultAudit(
            space_id=credential.space_id, agent_id=credential.agent_id,
            action="set", key_name=payload.key, created_at=now,
        ))
        db.commit()
        return {"key": payload.key, "version": version}

    @app.get("/api/v1/vault/items")
    def vault_list(
        credential: Credential = Depends(authenticate), db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        if vault_key_bytes is None:
            raise HTTPException(status_code=503, detail="Vault not configured (set SPACE_MEMORY_VAULT_KEY)")
        rows = list(db.scalars(select(VaultItem).where(
            VaultItem.space_id == credential.space_id,
        ).order_by(VaultItem.updated_at.desc())))
        items = []
        for r in rows:
            shared = json.loads(r.shared_with or "[]")
            items.append({
                "key": r.key_name, "owner": r.owner, "shared_with": shared,
                "version": r.version, "updated_at": r.updated_at.isoformat(),
                "can_read": r.owner == credential.agent_id or credential.agent_id in shared,
            })
        db.add(VaultAudit(
            space_id=credential.space_id, agent_id=credential.agent_id,
            action="list", key_name="*", created_at=utcnow().replace(tzinfo=None),
        ))
        db.commit()
        return {"items": items, "total": len(items)}

    @app.get("/api/v1/vault/items/{key_name:path}")
    def vault_get(
        key_name: str, credential: Credential = Depends(authenticate),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        if vault_key_bytes is None:
            raise HTTPException(status_code=503, detail="Vault not configured (set SPACE_MEMORY_VAULT_KEY)")
        item = db.scalar(select(VaultItem).where(
            VaultItem.space_id == credential.space_id, VaultItem.key_name == key_name,
        ))
        if item is None:
            raise HTTPException(status_code=404, detail="Secret not found")
        shared = json.loads(item.shared_with or "[]")
        if item.owner != credential.agent_id and credential.agent_id not in shared:
            raise HTTPException(status_code=403, detail="Access denied")
        value = vault_decrypt(vault_key_bytes, credential.space_id, key_name, item.value_blob)
        db.add(VaultAudit(
            space_id=credential.space_id, agent_id=credential.agent_id,
            action="get", key_name=key_name, created_at=utcnow().replace(tzinfo=None),
        ))
        db.commit()
        return {"key": key_name, "value": value, "owner": item.owner, "version": item.version}

    @app.delete("/api/v1/vault/items/{key_name:path}")
    def vault_delete(
        key_name: str, credential: Credential = Depends(authenticate),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        if vault_key_bytes is None:
            raise HTTPException(status_code=503, detail="Vault not configured (set SPACE_MEMORY_VAULT_KEY)")
        item = db.scalar(select(VaultItem).where(
            VaultItem.space_id == credential.space_id, VaultItem.key_name == key_name,
        ))
        if item is None:
            raise HTTPException(status_code=404, detail="Secret not found")
        if item.owner != credential.agent_id:
            raise HTTPException(status_code=403, detail="Only the owner can delete this secret")
        db.delete(item)
        db.add(VaultAudit(
            space_id=credential.space_id, agent_id=credential.agent_id,
            action="delete", key_name=key_name, created_at=utcnow().replace(tzinfo=None),
        ))
        db.commit()
        return {"deleted": key_name}

    @app.get("/api/v1/vault/audit")
    def vault_audit(
        limit: int = Query(default=100, ge=1, le=500),
        credential: Credential = Depends(authenticate), db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        if vault_key_bytes is None:
            raise HTTPException(status_code=503, detail="Vault not configured (set SPACE_MEMORY_VAULT_KEY)")
        rows = list(db.scalars(select(VaultAudit).where(
            VaultAudit.space_id == credential.space_id,
        ).order_by(VaultAudit.id.desc()).limit(limit)))
        return {"items": [
            {"agent_id": r.agent_id, "action": r.action, "key": r.key_name, "at": r.created_at.isoformat()}
            for r in rows
        ], "total": len(rows)}

    class SpaceTokenVerifier:
        async def verify_token(self, token: str) -> AccessToken | None:
            with SessionLocal() as db:
                credential = db.scalar(
                    select(Credential).where(Credential.token_hash == token_hash(token))
                )
                if credential is None:
                    return None
                _touch_presence(credential.space_id, credential.agent_id)
                return AccessToken(
                    token=token,
                    client_id=credential.agent_id,
                    subject=credential.agent_id,
                    scopes=["memory:read", "memory:write"],
                    claims={"space_id": credential.space_id, "agent_id": credential.agent_id},
                )

    def mcp_identity() -> tuple[str, str]:
        access = get_access_token()
        if access is None or not access.claims:
            raise ValueError("Missing authenticated Space Memory key")
        return str(access.claims["space_id"]), str(access.claims["agent_id"])

    mcp = FastMCP(
        "Space Memory",
        instructions=(
            "Shared persistent memory for AI agents. Search before beginning work, "
            "remember durable facts and decisions as work progresses, and resume from cursors after restart."
        ),
        token_verifier=SpaceTokenVerifier(),
        auth=AuthSettings(
            issuer_url="https://spacememory.local",
            resource_server_url=None,
            required_scopes=[],
        ),
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool(name="memory_remember", description="Persist a durable fact or project decision.")
    def mcp_memory_remember(
        content: str, session_id: str, idempotency_key: str, kind: str = "fact",
        project_id: str | None = None, conversation_id: str | None = None,
        valid_at: str | None = None,
    ) -> dict[str, Any]:
        space_id, agent_id = mcp_identity()
        parsed_valid_at = parse_valid_at(valid_at)
        with SessionLocal() as db:
            existing = db.scalar(select(Event).where(
                Event.space_id == space_id,
                Event.agent_id == agent_id,
                Event.idempotency_key == idempotency_key,
            ))
            if existing:
                return memory_dict(db.scalar(select(Memory).where(Memory.id == existing.aggregate_id)))
            memory_id = f"mem_{secrets.token_hex(12)}"
            now = utcnow()
            memory = Memory(id=memory_id, space_id=space_id, content=content, kind=kind,
                            version=1, written_by=agent_id, session_id=session_id,
                            project_id=project_id, conversation_id=conversation_id,
                            valid_at=parsed_valid_at, updated_at=now)
            db.add_all([memory, Event(
                event_id=f"evt_{secrets.token_hex(12)}", space_id=space_id,
                agent_id=agent_id, session_id=session_id,
                project_id=project_id, conversation_id=conversation_id,
                idempotency_key=idempotency_key,
                event_type="memory.created", aggregate_id=memory_id, aggregate_version=1, created_at=now,
            )])
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                winning_event = db.scalar(select(Event).where(
                    Event.space_id == space_id,
                    Event.agent_id == agent_id,
                    Event.idempotency_key == idempotency_key,
                ))
                if winning_event is None:
                    raise
                return memory_dict(db.scalar(select(Memory).where(
                    Memory.id == winning_event.aggregate_id,
                    Memory.space_id == space_id,
                )))
            return memory_dict(memory)

    @mcp.tool(name="memory_search", description="Search durable memories in the key's Space.")
    def mcp_memory_search(query: str = "", project_id: str | None = None, kind: str | None = None) -> dict[str, Any]:
        space_id, _agent_id = mcp_identity()
        with SessionLocal() as db:
            statement = select(Memory).where(Memory.space_id == space_id)
            for term in query.split():
                statement = statement.where(Memory.content.ilike(f"%{term}%"))
            if project_id:
                statement = statement.where(Memory.project_id == project_id)
            if kind:
                statement = statement.where(Memory.kind == kind)
            items = [memory_dict(item) for item in db.scalars(statement.order_by(Memory.updated_at.desc()))]
            return {"items": items, "total": len(items)}

    @mcp.tool(name="project_resume", description="Resume a project's latest handoff, state, and recent activity.")
    def mcp_project_resume(project_id: str) -> dict[str, Any]:
        space_id, _agent_id = mcp_identity()
        with SessionLocal() as db:
            memories = list(db.scalars(select(Memory).where(
                Memory.space_id == space_id, Memory.project_id == project_id,
            ).order_by(Memory.updated_at.desc())))
            if not memories:
                raise ValueError("Project not found in this Space")
            last_handoff = next((m for m in memories if m.kind == "handoff"), None)
            events = list(db.scalars(select(Event).where(
                Event.space_id == space_id, Event.project_id == project_id,
            ).order_by(Event.sequence.desc()).limit(20)))
            return {
                "project_id": project_id,
                "last_handoff": memory_dict(last_handoff) if last_handoff else None,
                "last_agent": memories[0].written_by,
                "last_activity": memories[0].updated_at.isoformat(),
                "recent_memories": [memory_dict(m) for m in memories[:10]],
                "recent_events": [
                    {"sequence": e.sequence, "type": e.event_type, "agent_id": e.agent_id,
                     "aggregate_id": e.aggregate_id, "aggregate_version": e.aggregate_version,
                     "created_at": e.created_at.isoformat()}
                    for e in events
                ],
            }

    @mcp.tool(name="events_after", description="Resume confirmed Space events after a durable cursor.")
    def mcp_events_after(cursor: int = 0) -> dict[str, Any]:
        space_id, _agent_id = mcp_identity()
        with SessionLocal() as db:
            events = list(db.scalars(select(Event).where(
                Event.space_id == space_id, Event.sequence > cursor
            ).order_by(Event.sequence)))
            items = [{"sequence": e.sequence, "type": e.event_type, "agent_id": e.agent_id,
                      "aggregate_id": e.aggregate_id, "aggregate_version": e.aggregate_version}
                     for e in events]
            return {"items": items, "next_cursor": items[-1]["sequence"] if items else cursor}

    @mcp.tool(name="vault_set", description="Encrypt and store a secret (password, token) in the vault. Owner-only update/delete; optionally shared read-only.")
    def mcp_vault_set(key: str, value: str, shared_with: list[str] | None = None) -> dict[str, Any]:
        space_id, agent_id = mcp_identity()
        if vault_key_bytes is None:
            raise ValueError("Vault not configured: set SPACE_MEMORY_VAULT_KEY")
        shared = shared_with or []
        now = utcnow().replace(tzinfo=None)
        blob = vault_encrypt(vault_key_bytes, space_id, key, value)
        with SessionLocal() as db:
            existing = db.scalar(select(VaultItem).where(
                VaultItem.space_id == space_id, VaultItem.key_name == key,
            ))
            if existing is not None and existing.owner != agent_id:
                raise PermissionError(f"Secret '{key}' is owned by {existing.owner}")
            if existing is None:
                db.add(VaultItem(
                    space_id=space_id, key_name=key, value_blob=blob, owner=agent_id,
                    shared_with=json.dumps(shared), version=1, created_at=now, updated_at=now,
                ))
                version = 1
            else:
                existing.value_blob = blob
                existing.shared_with = json.dumps(shared)
                existing.version += 1
                existing.updated_at = now
                version = existing.version
            db.add(VaultAudit(
                space_id=space_id, agent_id=agent_id, action="set", key_name=key, created_at=now,
            ))
            db.commit()
        return {"key": key, "version": version}

    @mcp.tool(name="vault_get", description="Decrypt and read a secret from the vault.")
    def mcp_vault_get(key: str) -> dict[str, Any]:
        space_id, agent_id = mcp_identity()
        if vault_key_bytes is None:
            raise ValueError("Vault not configured: set SPACE_MEMORY_VAULT_KEY")
        with SessionLocal() as db:
            item = db.scalar(select(VaultItem).where(
                VaultItem.space_id == space_id, VaultItem.key_name == key,
            ))
            if item is None:
                raise KeyError(f"Secret '{key}' not found")
            shared = json.loads(item.shared_with or "[]")
            if item.owner != agent_id and agent_id not in shared:
                raise PermissionError(f"Access denied to secret '{key}'")
            value = vault_decrypt(vault_key_bytes, space_id, key, item.value_blob)
            db.add(VaultAudit(
                space_id=space_id, agent_id=agent_id, action="get", key_name=key,
                created_at=utcnow().replace(tzinfo=None),
            ))
            db.commit()
        return {"key": key, "value": value, "owner": item.owner, "version": item.version}

    @mcp.tool(name="vault_list", description="List vault keys and metadata (never the secret values).")
    def mcp_vault_list() -> dict[str, Any]:
        space_id, agent_id = mcp_identity()
        if vault_key_bytes is None:
            raise ValueError("Vault not configured: set SPACE_MEMORY_VAULT_KEY")
        with SessionLocal() as db:
            rows = list(db.scalars(select(VaultItem).where(
                VaultItem.space_id == space_id,
            ).order_by(VaultItem.updated_at.desc())))
            items = []
            for r in rows:
                shared = json.loads(r.shared_with or "[]")
                items.append({
                    "key": r.key_name, "owner": r.owner, "shared_with": shared,
                    "version": r.version,
                    "can_read": r.owner == agent_id or agent_id in shared,
                })
            db.add(VaultAudit(
                space_id=space_id, agent_id=agent_id, action="list", key_name="*",
                created_at=utcnow().replace(tzinfo=None),
            ))
            db.commit()
        return {"items": items, "total": len(items)}

    @mcp.tool(name="vault_delete", description="Delete a secret from the vault (owner only).")
    def mcp_vault_delete(key: str) -> dict[str, Any]:
        space_id, agent_id = mcp_identity()
        if vault_key_bytes is None:
            raise ValueError("Vault not configured: set SPACE_MEMORY_VAULT_KEY")
        with SessionLocal() as db:
            item = db.scalar(select(VaultItem).where(
                VaultItem.space_id == space_id, VaultItem.key_name == key,
            ))
            if item is None:
                raise KeyError(f"Secret '{key}' not found")
            if item.owner != agent_id:
                raise PermissionError(f"Only the owner can delete secret '{key}'")
            db.delete(item)
            db.add(VaultAudit(
                space_id=space_id, agent_id=agent_id, action="delete", key_name=key,
                created_at=utcnow().replace(tzinfo=None),
            ))
            db.commit()
        return {"deleted": key}

    mcp_app = mcp.streamable_http_app()
    app.mount("/mcp", mcp_app)
    app.router.lifespan_context = mcp_app.router.lifespan_context
    app.state.mcp = mcp

    return app
