from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .db import init_db
from .routers import assets, catalog, compose, download, edits, fonts, pages, typeset, vision
from .settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="FukiFlow App",
    version="0.1.0",
    description="Catalog + seed + vision proxies for the demo UI.",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > settings.max_body_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"request body too large (max {settings.max_body_bytes} bytes)"},
                )
        except ValueError:
            pass
    return await call_next(request)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "vision_mode": settings.vision_mode,
        "vision_url": settings.vision_url,
        "vision_full_url": settings.vision_full_url,
    }


app.include_router(catalog.router)
app.include_router(pages.router)
app.include_router(edits.router)
app.include_router(assets.router)
app.include_router(fonts.router)
app.include_router(typeset.router)
app.include_router(compose.router)
app.include_router(download.router)
app.include_router(vision.router)
