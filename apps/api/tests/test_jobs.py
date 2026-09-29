"""Tests for the durable analysis-job queue (models/jobs + worker + retry).

外部依赖全部 mock（Vision/存储/Neo4j），fixture 一律虚构名。
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app import worker
from app.config import Settings
from app.exceptions import ApiError
from app.models import AnalysisJob, CreativeAsset, CreativeVariant
from app.repositories.assets import AssetRepository
from app.repositories.jobs import JobRepository, utcnow
from app.schemas.analysis import AnalysisPayload
from app.services import graph_sync, judge_pipeline, pipeline
from app.services.settings import AIConfig


def _seed_asset(db: Session, filename: str, status: str = "pending") -> CreativeAsset:
    asset = CreativeAsset(
        id=str(uuid.uuid4()),
        filename=filename,
        file_type="video",
        storage_key=f"test/{uuid.uuid4()}",
        analysis_status=status,
    )
    db.add(asset)
    db.flush()
    return asset


# ----------------------------------------------------------------------
# enqueue / upload 路由
# ----------------------------------------------------------------------
class TestEnqueue:
    def test_enqueue_creates_queued_job(self, db_session: Session) -> None:
        asset = _seed_asset(db_session, "KS_FAKE-260901-enqueue-a.mp4")
        repo = JobRepository(db_session)
        assert repo.enqueue(asset.id) is True
        job = repo.get_by_asset(asset.id)
        assert job is not None
        assert job.status == "queued"
        assert job.attempt == 0
        assert job.max_attempts == 3

    def test_enqueue_is_idempotent_per_asset(self, db_session: Session) -> None:
        asset = _seed_asset(db_session, "KS_FAKE-260901-enqueue-b.mp4")
        repo = JobRepository(db_session)
        assert repo.enqueue(asset.id) is True
        assert repo.enqueue(asset.id) is False  # asset_id 唯一 = 幂等键
        jobs = db_session.scalars(
            select(AnalysisJob).where(AnalysisJob.asset_id == asset.id)
        ).all()
        assert len(jobs) == 1


class _FakeStorage:
    def __init__(self, *_args) -> None:  # noqa: ANN002
        pass

    def put_bytes(self, *_args) -> None:  # noqa: ANN002
        pass

    def download_to(self, *_args) -> None:  # noqa: ANN002
        pass

    def get_bytes(self, *_args) -> bytes:  # noqa: ANN002
        return b"\xff\xd8\xff"


@pytest.fixture()
def api_client(db_session: Session, tmp_path: Path) -> TestClient:
    from app.api.deps import get_db, get_storage
    from app.config import get_settings
    from app.main import app

    def _db():  # noqa: ANN202
        yield db_session

    # upload_dir 默认 /app/uploads：CI Linux runner 上 /app 不可写（Excel 上传
    # 会写临时文件），本地 Windows 会解析到当前盘根目录——两种环境都不可依赖，
    # 统一指到 pytest 的 tmp_path。
    settings = get_settings().model_copy(update={"upload_dir": str(tmp_path)})

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_storage] = lambda: _FakeStorage()
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


class TestUploadEnqueue:
    def test_upload_video_creates_job(
        self, api_client: TestClient, db_session: Session
    ) -> None:
        response = api_client.post(
            "/upload",
            files=[("files", ("KS_FAKE-260901-upload-a.mp4", b"\x00fake", "video/mp4"))],
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        asset_id = body["data"]["uploaded"][0]["id"]
        job = JobRepository(db_session).get_by_asset(asset_id)
        assert job is not None
        assert job.status == "queued"

    def test_upload_excel_creates_no_job(
        self, api_client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        import io

        import pandas as pd

        buffer = io.BytesIO()
        pd.DataFrame(
            {"素材名称": ["KS_FAKE-260901-upload-a.mp4"], "消耗": [1.0]}
        ).to_excel(buffer, index=False)
        response = api_client.post(
            "/upload",
            files=[
                (
                    "files",
                    (
                        "KS_FAKE-260901-upload-b.xlsx",
                        buffer.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                )
            ],
        )
        assert response.status_code == 200, response.text
        asset_id = response.json()["data"]["uploaded"][0]["id"]
        assert JobRepository(db_session).get_by_asset(asset_id) is None


# ----------------------------------------------------------------------
# classify_error / backoff（纯函数）
# ----------------------------------------------------------------------
class TestClassifyError:
    def test_missing_key_is_auth(self) -> None:
        assert (
            worker.classify_error(pipeline.MissingAIKeyError("AI API Key 未配置"))
            == "auth"
        )

    def test_openai_401_is_auth(self) -> None:
        request = httpx.Request("POST", "http://fake.local/v1/chat/completions")
        exc = openai.AuthenticationError(
            "bad key", response=httpx.Response(401, request=request), body=None
        )
        assert worker.classify_error(exc) == "auth"

    def test_message_401_is_auth(self) -> None:
        assert worker.classify_error(Exception("Error code: 401 - invalid api key")) == "auth"

    def test_429_is_retryable(self) -> None:
        assert worker.classify_error(Exception("Error code: 429 - rate limit")) == "retryable"

    def test_openai_500_is_retryable(self) -> None:
        request = httpx.Request("POST", "http://fake.local/v1/chat/completions")
        exc = openai.InternalServerError(
            "boom", response=httpx.Response(500, request=request), body=None
        )
        assert worker.classify_error(exc) == "retryable"

    def test_timeout_is_retryable(self) -> None:
        assert worker.classify_error(TimeoutError("request timed out")) == "retryable"

    def test_ffmpeg_failure_is_fatal(self) -> None:
        assert (
            worker.classify_error(RuntimeError("ffmpeg exited with code 1")) == "fatal"
        )


class TestBackoff:
    def test_schedule_steps(self) -> None:
        midpoint = lambda: 0.5  # noqa: E731 — 无 jitter
        assert worker.compute_backoff_seconds(1, rand=midpoint) == 5.0
        assert worker.compute_backoff_seconds(2, rand=midpoint) == 30.0
        assert worker.compute_backoff_seconds(3, rand=midpoint) == 300.0
        assert worker.compute_backoff_seconds(9, rand=midpoint) == 300.0  # 末档封顶

    def test_jitter_range(self) -> None:
        assert worker.compute_backoff_seconds(1, rand=lambda: 0.0) == pytest.approx(4.0)
        assert worker.compute_backoff_seconds(1, rand=lambda: 1.0) == pytest.approx(6.0)


# ----------------------------------------------------------------------
# 启动恢复
# ----------------------------------------------------------------------
@pytest.fixture()
def worker_session(monkeypatch, db_session: Session):  # noqa: ANN201
    """worker 的 SessionLocal 绑到测试连接（改动随测试连接回滚/提交）。"""
    monkeypatch.setattr(
        worker,
        "SessionLocal",
        sessionmaker(bind=db_session.get_bind(), autoflush=False,
                     expire_on_commit=False),
    )
    return db_session


class TestStartupRecovery:
    def test_expired_lease_requeued(self, worker_session: Session) -> None:
        asset = _seed_asset(worker_session, "KS_FAKE-260901-recover-a.mp4", "processing")
        repo = JobRepository(worker_session)
        repo.enqueue(asset.id)
        job = repo.get_by_asset(asset.id)
        assert job is not None
        job.status = "running"
        job.lease_until = utcnow() - timedelta(seconds=30)
        job.worker_id = "dead-worker-1"
        worker_session.flush()

        worker.recover_on_startup()

        worker_session.expire_all()
        job = repo.get_by_asset(asset.id)
        assert job is not None
        assert job.status == "queued"
        assert job.lease_until is None
        assert job.worker_id is None
        assert AssetRepository(worker_session).get(asset.id).analysis_status == "pending"  # type: ignore[union-attr]

    def test_processing_asset_aligned_with_terminal_job(
        self, worker_session: Session
    ) -> None:
        asset = _seed_asset(worker_session, "KS_FAKE-260901-recover-b.mp4", "processing")
        repo = JobRepository(worker_session)
        repo.enqueue(asset.id)
        job = repo.get_by_asset(asset.id)
        assert job is not None
        job.status = "dead"
        job.error_message = "boom"
        worker_session.flush()

        worker.recover_on_startup()

        worker_session.expire_all()
        assert AssetRepository(worker_session).get(asset.id).analysis_status == "failed"  # type: ignore[union-attr]

    def test_processing_asset_without_job_gets_one(
        self, worker_session: Session
    ) -> None:
        asset = _seed_asset(worker_session, "KS_FAKE-260901-recover-c.mp4", "processing")

        worker.recover_on_startup()

        worker_session.expire_all()
        assert AssetRepository(worker_session).get(asset.id).analysis_status == "pending"  # type: ignore[union-attr]
        job = JobRepository(worker_session).get_by_asset(asset.id)
        assert job is not None
        assert job.status == "queued"


class TestLeaseReaper:
    """主循环周期回收（reap_expired_leases）：启动恢复之外的兜底。

    实机教训：worker 被杀后立即重启，启动恢复看到的租约尚未过期，孤儿
    job 会滞留 running——周期回收只动过期租约，不碰存活 worker 的 job。
    """

    def test_reaper_requeues_only_expired(self, worker_session: Session) -> None:
        expired_asset = _seed_asset(
            worker_session, "KS_FAKE-260901-reaper-a.mp4", "processing"
        )
        valid_asset = _seed_asset(
            worker_session, "KS_FAKE-260901-reaper-b.mp4", "processing"
        )
        repo = JobRepository(worker_session)
        repo.enqueue(expired_asset.id)
        repo.enqueue(valid_asset.id)
        expired = repo.get_by_asset(expired_asset.id)
        valid = repo.get_by_asset(valid_asset.id)
        assert expired is not None and valid is not None
        expired.status = "running"
        expired.lease_until = utcnow() - timedelta(seconds=5)
        expired.worker_id = "dead-worker-2"
        valid.status = "running"
        valid.lease_until = utcnow() + timedelta(seconds=600)
        valid.worker_id = "live-worker-1"
        worker_session.flush()

        requeued = worker.reap_expired_leases()

        worker_session.expire_all()
        assert requeued == 1
        expired = repo.get_by_asset(expired_asset.id)
        valid = repo.get_by_asset(valid_asset.id)
        assert expired is not None and valid is not None
        assert expired.status == "queued"
        assert expired.lease_until is None
        assert expired.worker_id is None
        assert valid.status == "running"
        assert valid.worker_id == "live-worker-1"


# ----------------------------------------------------------------------
# SKIP LOCKED 抢任务
# ----------------------------------------------------------------------
def test_skip_locked_concurrent_claims(test_db_url: str) -> None:
    """两个并发 claim 拿到不同 job：第一个事务持锁，第二个跳过该行。"""
    engine = create_engine(test_db_url)
    asset_ids = [str(uuid.uuid4()) for _ in range(2)]
    with Session(engine) as session:
        for index, asset_id in enumerate(asset_ids):
            session.add(
                CreativeAsset(
                    id=asset_id,
                    filename=f"KS_FAKE-260901-skiplocked-{index}.mp4",
                    file_type="video",
                    storage_key=f"test/{uuid.uuid4()}",
                )
            )
        session.flush()
        repo = JobRepository(session)
        for asset_id in asset_ids:
            repo.enqueue(asset_id)
        session.commit()
        job_ids = set(
            session.scalars(
                select(AnalysisJob.id).where(AnalysisJob.asset_id.in_(asset_ids))
            ).all()
        )
    assert len(job_ids) == 2
    try:
        session_a = Session(engine)
        session_b = Session(engine)
        claimed_a = JobRepository(session_a).claim_next("worker-a", lease_seconds=120)
        # A 未提交，行锁仍在；B 必须跳过它拿到另一行
        claimed_b = JobRepository(session_b).claim_next("worker-b", lease_seconds=120)
        assert claimed_a is not None
        assert claimed_b is not None
        assert {claimed_a.id, claimed_b.id} == job_ids
        session_b.commit()
        session_a.rollback()
        session_a.close()
        session_b.close()
    finally:
        with Session(engine) as session:
            session.execute(
                delete(CreativeAsset).where(CreativeAsset.id.in_(asset_ids))
            )
            session.commit()
        engine.dispose()


# ----------------------------------------------------------------------
# 流水线幂等（重跑不重复 Vision、不新建第二个 Variant）
# ----------------------------------------------------------------------
class _CountingAnalysisService:
    calls = 0

    def __init__(self, _config) -> None:  # noqa: ANN001
        pass

    def analyze_frames(self, frames, media_type):  # noqa: ANN001, ANN201
        type(self).calls += 1
        return AnalysisPayload(
            summary="fake summary",
            creative_name="fake-idempotent-creative",
            tags=["fake-tag"],
        )

    def embed(self, _text: str) -> list[float]:
        raise RuntimeError("no embeddings in test")


@pytest.fixture()
def pipeline_mocks(monkeypatch, tmp_path: Path, db_session: Session) -> None:
    """同 test_judge_pipeline 的套路：外部依赖全 mock，SessionLocal 绑测试连接。"""
    _CountingAnalysisService.calls = 0
    monkeypatch.setattr(
        pipeline, "get_settings", lambda: Settings(upload_dir=str(tmp_path))
    )
    monkeypatch.setattr(
        pipeline,
        "SessionLocal",
        sessionmaker(bind=db_session.get_bind(), autoflush=False,
                     expire_on_commit=False),
    )
    monkeypatch.setattr(graph_sync, "sync_asset_subgraph", lambda *a, **k: None)
    monkeypatch.setattr(judge_pipeline, "run_post_analysis", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "StorageService", _FakeStorage)
    monkeypatch.setattr(pipeline, "AnalysisService", _CountingAnalysisService)
    frame = tmp_path / "frame-00.jpg"
    frame.write_bytes(b"\xff\xd8\xff")
    monkeypatch.setattr(
        pipeline, "extract_smart_frames", lambda _v, _d: [(str(frame), 0.0)]
    )
    monkeypatch.setattr(pipeline, "build_contact_sheet", lambda *_a: None)
    monkeypatch.setattr(
        pipeline,
        "resolve_ai_config",
        lambda _db, _s: AIConfig(
            api_key="sk-fake", base_url="", vision_model="fake-vision-v1",
            embedding_model="",
        ),
    )


def _variants_of(db: Session, asset_id: str) -> list[CreativeVariant]:
    return list(
        db.scalars(
            select(CreativeVariant).where(CreativeVariant.asset_id == asset_id)
        ).all()
    )


class TestPipelineIdempotency:
    def test_rerun_skips_vision_and_reuses_variant(
        self, db_session: Session, pipeline_mocks: None
    ) -> None:
        asset = _seed_asset(db_session, "KS_FAKE-260901-idem-a.mp4")

        pipeline.run_analysis_pipeline(asset.id)
        db_session.expire_all()
        assert _CountingAnalysisService.calls == 1
        assert len(_variants_of(db_session, asset.id)) == 1
        first_creative_id = _variants_of(db_session, asset.id)[0].creative_id

        # 崩溃重跑：Vision 已成功过（engine_version 匹配）→ 不再调模型
        pipeline.run_analysis_pipeline(asset.id)
        db_session.expire_all()
        assert _CountingAnalysisService.calls == 1
        variants = _variants_of(db_session, asset.id)
        assert len(variants) == 1
        assert variants[0].creative_id == first_creative_id
        assert (
            AssetRepository(db_session).get(asset.id).analysis_status == "completed"  # type: ignore[union-attr]
        )

    def test_rerun_reports_job_stage(
        self, db_session: Session, pipeline_mocks: None
    ) -> None:
        asset = _seed_asset(db_session, "KS_FAKE-260901-idem-b.mp4")
        repo = JobRepository(db_session)
        repo.enqueue(asset.id)
        job = repo.get_by_asset(asset.id)
        assert job is not None

        pipeline._run_analysis_pipeline(asset.id, job_id=job.id)

        db_session.expire_all()
        # F3：judge 已拆出 pipeline（worker 在分析槽外单独执行并上报 judge
        # stage），pipeline 自身上报的最后一个 stage 是 cluster
        assert repo.get_by_asset(asset.id).stage == "cluster"  # type: ignore[union-attr]


# ----------------------------------------------------------------------
# worker._execute_job 结果落库
# ----------------------------------------------------------------------
class TestExecuteJobOutcome:
    def _seed_running_job(
        self, db: Session, filename: str, **job_kwargs: object
    ) -> tuple[CreativeAsset, AnalysisJob]:
        asset = _seed_asset(db, filename, "processing")
        repo = JobRepository(db)
        repo.enqueue(asset.id)
        job = repo.get_by_asset(asset.id)
        assert job is not None
        job.status = "running"
        for key, value in job_kwargs.items():
            setattr(job, key, value)
        db.flush()
        return asset, job

    def test_success_marks_done(
        self, worker_session: Session, monkeypatch
    ) -> None:
        _asset, job = self._seed_running_job(
            worker_session, "KS_FAKE-260901-exec-a.mp4"
        )
        monkeypatch.setattr(
            worker, "_run_analysis_pipeline", lambda *a, **k: None
        )
        worker._execute_job(job.id)
        worker_session.expire_all()
        job = JobRepository(worker_session).get(job.id)
        assert job.status == "done"  # type: ignore[union-attr]
        assert job.stage == "done"  # type: ignore[union-attr]
        assert job.lease_until is None  # type: ignore[union-attr]

    def test_success_runs_judge_phase(
        self, worker_session: Session, monkeypatch
    ) -> None:
        # F3：分析产出 creative 后，worker 上报 judge stage 并在判定槽跑 judge
        _asset, job = self._seed_running_job(
            worker_session, "KS_FAKE-260901-exec-j.mp4"
        )
        stages: list[str] = []
        judged: list[str] = []
        monkeypatch.setattr(
            worker, "_run_analysis_pipeline", lambda *a, **k: "creative-1"
        )
        monkeypatch.setattr(
            worker,
            "_update_job_stage",
            lambda _job_id, stage: stages.append(stage),
        )
        monkeypatch.setattr(
            worker, "_run_judge_phase", lambda creative_id: judged.append(creative_id)
        )
        worker._execute_job(job.id)
        assert stages == ["judge"]
        assert judged == ["creative-1"]
        worker_session.expire_all()
        job = JobRepository(worker_session).get(job.id)
        assert job.status == "done"  # type: ignore[union-attr]

    def test_auth_failure_waits_for_user(
        self, worker_session: Session, monkeypatch
    ) -> None:
        asset, job = self._seed_running_job(
            worker_session, "KS_FAKE-260901-exec-b.mp4"
        )

        def _raise(*_a, **_k) -> None:  # noqa: ANN001
            raise pipeline.MissingAIKeyError("AI API Key 未配置")

        monkeypatch.setattr(worker, "_run_analysis_pipeline", _raise)
        worker._execute_job(job.id)
        worker_session.expire_all()
        job = JobRepository(worker_session).get(job.id)
        assert job.status == "waiting_user"  # type: ignore[union-attr]
        assert job.error_code == "auth"  # type: ignore[union-attr]
        assert "Settings" in job.error_message  # type: ignore[operator]

    def test_retryable_failure_requeues_with_backoff(
        self, worker_session: Session, monkeypatch
    ) -> None:
        asset, job = self._seed_running_job(
            worker_session, "KS_FAKE-260901-exec-c.mp4"
        )

        def _raise(*_a, **_k) -> None:  # noqa: ANN001
            raise Exception("Error code: 429 - rate limit")

        monkeypatch.setattr(worker, "_run_analysis_pipeline", _raise)
        before = utcnow()
        worker._execute_job(job.id)
        worker_session.expire_all()
        job = JobRepository(worker_session).get(job.id)
        assert job.status == "queued"  # type: ignore[union-attr]
        assert job.attempt == 1  # type: ignore[union-attr]
        # 5s ±20% jitter
        assert job.available_at > before + timedelta(seconds=3)  # type: ignore[operator]
        assert (
            AssetRepository(worker_session).get(asset.id).analysis_status == "pending"  # type: ignore[union-attr]
        )

    def test_retryable_exhaustion_marks_dead(
        self, worker_session: Session, monkeypatch
    ) -> None:
        _asset, job = self._seed_running_job(
            worker_session, "KS_FAKE-260901-exec-d.mp4", attempt=2
        )

        def _raise(*_a, **_k) -> None:  # noqa: ANN001
            raise Exception("Error code: 500 - internal")

        monkeypatch.setattr(worker, "_run_analysis_pipeline", _raise)
        worker._execute_job(job.id)
        worker_session.expire_all()
        job = JobRepository(worker_session).get(job.id)
        assert job.status == "dead"  # type: ignore[union-attr]
        assert job.attempt == 3  # type: ignore[union-attr]

    def test_fatal_failure_marks_failed(
        self, worker_session: Session, monkeypatch
    ) -> None:
        _asset, job = self._seed_running_job(
            worker_session, "KS_FAKE-260901-exec-e.mp4"
        )

        def _raise(*_a, **_k) -> None:  # noqa: ANN001
            raise RuntimeError("ffmpeg exited with code 1")

        monkeypatch.setattr(worker, "_run_analysis_pipeline", _raise)
        worker._execute_job(job.id)
        worker_session.expire_all()
        job = JobRepository(worker_session).get(job.id)
        assert job.status == "failed"  # type: ignore[union-attr]
        assert job.error_code == "fatal"  # type: ignore[union-attr]


# ----------------------------------------------------------------------
# POST /jobs/{asset_id}/retry
# ----------------------------------------------------------------------
class TestRetryEndpoint:
    def test_dead_job_reset_to_queued(self, db_session: Session) -> None:
        from app.api.routes.jobs import retry_analysis_job

        asset = _seed_asset(db_session, "KS_FAKE-260901-retry-a.mp4", "failed")
        repo = JobRepository(db_session)
        repo.enqueue(asset.id)
        job = repo.get_by_asset(asset.id)
        assert job is not None
        job.status = "dead"
        job.attempt = 3
        job.error_code = "retryable"
        job.error_message = "boom"
        db_session.flush()

        envelope = retry_analysis_job(asset_id=asset.id, db=db_session)

        assert envelope.success is True
        assert envelope.data.status == "queued"
        db_session.expire_all()
        job = repo.get_by_asset(asset.id)
        assert job.status == "queued"  # type: ignore[union-attr]
        assert job.attempt == 0  # type: ignore[union-attr]
        assert job.error_message is None  # type: ignore[union-attr]
        assert (
            AssetRepository(db_session).get(asset.id).analysis_status == "pending"  # type: ignore[union-attr]
        )

    def test_done_job_conflict(self, db_session: Session) -> None:
        from app.api.routes.jobs import retry_analysis_job

        asset = _seed_asset(db_session, "KS_FAKE-260901-retry-b.mp4", "completed")
        repo = JobRepository(db_session)
        repo.enqueue(asset.id)
        job = repo.get_by_asset(asset.id)
        assert job is not None
        job.status = "done"
        db_session.flush()

        with pytest.raises(ApiError) as excinfo:
            retry_analysis_job(asset_id=asset.id, db=db_session)
        assert excinfo.value.status_code == 409

    def test_missing_job_404(self, db_session: Session) -> None:
        from app.api.routes.jobs import retry_analysis_job

        asset = _seed_asset(db_session, "KS_FAKE-260901-retry-c.mp4")
        with pytest.raises(ApiError) as excinfo:
            retry_analysis_job(asset_id=asset.id, db=db_session)
        assert excinfo.value.status_code == 404

    def test_missing_asset_404(self, db_session: Session) -> None:
        from app.api.routes.jobs import retry_analysis_job

        with pytest.raises(ApiError) as excinfo:
            retry_analysis_job(asset_id=str(uuid.uuid4()), db=db_session)
        assert excinfo.value.status_code == 404
