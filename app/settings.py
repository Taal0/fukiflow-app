from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        root = Path(__file__).resolve().parent.parent
        self.root = root

        seed_env = os.environ.get("SEED_DIR", str(root / "data" / "seed"))
        sp = Path(seed_env)
        self.seed_dir = sp if sp.is_absolute() else (root / sp).resolve()

        db_env = os.environ.get("DB_PATH", str(root / "data" / "fukiflow.db"))
        dp = Path(db_env)
        self.db_path = dp if dp.is_absolute() else (root / dp).resolve()

        self.vision_url = os.environ.get("VISION_URL", "http://127.0.0.1:8001").rstrip(
            "/"
        )
        self.cors_origins = [
            o.strip()
            for o in os.environ.get(
                "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
            ).split(",")
            if o.strip()
        ]
        # Chapter zip download sends many PageDocs; allow a larger body.
        self.max_body_bytes = int(os.environ.get("MAX_BODY_BYTES", str(32 * 1024 * 1024)))
        self.fonts_cache_ttl_s = float(os.environ.get("FONTS_CACHE_TTL_S", "30"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
