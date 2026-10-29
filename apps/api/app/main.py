"""Augura API — FastAPI application entrypoint.

Container contract (infra/docker/api.Dockerfile, fixed):
workdir=/app/apps/api, PYTHONPATH=/app/apps/api,
CMD `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000`.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    admin,
    analysis,
    assets,
    creatives,
    derivations,
    dnas,
    graph,
    jobs,
    lab,
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
    # 启动时后台对账 Neo4j：幽灵节点/幽灵边会在 /graph（Neo4j 优先读）
    # 直接渲染成重复节点；sync 写入静默失败的设定决定了漂移必然累积，
    # Neo4j 慢启动时由后台任务有界重试，API 本身不等待。
    threading.Thread(target=_reconcile_graph_bg, daemon=True).start()
    yield


def _reconcile_graph_bg(
    *,
    retry_delays: tuple[float, ...] = (1, 2, 5, 10, 20, 30),
    sleep=time.sleep,
) -> bool:
    from app.database import SessionLocal
    from app.services import graph_sync

    for attempt in range(len(retry_delays) + 1):
        try:
            with SessionLocal() as db:
                if graph_sync.reconcile_graph(db, get_settings()):
                    logger.info("Neo4j 启动对账成功（第 %d 次尝试）", attempt + 1)
                    return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Neo4j 启动对账异常（第 %d 次）：%s", attempt + 1, exc)
        if attempt < len(retry_delays):
            sleep(retry_delays[attempt])
    logger.error("Neo4j 启动对账在 %d 次尝试后仍未成功", len(retry_delays) + 1)
    return False


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
    # code/params 为 i18n 结构化字段（增量，旧客户端只看 message）
    content: dict[str, object] = {
        "success": False,
        "data": None,
        "message": exc.message,
    }
    if exc.code:
        content["code"] = exc.code
        content["params"] = exc.params
    return JSONResponse(status_code=exc.status_code, content=content)


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
            "code": "validation_error",
            "params": {"details": details},
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
            "code": "internal_error",
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
app.include_router(jobs.router)
app.include_router(admin.router)
app.include_router(lab.router)
