"""Standalone durable analysis worker — ``python -m app.worker``.

Replaces FastAPI BackgroundTasks for post-upload analysis: the API only
INSERTs ``analysis_jobs`` rows; this process claims them with
``SELECT ... FOR UPDATE SKIP LOCKED``, runs the pipeline on a bounded
thread pool (``settings.analysis_concurrency``), renews a DB lease while
working, and classifies failures into auth (waiting_user, no retry) /
retryable (backoff requeue, then dead) / fatal.

Startup recovery re-queues running jobs with expired leases, aligns
assets stuck in ``processing`` with their job's terminal state, and
enqueues ``pending`` assets that have no job row (pre-queue leftovers).
"""

from __future__ import annotations

import logging
import os
import random
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta

import openai

from app.config import get_settings
from app.database import SessionLocal
from app.repositories.assets import AssetRepository
from app.repositories.jobs import JobRepository, utcnow
from app.services.pipeline import (
    MissingAIKeyError,
    _run_analysis_pipeline,
    _run_judge_phase,
    _update_job_stage,
)

logger = logging.getLogger(__name__)

POLL_SECONDS = 1.5
ERROR_BACKOFF_SECONDS = 5.0
LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 30.0
# 主循环周期性回收过期租约的间隔：recover_on_startup 只在启动时跑一次，
# 进程长期运行期间崩溃线程留下的 running job 只能靠它兜底。
REAPER_SECONDS = 30.0
# 按 attempt 取档：第 1 次失败 5s，第 2 次 30s，之后 300s。
BACKOFF_SCHEDULE = (5.0, 30.0, 300.0)
MAX_ERROR_MESSAGE = 2000


def classify_error(exc: BaseException) -> str:
    """失败分类："auth"（waiting_user，不重试）| "retryable"（退避重试）| "fatal"."""
    if isinstance(exc, (MissingAIKeyError, openai.AuthenticationError)):
        return "auth"
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        if status in (401, 403):
            return "auth"
        if status == 429 or status >= 500:
            return "retryable"
        return "fatal"
    if isinstance(exc, (openai.APIConnectionError, TimeoutError, ConnectionError)):
        return "retryable"
    text = str(exc).lower()
    if (
        "401" in text
        or "unauthorized" in text
        or "invalid api key" in text
        or "invalid_api_key" in text
        or "api key 未配置" in text
    ):
        return "auth"
    if (
        "429" in text
        or "rate limit" in text
        or "timed out" in text
        or "timeout" in text
        or "error code: 5" in text  # 500/502/503/504（openai 风格消息）
        or "service unavailable" in text
    ):
        return "retryable"
    return "fatal"


def compute_backoff_seconds(
    attempt: int, rand: Callable[[], float] = random.random
) -> float:
    """重试退避：5s / 30s / 300s（attempt 1 起，超出取末档）±20% jitter。

    ``rand`` 可注入（默认 random.random）以便测试确定性。
    """
    base = BACKOFF_SCHEDULE[min(max(attempt - 1, 0), len(BACKOFF_SCHEDULE) - 1)]
    return base * (1.0 + 0.4 * (rand() - 0.5))


class _LeaseHeartbeat:
    """执行期间周期性续租（独立短 session，30s 一拍）。"""

    def __init__(self, job_id: str, lease_seconds: int) -> None:
        self._job_id = job_id
        self._lease_seconds = lease_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"lease-{job_id[:8]}", daemon=True
        )

    def __enter__(self) -> _LeaseHeartbeat:
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                with SessionLocal() as db:
                    JobRepository(db).renew_lease(
                        self._job_id, lease_seconds=self._lease_seconds
                    )
                    db.commit()
            except Exception:  # noqa: BLE001 — 续租失败下一拍再试
                logger.warning("租约续期失败 job=%s", self._job_id)


def recover_on_startup() -> None:
    """启动恢复：过期租约回 queued；卡在 processing 的 asset 按 job 终态对齐。"""
    with SessionLocal() as db:
        repo = JobRepository(db)
        recovered = repo.requeue_expired_leases()
        if recovered:
            logger.info("恢复过期租约 job：%d 个回 queued", recovered)
        asset_repo = AssetRepository(db)
        for asset in repo.processing_assets():
            job = repo.get_by_asset(asset.id)
            if job is None:
                asset_repo.set_status(asset, "pending", "")
                repo.enqueue(asset.id)
            elif job.status == "done":
                asset_repo.set_status(asset, "completed", "")
            elif job.status in ("failed", "dead", "cancelled", "waiting_user"):
                asset_repo.set_status(
                    asset, "failed", job.error_message or asset.status_message
                )
            elif job.status == "queued":
                asset_repo.set_status(asset, "pending", "")
            # running 且租约未过期 = 另一个活着的 worker 持有，不动
        # 孤儿补建：pending 但无 job 的素材（队列化之前的升级遗留；upload
        # 同事务建 job，正常路径不会产生）——补建队列行，否则永远停 pending。
        orphans = repo.pending_orphan_assets()
        for orphan in orphans:
            repo.enqueue(orphan.id)
        if orphans:
            logger.info("孤儿素材补建：%d 个 pending 素材重新入队", len(orphans))
        db.commit()


def _execute_job(job_id: str) -> None:
    """工作线程入口：跑 pipeline，按异常分类落终态/重试。"""
    with SessionLocal() as db:
        job = JobRepository(db).get(job_id)
        asset_id = job.asset_id if job is not None else None
    if asset_id is None:
        return

    failure: BaseException | None = None
    with _LeaseHeartbeat(job_id, LEASE_SECONDS):
        try:
            creative_id = _run_analysis_pipeline(
                asset_id, job_id=job_id, raise_on_error=True
            )
            if creative_id is not None:
                # F3：判定阶段在分析槽外、判定槽内运行；内部全程容错，
                # 失败不翻转 completed，也不影响本 job 的 done 终态
                _update_job_stage(job_id, "judge")
                _run_judge_phase(creative_id)
        except Exception as exc:  # noqa: BLE001 — 分类落库
            failure = exc

    with SessionLocal() as db:
        repo = JobRepository(db)
        job = repo.get(job_id)
        if job is None:
            return
        job.lease_until = None
        job.worker_id = None
        if failure is None:
            job.status = "done"
            job.stage = "done"
            job.error_code = None
            job.error_message = None
        else:
            kind = classify_error(failure)
            message = str(failure)[:MAX_ERROR_MESSAGE]
            logger.warning("job 失败 kind=%s job=%s: %s", kind, job_id, message)
            if kind == "auth":
                job.status = "waiting_user"
                job.error_code = "auth"
                job.error_message = (
                    f"AI 接口鉴权失败：{message}"
                    "（请到 Settings 检查 API Key / Base URL 后重试）"
                )
            elif kind == "retryable":
                job.attempt += 1
                job.error_code = "retryable"
                job.error_message = message
                if job.attempt < job.max_attempts:
                    delay = compute_backoff_seconds(job.attempt)
                    job.status = "queued"
                    job.available_at = utcnow() + timedelta(seconds=delay)
                    asset = AssetRepository(db).get(job.asset_id)
                    if asset is not None:
                        AssetRepository(db).set_status(
                            asset, "pending", f"等待自动重试（第 {job.attempt} 次失败）"
                        )
                else:
                    job.status = "dead"
            else:
                job.status = "failed"
                job.error_code = "fatal"
                job.error_message = message
        db.commit()


def _claim(worker_id: str) -> str | None:
    """抢一个到期 queued job（同事务置 running + 租约后提交）。"""
    with SessionLocal() as db:
        job = JobRepository(db).claim_next(worker_id, lease_seconds=LEASE_SECONDS)
        if job is None:
            return None
        job_id = job.id
        db.commit()
        return job_id


def reap_expired_leases() -> int:
    """周期回收：把租约过期的 running job 回 queued，返回回收个数。

    实机教训（2026-09-28 杀 worker 演练）：worker 被 `docker kill` 后立即
    重启时 recover_on_startup 看到的租约尚未过期，孤儿 job 会永远滞留在
    running——必须在主循环里周期性回收，而不是只在启动时回收一次。
    """
    with SessionLocal() as db:
        requeued = JobRepository(db).requeue_expired_leases()
        db.commit()
    if requeued:
        logger.info("周期回收：%d 个过期租约 job 回 queued", requeued)
    return requeued


def run_forever() -> None:
    settings = get_settings()
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    recover_on_startup()
    logger.info(
        "analysis worker 启动 worker_id=%s 并发=%d",
        worker_id,
        settings.analysis_concurrency,
    )
    max_workers = max(1, settings.analysis_concurrency)
    last_reap = time.monotonic()
    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="analysis"
    ) as pool:
        running: set[Future[None]] = set()
        while True:
            try:
                done = {future for future in running if future.done()}
                for future in done:
                    exc = future.exception()
                    if exc is not None:
                        logger.error("job 执行器未捕获异常: %s", exc)
                running -= done
                while len(running) < max_workers:
                    job_id = _claim(worker_id)
                    if job_id is None:
                        break
                    logger.info("claim job=%s", job_id)
                    running.add(pool.submit(_execute_job, job_id))
                if time.monotonic() - last_reap >= REAPER_SECONDS:
                    last_reap = time.monotonic()
                    reap_expired_leases()
                time.sleep(POLL_SECONDS)
            except Exception:  # noqa: BLE001 — 主循环永不退出
                logger.exception("worker 主循环出错，%ss 后重试", ERROR_BACKOFF_SECONDS)
                time.sleep(ERROR_BACKOFF_SECONDS)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    run_forever()


if __name__ == "__main__":
    main()
