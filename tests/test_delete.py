"""Chapter/page delete — seed and upload both allowed."""

from __future__ import annotations

from app.db import connect, init_db
from app.routers.delete import delete_chapter
from app.settings import get_settings


def test_delete_seed_chapter_removes_db_and_disk(tmp_path, monkeypatch):
    db_path = tmp_path / "fukiflow.db"
    seed_dir = tmp_path / "seed"
    chapter_dir = seed_dir / "test-manga"
    chapter_dir.mkdir(parents=True)
    (chapter_dir / "page_1").mkdir()
    (chapter_dir / "page_1" / "page_1-doc.json").write_text("{}", encoding="utf-8")

    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("SEED_DIR", str(seed_dir))
    get_settings.cache_clear()

    conn = connect(db_path)
    init_db(conn)
    conn.execute(
        "INSERT INTO chapter (slug, title, source_dir, source_type) VALUES (?, ?, ?, ?)",
        ("test-manga", "Test Manga", "batch", "seed"),
    )
    conn.commit()
    conn.close()

    result = delete_chapter("test-manga")
    assert result["ok"] is True
    assert result["slug"] == "test-manga"
    assert not chapter_dir.exists()

    conn = connect(db_path)
    row = conn.execute("SELECT id FROM chapter WHERE slug = ?", ("test-manga",)).fetchone()
    conn.close()
    assert row is None
    get_settings.cache_clear()
