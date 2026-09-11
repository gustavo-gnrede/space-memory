from __future__ import annotations

import os

from .app import create_app


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


app = create_app(
    database_url=os.environ.get("SPACE_MEMORY_DATABASE_URL", "sqlite:///./data/space-memory.db"),
    token_pepper=os.environ.get("SPACE_MEMORY_TOKEN_PEPPER", "change-me-before-public-use"),
    bootstrap_tokens=load_bootstrap_tokens(),
)
