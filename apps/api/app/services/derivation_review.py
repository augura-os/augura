"""单条裂变边的测量复核（裂变边创建即自动复核 + 存量批量复核共用）。

backfill 的 factor 来自文件名/标签猜测，误判多；本模块用视频内容测量
（services/variant_diff）重判一条 DERIVED_FROM 边：

1. source/target 视频从 MinIO 下载到临时目录（用完清理）
2. Layer 0-2 测量：ffprobe 元数据 + 帧级 pHash 时序对齐 + 可选 fpcalc
3. classify 出确定性因子（aspect-ratio / intro-sticker / brand-endcard）
   → UPDATE factor + edit_logs + Neo4j link_derivation + rebuild_mirror
4. not-a-derivation（对齐比例过低，疑似误链）→ judge_suggestions
   （kind="derivation-factor"）进收件箱人工确认，绝不自动解链
5. 测量定不了的 → derivation_judge LLM 自洽投票 → 同样仅建议级

幂等（upsert 唯一约束；因子相同不动）；全程容错——复核失败只记日志，
绝不影响边本身的存在。注意：失败路径不做 rollback（调用方可能共享
session，rollback 会误伤调用方既有事务状态）；持有长期 session 的
调用方（批量脚本/后台任务）收到 "failed" 后自行 rollback。
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.repositories.graph_mirror import rebuild_mirror
from app.services import graph_sync
from app.services.derivation_judge import judge_factor
from app.services.judge_suggestions import upsert_suggestion
from app.services.settings import resolve_ai_config
from app.services.storage import StorageService
from app.services.variant_diff import classify, describe, measure_pair

logger = logging.getLogger(__name__)

_EDGE_SQL = text(
    "select d.id, d.factor, c.name, "
    "       vs.id, vs.name, fs.id, fs.storage_key, fs.filename, fs.file_type, "
    "       vt.id, vt.name, ft.id, ft.storage_key, ft.filename, ft.file_type, "
    "       d.factor_reviewed "
    "from variant_derivations d "
    "join creative_variants vs on vs.id = d.source_variant_id "
    "join creative_variants vt on vt.id = d.target_variant_id "
    "join creative_assets fs on fs.id = vs.asset_id "
    "join creative_assets ft on ft.id = vt.asset_id "
    "join creatives c on c.id = vs.creative_id "
    "where d.id = :id"
)


class EdgeContext:
    """一条 DERIVED_FROM 边的复核上下文（列名对齐 _EDGE_SQL）。"""

    def __init__(self, row) -> None:  # noqa: ANN001
        (
            self.derivation_id, self.factor, self.creative_name,
            self.source_variant_id, self.source_name,
            self.source_asset_id, self.source_key, self.source_filename,
            self.source_type,
            self.target_variant_id, self.target_name,
            self.target_asset_id, self.target_key, self.target_filename,
            self.target_type,
            self.factor_reviewed,
        ) = row

    @property
    def title(self) -> str:
        return (
            f"{self.creative_name}: {self.source_name[:24]} "
            f"-[{self.factor}]-> {self.target_name[:24]}"
        )


def _analysis_brief(db: Session, asset_id: str) -> str:
    row = db.execute(
        text(
            "select hook, conflict, gameplay, tags "
            "from analysis_results where asset_id = :a"
        ),
        {"a": asset_id},
    ).first()
    if row is None:
        return ""
    hook, conflict, gameplay, tags = row
    return (
        f"钩子：{hook or '-'}\n冲突：{conflict or '-'}\n"
        f"玩法：{gameplay or '-'}\n标签：{', '.join(tags or []) or '-'}"
    )


def _download(
    storage: StorageService, tmp_dir: Path, asset_id: str,
    storage_key: str, filename: str,
) -> str:
    local = tmp_dir / f"{asset_id}{Path(filename).suffix.lower()}"
    storage.download_to(storage_key, str(local))
    return str(local)


def review_derivation_edge(
    db: Session,
    settings: Settings,
    derivation_id: str,
    *,
    dry_run: bool = False,
    sync: bool = True,
    emit: Callable[[str], None] = lambda _msg: None,
) -> str:
    """复核一条裂变边，返回结果分类：

    ``updated:<factor>`` / ``suggested:<factor|not-a-derivation>`` /
    ``unchanged`` / ``undecided`` / ``skipped:non-video`` / ``missing`` /
    ``failed``。``sync=False`` 时跳过 Neo4j/镜像同步（调用方统一收尾，
    如 backfill 脚本的批量重同步）。
    """
    try:
        row = db.execute(_EDGE_SQL, {"id": derivation_id}).first()
        if row is None:
            return "missing"
        edge = EdgeContext(row)
        if edge.factor_reviewed:
            # 人工已拍板因子的边，复核一律跳过（Human > AI）——
            # 否则批量重跑会把人工裁决过的边重新拎进收件箱
            emit(f"skip {edge.title}（因子已人工确认）")
            return "unchanged"
        if edge.source_type != "video" or edge.target_type != "video":
            emit(f"skip {edge.title}（非视频边）")
            return "skipped:non-video"

        storage = StorageService(settings)
        # 系统临时目录而非 settings.upload_dir：容器默认 /app/uploads 在
        # CI runner 等无权限路径上 mkdir 会 PermissionError
        tmp_dir = Path(tempfile.mkdtemp(prefix="deriv-review-"))
        try:
            tmp_dir.mkdir(parents=True, exist_ok=True)
            source_path = _download(
                storage, tmp_dir, edge.source_asset_id,
                edge.source_key, edge.source_filename,
            )
            target_path = _download(
                storage, tmp_dir, edge.target_asset_id,
                edge.target_key, edge.target_filename,
            )
            source_meta, target_meta, diff = measure_pair(source_path, target_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        evidence = describe(diff, source_meta, target_meta)
        factor, reason = classify(diff, source_meta, target_meta)
        emit(f"edge {edge.title}")
        emit(f"     测量: {evidence}")

        if factor is not None and factor != "not-a-derivation":
            emit(f"     判定: {factor}（{reason}）")
            if factor == edge.factor:
                return "unchanged"
            if not dry_run:
                db.execute(
                    text(
                        "update variant_derivations set factor = :f where id = :id"
                    ),
                    {"f": factor, "id": edge.derivation_id},
                )
                db.execute(
                    text(
                        "insert into edit_logs "
                        "(id, entity_type, entity_id, action, field, "
                        " old_value, new_value) "
                        "values (:id, 'derivation', :did, 'update', 'factor', "
                        ":old, :new)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "did": edge.derivation_id,
                        "old": edge.factor,
                        "new": f"{factor}（测量：{reason}）",
                    },
                )
                db.commit()
                if sync:
                    try:
                        graph_sync.get_graph_repository(settings).link_derivation(
                            edge.source_variant_id, edge.target_variant_id, factor
                        )
                    except Exception:  # noqa: BLE001 — Neo4j down 不阻断
                        pass
                    rebuild_mirror(db)
            return f"updated:{factor}"

        if factor == "not-a-derivation":
            # 疑似误链：只进收件箱人工确认，绝不自动解链（Human > AI）
            emit(f"     判定: not-a-derivation（{reason}）→ 建议")
            if not dry_run:
                upsert_suggestion(
                    db, kind="derivation-factor", left_id=edge.derivation_id,
                    right_id=None, verdict="not-a-derivation", votes=3,
                    reason=reason,
                )
                db.commit()
            return "suggested:not-a-derivation"

        config = resolve_ai_config(db, settings)
        judgement = judge_factor(
            config,
            source_name=edge.source_name,
            target_name=edge.target_name,
            current_factor=edge.factor,
            evidence=evidence,
            source_analysis=_analysis_brief(db, edge.source_asset_id),
            target_analysis=_analysis_brief(db, edge.target_asset_id),
        )
        if judgement is None:
            emit(f"     判定: 未定（{reason}；LLM 无建议）")
            return "undecided"
        emit(
            f"     判定: LLM 建议 {judgement.factor} "
            f"({judgement.votes}/3) :: {judgement.reason[:40]}"
        )
        if not dry_run:
            upsert_suggestion(
                db, kind="derivation-factor", left_id=edge.derivation_id,
                right_id=None, verdict=judgement.factor,
                votes=judgement.votes, reason=judgement.reason,
            )
            db.commit()
        return f"suggested:{judgement.factor}"
    except Exception:  # noqa: BLE001 — 复核失败绝不影响边本身
        logger.exception("裂变边复核失败 derivation=%s", derivation_id)
        return "failed"


def run_derivation_review(derivation_id: str) -> None:
    """FastAPI 后台任务入口：自建 session（照 run_analysis_pipeline 模式）。"""
    settings = get_settings()
    db = SessionLocal()
    try:
        result = review_derivation_edge(db, settings, derivation_id)
        logger.info("裂变边复核 derivation=%s → %s", derivation_id, result)
        if result == "failed":
            db.rollback()
    except Exception:  # noqa: BLE001
        logger.exception("裂变边复核后台任务失败 derivation=%s", derivation_id)
        db.rollback()
    finally:
        db.close()
