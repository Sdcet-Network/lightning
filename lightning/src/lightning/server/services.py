from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException

from lightning.server.db import Connection, Row
from lightning.server.models import Intent, ProjectMeta, ProjectReason

def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_heartbeat(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def next_project_id(conn: Connection) -> str:
    conn.execute("UPDATE counters SET value = value + 1 WHERE name = 'project'")
    row = conn.execute("SELECT value FROM counters WHERE name = 'project'").fetchone()
    return f"proj_{row['value']:03d}"


def _next_scoped_id(
    conn: Connection, kind: str, prefix: str, project_id: str
) -> str:
    conn.execute(
        "INSERT IGNORE INTO scoped_counters (project_id, kind, value) VALUES (%s, %s, 0)",
        (project_id, kind),
    )
    conn.execute(
        "UPDATE scoped_counters SET value = value + 1 WHERE project_id = %s AND kind = %s",
        (project_id, kind),
    )
    row = conn.execute(
        "SELECT value FROM scoped_counters WHERE project_id = %s AND kind = %s",
        (project_id, kind),
    ).fetchone()
    assert row is not None
    return f"{prefix}{row['value']:03d}"


def next_fact_id(conn: Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "fact", "f", project_id)


def next_intent_id(conn: Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "intent", "i", project_id)


def next_hint_id(conn: Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "hint", "h", project_id)


def get_project_or_404(conn: Connection, project_id: str) -> Row:
    row = conn.execute("SELECT * FROM projects WHERE id = %s", (project_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Project not found")
    return row


def check_project_active(conn: Connection, project_id: str) -> Row:
    row = get_project_or_404(conn, project_id)
    if row["status"] != "active":
        raise HTTPException(403, f"Project is {row['status']}")
    return row


def check_project_hint_writable(conn: Connection, project_id: str) -> Row:
    row = get_project_or_404(conn, project_id)
    if row["status"] not in ("active", "stopped", "completed"):
        raise HTTPException(403, f"Project is {row['status']}")
    return row


def check_project_completed(conn: Connection, project_id: str) -> Row:
    row = get_project_or_404(conn, project_id)
    if row["status"] != "completed":
        raise HTTPException(403, f"Project is {row['status']}")
    return row


def validate_facts_exist(
    conn: Connection, project_id: str, fact_ids: list[str]
) -> None:
    for fid in fact_ids:
        row = conn.execute(
            "SELECT 1 FROM facts WHERE id = %s AND project_id = %s", (fid, project_id)
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"Fact {fid} not found")


def validate_goal_not_in_sources(fact_ids: list[str]) -> None:
    if "goal" in fact_ids:
        raise HTTPException(400, "goal cannot be used in from")


def validate_intent_creator_worker(creator: str, worker: str | None) -> None:
    if worker is not None and worker != creator:
        raise HTTPException(400, "worker must be null or equal to creator")


def get_intent_or_404(
    conn: Connection, project_id: str, intent_id: str
) -> Row:
    row = conn.execute(
        "SELECT * FROM intents WHERE id = %s AND project_id = %s",
        (intent_id, project_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Intent not found")
    return row


def get_claimable_open_intent_or_404(
    conn: Connection, project_id: str, intent_id: str, worker: str
) -> Row:
    expire_workers(conn, project_id)
    row = get_intent_or_404(conn, project_id, intent_id)
    if row["to_fact_id"] is not None:
        raise HTTPException(409, "Intent already concluded")
    if row["worker"] is not None and row["worker"] != worker:
        raise HTTPException(409, f"Intent is currently claimed by {row['worker']}")
    return row


def get_releasable_open_intent_or_404(
    conn: Connection, project_id: str, intent_id: str, worker: str
) -> Row:
    expire_workers(conn, project_id)
    row = get_intent_or_404(conn, project_id, intent_id)
    if row["to_fact_id"] is not None:
        raise HTTPException(409, "Intent already concluded")
    if row["worker"] is None:
        return row
    if row["worker"] != worker:
        raise HTTPException(409, f"Intent is currently claimed by {row['worker']}")
    return row


def get_completion_intent_or_409(conn: Connection, project_id: str) -> Row:
    rows = conn.execute(
        "SELECT * FROM intents WHERE project_id = %s AND to_fact_id = 'goal'",
        (project_id,),
    ).fetchall()
    if not rows:
        raise HTTPException(409, "Completed project is missing its completion intent")
    if len(rows) != 1:
        raise HTTPException(409, "Completed project has multiple completion intents")
    return rows[0]


def intent_to_model(conn: Connection, row: Row, project_id: str) -> Intent:
    sources = conn.execute(
        "SELECT fact_id FROM intent_sources WHERE intent_id = %s AND project_id = %s ORDER BY id",
        (row["id"], project_id),
    ).fetchall()
    return Intent(
        id=row["id"],
        **{"from": [s["fact_id"] for s in sources]},
        to=row["to_fact_id"],
        description=row["description"],
        creator=row["creator"],
        worker=row["worker"],
        last_heartbeat_at=row["last_heartbeat_at"],
        created_at=row["created_at"],
        concluded_at=row["concluded_at"],
    )


def build_intents(conn: Connection, project_id: str) -> list[Intent]:
    rows = conn.execute(
        "SELECT * FROM intents WHERE project_id = %s ORDER BY created_at",
        (project_id,),
    ).fetchall()
    return [intent_to_model(conn, r, project_id) for r in rows]


def get_intent_timeout(conn: Connection) -> int:
    row = conn.execute("SELECT intent_timeout FROM settings WHERE id = 1").fetchone()
    return row["intent_timeout"]


def get_reason_timeout(conn: Connection) -> int:
    row = conn.execute("SELECT reason_timeout FROM settings WHERE id = 1").fetchone()
    return row["reason_timeout"]


def project_reason_from_row(row: Row) -> ProjectReason | None:
    if row["reason_worker"] is None:
        return None
    return ProjectReason(
        worker=row["reason_worker"],
        trigger=row["reason_trigger"],
        started_at=row["reason_started_at"],
        last_heartbeat_at=row["reason_last_heartbeat_at"],
    )


def project_meta_from_row(row: Row) -> ProjectMeta:
    return ProjectMeta(
        id=row["id"],
        title=row["title"],
        status=row["status"],
        bootstrap_enabled=bool(row["bootstrap_enabled"]),
        created_at=row["created_at"],
        reason=project_reason_from_row(row),
    )


def clear_project_reason(conn: Connection, project_id: str) -> None:
    conn.execute(
        """
        UPDATE projects
        SET reason_worker = NULL,
            reason_trigger = NULL,
            reason_started_at = NULL,
            reason_last_heartbeat_at = NULL
        WHERE id = %s
        """,
        (project_id,),
    )


def expire_workers(conn: Connection, project_id: str | None = None) -> None:
    timeout = get_intent_timeout(conn)
    now = datetime.now(timezone.utc)
    query = (
        "SELECT id, project_id, last_heartbeat_at FROM intents "
        "WHERE to_fact_id IS NULL AND worker IS NOT NULL AND last_heartbeat_at IS NOT NULL"
    )
    params: tuple = ()
    if project_id is not None:
        query += " AND project_id = %s"
        params = (project_id,)
    for row in conn.execute(query, params).fetchall():
        hb = _parse_heartbeat(row["last_heartbeat_at"])
        if hb is not None and (now - hb).total_seconds() > timeout:
            conn.execute(
                "UPDATE intents SET worker = NULL WHERE id = %s AND project_id = %s",
                (row["id"], row["project_id"]),
            )


def expire_reason_leases(conn: Connection, project_id: str | None = None) -> None:
    timeout = get_reason_timeout(conn)
    now = datetime.now(timezone.utc)
    query = (
        "SELECT id, reason_last_heartbeat_at FROM projects "
        "WHERE reason_worker IS NOT NULL AND reason_last_heartbeat_at IS NOT NULL"
    )
    params: tuple = ()
    if project_id is not None:
        query += " AND id = %s"
        params = (project_id,)
    for row in conn.execute(query, params).fetchall():
        hb = _parse_heartbeat(row["reason_last_heartbeat_at"])
        if hb is not None and (now - hb).total_seconds() > timeout:
            clear_project_reason(conn, row["id"])
