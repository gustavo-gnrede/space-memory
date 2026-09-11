from __future__ import annotations

import os
import json
from pathlib import Path

from dotenv import load_dotenv

from .app import create_app

load_dotenv(Path.cwd() / ".env")


def _database_url() -> str:
    return os.environ.get("SPACE_MEMORY_DATABASE_URL", "sqlite:///./data/space-memory.db")


def _ensure_sqlite_parent(url: str) -> None:
    if url.startswith("sqlite:///"):
        path = url.removeprefix("sqlite:///")
        if path and not path.startswith(":memory:"):
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)


def load_bootstrap_tokens() -> dict[str, dict[str, str]]:
    tokens: dict[str, dict[str, str]] = {}

    single = os.environ.get("SPACE_MEMORY_BOOTSTRAP_TOKEN")
    if single:
        tokens[single] = {
            "space_id": os.environ.get("SPACE_MEMORY_SPACE_ID", "default"),
            "agent_id": os.environ.get("SPACE_MEMORY_AGENT_ID", "bootstrap-agent"),
        }

    raw = os.environ.get("SPACE_MEMORY_BOOTSTRAP_TOKENS")
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("SPACE_MEMORY_BOOTSTRAP_TOKENS must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("SPACE_MEMORY_BOOTSTRAP_TOKENS must be a JSON object")
        for token, identity in parsed.items():
            if not isinstance(identity, dict) or "agent_id" not in identity:
                raise RuntimeError("each SPACE_MEMORY_BOOTSTRAP_TOKENS entry needs an agent_id")
            tokens[token] = {
                "space_id": identity.get("space_id", "default"),
                "agent_id": identity["agent_id"],
            }

    return tokens


def require_pepper() -> str:
    pepper = os.environ.get("SPACE_MEMORY_TOKEN_PEPPER")
    if not pepper:
        raise RuntimeError(
            "SPACE_MEMORY_TOKEN_PEPPER is required and must never be hardcoded. "
            "Set it to a long, stable random secret; it salts every agent credential hash. "
            "Changing it invalidates all stored credential hashes."
        )
    return pepper


def _rate_limit_per_minute() -> int:
    raw = os.environ.get("SPACE_MEMORY_RATE_LIMIT_PER_MINUTE", "300")
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError("SPACE_MEMORY_RATE_LIMIT_PER_MINUTE must be an integer (0 disables rate limiting)")
    if value < 0:
        raise RuntimeError("SPACE_MEMORY_RATE_LIMIT_PER_MINUTE must be >= 0")
    return value


def _vault_key() -> str | None:
    return os.environ.get("SPACE_MEMORY_VAULT_KEY") or None


pepper = require_pepper()
database_url = _database_url()
_ensure_sqlite_parent(database_url)

app = create_app(
    database_url=database_url,
    token_pepper=pepper,
    bootstrap_tokens=load_bootstrap_tokens(),
    rate_limit_per_minute=_rate_limit_per_minute(),
    vault_key=_vault_key(),
)
