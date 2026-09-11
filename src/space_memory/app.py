from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import FileResponse, JSONResponse
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, create_engine, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


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


def create_app(
    *, database_url: str = "sqlite:///./space-memory.db", token_pepper: str,
    bootstrap_tokens: dict[str, dict[str, str]] | None = None,
) -> FastAPI:
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {},
    )
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def token_hash(token: str) -> str:
        return hashlib.sha256(f"{token_pepper}:{token}".encode()).hexdigest()

    if bootstrap_tokens:
        with SessionLocal.begin() as db:
            for token, identity in bootstrap_tokens.items():
                digest = token_hash(token)
                if db.scalar(select(Credential).where(Credential.token_hash == digest)) is None:
                    db.add(Credential(token_hash=digest, **identity))

    app = FastAPI(title="Space Memory", version="0.1.0")

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

    class SpaceTokenVerifier:
        async def verify_token(self, token: str) -> AccessToken | None:
            with SessionLocal() as db:
                credential = db.scalar(
                    select(Credential).where(Credential.token_hash == token_hash(token))
                )
                if credential is None:
                    return None
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

    mcp_app = mcp.streamable_http_app()
    app.mount("/mcp", mcp_app)
    app.router.lifespan_context = mcp_app.router.lifespan_context
    app.state.mcp = mcp

    return app
