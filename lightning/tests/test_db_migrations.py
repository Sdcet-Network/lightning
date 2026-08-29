from __future__ import annotations

from lightning.server import db


def test_ensure_project_columns_adds_bootstrap_enabled(mysql_ready) -> None:
    with db.get_conn() as conn:
        conn.execute("SET FOREIGN_KEY_CHECKS = 0")
        conn.execute("DROP TABLE IF EXISTS projects")
        conn.execute(
            """
            CREATE TABLE projects (
                id VARCHAR(64) PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'active',
                created_at VARCHAR(64) NOT NULL,
                reason_worker VARCHAR(255),
                reason_trigger TEXT,
                reason_started_at VARCHAR(64),
                reason_last_heartbeat_at VARCHAR(64)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        conn.execute(
            "INSERT INTO projects (id, title, created_at) VALUES ('proj_001', 'legacy', '2026-01-01T00:00:00Z')"
        )
        conn.execute("SET FOREIGN_KEY_CHECKS = 1")
        db._ensure_project_columns(conn)
        row = conn.execute("SELECT bootstrap_enabled FROM projects WHERE id = 'proj_001'").fetchone()
    assert row["bootstrap_enabled"] == 1


def test_ensure_project_columns_maps_bootstrap_mode_to_bool(mysql_ready) -> None:
    with db.get_conn() as conn:
        conn.execute("SET FOREIGN_KEY_CHECKS = 0")
        conn.execute("DROP TABLE IF EXISTS projects")
        conn.execute(
            """
            CREATE TABLE projects (
                id VARCHAR(64) PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'active',
                bootstrap_mode VARCHAR(16) NOT NULL DEFAULT 'auto',
                created_at VARCHAR(64) NOT NULL,
                reason_worker VARCHAR(255),
                reason_trigger TEXT,
                reason_started_at VARCHAR(64),
                reason_last_heartbeat_at VARCHAR(64)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        conn.execute(
            "INSERT INTO projects (id, title, bootstrap_mode, created_at) "
            "VALUES ('proj_001', 'disabled', 'disabled', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO projects (id, title, bootstrap_mode, created_at) "
            "VALUES ('proj_002', 'enabled', 'enabled', '2026-01-01T00:00:00Z')"
        )
        conn.execute("SET FOREIGN_KEY_CHECKS = 1")
        db._ensure_project_columns(conn)
        rows = conn.execute("SELECT id, bootstrap_enabled FROM projects ORDER BY id").fetchall()
    assert [(row["id"], row["bootstrap_enabled"]) for row in rows] == [
        ("proj_001", 0),
        ("proj_002", 1),
    ]
