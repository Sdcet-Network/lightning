from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Generator
from urllib.parse import unquote, urlparse

import pymysql
from pymysql.cursors import DictCursor

DEFAULT_DB_URL = os.environ.get(
    "DATABASE_URL", "mysql://lightning:lightning@127.0.0.1:3306/lightning"
)

# A result row: DictCursor yields plain dicts, so `row["col"]` and `dict(row)` both work.
Row = dict

_db_kwargs: dict[str, Any] | None = None

SCHEMA: list[str] = [
    """CREATE TABLE IF NOT EXISTS settings (
        id INT PRIMARY KEY,
        intent_timeout INT NOT NULL DEFAULT 15,
        reason_timeout INT NOT NULL DEFAULT 15
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "INSERT IGNORE INTO settings (id, intent_timeout, reason_timeout) VALUES (1, 15, 15)",
    """CREATE TABLE IF NOT EXISTS projects (
        id VARCHAR(64) PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'active',
        bootstrap_enabled TINYINT(1) NOT NULL DEFAULT 1,
        created_at VARCHAR(64) NOT NULL,
        reason_worker VARCHAR(255),
        reason_trigger TEXT,
        reason_started_at VARCHAR(64),
        reason_last_heartbeat_at VARCHAR(64)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS facts (
        id VARCHAR(64) NOT NULL,
        project_id VARCHAR(64) NOT NULL,
        description TEXT NOT NULL,
        PRIMARY KEY (id, project_id),
        CONSTRAINT fk_facts_project FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS intents (
        id VARCHAR(64) NOT NULL,
        project_id VARCHAR(64) NOT NULL,
        to_fact_id VARCHAR(64),
        description TEXT NOT NULL,
        creator VARCHAR(255) NOT NULL,
        worker VARCHAR(255),
        last_heartbeat_at VARCHAR(64),
        created_at VARCHAR(64) NOT NULL,
        concluded_at VARCHAR(64),
        PRIMARY KEY (id, project_id),
        CONSTRAINT fk_intents_project FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS intent_sources (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        intent_id VARCHAR(64) NOT NULL,
        project_id VARCHAR(64) NOT NULL,
        fact_id VARCHAR(64) NOT NULL,
        UNIQUE KEY uq_intent_sources (intent_id, project_id, fact_id),
        CONSTRAINT fk_intent_sources_intent FOREIGN KEY (intent_id, project_id)
            REFERENCES intents (id, project_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS hints (
        id VARCHAR(64) NOT NULL,
        project_id VARCHAR(64) NOT NULL,
        content TEXT NOT NULL,
        creator VARCHAR(255) NOT NULL,
        created_at VARCHAR(64) NOT NULL,
        PRIMARY KEY (id, project_id),
        CONSTRAINT fk_hints_project FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS counters (
        name VARCHAR(64) PRIMARY KEY,
        value INT NOT NULL DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "INSERT IGNORE INTO counters (name, value) VALUES ('project', 0)",
    """CREATE TABLE IF NOT EXISTS scoped_counters (
        project_id VARCHAR(64) NOT NULL,
        kind VARCHAR(64) NOT NULL,
        value INT NOT NULL DEFAULT 0,
        PRIMARY KEY (project_id, kind),
        CONSTRAINT fk_scoped_counters_project FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]


def configure(url: str) -> None:
    global _db_kwargs
    if _db_kwargs is not None:
        return
    kwargs = _parse_url(url)
    dbname = kwargs["database"]
    assert dbname, "a database name is required in the MySQL URL"
    _ensure_database(kwargs, dbname)
    _db_kwargs = kwargs
    with get_conn() as conn:
        for statement in SCHEMA:
            conn.execute(statement)
        _ensure_project_columns(conn)


def _parse_url(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in ("mysql", "mysql+pymysql"):
        raise ValueError(
            f"unsupported database URL scheme: {parsed.scheme!r} (expected mysql://)"
        )
    database = (parsed.path or "").lstrip("/") or None
    if database:
        database = unquote(database)
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": database,
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": False,
    }


def _ensure_database(kwargs: dict[str, Any], dbname: str) -> None:
    if "`" in dbname:
        raise ValueError(f"invalid database name: {dbname!r}")
    server_kwargs = {**kwargs, "database": None}
    conn = pymysql.connect(**server_kwargs)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{dbname}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    finally:
        conn.close()


def _ensure_project_columns(conn: "Connection") -> None:
    columns = {row["Field"] for row in conn.execute("SHOW COLUMNS FROM projects").fetchall()}
    if "bootstrap_enabled" not in columns:
        conn.execute(
            "ALTER TABLE projects ADD COLUMN bootstrap_enabled TINYINT(1) NOT NULL DEFAULT 1"
        )
        if "bootstrap_mode" in columns:
            conn.execute(
                "UPDATE projects SET bootstrap_enabled = "
                "CASE WHEN bootstrap_mode = 'disabled' THEN 0 ELSE 1 END"
            )


class Connection:
    """Thin wrapper exposing a cursor-returning ``execute()`` for call sites."""

    def __init__(self, conn: pymysql.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, params: tuple | list | None = None) -> pymysql.cursors.Cursor:
        cursor = self._conn.cursor()
        cursor.execute(sql, params or ())
        return cursor


@contextmanager
def get_conn() -> Generator[Connection, None, None]:
    assert _db_kwargs is not None, "database not configured; call db.configure() first"
    conn = pymysql.connect(**_db_kwargs)
    try:
        yield Connection(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
