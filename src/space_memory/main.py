from __future__ import annotations

import os
from pathlib import Path

from .app import create_app


def _database_url() -> str:
    return os.environ.get("SPACE_MEMORY_DATABASE_URL", "sqlite:///./data/space-memory.db")


def _ensure_sqlite_parent(url: str) -> None:
    if url.startswith("sqlite:///"):
        path = url.removeprefix("sqlite:///")
        if path and not path.startswith(":memory:"):
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)


def load_bootstrap_tokens() -> dict[str, dict[str, str]]:
    token = os.environ.get("SPACE_MEMORY_BOOTSTRAP_TOKEN")
    if not token:
        return {}
    return {
        token: {
            "space_id": os.environ.get("SPACE_MEMORY_SPACE_ID", "default"),
            "agent_id": os.environ.get("SPACE_MEMORY_AGENT_ID", "bootstrap-agent"),
        }
    }


def require_pepper() -> str:
    pepper = os.environ.get("SPACE_MEMORY_TOKEN_PEPPER")
    if not pepper:
        raise RuntimeError(
            "SPACE_MEMORY_TOKEN_PEPPER is required and must never be hardcoded. "
            "Set it to a long, stable random secret; it salts every agent credential hash. "
            "Changing it invalidates all stored credential hashes."
        )
    return pepper


pepper = require_pepper()
database_url = _database_url()
_ensure_sqlite_parent(database_url)

app = create_app(
    database_url=database_url,
    token_pepper=pepper,
    bootstrap_tokens=load_bootstrap_tokens(),
)
