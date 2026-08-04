from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .settings import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS chapter (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    source_dir TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS page (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id INTEGER NOT NULL REFERENCES chapter(id) ON DELETE CASCADE,
    page_no INTEGER NOT NULL,
    stem TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    dpi REAL NOT NULL DEFAULT 350,
    clean_status TEXT NOT NULL DEFAULT 'ok',
    typeset_status TEXT NOT NULL DEFAULT 'ok',
    bubble_count INTEGER NOT NULL DEFAULT 0,
    editor_bubble_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(chapter_id, stem)
);

CREATE TABLE IF NOT EXISTS page_asset (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL REFERENCES page(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    UNIQUE(page_id, kind)
);

CREATE TABLE IF NOT EXISTS layer_asset (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL REFERENCES page(id) ON DELETE CASCADE,
    layer_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    bubble_id INTEGER,
    rel_path TEXT NOT NULL,
    offset_x INTEGER NOT NULL DEFAULT 0,
    offset_y INTEGER NOT NULL DEFAULT 0,
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    UNIQUE(page_id, layer_id)
);

CREATE TABLE IF NOT EXISTS page_edit (
    page_id INTEGER PRIMARY KEY REFERENCES page(id) ON DELETE CASCADE,
    rev INTEGER NOT NULL DEFAULT 0,
    edit_json TEXT NOT NULL,
    done_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_page_chapter ON page(chapter_id, page_no);
CREATE INDEX IF NOT EXISTS idx_page_asset_page ON page_asset(page_id);
CREATE INDEX IF NOT EXISTS idx_layer_asset_page ON layer_asset(page_id);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    if own:
        conn = connect()
    assert conn is not None
    conn.executescript(SCHEMA)
    conn.commit()
    if own:
        conn.close()


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
