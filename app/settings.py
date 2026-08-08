from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_VALID_VISION_MODES = frozenset({"local", "remote"})


class Settings:
    def __init__(self) -> None:
        root = Path(__file__).resolve().parent.parent
        self.root = root

        seed_env = os.environ.get("SEED_DIR", str(root / "data" / "seed"))
        sp = Path(seed_env)
        self.seed_dir = sp if sp.is_absolute() else (root / sp).resolve()

        upload_env = os.environ.get("UPLOAD_DIR", str(root / "data" / "manga"))
        up = Path(upload_env)
        self.upload_dir = up if up.is_absolute() else (root / up).resolve()

        db_env = os.environ.get("DB_PATH", str(root / "data" / "fukiflow.db"))
        dp = Path(db_env)
        self.db_path = dp if dp.is_absolute() else (root / dp).resolve()

        self.vision_url = os.environ.get("VISION_URL", "http://127.0.0.1:8001").rstrip(
            "/"
        )

        mode = os.environ.get("VISION_MODE", "local").strip().lower()
        if mode not in _VALID_VISION_MODES:
            raise ValueError(
                f"VISION_MODE must be 'local' or 'remote', got {mode!r}"
            )
        self.vision_mode = mode

        full_env = os.environ.get("VISION_FULL_URL", "").strip()
        if self.vision_mode == "remote":
            if not full_env:
                raise ValueError(
                    "VISION_FULL_URL is required when VISION_MODE=remote "
                    "(no silent fallback to light)"
                )
            self.vision_full_url = full_env.rstrip("/")
        else:
            # local: full worker defaults to same host as light (Mac full on :8001)
            self.vision_full_url = (
                full_env.rstrip("/") if full_env else self.vision_url
            )

        self.cors_origins = [
            o.strip()
            for o in os.environ.get(
                "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
            ).split(",")
            if o.strip()
        ]
        # Chapter upload (ZIP / multi-file) + zip download; allow a larger body.
        self.max_body_bytes = int(
            os.environ.get("MAX_BODY_BYTES", str(150 * 1024 * 1024))
        )
        self.fonts_cache_ttl_s = float(os.environ.get("FONTS_CACHE_TTL_S", "30"))

        # Region YOLO verification (translator boxes → promote to bubbles).
        # Keep ≤ vision BUBBLE_CONF (0.65): verify used to default 0.85 and
        # missed bubbles the full-page pass already found. Crops are harder
        # for YOLO than full pages, so matching detect threshold is the floor.
        self.verify_conf = float(os.environ.get("VERIFY_CONF", "0.65"))
        self.verify_padding = float(os.environ.get("VERIFY_PADDING", "0.15"))
        self.verify_pad_min_px = float(os.environ.get("VERIFY_PAD_MIN_PX", "80"))
        self.verify_iou_min = float(os.environ.get("VERIFY_IOU_MIN", "0.30"))
        self.verify_additional_conf = float(
            os.environ.get("VERIFY_ADDITIONAL_CONF", "0.50")
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
