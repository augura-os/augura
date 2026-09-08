"""Augura API — FastAPI application entrypoint.

Container contract (infra/docker/api.Dockerfile, fixed):
workdir=/app/apps/api, PYTHONPATH=/app/apps/api,
CMD `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000`.
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    analysis,
    assets,
    creatives,
    derivations,
    dnas,
    graph,
    review,
    settings,
    upload,
)
from app.config import get_settings
from app.exceptions import ApiError
from app.services.storage import StorageService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        StorageService(get_settings()).ensure_bucket()
    except Exception as exc:  # noqa: BLE001 — storage may boot after the API
        logger.warning("MinIO bucket 初始化失败（将在请求时重试）: %s", exc)
    # 启动时后台 flush 遥测队列（endpoint 未配置时为 no-op）——
    # 否则用户永远不会手动跑 scripts/flush_telemetry（beta 实测发现）
    threading.Thread(target=_flush_telemetry_bg, daemon=True).start()
    yield


def _flush_telemetry_bg() -> None:
    try:
        from scripts.flush_telemetry import main as flush_main

        flush_main()
    except Exception as exc:  # noqa: BLE001 — 失败静默，不影响主流程
        logger.debug("telemetry flush on startup skipped: %s", exc)


app = FastAPI(title="Augura API", version="0.1.0", lifespan=lifespan)

# CORS allow-all (contract — local MVP; frontend uses relative paths anyway).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------------
# Unified error envelope: {success: false, data: null, message} (§3)
# ----------------------------------------------------------------------
@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "data": None, "message": exc.message},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    details = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors()
    )
    return JSONResponse(
        status_code=400,
        content={
            "success": False,
            "data": None,
            "message": f"请求参数错误：{details}",
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("未处理异常: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "data": None,
            "message": f"服务器内部错误：{exc}",
        },
    )


# ----------------------------------------------------------------------
# Routers — root-mounted, no prefix (contract §3)
# ----------------------------------------------------------------------
app.include_router(upload.router)
app.include_router(assets.router)
app.include_router(creatives.router)
app.include_router(graph.router)
app.include_router(analysis.router)
app.include_router(settings.router)
app.include_router(review.router)
app.include_router(derivations.router)
app.include_router(dnas.router)
