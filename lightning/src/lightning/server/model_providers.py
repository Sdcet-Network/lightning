from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from lightning.server.models import ModelProvider


def _row_to_provider(row) -> ModelProvider:
    return ModelProvider(
        name=row["name"],
        api=row["api"],
        base_url=row["base_url"],
        model=row["model"],
        api_key=row["api_key"],
        context_window=row["context_window"],
        enabled=bool(row["enabled"]),
    )


def list_providers(conn: sqlite3.Connection) -> list[ModelProvider]:
    rows = conn.execute(
        "SELECT name, api, base_url, model, api_key, context_window, enabled "
        "FROM model_providers ORDER BY enabled DESC, name"
    ).fetchall()
    return [_row_to_provider(row) for row in rows]


def replace_providers(conn: sqlite3.Connection, providers: list[ModelProvider]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM model_providers")
    for provider in providers:
        conn.execute(
            "INSERT INTO model_providers "
            "(name, api, base_url, model, api_key, context_window, enabled, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                provider.name,
                provider.api,
                provider.base_url,
                provider.model,
                provider.api_key,
                provider.context_window,
                int(provider.enabled),
                now,
            ),
        )
